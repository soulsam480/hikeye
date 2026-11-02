from netra.camera import CameraWorker, split_jpegs
from netra.config import CameraConfig

JPEG = b"\xff\xd8\xff" + b"payload" + b"\xff\xd9"


def _camera() -> CameraConfig:
    return CameraConfig(
        id="1",
        channel=1,
        name="Front",
        stream_id="102",
        stream_url="rtsp://admin:secret@192.0.2.1:554/Streaming/Channels/102",
        snapshot_url="http://192.0.2.1/ISAPI/Streaming/channels/102/picture",
    )


def test_split_jpegs_handles_multiple_and_partial_frames() -> None:
    frames, rest = split_jpegs(JPEG + b"noise" + JPEG)

    assert frames == [JPEG, JPEG]
    assert rest == b""


def test_split_jpegs_keeps_partial_frame() -> None:
    frames, rest = split_jpegs(JPEG + JPEG[:-2])

    assert frames == [JPEG]
    assert rest == JPEG[:-2]


def test_split_jpegs_discards_bytes_before_soi() -> None:
    frames, _ = split_jpegs(b"garbage" + JPEG)

    assert frames == [JPEG]


def test_worker_starts_idle_and_reports_rtsp_aes() -> None:
    worker = CameraWorker(_camera(), "ABC123")
    status = worker.status()

    assert status["state"] == "idle"
    assert status["live"] is False
    assert status["transport"] == "rtsp+aes"
    assert status["decryption"]["enabled"] is True
    assert status["width"] is None


def test_snapshot_times_out_without_frames() -> None:
    worker = CameraWorker(_camera(), "ABC123")

    try:
        worker.snapshot(timeout=0.05)
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected TimeoutError")
