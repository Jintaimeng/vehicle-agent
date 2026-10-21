import os
from pathlib import Path

import pytest

from vehicle_agent.config import Settings
from vehicle_agent.domain.models import Detection
from vehicle_agent.harness.model import OpenAICompatibleModel
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import build_vehicle_tools
from vehicle_agent.knowledge.vision import VISION_LABELS, VlmObservation, compare_observation
from vehicle_agent.repositories.memory import build_seed_repository

pytestmark = pytest.mark.skipif(
    os.environ.get("VEHICLE_AGENT_RUN_VLM") != "1",
    reason="设置 VEHICLE_AGENT_RUN_VLM=1 后使用本地图片和视觉模型验收关键帧复核",
)


def test_live_vlm_labels_enter_comparison() -> None:
    settings = Settings()
    image = Path(os.environ.get("VEHICLE_AGENT_VLM_IMAGE", "data/vision-sample.jpg"))
    if not image.is_file():
        pytest.skip(f"sample image not found: {image}")

    harness = Harness(
        settings,
        OpenAICompatibleModel(settings),
        build_vehicle_tools(build_seed_repository()),
    )
    labels = VlmObservation(harness, image.parent).observe(image.name, task_id="vlm-acceptance")

    assert all(label in VISION_LABELS for label in labels)
    detection = Detection(
        label=labels[0] if labels else "closed_eye",
        confidence=0.91,
        bbox=(0.1, 0.1, 0.2, 0.2),
        model_version="acceptance",
    )
    agreement, facts = compare_observation([detection], labels)
    assert agreement in {"match", "conflict"}
    assert facts
