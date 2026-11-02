from pathlib import Path

from netra.config import Settings


def test_parses_cameras_and_encodes_credentials() -> None:
    settings = Settings.load(
        env={
            "STREAM_IP": "192.168.0.100",
            "STREAM_USER": "admin",
            "STREAM_PASS": "p@ss&x",
            "CAMERAS": "1:Front Door,2:Backyard",
            "STREAM_MODE": "decrypt",
        },
        env_file=None,
        cwd=Path("/tmp/netra-config-test"),
    )

    assert [camera.id for camera in settings.cameras] == ["1", "2"]
    assert settings.cameras[0].name == "Front Door"
    assert settings.cameras[0].stream_id == "102"
    assert (
        settings.cameras[0].stream_url
        == "rtsp://admin:p%40ss%26x@192.168.0.100:554/Streaming/Channels/102"
    )
    assert settings.stream_mode == "decrypt"
    assert settings.verification_code == ""


def test_main_stream_uses_channel_01() -> None:
    settings = Settings.load(
        env={
            "STREAM_IP": "10.0.0.5",
            "STREAM_USER": "admin",
            "STREAM_PASS": "secret",
            "CAMERAS": "1:Front,2:Back",
            "STREAM_STREAM": "main",
        },
        env_file=None,
        cwd=Path("/tmp/netra-config-test"),
    )

    assert [camera.stream_id for camera in settings.cameras] == ["101", "201"]
    assert settings.stream_kind == "main"


def test_single_camera_defaults_from_stream_path() -> None:
    settings = Settings.load(
        env={
            "STREAM_IP": "192.168.0.100",
            "STREAM_USER": "admin",
            "STREAM_PASS": "secret",
            "STREAM_PATH": "/Streaming/Channels/302",
            "CAMERA_NAME": "Garage",
        },
        env_file=None,
        cwd=Path("/tmp/netra-config-test"),
    )

    assert len(settings.cameras) == 1
    assert settings.cameras[0].id == "3"
    assert settings.cameras[0].name == "Garage"
    assert settings.cameras[0].stream_id == "302"


def test_invalid_mode_falls_back_to_rtsp() -> None:
    settings = Settings.load(
        env={"STREAM_IP": "192.168.0.100", "STREAM_MODE": "banana"},
        env_file=None,
        cwd=Path("/tmp/netra-config-test"),
    )

    assert settings.stream_mode == "rtsp"


def test_verification_code_is_read_from_secret_key() -> None:
    settings = Settings.load(
        env={
            "STREAM_IP": "192.168.0.100",
            "STREAM_SECRET_KEY": "ABC123",
            "VISION_BACKEND": "liquid",
            "VISION_MODEL": "LiquidAI/LFM2.5-VL-3B",
        },
        env_file=None,
        cwd=Path("/tmp/netra-config-test"),
    )

    assert settings.verification_code == "ABC123"
    assert settings.vision_backend == "liquid"
    assert settings.vision_model == "LiquidAI/LFM2.5-VL-3B"
