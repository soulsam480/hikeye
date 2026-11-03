from pathlib import Path

from fastapi.testclient import TestClient

from netra.config import Settings
from netra.server import _audio_command, create_app


def _settings(tmp_path: Path, *, dashboard_password: str = "") -> Settings:
    return Settings.from_mapping(
        {
            "server": {
                "host": "127.0.0.1",
                "port": 8080,
                "dashboard_user": "admin",
                "dashboard_password": dashboard_password,
            },
            "dvr": {"host": "127.0.0.1", "port": 1, "user": "admin", "password": "secret"},
            "capture": {"mode": "rtsp", "stream": "sub"},
            "cameras": [{"id": "1", "name": "Test", "channel": 1}],
            "vision": {"backend": "mock"},
            "events": {
                "db": str(tmp_path / "data/events.db"),
                "dir": str(tmp_path / "data/events"),
            },
            "recording": {"dir": str(tmp_path / "recordings")},
        },
        root=tmp_path,
    )


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
        assert "Activity journal" in index.text
        assert "All cameras" in index.text
        assert "Test" in index.text
        assert "app.js?v=" in index.text
        assert "styles.css?v=" in index.text
        assert index.headers["cache-control"].startswith("no-store")
        assert client.get("/static/styles.css").status_code == 200


def test_audio_command_extracts_pcm(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    command = _audio_command(settings, settings.cameras[0])

    assert "-vn" in command
    assert command[command.index("-map") + 1] == "0:a:0"
    assert command[command.index("-ar") + 1] == "16000"
    assert command[-1] == "pipe:1"
    assert settings.cameras[0].stream_url in command


def test_still_route_returns_jpeg(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "netra.server._read_still",
        lambda settings, camera: b"\xff\xd8still",
    )
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        still = client.get("/api/cameras/1/still.jpg")
        assert still.status_code == 200
        assert still.content == b"\xff\xd8still"
        assert still.headers["content-type"] == "image/jpeg"
        assert client.get("/api/cameras/nope/still.jpg").status_code == 404


def test_basic_auth_protects_every_route(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, dashboard_password="pw"))

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 401
        assert client.get("/").status_code == 401
        assert client.get("/healthz", auth=("admin", "pw")).status_code == 200
        assert client.get("/healthz", auth=("admin", "wrong")).status_code == 401
