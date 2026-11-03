from __future__ import annotations

import io
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from mu_dekhuchi.recorder import NalRecorder

NAME_RE = r"^rec-\d{8}-\d{6}\.mp4$"


class _FakeStdin:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> int:
        if self.closed:
            raise ValueError("write to closed pipe")
        self.data += data
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.stdin = _FakeStdin()
        self.stderr = io.BytesIO(stderr)
        self.returncode: int | None = None
        self.killed = False
        self._exit_code = returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self._exit_code
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class _StuckProc(_FakeProc):
    """ffmpeg that ignores stdin EOF until it is killed."""

    def wait(self, timeout: float | None = None) -> int:
        if self.killed:
            return self.returncode if self.returncode is not None else -9
        raise subprocess.TimeoutExpired("ffmpeg", timeout or 0)


class _FakePopen:
    def __init__(self, proc: _FakeProc) -> None:
        self.proc = proc
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> _FakeProc:
        self.calls.append((argv, kwargs))
        return self.proc


@pytest.fixture
def fake_popen(monkeypatch: pytest.MonkeyPatch) -> _FakePopen:
    fake = _FakePopen(_FakeProc())
    monkeypatch.setattr("mu_dekhuchi.recorder.subprocess.Popen", fake)
    return fake


def test_status_starts_idle(tmp_path: Path) -> None:
    status = NalRecorder(tmp_path).status()

    assert status == {
        "recording": False,
        "name": None,
        "bytes": 0,
        "started_at": None,
        "error": None,
    }


def test_start_spawns_ffmpeg_with_expected_arguments(
    tmp_path: Path, fake_popen: _FakePopen
) -> None:
    recorder = NalRecorder(tmp_path)

    status = recorder.start()

    assert status["recording"] is True
    name = status["name"]
    assert isinstance(name, str)
    assert re.fullmatch(NAME_RE, name)
    assert recorder.resolve(name) == tmp_path / name

    started_at = status["started_at"]
    assert isinstance(started_at, str)
    assert datetime.fromisoformat(started_at)

    argv, kwargs = fake_popen.calls[0]
    assert argv == [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "h264",
        "-i",
        "pipe:0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(tmp_path / name),
    ]
    assert kwargs == {"stdin": subprocess.PIPE, "stderr": subprocess.PIPE}


def test_start_twice_raises(tmp_path: Path, fake_popen: _FakePopen) -> None:
    recorder = NalRecorder(tmp_path)
    recorder.start()

    with pytest.raises(RuntimeError):
        recorder.start()


def test_push_writes_annex_b_start_code(tmp_path: Path, fake_popen: _FakePopen) -> None:
    recorder = NalRecorder(tmp_path)
    recorder.start()

    recorder.push(b"\x65payload")

    assert bytes(fake_popen.proc.stdin.data) == b"\x00\x00\x00\x01\x65payload"


def test_push_without_recording_is_a_noop(tmp_path: Path) -> None:
    NalRecorder(tmp_path).push(b"\x65payload")


def test_push_after_stop_is_a_noop(tmp_path: Path, fake_popen: _FakePopen) -> None:
    recorder = NalRecorder(tmp_path)
    recorder.start()
    recorder.stop()

    recorder.push(b"\x65payload")

    assert bytes(fake_popen.proc.stdin.data) == b""


def test_stop_closes_stdin_and_reports_file_size(tmp_path: Path, fake_popen: _FakePopen) -> None:
    recorder = NalRecorder(tmp_path)
    started = recorder.start()
    name = started["name"]
    assert isinstance(name, str)
    (tmp_path / name).write_bytes(b"x" * 32)

    status = recorder.stop()

    assert fake_popen.proc.stdin.closed is True
    assert status == {
        "recording": False,
        "name": name,
        "bytes": 32,
        "started_at": started["started_at"],
        "error": None,
    }


def test_stop_without_recording_returns_idle_status(tmp_path: Path) -> None:
    recorder = NalRecorder(tmp_path)

    assert recorder.stop() == recorder.status()


def test_stop_kills_ffmpeg_after_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _StuckProc()
    monkeypatch.setattr("mu_dekhuchi.recorder.subprocess.Popen", lambda *args, **kwargs: proc)
    recorder = NalRecorder(tmp_path)
    recorder.start()

    status = recorder.stop()

    assert proc.killed is True
    assert status["recording"] is False
    assert status["error"] == "ffmpeg did not exit in time"


def test_stop_reports_last_stderr_line_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _FakeProc(returncode=1, stderr=b"first problem\nInvalid data found\n")
    monkeypatch.setattr("mu_dekhuchi.recorder.subprocess.Popen", lambda *args, **kwargs: proc)
    recorder = NalRecorder(tmp_path)
    recorder.start()

    status = recorder.stop()

    assert status["error"] == "Invalid data found"


def test_list_sorts_newest_first_and_ignores_junk(tmp_path: Path) -> None:
    names = [
        "rec-20260101-000000.mp4",
        "rec-20260102-000000.mp4",
        "rec-20260103-000000.mp4",
    ]
    for index, name in enumerate(names):
        path = tmp_path / name
        path.write_bytes(b"x" * (index + 1))
        stamp = 1_700_000_000 + index * 60
        os.utime(path, (stamp, stamp))
    (tmp_path / "notes.txt").write_text("junk")
    (tmp_path / "rec-2026-01-01.mp4").write_text("bad name")
    (tmp_path / "rec-20260104-000000.mp4").mkdir()

    items = NalRecorder(tmp_path).list()

    assert [item["name"] for item in items] == list(reversed(names))
    assert items[0]["bytes"] == 3
    assert isinstance(items[0]["modified_at"], str)
    assert set(items[0]) == {"name", "bytes", "modified_at"}


def test_list_creates_missing_directory(tmp_path: Path) -> None:
    directory = tmp_path / "recordings"

    assert NalRecorder(directory).list() == []
    assert directory.is_dir()


def test_resolve_accepts_a_recording_name(tmp_path: Path) -> None:
    name = "rec-20260101-120000.mp4"

    assert NalRecorder(tmp_path).resolve(name) == tmp_path / name


@pytest.mark.parametrize(
    "name",
    [
        "../rec-20260101-000000.mp4",
        "/tmp/rec-20260101-000000.mp4",
        "rec-20260101-000000.mp4/../x",
        "rec-20260101-00000.mp4",
        "rec-20260101-000000.mkv",
        "rec-20260101-000000.mp4\n",
        "",
        "notes.txt",
    ],
)
def test_resolve_rejects_invalid_names(tmp_path: Path, name: str) -> None:
    assert NalRecorder(tmp_path).resolve(name) is None


def test_remove_deletes_a_recording(tmp_path: Path) -> None:
    path = tmp_path / "rec-20260101-120000.mp4"
    path.write_bytes(b"data")

    NalRecorder(tmp_path).remove(path.name)

    assert not path.exists()


def test_remove_refuses_the_active_recording(tmp_path: Path, fake_popen: _FakePopen) -> None:
    recorder = NalRecorder(tmp_path)
    started = recorder.start()
    name = started["name"]
    assert isinstance(name, str)
    path = tmp_path / name
    path.write_bytes(b"data")

    with pytest.raises(ValueError):
        recorder.remove(name)

    assert path.exists()


def test_remove_rejects_invalid_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        NalRecorder(tmp_path).remove("../rec-20260101-120000.mp4")
