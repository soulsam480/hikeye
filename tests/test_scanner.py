from __future__ import annotations

import io

from PIL import Image, ImageDraw

from mu_dekhuchi.scanner import StillMotion, accept_motion
from mu_dekhuchi.vision.base import VisionResult


def _jpeg(
    width: int = 120, height: int = 90, box: tuple[int, int, int, int] | None = None
) -> bytes:
    image = Image.new("L", (width, height), "white")
    if box is not None:
        ImageDraw.Draw(image).rectangle(box, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")
    return buffer.getvalue()


def test_still_motion_ignores_first_frame_and_static_scene() -> None:
    motion = StillMotion()
    assert motion.update(_jpeg()) is False
    assert motion.update(_jpeg()) is False


def test_still_motion_detects_center_change() -> None:
    motion = StillMotion()
    assert motion.update(_jpeg()) is False
    assert motion.update(_jpeg(box=(40, 30, 80, 60))) is True


def test_still_motion_ignores_scene_change() -> None:
    motion = StillMotion()
    assert motion.update(_jpeg()) is False
    assert motion.update(_jpeg(box=(0, 0, 120, 90))) is False


def test_still_motion_masks_timestamp_corner() -> None:
    motion = StillMotion()
    assert motion.update(_jpeg(box=(2, 2, 18, 7))) is False
    assert motion.update(_jpeg(box=(4, 2, 22, 7))) is False


def test_accept_motion_keeps_people_and_animals_only() -> None:
    assert accept_motion(VisionResult("person", confidence=0.9), 0.75) is True
    assert accept_motion(VisionResult("animal", confidence=0.8), 0.75) is True
    assert (
        accept_motion(VisionResult("vehicle", description="Parked car", confidence=0.9), 0.75)
        is False
    )
    assert accept_motion(VisionResult("person", confidence=0.5), 0.75) is False
    assert accept_motion(VisionResult("none", confidence=0.0), 0.75) is False
