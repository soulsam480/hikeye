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


def test_build_stories_stops_growing_at_max_span() -> None:
    events = [
        _event(1, "4", "2026-11-03T10:00:00+00:00"),
        _event(2, "2", "2026-11-03T10:00:50+00:00"),
        _event(3, "7", "2026-11-03T10:01:40+00:00"),
    ]
    stories = build_stories(
        events,
        {"4": "Gate", "2": "Driveway", "7": "Porch"},
        max_gap_seconds=100,
    )

    assert len(stories) == 1
    assert stories[0]["path"] == "Gate → Driveway"
    assert stories[0]["duration_seconds"] == 50


def test_build_stories_breaks_on_gap_larger_than_max_gap() -> None:
    events = [
        _event(1, "4", "2026-11-03T10:00:00+00:00"),
        _event(2, "2", "2026-11-03T10:01:00+00:00"),
    ]
    assert build_stories(events, {"4": "Gate", "2": "Driveway"}) == []


def test_build_stories_confidence_tracks_timing_not_event_count() -> None:
    tight = [
        _event(1, "4", "2026-11-03T10:00:00+00:00"),
        _event(2, "2", "2026-11-03T10:00:10+00:00"),
        _event(3, "7", "2026-11-03T10:00:20+00:00"),
    ]
    tight_with_noise = [
        _event(1, "4", "2026-11-03T10:00:00+00:00"),
        _event(2, "4", "2026-11-03T10:00:05+00:00"),
        _event(3, "2", "2026-11-03T10:00:10+00:00"),
        _event(4, "2", "2026-11-03T10:00:15+00:00"),
        _event(5, "7", "2026-11-03T10:00:20+00:00"),
    ]
    loose = [
        _event(1, "4", "2026-11-03T10:00:00+00:00"),
        _event(2, "2", "2026-11-03T10:00:35+00:00"),
        _event(3, "7", "2026-11-03T10:01:10+00:00"),
    ]
    names = {"4": "Gate", "2": "Driveway", "7": "Porch"}

    tight_confidence = build_stories(tight, names)[0]["confidence"]
    noisy_confidence = build_stories(tight_with_noise, names)[0]["confidence"]
    loose_confidence = build_stories(loose, names)[0]["confidence"]

    assert noisy_confidence == tight_confidence
    assert tight_confidence > loose_confidence


def test_build_stories_marks_long_chains_as_loose_activity() -> None:
    events = [
        _event(1, "4", "2026-11-03T10:00:00+00:00"),
        _event(2, "2", "2026-11-03T10:00:25+00:00"),
        _event(3, "7", "2026-11-03T10:00:50+00:00"),
        _event(4, "5", "2026-11-03T10:01:15+00:00"),
    ]
    stories = build_stories(
        events,
        {"4": "Gate", "2": "Driveway", "7": "Porch", "5": "Back"},
    )

    assert len(stories) == 1
    story = stories[0]
    assert story["loose"] is True
    assert story["title"] == "Possible person activity"
    assert story["link_label"] == "loose link"
    assert story["duration_seconds"] == 75
    assert "loose" in story["narrative"]


def test_build_stories_tight_story_has_percentage_link() -> None:
    events = [
        _event(1, "4", "2026-11-03T10:00:00+00:00"),
        _event(2, "2", "2026-11-03T10:00:10+00:00"),
    ]
    story = build_stories(events, {"4": "Gate", "2": "Driveway"})[0]

    assert story["loose"] is False
    assert story["title"] == "Possible person movement"
    assert story["link_label"].endswith("% link")
    assert story["duration_seconds"] == 10
