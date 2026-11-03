from __future__ import annotations

import json
import re

from .base import VisionParseError, VisionResult

EVENT_TYPES = {"person", "vehicle", "animal", "motion", "none"}

MOTION_PROMPT = """You are a CCTV motion filter. Look at the image and return JSON only.

Schema:
{
  "event_type": "person|animal|vehicle|none",
  "people_count": 0,
  "vehicles_count": 0,
  "description": "short factual sentence",
  "confidence": 0.0
}

Rules:
- Use "none" for empty scenes, laundry, clothes, trees, bushes, shadows, rain,
  lighting changes, camera noise, and parked objects with no living subject.
- Use "person", "animal", or "vehicle" only when a real one is clearly visible.
- If something is visible, describe the movement in one short factual sentence.
- Count clearly visible people and vehicles.
- Do not guess an identity or intent.
- confidence is 0.0-1.0; stay below 0.75 when unsure.
"""

PROMPT = """You are a CCTV analyst. Look at the image and return JSON only.

Schema:
{
  "event_type": "person|vehicle|animal|motion|none",
  "people_count": 0,
  "vehicles_count": 0,
  "description": "short factual sentence",
  "confidence": 0.0
}

Rules:
- Use "none" when no person, vehicle, animal or notable motion is visible.
- Report "person" only when a human is clearly visible (head/torso/limbs), not
  a shadow, reflection, motion blur, animal or object.
- If the frame is too dark or blurred to identify, use "none" and confidence 0.
- Count every clearly visible person and vehicle.
- Describe only what is visible; do not guess identities or intent.
- confidence is 0.0-1.0; stay below 0.75 when unsure.
"""


def parse_result(text: str) -> VisionResult:
    """Parse a model response, tolerating fenced JSON and extra prose."""
    payload = _extract_json(text)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as error:
        raise VisionParseError(f"Invalid JSON: {error}") from error

    if not isinstance(data, dict):
        raise VisionParseError("Model output is not a JSON object")

    event_type = str(data.get("event_type", "none")).lower().strip()
    if event_type not in EVENT_TYPES:
        raise VisionParseError(f"Unknown event_type: {event_type!r}")

    return VisionResult(
        event_type=event_type,
        people_count=_count(data.get("people_count")),
        vehicles_count=_count(data.get("vehicles_count")),
        description=str(data.get("description", ""))[:300],
        confidence=_confidence(data.get("confidence")),
    )


def _extract_json(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise VisionParseError("No JSON object in model output")
    return text[start : end + 1]


def _count(value: object) -> int:
    if not isinstance(value, (int, float, str)):
        return 0
    try:
        return max(0, min(99, int(value)))
    except (TypeError, ValueError):
        return 0


def _confidence(value: object) -> float:
    if not isinstance(value, (int, float, str)):
        return 0.0
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
