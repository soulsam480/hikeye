from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from dotenv import dotenv_values

DEFAULT_STREAM_PATH = "/Streaming/Channels/102"


def _as_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _as_float(value: str | None, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


@dataclass(frozen=True)
class CameraConfig:
    id: str
    channel: int
    name: str
    stream_id: str
    stream_url: str
    snapshot_url: str


@dataclass
class Settings:
    root: Path
    host: str = "127.0.0.1"
    port: int = 8080
    dashboard_user: str = "admin"
    dashboard_password: str = ""

    stream_mode: str = "rtsp"  # decrypt | rtsp | snapshot
    stream_kind: str = "sub"  # sub | main
    stream_fps: int = 10
    stream_quality: int = 5
    stream_ip: str = ""
    stream_user: str = ""
    stream_pass: str = ""
    verification_code: str = ""
    cameras: list[CameraConfig] = field(default_factory=list)

    recordings_dir: Path = Path("recordings")
    data_dir: Path = Path("data")
    events_db: Path = Path("data/events.db")
    events_dir: Path = Path("data/events")
    event_retention_days: int = 30

    vision_backend: str = "mock"  # mock | openai
    vision_base_url: str = "http://127.0.0.1:8000/v1"
    vision_api_key: str = ""
    vision_model: str = "lfm2.5-vl-3b"
    vision_interval: float = 2.0
    vision_cooldown: int = 20
    vision_max_tokens: int = 160
    event_webhook_url: str = ""

    ffmpeg_path: str = "ffmpeg"

    @property
    def active_camera_id(self) -> str | None:
        return self.cameras[0].id if self.cameras else None

    @classmethod
    def load(
        cls,
        env: dict[str, str] | None = None,
        env_file: Path | None = None,
        cwd: Path | None = None,
    ) -> Settings:
        """Load settings from a dotenv file plus process overrides.

        When `env` is supplied it is treated as the complete override layer
        (process env is not consulted), which keeps tests hermetic.
        """
        if env_file is None and env is None:
            env_file = _default_env_file()

        file_env: dict[str, str] = {}
        if env_file is not None and Path(env_file).exists():
            file_env = {
                key: value for key, value in dotenv_values(env_file).items() if value is not None
            }

        overrides = env if env is not None else os.environ
        merged = {**file_env, **overrides}

        def get(key: str, default: str | None = None) -> str | None:
            value = merged.get(key)
            return value if value not in (None, "") else default

        root = (
            Path(cwd)
            if cwd is not None
            else (Path(env_file).resolve().parent if env_file is not None else Path.cwd())
        )

        host_ip = get("STREAM_IP", "") or ""
        stream_user = get("STREAM_USER", "") or ""
        stream_pass = get("STREAM_PASS", "") or ""
        stream_port = _as_int(get("STREAM_PORT"), 554, 1, 65535)
        stream_kind = "main" if (get("STREAM_STREAM", "sub") or "sub").lower() == "main" else "sub"
        suffix = "01" if stream_kind == "main" else "02"

        stream_path = get("STREAM_PATH")
        if stream_path is None:
            stream_path = (
                f"/Streaming/Channels/{suffix}"
                if stream_kind == "sub"
                else "/Streaming/Channels/101"
            )

        stream_url = get("STREAM_URL") or _build_rtsp_url(
            host_ip, stream_user, stream_pass, stream_port, stream_path
        )

        if not host_ip:
            try:
                from urllib.parse import urlparse

                host_ip = urlparse(stream_url).hostname or ""
            except ValueError:
                host_ip = ""

        raw_cameras = get("CAMERAS", "") or ""
        requested: list[tuple[int, str]] = []
        if raw_cameras:
            for entry in raw_cameras.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                head, _, tail = entry.partition(":")
                try:
                    channel = int(head.strip())
                except ValueError:
                    continue
                if channel < 1:
                    continue
                requested.append((channel, tail.strip() or f"Camera {channel}"))

        if not requested:
            match = _channel_number(stream_path)
            requested.append(
                (
                    int(get("CAMERA_CHANNEL", "0") or 0) or match,
                    get("CAMERA_NAME", "Front Camera") or "Front Camera",
                )
            )

        cameras: list[CameraConfig] = []
        for channel, name in requested:
            stream_id = f"{channel}{suffix}"
            path = f"/Streaming/Channels/{stream_id}"
            cameras.append(
                CameraConfig(
                    id=str(channel),
                    channel=channel,
                    name=name,
                    stream_id=stream_id,
                    stream_url=_build_rtsp_url(
                        host_ip, stream_user, stream_pass, stream_port, path
                    ),
                    snapshot_url=f"http://{host_ip}/ISAPI/Streaming/channels/{stream_id}/picture",
                )
            )

        # Explicit single-camera overrides.
        if len(cameras) == 1 and get("STREAM_URL"):
            first = cameras[0]
            cameras[0] = CameraConfig(
                id=first.id,
                channel=first.channel,
                name=first.name,
                stream_id=first.stream_id,
                stream_url=stream_url,
                snapshot_url=get("SNAPSHOT_URL") or first.snapshot_url,
            )

        raw_mode = (get("STREAM_MODE", "rtsp") or "rtsp").lower()
        stream_mode = raw_mode if raw_mode in {"decrypt", "rtsp", "snapshot"} else "rtsp"

        recordings_dir = root / (get("RECORDINGS_DIR", "recordings") or "recordings")
        data_dir = root / (get("DATA_DIR", "data") or "data")

        return cls(
            root=root,
            host=get("HOST", "127.0.0.1") or "127.0.0.1",
            port=_as_int(get("PORT"), 8080, 1, 65535),
            dashboard_user=get("DASHBOARD_USER", "admin") or "admin",
            dashboard_password=get("DASHBOARD_PASSWORD", "") or "",
            stream_mode=stream_mode,
            stream_kind=stream_kind,
            stream_fps=_as_int(get("STREAM_FPS"), 10, 1, 30),
            stream_quality=_as_int(get("STREAM_QUALITY"), 5, 2, 31),
            stream_ip=host_ip,
            stream_user=stream_user,
            stream_pass=stream_pass,
            verification_code=get("STREAM_SECRET_KEY", "")
            or get("STREAM_VERIFICATION_CODE", "")
            or "",
            cameras=cameras,
            recordings_dir=recordings_dir,
            data_dir=data_dir,
            events_db=data_dir / "events.db",
            events_dir=data_dir / "events",
            event_retention_days=_as_int(get("EVENT_RETENTION_DAYS"), 30, 1, 3650),
            vision_backend=(get("VISION_BACKEND", "mock") or "mock").lower(),
            vision_base_url=get("VISION_BASE_URL", "http://127.0.0.1:8000/v1")
            or "http://127.0.0.1:8000/v1",
            vision_api_key=get("VISION_API_KEY", "") or "",
            vision_model=get("VISION_MODEL", "lfm2.5-vl-3b") or "lfm2.5-vl-3b",
            vision_interval=_as_float(get("VISION_INTERVAL"), 2.0, 0.5, 60.0),
            vision_cooldown=_as_int(get("VISION_COOLDOWN"), 20, 1, 3600),
            vision_max_tokens=_as_int(get("VISION_MAX_TOKENS"), 160, 16, 1024),
            event_webhook_url=get("EVENT_WEBHOOK_URL", "") or "",
            ffmpeg_path=get("FFMPEG_PATH", "ffmpeg") or "ffmpeg",
        )


def _default_env_file() -> Path:
    # python/netra/config.py -> python/netra -> python -> repo root
    return Path(__file__).resolve().parents[2] / ".env"


def _build_rtsp_url(host: str, user: str, password: str, port: int, stream_path: str) -> str:
    auth = ""
    if user:
        auth = f"{quote(user, safe='')}:{quote(password, safe='')}@"
    path = stream_path if stream_path.startswith("/") else f"/{stream_path}"
    return f"rtsp://{auth}{host}:{port}{path}"


def _channel_number(stream_path: str) -> int:
    digits = "".join(ch for ch in stream_path if ch.isdigit())
    if not digits:
        return 1
    return max(1, int(digits) // 100)
