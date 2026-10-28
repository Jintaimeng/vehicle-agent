import os
from pathlib import Path

import pytest

from vehicle_agent.knowledge.vision import VISION_LABELS, YoloVisionIndex

pytestmark = pytest.mark.skipif(
    os.environ.get("VEHICLE_AGENT_RUN_YOLO") != "1",
    reason="设置 VEHICLE_AGENT_RUN_YOLO=1 后使用本地权重和图片验收 YOLO",
)

_OFFICIAL_WEIGHTS = "yolov8n.pt"


def _live_inputs() -> tuple[str, Path]:
    configured = os.environ.get("VEHICLE_AGENT_YOLO_WEIGHTS")
    if configured:
        assert Path(configured).is_file(), f"YOLO weights not found: {configured}"
    image = Path(os.environ.get("VEHICLE_AGENT_YOLO_IMAGE", "data/vision-sample.jpg"))
    assert image.is_file(), f"sample image not found: {image}"
    return configured or _OFFICIAL_WEIGHTS, image


def test_yolo_returns_boxes_inside_allowed_labels() -> None:
    weights, image = _live_inputs()

    index = YoloVisionIndex(weights)
    with pytest.raises(ValueError, match="image bytes"):
        index.analyze(image.stem, None)
    detections = index.analyze(image.stem, image.read_bytes())

    assert detections
    for item in detections:
        assert item.label in VISION_LABELS
        assert all(0 <= value <= 1 for value in item.bbox)


def test_yolo_track_frames_returns_integer_ids() -> None:
    weights, image = _live_inputs()

    content = image.read_bytes()
    tracked = YoloVisionIndex(weights).track_frames([content, content])
    ids = [item.track_id for frame in tracked for item in frame if item.track_id is not None]

    assert len(tracked) == 2
    assert ids
    assert all(isinstance(item, int) for item in ids)
