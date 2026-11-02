from __future__ import annotations

import asyncio
import base64
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .engine import EventEngine, create_backend
from .events import EventStore
from .hub import CameraHub, UnknownCameraError
from .isapi import IsapiClient

STATIC_DIR = Path(__file__).parent / "static"
BOUNDARY = "netra-frame"


def create_app(settings: Settings) -> FastAPI:
    hub = CameraHub(settings)
    store = EventStore(settings.events_db, settings.events_dir, settings.vision_cooldown)
    backend = create_backend(settings)
    engine = EventEngine(settings, hub, store, backend)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
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

    @app.post("/api/cameras/{camera_id}/activate")
    async def activate(camera_id: str) -> dict[str, Any]:
        try:
            await asyncio.to_thread(hub.activate, camera_id)
        except UnknownCameraError as error:
            raise HTTPException(status_code=404, detail=f"Unknown camera: {camera_id}") from error
        return {"ok": True, "activeId": hub.active_id, "cameras": hub.list()}

    @app.get("/api/snapshot.jpg")
    async def snapshot(camera: str | None = Query(default=None)) -> Response:
        try:
            worker = await asyncio.to_thread(hub.activate, camera or hub.active_id)
            frame = await asyncio.to_thread(worker.snapshot, 8.0)
        except UnknownCameraError as error:
            raise HTTPException(status_code=404, detail="Unknown camera") from error
        except TimeoutError as error:
            raise HTTPException(status_code=502, detail="Timed out waiting for a frame") from error
        return Response(
            content=frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )

    @app.get("/stream.mjpeg")
    async def stream(camera: str | None = Query(default=None)) -> StreamingResponse:
        try:
            worker = await asyncio.to_thread(hub.activate, camera or hub.active_id)
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
        recorder = _recorder_for(hub, camera)
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
            await asyncio.to_thread(hub.activate, camera or hub.active_id)
            recorder = _recorder_for(hub, camera)
            status = await asyncio.to_thread(recorder.start)
        except (UnknownCameraError, RuntimeError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"ok": True, "recorder": status}

    @app.post("/api/recordings/stop")
    async def stop_recording(camera: str | None = Query(default=None)) -> dict[str, Any]:
        recorder = _recorder_for(hub, camera)
        status = await asyncio.to_thread(recorder.stop)
        return {"ok": True, "recorder": status}

    @app.get("/api/recordings/{name}")
    async def serve_recording(name: str, camera: str | None = Query(default=None)) -> FileResponse:
        recorder = _recorder_for(hub, camera)
        path = recorder.resolve(name)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="Recording not found")
        return FileResponse(path, media_type="video/mp4", filename=name)

    @app.delete("/api/recordings/{name}")
    async def delete_recording(
        name: str, camera: str | None = Query(default=None)
    ) -> dict[str, Any]:
        recorder = _recorder_for(hub, camera)
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

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


def _status_payload(hub: CameraHub, engine: EventEngine) -> dict[str, Any]:
    payload = hub.status_payload()
    payload["vision"] = engine.status()
    return payload


def _read_verification_code(settings: Settings) -> str | None:
    client = IsapiClient(
        f"http://{settings.stream_ip}",
        settings.stream_user,
        settings.stream_pass,
    )
    return client.read_verification_code()


def _recorder_for(hub: CameraHub, camera_id: str | None):
    try:
        return hub.recorder_for(camera_id or hub.active_id)
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


def _host_from_url(url: str) -> str:
    return urlsplit(url).hostname or ""
