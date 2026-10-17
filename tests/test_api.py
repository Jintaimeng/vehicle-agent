import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from vehicle_agent.api.main import create_app
from vehicle_agent.config import Settings
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.repositories.memory import build_seed_repository

REPORT = json.dumps(
    {
        "summary": "根据车速、历史报警和车辆资料完成研判。",
        "recommendations": ["通知企业安全管理员复核"],
    },
    ensure_ascii=False,
)


def _client(tmp_path, *, content: str = REPORT, **settings):
    config = Settings(max_retries=1, **settings)
    app = create_app(
        config,
        build_seed_repository(),
        FakeModelAdapter(content),
        open_checkpointer(tmp_path / "checkpoints.sqlite"),
    )
    return TestClient(app)


def _alarm(**overrides) -> dict:
    payload = {
        "id": "alarm_current",
        "vehicle_id": "vehicle_002",
        "driver_id": "driver_002",
        "device_id": "device_002",
        "alarm_type": "driver_fatigue",
        "alarm_time": datetime(2026, 10, 9, 10, 30, tzinfo=UTC).isoformat(),
        "longitude": 113.26,
        "latitude": 23.12,
        "speed_kmh": 40,
        "raw_confidence": 0.8,
    }
    payload.update(overrides)
    return payload


def test_normal_judgement(tmp_path) -> None:
    client = _client(tmp_path)
    created = client.post("/api/v1/judgements", json=_alarm())

    assert created.status_code == 200
    body = created.json()
    assert body["status"] == "completed"
    assert body["result"]["risk_level"] == "medium"
    assert body["result"]["requires_human_review"] is False

    fetched = client.get(f"/api/v1/judgements/{body['task_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["result"]["summary"].startswith("根据车速")


def test_high_risk_judgement(tmp_path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/v1/judgements",
        json=_alarm(
            vehicle_id="vehicle_001",
            driver_id="driver_001",
            speed_kmh=85,
            raw_confidence=0.9,
        ),
    )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["risk_level"] == "high"
    assert result["alarm_validity"] == "likely_true"


def test_insufficient_evidence_can_be_reviewed(tmp_path) -> None:
    client = _client(tmp_path)
    created = client.post("/api/v1/judgements", json=_alarm(vehicle_id="vehicle_missing"))
    body = created.json()

    assert body["status"] == "awaiting_review"
    assert body["interrupts"][0]["reason"] == "low_confidence"

    reviewed = client.post(
        f"/api/v1/judgements/{body['task_id']}/review",
        json={"reviewer": "ops", "decision": "confirm", "comment": "人工确认"},
    )

    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "completed"
    assert reviewed.json()["human_review"]["decision"] == "confirm"


def test_tool_failure_returns_failed_task(tmp_path) -> None:
    response = _client(tmp_path).post("/api/v1/judgements", json=_alarm(vehicle_id="vehicle_error"))

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["errors"][-1]["code"] == "tool_failed"


def test_invalid_model_output_returns_failed_task(tmp_path) -> None:
    response = _client(tmp_path, content="not-json").post("/api/v1/judgements", json=_alarm())

    assert response.status_code == 200
    assert response.json()["errors"][-1]["code"] == "output_invalid"


def test_budget_exhaustion_returns_failed_task(tmp_path) -> None:
    response = _client(tmp_path, max_model_calls=0).post("/api/v1/judgements", json=_alarm())

    assert response.status_code == 200
    assert response.json()["errors"][-1]["code"] == "budget_exceeded"


def test_missing_task_uses_error_body(tmp_path) -> None:
    response = _client(tmp_path).get("/api/v1/judgements/missing")

    assert response.status_code == 404
    assert response.json() == {"code": "not_found", "message": "task not found"}
