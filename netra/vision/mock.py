from __future__ import annotations

from .base import VisionResult


class MockVision:
    """Deterministic backend for development and tests."""

    name = "mock"
    model = "mock"

    def analyze(self, jpeg: bytes, prompt: str) -> VisionResult:
        return VisionResult(
            event_type="person",
            people_count=1,
            vehicles_count=0,
            description="Mock detection",
            confidence=0.5,
        )

    def status(self) -> dict[str, object]:
        return {
            "name": self.name,
            "model": self.model,
            "available": True,
            "error": None,
        }
