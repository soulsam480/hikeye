from __future__ import annotations

from datetime import datetime
from typing import Any

STORY_WINDOW_SECONDS = 180
STORY_KINDS = {"person", "animal"}


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_stories(
    events: list[dict[str, Any]],
    camera_names: dict[str, str],
    *,
    window_seconds: int = STORY_WINDOW_SECONDS,
    min_cameras: int = 2,
) -> list[dict[str, Any]]:
    """Correlate nearby person/animal observations into cross-camera stories.

    This is deliberately conservative in wording: a story is a *possible*
    link based on kind + time, not a biometric identity match.
    """
    stories: list[dict[str, Any]] = []

    for event in sorted(events, key=lambda item: _when(item["created_at"])):
        kind = str(event.get("event_type", "")).lower()
        if kind not in STORY_KINDS:
            continue

        at = _when(event["created_at"])
        candidates = [
            story
            for story in stories
            if story["kind"] == kind and (at - story["_last"]).total_seconds() <= window_seconds
        ]
        if candidates:
            story = min(candidates, key=lambda item: at - item["_last"])
            story["events"].append(event)
            story["_last"] = at
            story["ended_at"] = event["created_at"]
            if event["camera_id"] not in story["_camera_ids"]:
                story["_camera_ids"].append(event["camera_id"])
                story["cameras"].append(
                    {
                        "id": event["camera_id"],
                        "name": camera_names.get(
                            str(event["camera_id"]), f"Camera {event['camera_id']}"
                        ),
                    }
                )
        else:
            story = {
                "kind": kind,
                "events": [event],
                "cameras": [
                    {
                        "id": event["camera_id"],
                        "name": camera_names.get(
                            str(event["camera_id"]), f"Camera {event['camera_id']}"
                        ),
                    }
                ],
                "started_at": event["created_at"],
                "ended_at": event["created_at"],
                "_last": at,
                "_camera_ids": [event["camera_id"]],
            }
            stories.append(story)

    result: list[dict[str, Any]] = []
    for story in stories:
        if len(story["cameras"]) < min_cameras:
            continue
        path = " → ".join(camera["name"] for camera in story["cameras"])
        count = len(story["events"])
        confidence = min(0.9, 0.35 + 0.12 * len(story["cameras"]) + 0.03 * min(count, 6))
        result.append(
            {
                "id": f"story-{story['events'][0]['id']}-{story['events'][-1]['id']}",
                "kind": story["kind"],
                "events": story["events"],
                "latest": story["events"][-1],
                "cameras": story["cameras"],
                "started_at": story["started_at"],
                "ended_at": story["ended_at"],
                "path": path,
                "confidence": round(confidence, 2),
                "narrative": f"Possible {story['kind']} movement across {path}.",
            }
        )

    return sorted(result, key=lambda item: item["ended_at"], reverse=True)
