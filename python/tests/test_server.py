from pathlib import Path

from fastapi.testclient import TestClient

from netra.config import Settings
from netra.server import create_app


def _settings(tmp_path: Path, **overrides: str) -> Settings:
    env = {
        "STREAM_IP": "127.0.0.1",
        "STREAM_PORT": "1",
        "STREAM_USER": "admin",
        "STREAM_PASS": "secret",
        "CAMERAS": "1:Test",
        "STREAM_MODE": "rtsp",
        "RECORDINGS_DIR": str(tmp_path / "recordings"),
        "DATA_DIR": str(tmp_path / "data"),
        "VISION_BACKEND": "mock",
    }
    env.update(overrides)
    return Settings.load(env=env, env_file=None, cwd=tmp_path)


def test_health_status_and_ui_contract(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["cameras"] == 1

        status = client.get("/api/status").json()
        assert status["activeId"] == "1"
        assert status["vision"]["name"] == "mock"
        assert set(status["recorder"]) >= {"recording", "durationSeconds", "bytes"}

        cameras = client.get("/api/cameras").json()
        assert cameras["cameras"][0]["id"] == "1"
        assert cameras["activeId"] == "1"

        index = client.get("/")
        assert index.status_code == 200
        assert "eventList" in index.text
        assert "visionChip" in index.text
        assert "/api/events" in client.get("/app.js").text


def test_basic_auth_protects_every_route(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, DASHBOARD_PASSWORD="pw"))

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 401
        assert client.get("/").status_code == 401
        assert client.get("/healthz", auth=("admin", "pw")).status_code == 200
        assert client.get("/healthz", auth=("admin", "wrong")).status_code == 401
