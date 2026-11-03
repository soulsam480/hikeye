import io
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

from mu_dekhuchi.events import EventStore, MotionDetector
from mu_dekhuchi.vision.base import VisionResult

FRAME_SIZE = (128, 96)


def _jpeg(
    fill: tuple[int, int, int] = (0, 0, 0), box: tuple[int, int, int, int] | None = None
) -> bytes:
    image = Image.new("RGB", FRAME_SIZE, fill)
    if box is not None:
        ImageDraw.Draw(image).rectangle(box, fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _store(tmp_path: Path, cooldown_seconds: int = 20) -> EventStore:
    return EventStore(
        tmp_path / "events.db", tmp_path / "events", cooldown_seconds=cooldown_seconds
    )


def _execute(db_path: Path, sql: str, params: tuple[object, ...]) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(sql, params)
        connection.commit()
    finally:
        connection.close()


def _backdate(db_path: Path, event_id: int, *, days: int = 0, seconds: int = 0) -> None:
    moment = datetime.now(UTC) - timedelta(days=days, seconds=seconds)
    stamp = moment.isoformat(timespec="microseconds")
    _execute(
        db_path,
        "UPDATE events SET created_at = ?, last_seen = ? WHERE id = ?",
        (stamp, stamp, event_id),
    )


def test_add_persists_event_and_snapshot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    jpeg = _jpeg()

    event = store.add(
        "1",
        VisionResult(
            "person", people_count=2, description="two people at the gate", confidence=0.8
        ),
        jpeg,
        "mock-model",
    )

    assert event is not None
    assert event["deduped"] is False
    assert event["event_type"] == "person"
    assert event["people_count"] == 2
    assert event["description"] == "two people at the gate"
    assert event["confidence"] == 0.8
    assert event["model"] == "mock-model"
    assert event["last_seen"] == event["created_at"]

    created = datetime.fromisoformat(event["created_at"])
    assert created.tzinfo is not None
    assert created.utcoffset() == timedelta(0)

    assert not Path(event["snapshot_path"]).is_absolute()
    snapshot = store.snapshot_path(event["id"])
    assert snapshot is not None
    assert snapshot.is_file()
    assert snapshot.read_bytes() == jpeg
    assert snapshot.is_relative_to((tmp_path / "events").resolve())
    assert event["snapshot_path"] == f"1/{snapshot.name}"


def test_add_skips_none_events(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.add("1", VisionResult("none"), _jpeg(), "mock") is None
    assert store.list() == []
    assert list((tmp_path / "events").rglob("*.jpg")) == []


def test_add_dedupes_within_cooldown(tmp_path: Path) -> None:
    store = _store(tmp_path)

    first = store.add("1", VisionResult("person", people_count=1), _jpeg(), "mock")
    second = store.add("1", VisionResult("person", people_count=1), _jpeg((0, 0, 255)), "mock")

    assert first is not None and second is not None
    assert first["deduped"] is False
    assert second["deduped"] is True
    assert second["id"] == first["id"]
    assert second["created_at"] == first["created_at"]
    assert second["last_seen"] >= first["last_seen"]
    assert len(store.list()) == 1
    assert len(list((tmp_path / "events").rglob("*.jpg"))) == 1


def test_dedupe_requires_same_camera_and_counts(tmp_path: Path) -> None:
    store = _store(tmp_path)

    first = store.add("1", VisionResult("person", people_count=1), _jpeg(), "mock")
    more_people = store.add("1", VisionResult("person", people_count=2), _jpeg(), "mock")
    other_camera = store.add("2", VisionResult("person", people_count=1), _jpeg(), "mock")

    assert first is not None and more_people is not None and other_camera is not None
    assert more_people["deduped"] is False
    assert other_camera["deduped"] is False
    assert len({first["id"], more_people["id"], other_camera["id"]}) == 3


def test_add_inserts_again_after_cooldown(tmp_path: Path) -> None:
    store = _store(tmp_path, cooldown_seconds=20)

    first = store.add("1", VisionResult("person", people_count=1), _jpeg(), "mock")
    assert first is not None
    _backdate(tmp_path / "events.db", first["id"], seconds=60)

    second = store.add("1", VisionResult("person", people_count=1), _jpeg(), "mock")

    assert second is not None
    assert second["deduped"] is False
    assert second["id"] != first["id"]
    assert len(store.list()) == 2


def test_list_orders_newest_first_and_filters(tmp_path: Path) -> None:
    store = _store(tmp_path)

    first = store.add("1", VisionResult("person"), _jpeg(), "mock")
    second = store.add("2", VisionResult("vehicle", vehicles_count=1), _jpeg(), "mock")
    third = store.add("1", VisionResult("vehicle", vehicles_count=1), _jpeg(), "mock")
    assert first is not None and second is not None and third is not None

    assert [event["id"] for event in store.list()] == [third["id"], second["id"], first["id"]]
    assert [event["id"] for event in store.list(camera_id="1")] == [third["id"], first["id"]]
    assert len(store.list(limit=0)) == 1
    assert len(store.list(limit=10_000)) == 3


def test_snapshot_path_rejects_paths_outside_events_dir(tmp_path: Path) -> None:
    store = _store(tmp_path)
    event = store.add("1", VisionResult("person"), _jpeg(), "mock")
    assert event is not None

    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"secret")
    db_path = tmp_path / "events.db"

    for escaped in ("../outside.jpg", str(outside), "/etc/passwd"):
        _execute(
            db_path,
            "UPDATE events SET snapshot_path = ? WHERE id = ?",
            (escaped, event["id"]),
        )
        assert store.snapshot_path(event["id"]) is None

    _execute(db_path, "UPDATE events SET snapshot_path = NULL WHERE id = ?", (event["id"],))
    assert store.snapshot_path(event["id"]) is None
    assert store.snapshot_path(9999) is None


def test_prune_removes_old_rows_and_snapshots(tmp_path: Path) -> None:
    store = _store(tmp_path)

    old = store.add("1", VisionResult("person"), _jpeg(), "mock")
    fresh = store.add("1", VisionResult("vehicle", vehicles_count=1), _jpeg(), "mock")
    assert old is not None and fresh is not None

    old_snapshot = store.snapshot_path(old["id"])
    assert old_snapshot is not None
    _backdate(tmp_path / "events.db", old["id"], days=40)

    assert store.prune(30) == 1
    assert [event["id"] for event in store.list()] == [fresh["id"]]
    assert not old_snapshot.exists()

    fresh_snapshot = store.snapshot_path(fresh["id"])
    assert fresh_snapshot is not None
    assert fresh_snapshot.is_file()


def test_prune_keeps_recent_events(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add("1", VisionResult("person"), _jpeg(), "mock")

    assert store.prune(30) == 0
    assert len(store.list()) == 1


def test_motion_detector_first_frame_is_not_motion() -> None:
    detector = MotionDetector()

    assert detector.update(_jpeg()) is False


def test_motion_detector_identical_frames_are_not_motion() -> None:
    detector = MotionDetector()
    frame = _jpeg((10, 20, 30))

    assert detector.update(frame) is False
    assert detector.update(frame) is False


def test_motion_detector_detects_changed_region() -> None:
    detector = MotionDetector()

    assert detector.update(_jpeg()) is False
    assert detector.update(_jpeg(box=(20, 20, 100, 80))) is True


def test_motion_detector_ignores_tiny_changes() -> None:
    detector = MotionDetector()

    assert detector.update(_jpeg()) is False
    assert detector.update(_jpeg(box=(0, 0, 2, 2))) is False


def test_motion_detector_tracks_latest_frame() -> None:
    detector = MotionDetector()
    moved = _jpeg(box=(20, 20, 100, 80))

    assert detector.update(_jpeg()) is False
    assert detector.update(moved) is True
    assert detector.update(moved) is False
