from __future__ import annotations

import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

from .config import CameraConfig, Settings
from .events import EventStore
from .hub import CameraHub
from .isapi import IsapiClient
from .vision.base import VisionBackend, VisionResult
from .vision.prompts import MOTION_PROMPT


def accept_motion(result: VisionResult, min_confidence: float) -> bool:
    """Keep living movement; vehicles stay on the active-camera VLM."""
    return result.event_type in {"person", "animal"} and result.confidence >= min_confidence


class StillMotion:
    """Cheap motion score for full-size ISAPI stills.

    A small mask hides the DVR's burned-in timestamp and camera label so the
    clock ticking over does not count as movement. Very large changes (IR mode,
    camera reboot) are treated as a scene change and recalibrate the baseline.
    """

    def __init__(
        self,
        width: int = 96,
        height: int = 72,
        diff_threshold: int = 25,
        changed_ratio: float = 0.004,
        scene_ratio: float = 0.5,
    ) -> None:
        self.size = (width, height)
        self.diff_threshold = diff_threshold
        self.changed_ratio = changed_ratio
        self.scene_ratio = scene_ratio
        self._previous: np.ndarray | None = None
        self._mask = np.ones((height, width), dtype=bool)
        self._mask[: max(1, height // 9), : max(1, width * 2 // 5)] = False
        self._mask[-max(1, height // 12) :, width * 3 // 10 : width * 7 // 10] = False

    def update(self, jpeg: bytes) -> bool:
        with Image.open(io.BytesIO(jpeg)) as image:
            frame = np.asarray(image.convert("L").resize(self.size), dtype=np.int16)

        previous = self._previous
        self._previous = frame
        if previous is None:
            return False

        changed = np.abs(frame - previous) > self.diff_threshold
        ratio = float(changed[self._mask].mean())
        if ratio > self.scene_ratio:
            return False
        return ratio >= self.changed_ratio


class StillScanner:
    """Scan inactive channels for movement without opening another RTSP session."""

    def __init__(
        self,
        settings: Settings,
        hub: CameraHub,
        store: EventStore,
        backend: VisionBackend,
        *,
        analysis_lock: threading.Lock | None = None,
        interval: float | None = None,
    ) -> None:
        self.settings = settings
        self.hub = hub
        self.store = store
        self.backend = backend
        self.analysis_lock = analysis_lock or threading.Lock()
        self.interval = interval or settings.vision_scan_interval
        self.cooldown = max(float(settings.vision_cooldown), 30.0)
        self._last_probe: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._detectors: dict[str, StillMotion] = {}
        self._scanned = 0
        self._events = 0
        self._last_scan_at: float | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="still-scanner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "interval": self.interval,
                "scanned": self._scanned,
                "events": self._events,
                "lastScanAt": self._last_scan_at,
            }

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            cameras = [
                camera
                for camera in self.settings.cameras
                if str(camera.id) != str(self.hub.active_id)
            ]
            if not cameras:
                continue

            with ThreadPoolExecutor(max_workers=min(4, len(cameras))) as pool:
                stills = list(pool.map(self._fetch, cameras))

            with self._lock:
                self._scanned += len(stills)
                self._last_scan_at = time.time()

            for camera, jpeg in stills:
                if not jpeg:
                    continue
                detector = self._detectors.setdefault(camera.id, StillMotion())
                if not detector.update(jpeg):
                    continue

                now = time.monotonic()
                if now - self._last_probe.get(camera.id, 0.0) < self.cooldown:
                    continue
                self._last_probe[camera.id] = now

                try:
                    with self.analysis_lock:
                        check = self.backend.analyze(jpeg, MOTION_PROMPT)
                except Exception:
                    continue
                if not accept_motion(check, self.settings.vision_min_confidence):
                    continue

                description = check.description.strip()
                if not description:
                    description = f"{check.event_type.capitalize()} movement detected"
                people = check.people_count or (1 if check.event_type == "person" else 0)
                result = VisionResult(
                    event_type="motion",
                    people_count=people,
                    vehicles_count=check.vehicles_count,
                    description=description,
                    confidence=check.confidence,
                )
                event = self.store.add(camera.id, result, jpeg, "still-scan")
                with self._lock:
                    if event and not event.get("deduped"):
                        self._events += 1

    def _fetch(self, camera: CameraConfig) -> tuple[CameraConfig, bytes]:
        try:
            with IsapiClient(
                f"http://{self.settings.stream_ip}",
                self.settings.stream_user,
                self.settings.stream_pass,
                timeout=4.0,
            ) as client:
                return camera, client.get_snapshot(camera.stream_id)
        except Exception:
            return camera, b""
