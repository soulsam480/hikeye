from __future__ import annotations

from datetime import datetime
from typing import Any

STORY_WINDOW_SECONDS = 90
STORY_MAX_GAP_SECONDS = 45
STORY_KINDS = {"person", "animal"}
LOOSE_SPAN_SECONDS = 60
LOOSE_EVENT_COUNT = 8


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_stories(
    events: list[dict[str, Any]],
    camera_names: dict[str, str],
    *,
    window_seconds: int = STORY_WINDOW_SECONDS,
    max_gap_seconds: int = STORY_MAX_GAP_SECONDS,
    min_cameras: int = 2,
) -> list[dict[str, Any]]:
    """Correlate nearby person/animal observations into cross-camera stories.

    A story is a *possible* link based on kind + time, not a biometric
    identity match. It is bounded twice: no single gap larger than
    ``max_gap_seconds`` and no span larger than ``window_seconds``, so a
    chain cannot drift across unrelated sightings.
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
            if story["kind"] == kind
            and (at - story["_last"]).total_seconds() <= max_gap_seconds
            and (at - story["_start"]).total_seconds() <= window_seconds
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
                "_start": at,
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
        span = (_when(story["ended_at"]) - _when(story["started_at"])).total_seconds()
        duration = int(span)
        loose = span > LOOSE_SPAN_SECONDS or count > LOOSE_EVENT_COUNT

        camera_factor = min(len(story["cameras"]) - 1, 3) / 3
        time_factor = max(0.0, 1 - span / window_seconds)
        confidence = min(0.8, 0.35 + 0.25 * camera_factor + 0.2 * time_factor)

        if loose:
            title = f"Possible {story['kind']} activity"
            narrative = (
                f"Possible {story['kind']} activity across {path} "
                f"over {duration}s; the link is loose."
            )
            link_label = "loose link"
        else:
            title = f"Possible {story['kind']} movement"
            narrative = f"Possible {story['kind']} movement across {path}."
            link_label = f"{round(confidence * 100)}% link"

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
                "link_label": link_label,
                "loose": loose,
                "title": title,
                "duration_seconds": duration,
                "narrative": narrative,
            }
        )

    return sorted(result, key=lambda item: item["ended_at"], reverse=True)
