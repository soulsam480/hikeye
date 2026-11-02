from __future__ import annotations

import threading
import time
from contextlib import suppress
from typing import Any

import httpx

from .config import Settings
from .events import EventStore, MotionDetector
from .hub import CameraHub
from .vision.base import VisionBackend, VisionUnavailable
from .vision.mock import MockVision
from .vision.prompts import PROMPT


class EventEngine:
    """Sample the active camera, gate on motion, and run one VLM call at a time."""

    def __init__(
        self,
        settings: Settings,
        hub: CameraHub,
        store: EventStore,
        backend: VisionBackend,
    ) -> None:
        self.settings = settings
        self.hub = hub
        self.store = store
        self.backend = backend
        self._detector = MotionDetector()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_sequence = -1
        self._last_camera_id: str | None = None
        self._last_event: dict[str, Any] | None = None
        self._last_error = ""
        self._last_run_at: float | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="event-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self.backend.status(),
                "running": bool(self._thread and self._thread.is_alive()),
                "interval": self.settings.vision_interval,
                "cooldown": self.settings.vision_cooldown,
                "lastEvent": self._last_event,
                "lastError": self._last_error or None,
                "lastRunAt": self._last_run_at,
            }

    def _run(self) -> None:
        while not self._stop.wait(self.settings.vision_interval):
            camera = self.hub.active_camera
            if camera is None:
                continue

            if camera.camera.id != self._last_camera_id:
                self._last_camera_id = camera.camera.id
                self._last_sequence = -1
                self._detector = MotionDetector()

            frame, sequence = camera.latest()
            if not frame or sequence == self._last_sequence:
                continue
            self._last_sequence = sequence

            if not self._detector.update(frame):
                continue

            try:
                result = self.backend.analyze(frame, PROMPT)
            except VisionUnavailable as error:
                self._record_error(str(error))
                continue
            except Exception as error:
                self._record_error(str(error))
                continue

            self._last_run_at = time.time()
            if result.event_type == "none":
                continue

            event = self.store.add(camera.camera.id, result, frame, self.backend.model)
            if event:
                with self._lock:
                    self._last_event = event
                self._notify_webhook(event)

    def _record_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message[:300]

    def _notify_webhook(self, event: dict[str, Any]) -> None:
        url = self.settings.event_webhook_url
        if not url:
            return
        with suppress(httpx.HTTPError):
            httpx.post(url, json=event, timeout=3.0)


def create_backend(settings: Settings) -> VisionBackend:
    if settings.vision_backend == "openai":
        from .vision.openai import OpenAIVision

        return OpenAIVision(
            base_url=settings.vision_base_url,
            model=settings.vision_model,
            api_key=settings.vision_api_key,
            max_tokens=settings.vision_max_tokens,
        )
    return MockVision()
