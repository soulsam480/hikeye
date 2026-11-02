from __future__ import annotations

import argparse
import json
import time

from .camera import CameraWorker
from .config import Settings
from .isapi import IsapiClient
from .server import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="netra", description="Local CCTV dashboard with on-device vision"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the dashboard")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    subparsers.add_parser("check", help="Check the DVR, credentials and first camera")
    codecs = subparsers.add_parser("codecs", help="List channel codecs/resolutions")
    codecs.add_argument("--set-h264", action="store_true", help="Switch every channel to H.264")

    args = parser.parse_args(argv)
    settings = Settings.load()

    if args.command == "serve":
        import uvicorn

        app = create_app(settings)
        uvicorn.run(
            app,
            host=args.host or settings.host,
            port=args.port or settings.port,
            log_level="info",
        )
        return 0

    if args.command == "check":
        return _check(settings)

    if args.command == "codecs":
        return _codecs(settings, set_h264=args.set_h264)

    return 1


def _check(settings: Settings) -> int:
    if not settings.cameras:
        print("No cameras configured. Set STREAM_IP/CAMERAS in .env.")
        return 1

    client = IsapiClient(f"http://{settings.stream_ip}", settings.stream_user, settings.stream_pass)
    code = client.read_verification_code()
    if code:
        settings.verification_code = code
    print(f"verification code: {'read from DVR' if code else 'missing'}")
    print(f"cameras: {', '.join(f'{c.id}:{c.name}' for c in settings.cameras)}")

    camera = CameraWorker(
        settings.cameras[0],
        settings.verification_code,
        ffmpeg_path=settings.ffmpeg_path,
        fps=settings.stream_fps,
        quality=settings.stream_quality,
    )
    camera.start()
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        frame, _ = camera.latest()
        if frame:
            break
        time.sleep(0.25)
    print(json.dumps(camera.status(), indent=2))
    camera.stop()
    return 0 if camera.status()["live"] else 1


def _codecs(settings: Settings, *, set_h264: bool) -> int:
    client = IsapiClient(f"http://{settings.stream_ip}", settings.stream_user, settings.stream_pass)
    for camera in settings.cameras:
        try:
            xml = client.get_channel(camera.stream_id)
        except RuntimeError as error:
            print(f"{camera.id}: {error}")
            continue
        codec = _tag(xml, "videoCodecType")
        width = _tag(xml, "videoResolutionWidth")
        height = _tag(xml, "videoResolutionHeight")
        print(f"{camera.id} ({camera.stream_id}): {codec} {width}x{height}")
        if set_h264 and codec != "H.264":
            client.set_channel_codec(camera.stream_id, "H.264")
            print("  -> set to H.264")
    return 0


def _tag(xml: str, name: str) -> str:
    import re

    match = re.search(rf"<{name}>([^<]*)</{name}>", xml)
    return match.group(1) if match else "?"


if __name__ == "__main__":
    raise SystemExit(main())
