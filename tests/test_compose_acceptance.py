import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("VEHICLE_AGENT_RUN_COMPOSE") != "1",
    reason="设置 VEHICLE_AGENT_RUN_COMPOSE=1 后构建并启动 deployments/docker-compose.yml 中的 api",
)

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(ROOT / "deployments" / "docker-compose.yml"),
]
HEALTH_URL = "http://127.0.0.1:8000/health"
JUDGEMENT_URL = "http://127.0.0.1:8000/api/v1/judgements"


def test_compose_api_scores_demo_alarm() -> None:
    subprocess.run([*COMPOSE, "up", "-d", "--build", "api"], cwd=ROOT, check=True)
    try:
        _wait_until_healthy()
        body = _post_demo_alarm()
    finally:
        subprocess.run([*COMPOSE, "stop", "api"], cwd=ROOT, check=False)

    assert body["status"] == "completed"
    assert body["result"]["risk_score"] == 1.0
    assert body["result"]["risk_level"] == "high"
    assert body["result"]["requires_human_review"] is False
    assert body["workorder_id"] == f"wo-{body['task_id']}"


def _wait_until_healthy() -> None:
    deadline = time.monotonic() + 90
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2) as response:
                if response.status == 200 and json.load(response) == {"status": "ok"}:
                    return
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(1)
    raise AssertionError(f"api health check failed: {last_error}")


def _post_demo_alarm() -> dict:
    payload = {
        "id": "alarm_demo",
        "vehicle_id": "vehicle_001",
        "driver_id": "driver_001",
        "device_id": "device_001",
        "alarm_type": "driver_fatigue",
        "alarm_time": datetime(2026, 10, 9, 10, 30, tzinfo=UTC).isoformat(),
        "longitude": 113.2644,
        "latitude": 23.1291,
        "speed_kmh": 85,
        "raw_confidence": 0.9,
    }
    request = urllib.request.Request(
        JUDGEMENT_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)
