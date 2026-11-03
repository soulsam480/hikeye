from __future__ import annotations

from mu_dekhuchi.stories import build_stories


def _event(
    event_id: int,
    camera_id: str,
    at: str,
    kind: str = "person",
    description: str = "A person is walking.",
) -> dict:
    return {
        "id": event_id,
        "camera_id": camera_id,
        "event_type": kind,
        "created_at": at,
        "description": description,
    }


def test_build_stories_links_cameras_by_time_and_kind() -> None:
    events = [
        _event(1, "4", "2026-11-03T10:00:00+00:00"),
        _event(2, "2", "2026-11-03T10:00:30+00:00"),
        _event(3, "7", "2026-11-03T10:01:00+00:00"),
    ]
    stories = build_stories(
        events,
        {"4": "Gate", "2": "Driveway", "7": "Porch"},
    )

    assert len(stories) == 1
    story = stories[0]
    assert story["kind"] == "person"
    assert story["path"] == "Gate → Driveway → Porch"
    assert len(story["events"]) == 3
    assert story["narrative"] == "Possible person movement across Gate → Driveway → Porch."


def test_build_stories_requires_multiple_cameras() -> None:
    events = [
        _event(1, "4", "2026-11-03T10:00:00+00:00"),
        _event(2, "4", "2026-11-03T10:00:20+00:00"),
    ]
    assert build_stories(events, {"4": "Gate"}) == []


def test_build_stories_breaks_on_time_gap_and_kind() -> None:
    events = [
        _event(1, "4", "2026-11-03T10:00:00+00:00"),
        _event(2, "2", "2026-11-03T10:05:00+00:00"),
        _event(3, "7", "2026-11-03T10:05:10+00:00", kind="animal"),
    ]
    assert build_stories(events, {"4": "Gate", "2": "Driveway", "7": "Porch"}) == []


def test_build_stories_supports_animals() -> None:
    events = [
        _event(1, "2", "2026-11-03T10:00:00+00:00", kind="animal"),
        _event(2, "7", "2026-11-03T10:00:30+00:00", kind="animal"),
    ]
    stories = build_stories(events, {"2": "Driveway", "7": "Porch"})
    assert len(stories) == 1
    assert stories[0]["kind"] == "animal"
