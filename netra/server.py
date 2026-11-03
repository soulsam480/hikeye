from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import CameraConfig, Settings
from .engine import EventEngine, create_backend
from .events import EventStore
from .hub import CameraHub, UnknownCameraError
from .isapi import IsapiClient
from .ui import render_dashboard

STATIC_DIR = Path(__file__).parent / "static"
BOUNDARY = "netra-frame"


def create_app(settings: Settings) -> FastAPI:
    hub = CameraHub(settings)
    store = EventStore(settings.events_db, settings.events_dir, settings.vision_cooldown)
    backend = create_backend(settings)
    engine = EventEngine(settings, hub, store, backend)
    still_cache: dict[str, tuple[float, bytes]] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        if settings.stream_mode == "decrypt" and not settings.verification_code:
            settings.verification_code = (
                await asyncio.to_thread(_read_verification_code, settings) or ""
            )
        if settings.active_camera_id:
            try:
                await asyncio.to_thread(hub.activate, settings.active_camera_id)
            except Exception as error:
                print(f"[netra] camera start failed: {error}")
        engine.start()
        yield
        engine.stop()
        await asyncio.to_thread(hub.shutdown)

    app = FastAPI(title="Netra", lifespan=lifespan)

    @app.middleware("http")
    async def basic_auth(request: Request, call_next: Any) -> Response:
        if settings.dashboard_password and not _is_authorized(request, settings):
            return Response(
                "Authentication required",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Netra"'},
            )
        return await call_next(request)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        camera = hub.active_camera
        return {
            "ok": True,
            "cameras": len(settings.cameras),
            "activeId": hub.active_id,
            "camera": camera.status()["state"] if camera else "idle",
        }

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return _status_payload(hub, engine)

    @app.get("/api/status/stream")
    async def status_stream() -> StreamingResponse:
        async def events() -> AsyncIterator[bytes]:
            while True:
                payload = json.dumps(_status_payload(hub, engine), default=str)
                yield f"event: status\ndata: {payload}\n\n".encode()
                await asyncio.sleep(2.0)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/cameras")
    async def cameras() -> dict[str, Any]:
        return {"cameras": hub.list(), "activeId": hub.active_id}

    @app.get("/api/cameras/{camera_id}/still.jpg")
    async def camera_still(camera_id: str) -> Response:
        camera = _camera_definition(settings, camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="Unknown camera")

        cached = still_cache.get(camera.id)
        if cached and time.monotonic() - cached[0] < 2.5:
            frame = cached[1]
        else:
            try:
                frame = await asyncio.to_thread(_read_still, settings, camera)
            except Exception:
                worker = hub.active_camera if str(hub.active_id) == camera.id else None
                frame = worker.latest()[0] if worker else b""
                if not frame:
                    raise HTTPException(status_code=502, detail="Snapshot unavailable") from None
            else:
                still_cache[camera.id] = (time.monotonic(), frame)

        return Response(
            content=frame,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/cameras/{camera_id}/activate")
    async def activate(camera_id: str) -> dict[str, Any]:
        try:
            await asyncio.to_thread(hub.activate, camera_id)
        except UnknownCameraError as error:
            raise HTTPException(status_code=404, detail=f"Unknown camera: {camera_id}") from error
        return {"ok": True, "activeId": hub.active_id, "cameras": hub.list()}

    @app.get("/api/snapshot.jpg")
    async def snapshot(camera: str | None = Query(default=None)) -> Response:
        camera_id = _camera_id(hub, camera)
        try:
            worker = await asyncio.to_thread(hub.activate, camera_id)
            frame = await asyncio.to_thread(worker.snapshot, 8.0)
        except UnknownCameraError as error:
            raise HTTPException(status_code=404, detail="Unknown camera") from error
        except TimeoutError as error:
            raise HTTPException(status_code=502, detail="Timed out waiting for a frame") from error
        return Response(
            content=frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )

    @app.websocket("/ws/audio")
    async def audio(
        websocket: WebSocket,
        camera: str | None = Query(default=None),
    ) -> None:
        origin = websocket.headers.get("origin")
        host = websocket.headers.get("host")
        if origin and host and urlsplit(origin).netloc != host:
            await websocket.close(code=1008)
            return

        camera_id = camera or hub.active_id
        definition = _camera_definition(settings, camera_id) if camera_id else None
        if definition is None:
            await websocket.close(code=1008)
            return

        await websocket.accept()
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *_audio_command(settings, definition),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            assert process.stdout is not None
            while True:
                chunk = await process.stdout.read(3200)
                if not chunk:
                    break
                await websocket.send_bytes(chunk)
        except WebSocketDisconnect:
            pass
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                with suppress(ProcessLookupError):
                    await process.wait()
            with suppress(RuntimeError):
                await websocket.close()

    @app.get("/stream.mjpeg")
    async def stream(camera: str | None = Query(default=None)) -> StreamingResponse:
        camera_id = _camera_id(hub, camera)
        try:
            worker = await asyncio.to_thread(hub.activate, camera_id)
        except UnknownCameraError as error:
            raise HTTPException(status_code=404, detail="Unknown camera") from error

        async def frames() -> AsyncIterator[bytes]:
            worker.add_viewer()
            last_sequence = -1
            try:
                while True:
                    frame, sequence = worker.latest()
                    if frame and sequence != last_sequence:
                        last_sequence = sequence
                        header = (
                            f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                            f"Content-Length: {len(frame)}\r\n\r\n"
                        ).encode()
                        yield header + frame + b"\r\n"
                    await asyncio.sleep(0.01)
            finally:
                worker.remove_viewer()

        return StreamingResponse(
            frames(),
            media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/recordings")
    async def list_recordings(camera: str | None = Query(default=None)) -> dict[str, Any]:
        recorder = _recorder_for(hub, _camera_id(hub, camera))
        items = await asyncio.to_thread(recorder.list)
        status = _recorder_status(recorder)
        return {
            "items": [
                {
                    "name": item["name"],
                    "bytes": item.get("bytes", 0),
                    "modifiedAt": item.get("modified_at") or item.get("modifiedAt") or 0,
                    "active": bool(item.get("active"))
                    or (status["recording"] and item["name"] == status["name"]),
                }
                for item in items
            ],
            "active": status,
        }

    @app.post("/api/recordings/start")
    async def start_recording(camera: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            await asyncio.to_thread(hub.activate, _camera_id(hub, camera))
            recorder = _recorder_for(hub, _camera_id(hub, camera))
            status = await asyncio.to_thread(recorder.start)
        except (UnknownCameraError, RuntimeError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"ok": True, "recorder": status}

    @app.post("/api/recordings/stop")
    async def stop_recording(camera: str | None = Query(default=None)) -> dict[str, Any]:
        recorder = _recorder_for(hub, _camera_id(hub, camera))
        status = await asyncio.to_thread(recorder.stop)
        return {"ok": True, "recorder": status}

    @app.get("/api/recordings/{name}")
    async def serve_recording(name: str, camera: str | None = Query(default=None)) -> FileResponse:
        recorder = _recorder_for(hub, _camera_id(hub, camera))
        path = recorder.resolve(name)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="Recording not found")
        return FileResponse(path, media_type="video/mp4")

    @app.delete("/api/recordings/{name}")
    async def delete_recording(
        name: str, camera: str | None = Query(default=None)
    ) -> dict[str, Any]:
        recorder = _recorder_for(hub, _camera_id(hub, camera))
        try:
            await asyncio.to_thread(recorder.remove, name)
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"ok": True}

    @app.get("/api/events")
    async def list_events(
        camera: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        camera_id = camera or None
        return {"items": store.list(camera_id, limit)}

    @app.get("/api/events/{event_id}/snapshot")
    async def event_snapshot(event_id: int) -> FileResponse:
        path = store.snapshot_path(event_id)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="Snapshot not found")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(camera: str | None = Query(default=None)) -> HTMLResponse:
        if camera and camera != hub.active_id:
            try:
                await asyncio.to_thread(hub.activate, camera)
            except UnknownCameraError as error:
                raise HTTPException(status_code=404, detail="Unknown camera") from error
        page = await asyncio.to_thread(render_dashboard, hub, store, engine)
        return HTMLResponse(page, headers={"Cache-Control": "no-store, max-age=0"})

    @app.post("/ui/record/start")
    async def ui_record_start(camera: str | None = Query(default=None)) -> RedirectResponse:
        camera_id = _camera_id(hub, camera)
        try:
            await asyncio.to_thread(hub.activate, camera_id)
            await asyncio.to_thread(hub.recorder_for(camera_id).start)
        except (UnknownCameraError, RuntimeError):
            pass
        return RedirectResponse(url=f"/?camera={camera_id}", status_code=303)

    @app.post("/ui/record/stop")
    async def ui_record_stop(camera: str | None = Query(default=None)) -> RedirectResponse:
        camera_id = _camera_id(hub, camera)
        with suppress(UnknownCameraError):
            await asyncio.to_thread(hub.recorder_for(camera_id).stop)
        return RedirectResponse(url=f"/?camera={camera_id}", status_code=303)

    @app.post("/ui/recordings/{name}/delete")
    async def ui_delete_recording(
        name: str, camera: str | None = Query(default=None)
    ) -> RedirectResponse:
        camera_id = _camera_id(hub, camera)
        with suppress(UnknownCameraError, ValueError, FileNotFoundError):
            await asyncio.to_thread(hub.recorder_for(camera_id).remove, name)
        return RedirectResponse(url=f"/?camera={camera_id}", status_code=303)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


def _status_payload(hub: CameraHub, engine: EventEngine) -> dict[str, Any]:
    payload = hub.status_payload()
    payload["vision"] = engine.status()
    return payload


def _camera_definition(settings: Settings, camera_id: str) -> CameraConfig | None:
    return next((camera for camera in settings.cameras if camera.id == str(camera_id)), None)


def _audio_command(settings: Settings, camera: CameraConfig) -> tuple[str, ...]:
    """Extract the unencrypted PCM μ-law audio track from the camera's RTSP sub stream."""
    return (
        settings.ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-rtsp_transport",
        "tcp",
        "-i",
        camera.stream_url,
        "-vn",
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "pipe:1",
    )


def _read_still(settings: Settings, camera: CameraConfig) -> bytes:
    with IsapiClient(
        f"http://{settings.stream_ip}",
        settings.stream_user,
        settings.stream_pass,
        timeout=5.0,
    ) as client:
        return client.get_snapshot(camera.stream_id)


def _read_verification_code(settings: Settings) -> str | None:
    client = IsapiClient(
        f"http://{settings.stream_ip}",
        settings.stream_user,
        settings.stream_pass,
    )
    return client.read_verification_code()


def _camera_id(hub: CameraHub, camera: str | None) -> str:
    camera_id = camera or hub.active_id
    if not camera_id:
        raise HTTPException(status_code=400, detail="No camera configured")
    return camera_id


def _recorder_for(hub: CameraHub, camera_id: str):
    try:
        return hub.recorder_for(camera_id)
    except UnknownCameraError as error:
        raise HTTPException(status_code=404, detail="Unknown camera") from error


def _recorder_status(recorder: Any) -> dict[str, Any]:
    status = recorder.status()
    return {
        "recording": bool(status.get("recording")),
        "name": status.get("name"),
        "startedAt": status.get("started_at") or status.get("startedAt"),
        "durationSeconds": status.get("duration_seconds") or status.get("durationSeconds") or 0,
        "bytes": status.get("bytes", 0),
        "error": status.get("error"),
    }


def _is_authorized(request: Request, settings: Settings) -> bool:
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode()
    except (ValueError, UnicodeDecodeError):
        return False
    username, separator, password = decoded.partition(":")
    if not separator:
        return False
    return secrets.compare_digest(username, settings.dashboard_user) and secrets.compare_digest(
        password, settings.dashboard_password
    )
