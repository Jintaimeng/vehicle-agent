import pytest

from vehicle_agent.domain.models import Detection
from vehicle_agent.knowledge.vision import packaged_vision_index, render_detection_overlay

_HEADER = b"P6\n100 100\n255\n"
_WHITE = b"\xff\xff\xff"
_BLACK = b"\x00\x00\x00"


def test_closed_eye_box_is_a_white_border() -> None:
    detections = packaged_vision_index().analyze("image_closed_eye")

    overlay = render_detection_overlay(detections)

    assert overlay.startswith(_HEADER)
    assert _pixel(overlay, 0, 0) == _BLACK
    assert _pixel(overlay, 1, 0) == _BLACK
    assert _pixel(overlay, 2, 0) == _BLACK
    assert _pixel(overlay, 40, 20) == _WHITE
    assert _pixel(overlay, 61, 54) == _WHITE
    assert _pixel(overlay, 50, 30) == _BLACK
    assert detections[0].bbox == (0.4, 0.2, 0.62, 0.55)
    assert detections[0].label == "closed_eye"
    assert detections[0].confidence == 0.91


def test_empty_detections_stay_black() -> None:
    overlay = render_detection_overlay(packaged_vision_index().analyze("image_empty"))
    body = overlay[len(_HEADER) :]

    assert overlay.startswith(_HEADER)
    assert body == _BLACK * (100 * 100)
    assert _WHITE not in body


def test_degenerate_box_draws_nothing() -> None:
    detection = Detection(
        label="closed_eye",
        confidence=0.91,
        bbox=(0.5, 0.2, 0.509, 0.55),
        model_version="fixture-v1",
    )

    overlay = render_detection_overlay([detection])

    assert _WHITE not in overlay[len(_HEADER) :]


def test_overlay_side_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        render_detection_overlay([], width=0)
    with pytest.raises(ValueError, match="at least 1"):
        render_detection_overlay([], height=0)


def _pixel(overlay: bytes, x: int, y: int) -> bytes:
    offset = len(_HEADER) + (y * 100 + x) * 3
    return overlay[offset : offset + 3]
