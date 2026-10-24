import os
from pathlib import Path

import pytest

from vehicle_agent.config import Settings
from vehicle_agent.knowledge.vision import VISION_LABELS, YoloVisionIndex

pytestmark = pytest.mark.skipif(
    os.environ.get("VEHICLE_AGENT_RUN_YOLO") != "1",
    reason="设置 VEHICLE_AGENT_RUN_YOLO=1 后使用本地权重和图片验收 YOLO",
)


def test_yolo_returns_boxes_inside_allowed_labels() -> None:
    settings = Settings()
    weights = Path(settings.yolo_weights)
    image = Path(os.environ.get("VEHICLE_AGENT_YOLO_IMAGE", "data/vision-sample.jpg"))
    if not weights.is_file():
        pytest.skip(f"YOLO weights not found: {weights}")
    if not image.is_file():
        pytest.skip(f"sample image not found: {image}")

    index = YoloVisionIndex(str(weights))
    with pytest.raises(ValueError, match="image bytes"):
        index.analyze(image.stem, None)
    detections = index.analyze(image.stem, image.read_bytes())

    assert detections
    for item in detections:
        assert item.label in VISION_LABELS
        assert all(0 <= value <= 1 for value in item.bbox)


def test_yolo_track_frames_returns_integer_ids() -> None:
    settings = Settings()
    weights = Path(settings.yolo_weights)
    image = Path(os.environ.get("VEHICLE_AGENT_YOLO_IMAGE", "data/vision-sample.jpg"))
    if not weights.is_file():
        pytest.skip(f"YOLO weights not found: {weights}")
    if not image.is_file():
        pytest.skip(f"sample image not found: {image}")

    content = image.read_bytes()
    tracked = YoloVisionIndex(str(weights)).track_frames([content, content])
    ids = [item.track_id for frame in tracked for item in frame if item.track_id is not None]

    assert len(tracked) == 2
    assert ids
    assert all(isinstance(item, int) for item in ids)
