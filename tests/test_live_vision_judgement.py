import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vehicle_agent.api.main import create_app, open_model
from vehicle_agent.config import Settings
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import FIXTURE_REPORT
from vehicle_agent.knowledge.vision import store_image
from vehicle_agent.repositories.memory import build_seed_repository

pytestmark = pytest.mark.skipif(
    os.environ.get("VEHICLE_AGENT_RUN_YOLO") != "1"
    or os.environ.get("VEHICLE_AGENT_RUN_VLM") != "1",
    reason="设置 VEHICLE_AGENT_RUN_YOLO=1 和 VEHICLE_AGENT_RUN_VLM=1 后验收研判里的真实视觉",
)

_IMAGE_ID = "person-sample.jpg"
_OFFICIAL_WEIGHTS = "yolov8n.pt"
_FIXTURE_SUMMARY = json.loads(FIXTURE_REPORT)["summary"]
_COCO_LABELS = {"driver", "mobile_phone"}


def test_official_yolo_and_vlm_keep_the_score(tmp_path: Path) -> None:
    configured = os.environ.get("VEHICLE_AGENT_YOLO_WEIGHTS")
    weights = configured or _OFFICIAL_WEIGHTS
    if configured:
        assert Path(configured).is_file(), f"YOLO weights not found: {configured}"
    image = Path(os.environ.get("VEHICLE_AGENT_YOLO_IMAGE", "data/vision-sample.jpg"))
    assert image.is_file(), f"sample image not found: {image}"

    store = tmp_path / "vision"
    store_image(store, _IMAGE_ID, image.read_bytes())
    checkpoint = tmp_path / "checkpoints.sqlite"
    settings = Settings(
        model_backend="openai",
        vision_backend="yolo",
        review_backend="vlm",
        yolo_weights=weights,
        vision_store_dir=str(store),
        checkpoint_path=str(checkpoint),
        request_timeout_seconds=120,
        max_elapsed_seconds=180,
    )
    app = create_app(
        settings,
        build_seed_repository(),
        open_model(settings),
        open_checkpointer(checkpoint),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/judgements",
            json={
                "id": "alarm_live_vision",
                "vehicle_id": "vehicle_001",
                "driver_id": "driver_001",
                "device_id": "device_001",
                "alarm_type": "driver_fatigue",
                "alarm_time": datetime(2026, 10, 9, 10, 30, tzinfo=UTC).isoformat(),
                "longitude": 113.2644,
                "latitude": 23.1291,
                "speed_kmh": 85,
                "raw_confidence": 0.9,
                "image_id": _IMAGE_ID,
            },
        )
        assert response.status_code == 200
        body = response.json()
        values = app.state.graph.get_state(
            {"configurable": {"thread_id": body["task_id"]}}
        ).values

    assert body["status"] != "failed"
    assert values["vision_status"] == "hit"
    assert values["detections"]
    assert {item["label"] for item in values["detections"]} <= _COCO_LABELS
    assert values["vlm_status"] == "hit"
    assert values["vision_agreement"] in {"match", "conflict"}
    assert values["risk_score"] == 1.0
    vision = [item for item in values["evidence"] if item["type"] == "vision"]
    assert vision
    assert {item["source"] for item in vision} == {Path(weights).name}

    if values["vision_agreement"] == "match":
        assert body["status"] == "completed"
        assert body["result"]["summary"] != _FIXTURE_SUMMARY
        assert body["result"]["risk_score"] == 1.0
        return

    assert body["status"] == "awaiting_review"
    assert body["workorder_id"] is None
