import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

_COMPOSE_REQUIRED = pytest.mark.skipif(
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
INGEST_COMPOSE = [
    "docker",
    "compose",
    "-p",
    "vehicle-agent-ingest",
    "-f",
    str(ROOT / "deployments" / "docker-compose.yml"),
    "-f",
    str(ROOT / "deployments" / "docker-compose.ingest.yml"),
]
HEALTH_URL = "http://127.0.0.1:8000/health"
JUDGEMENT_URL = "http://127.0.0.1:8000/api/v1/judgements"
INGEST_EVENT_ID = "evt_20261009_000001"
MODEL_COMPOSE = [
    "docker",
    "compose",
    "-p",
    "vehicle-agent-model",
    "-f",
    str(ROOT / "deployments" / "docker-compose.yml"),
    "-f",
    str(ROOT / "deployments" / "docker-compose.ingest.yml"),
    "-f",
    str(ROOT / "deployments" / "docker-compose.model.yml"),
]
INGEST_WORKORDER_URL = (
    "http://127.0.0.1:8000/api/v1/workorders/wo-evt_20261009_000001"
)


@_COMPOSE_REQUIRED
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


@_COMPOSE_REQUIRED
def test_compose_ingest_judges_one_alarm_and_keeps_the_workorder() -> None:
    subprocess.run([*INGEST_COMPOSE, "down", "-v"], cwd=ROOT, check=True)
    try:
        subprocess.run(
            [*INGEST_COMPOSE, "up", "-d", "--build", "kafka", "api"],
            cwd=ROOT,
            check=True,
        )
        _wait_until_healthy()
        from vehicle_agent.ingest import fatigue_alarm_event
        from vehicle_agent.kafka_alarm import exchange_vehicle_alarm

        payloads = exchange_vehicle_alarm(fatigue_alarm_event())
        assert len(payloads) == 2
        viewed = _wait_for_ingested_judgement()
        order = _get_json(INGEST_WORKORDER_URL)
        subprocess.run([*INGEST_COMPOSE, "restart", "api"], cwd=ROOT, check=True)
        _wait_until_healthy()
        restarted = _wait_for_ingested_judgement()
        restarted_order = _get_json(INGEST_WORKORDER_URL)
    finally:
        subprocess.run([*INGEST_COMPOSE, "down", "-v"], cwd=ROOT, check=False)

    assert viewed["status"] == "completed"
    assert viewed["result"]["risk_score"] == 0.95
    assert viewed["result"]["risk_level"] == "high"
    assert order["workorder_id"] == "wo-evt_20261009_000001"
    assert order["status"] == "open"
    assert restarted["result"]["risk_score"] == 0.95
    assert restarted_order["status"] == "open"


@_COMPOSE_REQUIRED
@pytest.mark.skipif(
    os.environ.get("VEHICLE_AGENT_RUN_MODEL") != "1",
    reason="设置 VEHICLE_AGENT_RUN_MODEL=1 后连接宿主机上的 OpenAI 兼容接口",
)
def test_compose_online_model_keeps_the_score() -> None:
    from vehicle_agent.harness.model import FIXTURE_REPORT
    from vehicle_agent.ingest import fatigue_alarm_event
    from vehicle_agent.kafka_alarm import exchange_vehicle_alarm

    fixture_summary = json.loads(FIXTURE_REPORT)["summary"]
    subprocess.run([*MODEL_COMPOSE, "down", "-v"], cwd=ROOT, check=True)
    try:
        subprocess.run(
            [*MODEL_COMPOSE, "up", "-d", "--build", "kafka", "api"],
            cwd=ROOT,
            check=True,
        )
        _wait_until_healthy()
        payloads = exchange_vehicle_alarm(fatigue_alarm_event())
        assert len(payloads) == 2
        viewed = _wait_for_ingested_judgement()
        order = _get_json(INGEST_WORKORDER_URL)
    finally:
        subprocess.run([*MODEL_COMPOSE, "down", "-v"], cwd=ROOT, check=False)

    assert viewed["status"] == "completed"
    assert viewed["result"]["risk_score"] == 0.95
    assert viewed["result"]["summary"] != fixture_summary
    assert order["workorder_id"] == "wo-evt_20261009_000001"
    assert order["status"] == "open"


def _wait_until_healthy() -> None:
    deadline = time.monotonic() + 90
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2) as response:
                if response.status == 200 and json.load(response) == {"status": "ok"}:
                    return
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError) as exc:
            last_error = exc
        time.sleep(1)
    raise AssertionError(f"api health check failed: {last_error}")


def _ingested_judgement_ready(body: dict) -> bool:
    result = body.get("result") or {}
    return result.get("risk_score") == 0.95


def _wait_for_ingested_judgement() -> dict:
    deadline = time.monotonic() + 90
    last_error: Exception | None = None
    url = f"{JUDGEMENT_URL}/{INGEST_EVENT_ID}"
    while time.monotonic() < deadline:
        try:
            body = _get_json(url)
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            ConnectionError,
        ) as exc:
            last_error = exc
        else:
            if _ingested_judgement_ready(body):
                return body
            last_error = AssertionError(body)
        time.sleep(1)
    raise AssertionError(f"ingested judgement was not ready: {last_error}")


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


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


def test_ingested_judgement_ready_treats_null_result_as_not_ready() -> None:
    assert _ingested_judgement_ready({"result": None}) is False
    assert _ingested_judgement_ready({}) is False
    assert _ingested_judgement_ready({"result": {"risk_score": 0.9}}) is False
    assert _ingested_judgement_ready({"result": {"risk_score": 0.95}}) is True
