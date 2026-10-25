import json
import threading
import time
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from vehicle_agent.api.main import create_app, open_model
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
    checkpoint = tmp_path / "checkpoints.sqlite"
    config = Settings(max_retries=1, checkpoint_path=str(checkpoint), **settings)
    app = create_app(
        config,
        build_seed_repository(),
        FakeModelAdapter(content),
        open_checkpointer(checkpoint),
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
    assert response.json()["workorder_id"] == f"wo-{response.json()['task_id']}"


def test_workorder_api_creates_reads_and_updates(tmp_path) -> None:
    client = _client(tmp_path)
    created = client.post(
        "/api/v1/workorders",
        json={
            "task_id": "task_api",
            "alarm_id": "alarm_api",
            "risk_level": "high",
            "summary": "高风险摘要",
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["workorder_id"] == "wo-task_api"
    assert body["status"] == "open"

    again = client.post(
        "/api/v1/workorders",
        json={
            "task_id": "task_api",
            "alarm_id": "alarm_api",
            "risk_level": "high",
            "summary": "另一份摘要",
        },
    )
    assert again.status_code == 200
    assert again.json()["workorder_id"] == "wo-task_api"
    assert again.json()["summary"] == "高风险摘要"

    fetched = client.get("/api/v1/workorders/wo-task_api")
    assert fetched.status_code == 200
    assert fetched.json()["alarm_id"] == "alarm_api"

    updated = client.patch(
        "/api/v1/workorders/wo-task_api",
        json={"status": "closed", "comment": "已处置"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "closed"
    assert updated.json()["comment"] == "已处置"

    missing = client.get("/api/v1/workorders/wo-missing")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"

    rejected = client.post(
        "/api/v1/workorders",
        json={
            "task_id": "task_medium",
            "alarm_id": "alarm_medium",
            "risk_level": "medium",
            "summary": "中风险",
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "invalid_workorder"


def test_reviewed_judgement_has_no_workorder(tmp_path) -> None:
    client = _client(tmp_path)
    created = client.post("/api/v1/judgements", json=_alarm(vehicle_id="vehicle_missing"))
    reviewed = client.post(
        f"/api/v1/judgements/{created.json()['task_id']}/review",
        json={"reviewer": "ops", "decision": "confirm", "comment": "人工确认"},
    )

    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "completed"
    assert reviewed.json()["workorder_id"] is None


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


def test_health_does_not_call_the_model(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_kafka_health_stays_starting_until_the_consumer_is_ready(tmp_path, monkeypatch) -> None:
    entered = threading.Event()

    def serve(_bootstrap, _handle, stop, on_ready=None) -> None:
        del on_ready
        entered.set()
        while not stop():
            time.sleep(0.01)

    monkeypatch.setattr("vehicle_agent.kafka_alarm.serve_vehicle_alarms", serve)
    with _client(tmp_path, ingest="kafka") as client:
        assert entered.wait(2)
        starting = client.get("/health")
        client.app.state.ingest_ready.set()
        ready = client.get("/health")

    assert starting.status_code == 503
    assert starting.json() == {"status": "starting"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok"}


def test_fixture_backend_scores_demo_alarm(tmp_path) -> None:
    settings = Settings(
        model_backend="fixture",
        checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
        max_retries=1,
    )
    client = TestClient(
        create_app(
            settings,
            build_seed_repository(),
            open_model(settings),
            open_checkpointer(settings.checkpoint_path),
        )
    )

    response = client.post(
        "/api/v1/judgements",
        json=_alarm(
            id="alarm_demo",
            vehicle_id="vehicle_001",
            driver_id="driver_001",
            device_id="device_001",
            longitude=113.2644,
            latitude=23.1291,
            speed_kmh=85,
            raw_confidence=0.9,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["risk_score"] == 1.0
    assert body["result"]["risk_level"] == "high"
    assert body["result"]["requires_human_review"] is False
    assert body["workorder_id"] == f"wo-{body['task_id']}"
    assert body["result"]["summary"].startswith("驾驶员在高速行驶期间")


def test_local_process_defaults_stay_on_openai() -> None:
    settings = Settings()

    assert settings.model_backend == "openai"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.ingest == "off"
    assert settings.kafka_bootstrap == "127.0.0.1:9092"
