"""Motion gating and the SQLite-backed event log.

The dashboard process owns one writer (the vision engine) and any number of
readers (FastAPI request handlers), so every operation here takes a process
lock and uses a short-lived connection.
"""

from __future__ import annotations

import builtins
import io
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from netra.vision.base import VisionResult

MAX_LIMIT = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  camera_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  event_type TEXT NOT NULL,
  people_count INTEGER NOT NULL DEFAULT 0,
  vehicles_count INTEGER NOT NULL DEFAULT 0,
  description TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0,
  snapshot_path TEXT,
  model TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_camera_time ON events(camera_id, created_at DESC);
"""

INSERT_EVENT = """
INSERT INTO events (
  camera_id, created_at, last_seen, event_type,
  people_count, vehicles_count, description, confidence, snapshot_path, model
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class EventStore:
    """Structured vision events plus their JPEG snapshots.

    An event is identified by camera + label + counts. Repeats inside the
    cooldown extend ``last_seen`` instead of inserting a duplicate, so one row
    describes one continuing occurrence.
    """

    def __init__(self, db_path: Path, events_dir: Path, cooldown_seconds: int = 20) -> None:
        self.db_path = Path(db_path)
        self.events_dir = Path(events_dir)
        self.cooldown = timedelta(seconds=max(0, cooldown_seconds))
        self._root = self.events_dir.resolve()
        self._lock = threading.Lock()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def add(self, camera_id: str, result: VisionResult, jpeg: bytes, model: str) -> dict | None:
        """Store one vision result; ``None`` when the model saw nothing."""
        if result.event_type == "none":
            return None

        camera = _camera_segment(camera_id)
        now = datetime.now(UTC)
        stamp = _iso(now)

        with self._lock, self._connect() as conn:
            existing = self._find_within_cooldown(conn, camera, result, now)
            if existing is not None:
                conn.execute(
                    "UPDATE events SET last_seen = ? WHERE id = ?", (stamp, existing["id"])
                )
                conn.commit()
                return {**existing, "last_seen": stamp, "deduped": True}

            snapshot = self._write_snapshot(camera, now, jpeg)
            try:
                cursor = conn.execute(
                    INSERT_EVENT,
                    (
                        camera,
                        stamp,
                        stamp,
                        result.event_type,
                        result.people_count,
                        result.vehicles_count,
                        result.description,
                        result.confidence,
                        snapshot,
                        model,
                    ),
                )
                conn.commit()
            except sqlite3.Error:
                self._remove_snapshot(snapshot)
                raise

            row = conn.execute("SELECT * FROM events WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return {**dict(row), "deduped": False}

    def list(self, camera_id: str | None = None, limit: int = 50) -> builtins.list[dict[str, Any]]:
        """Newest first, optionally for one camera. ``limit`` is clamped 1..500."""
        capped = max(1, min(MAX_LIMIT, int(limit)))
        query = "SELECT * FROM events"
        params: list[object] = []
        if camera_id is not None:
            query += " WHERE camera_id = ?"
            params.append(camera_id)
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(capped)

        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def snapshot_path(self, event_id: int) -> Path | None:
        """Absolute snapshot path, or ``None`` if missing or outside the store."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT snapshot_path FROM events WHERE id = ?", (event_id,)
            ).fetchone()
        if row is None:
            return None
        return self._resolve_snapshot(row["snapshot_path"])

    def prune(self, days: int) -> int:
        """Delete events whose last occurrence is older than ``days``."""
        cutoff = _iso(datetime.now(UTC) - timedelta(days=max(0, days)))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, snapshot_path FROM events WHERE last_seen < ?", (cutoff,)
            ).fetchall()
            conn.execute("DELETE FROM events WHERE last_seen < ?", (cutoff,))
            conn.commit()

        for row in rows:
            self._remove_snapshot(row["snapshot_path"])
        return len(rows)

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.commit()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
        finally:
            conn.close()

    def _find_within_cooldown(
        self,
        conn: sqlite3.Connection,
        camera_id: str,
        result: VisionResult,
        now: datetime,
    ) -> dict | None:
        row = conn.execute(
            """
            SELECT * FROM events
            WHERE camera_id = ? AND event_type = ? AND people_count = ? AND vehicles_count = ?
            ORDER BY last_seen DESC LIMIT 1
            """,
            (camera_id, result.event_type, result.people_count, result.vehicles_count),
        ).fetchone()
        if row is None:
            return None
        if now - _parse_iso(row["last_seen"]) > self.cooldown:
            return None
        return dict(row)

    def _write_snapshot(self, camera_id: str, moment: datetime, jpeg: bytes) -> str:
        directory = self.events_dir / camera_id
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{moment.strftime('%Y%m%dT%H%M%S-%f')}.jpg"
        (directory / name).write_bytes(jpeg)
        return f"{camera_id}/{name}"

    def _remove_snapshot(self, relative: str | None) -> None:
        path = self._resolve_snapshot(relative)
        if path is not None:
            path.unlink(missing_ok=True)

    def _resolve_snapshot(self, relative: str | None) -> Path | None:
        """Resolve a stored path only if it is a file inside ``events_dir``."""
        if not relative:
            return None
        candidate = (self.events_dir / relative).resolve()
        if not candidate.is_relative_to(self._root) or not candidate.is_file():
            return None
        return candidate


class MotionDetector:
    """Frame differencing on a downscaled grayscale image.

    Cheap enough to run on every sampled frame: only meaningful changes in a
    small percentage of the frame report motion, which filters sensor noise and
    compression artifacts before the vision model is invoked.
    """

    def __init__(
        self,
        width: int = 64,
        height: int = 48,
        diff_threshold: int = 25,
        changed_ratio: float = 0.01,
    ) -> None:
        self.size = (width, height)
        self.diff_threshold = diff_threshold
        self.changed_ratio = changed_ratio
        self._previous: np.ndarray | None = None

    def update(self, jpeg: bytes) -> bool:
        """Return True when the frame changed enough since the previous call."""
        with Image.open(io.BytesIO(jpeg)) as image:
            frame = np.asarray(image.convert("L").resize(self.size), dtype=np.int16)

        previous = self._previous
        self._previous = frame
        if previous is None:
            return False

        changed = np.abs(frame - previous) > self.diff_threshold
        return bool(changed.mean() > self.changed_ratio)


def _camera_segment(camera_id: str) -> str:
    segment = str(camera_id).strip()
    if not segment or segment in {".", ".."} or "/" in segment or "\\" in segment:
        raise ValueError(f"Invalid camera id: {camera_id!r}")
    return segment


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
