from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class VisionUnavailable(RuntimeError):
    """The configured model/runtime could not be loaded."""


class VisionParseError(RuntimeError):
    """The model returned text that is not a valid event object."""


@dataclass(frozen=True)
class VisionResult:
    event_type: str
    people_count: int = 0
    vehicles_count: int = 0
    description: str = ""
    confidence: float = 0.0


class VisionBackend(Protocol):
    name: str
    model: str

    def analyze(self, jpeg: bytes, prompt: str) -> VisionResult: ...

    def status(self) -> dict[str, object]: ...
