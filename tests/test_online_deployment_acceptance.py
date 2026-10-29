import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from vehicle_agent.harness.model import FIXTURE_REPORT

pytestmark = pytest.mark.skipif(
    os.environ.get("VEHICLE_AGENT_RUN_ONLINE") != "1",
    reason="设置 VEHICLE_AGENT_RUN_ONLINE=1 后验收完整在线容器",
)

BASE_URL = "http://127.0.0.1:8000"


def test_online_container_runs_yolo_vlm_report_and_workflow() -> None:
    image = Path(os.environ["VEHICLE_AGENT_YOLO_IMAGE"])
    assert image.is_file(), f"sample image not found: {image}"
    headers = _headers()
    _wait_until_healthy(headers)

    with image.open("rb") as stream:
        response = httpx.post(
            f"{BASE_URL}/api/v1/vision/images",
            headers=headers,
            data={"image_id": image.name},
            files={"file": (image.name, stream, "image/jpeg")},
            timeout=180,
        )
    response.raise_for_status()
    analyzed = response.json()
    assert analyzed["detections"]
    assert {item["model_version"] for item in analyzed["detections"]} == {"yolov8n.pt"}

    judged = httpx.post(
        f"{BASE_URL}/api/v1/judgements",
        headers=headers,
        json={
            "id": "alarm_online_vision",
            "vehicle_id": "vehicle_001",
            "driver_id": "driver_001",
            "device_id": "device_001",
            "alarm_type": "driver_fatigue",
            "alarm_time": datetime(2026, 10, 9, 10, 30, tzinfo=UTC).isoformat(),
            "longitude": 113.2644,
            "latitude": 23.1291,
            "speed_kmh": 85,
            "raw_confidence": 0.9,
            "image_id": image.name,
        },
        timeout=360,
    )
    judged.raise_for_status()
    body = judged.json()
    assert body["status"] == "completed", body
    assert body["errors"] == [], body
    assert body["result"]["risk_score"] == 1.0
    assert body["result"]["summary"] != json.loads(FIXTURE_REPORT)["summary"]
    vision = [item for item in body["result"]["evidence"] if item["type"] == "vision"]
    assert vision
    assert all(item["source"] == "yolov8n.pt" for item in vision)
    assert all("可见事实：画面观察支持" in item["description"] for item in vision)
    assert body["workorder_id"], body
    order = httpx.get(
        f"{BASE_URL}/api/v1/workorders/{body['workorder_id']}",
        headers=headers,
        timeout=30,
    )
    order.raise_for_status()
    assert order.json()["status"] == "open"
    assert order.json()["task_id"] == body["task_id"]
    assert order.json()["summary"] == body["result"]["summary"]
    print(json.dumps({"task_id": body["task_id"], "workorder_id": body["workorder_id"],
                      "vision": vision, "summary": body["result"]["summary"]}, ensure_ascii=False))


def test_online_container_uses_remote_report_and_creates_workorder() -> None:
    headers = _headers()
    _wait_until_healthy(headers)
    judged = httpx.post(
        f"{BASE_URL}/api/v1/judgements",
        headers=headers,
        json={
            "id": "alarm_online_workorder",
            "vehicle_id": "vehicle_001",
            "driver_id": "driver_001",
            "device_id": "device_001",
            "alarm_type": "driver_fatigue",
            "alarm_time": datetime(2026, 10, 9, 10, 30, tzinfo=UTC).isoformat(),
            "longitude": 113.2644,
            "latitude": 23.1291,
            "speed_kmh": 85,
            "raw_confidence": 0.9,
        },
        timeout=360,
    )
    judged.raise_for_status()
    body = judged.json()

    assert body["status"] == "completed"
    assert body["result"]["risk_score"] == 1.0
    assert body["result"]["summary"] != json.loads(FIXTURE_REPORT)["summary"]
    assert body["workorder_id"]
    order = httpx.get(
        f"{BASE_URL}/api/v1/workorders/{body['workorder_id']}",
        headers=headers,
        timeout=30,
    )
    order.raise_for_status()
    assert order.json()["status"] == "open"


def _headers() -> dict[str, str]:
    token = os.environ.get("VEHICLE_AGENT_API_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _wait_until_healthy(headers: dict[str, str]) -> None:
    deadline = time.monotonic() + 300
    last = "not started"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{BASE_URL}/health", headers=headers, timeout=5)
            last = response.text
            if response.status_code == 200 and response.json() == {"status": "ok"}:
                return
        except httpx.HTTPError as exc:
            last = str(exc)
        time.sleep(2)
    raise AssertionError(f"online api health check failed: {last}")
