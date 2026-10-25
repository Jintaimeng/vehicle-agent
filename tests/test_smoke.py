from fastapi.testclient import TestClient

from vehicle_agent.api.main import create_app, handle_alarm_payload, open_model
from vehicle_agent.config import Settings
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.ingest import MemoryAlarmTopic, fatigue_alarm_event, publish_vehicle_alarm
from vehicle_agent.repositories.memory import build_seed_repository

_EVENT_ID = "evt_20261009_000001"
_ORDER_ID = "wo-evt_20261009_000001"


def test_health_then_alarm_leaves_an_open_workorder(tmp_path) -> None:
    checkpoint = tmp_path / "checkpoints.sqlite"
    settings = Settings(
        model_backend="fixture",
        checkpoint_path=str(checkpoint),
        max_retries=1,
    )
    sent: list[tuple[str, bytes]] = []
    publish_vehicle_alarm(
        fatigue_alarm_event(),
        lambda topic, payload: sent.append((topic, payload)),
    )

    first = _app(settings, checkpoint)
    with TestClient(first) as client:
        health = client.get("/health")
        task_id = handle_alarm_payload(
            sent[0][1],
            first.state.graph,
            MemoryAlarmTopic(),
            {},
        )
        viewed = client.get(f"/api/v1/judgements/{_EVENT_ID}")
        created = client.get(f"/api/v1/workorders/{_ORDER_ID}")

    assert sent[0][0] == "vehicle.alarm"
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert task_id == _EVENT_ID
    body = viewed.json()
    assert viewed.status_code == 200
    assert body["status"] == "completed"
    assert body["result"]["risk_score"] == 0.95
    assert body["result"]["risk_level"] == "high"
    assert body["result"]["requires_human_review"] is False
    assert created.status_code == 200
    assert created.json()["status"] == "open"

    second = _app(settings, checkpoint)
    with TestClient(second) as client:
        fetched = client.get(f"/api/v1/workorders/{_ORDER_ID}")

    assert fetched.status_code == 200
    assert fetched.json()["workorder_id"] == _ORDER_ID
    assert fetched.json()["status"] == "open"


def _app(settings: Settings, checkpoint):
    return create_app(
        settings,
        build_seed_repository(),
        open_model(settings),
        open_checkpointer(checkpoint),
    )
