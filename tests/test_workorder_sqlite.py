import pytest
from fastapi.testclient import TestClient

from vehicle_agent.api.main import create_app, ingest_alarm_payload, open_model
from vehicle_agent.config import Settings
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.ingest import MemoryAlarmTopic, alarm_event_payload, fatigue_alarm_event
from vehicle_agent.repositories.memory import build_seed_repository
from vehicle_agent.workorders import (
    MemoryWorkOrderStore,
    SqliteWorkOrderStore,
    seed_report_workorders,
    workorder_sqlite_path,
)

_EVENT_ID = "evt_20261009_000001"
_ORDER_ID = "wo-evt_20261009_000001"


def test_judgement_workorder_survives_a_new_process(tmp_path) -> None:
    checkpoint = tmp_path / "checkpoints.sqlite"
    settings = Settings(
        model_backend="fixture",
        checkpoint_path=str(checkpoint),
        max_retries=1,
    )
    payload = alarm_event_payload(fatigue_alarm_event())

    first = _app(settings, checkpoint)
    with TestClient(first) as client:
        task_id = ingest_alarm_payload(payload, first.state.graph, MemoryAlarmTopic(), {})
        viewed = client.get(f"/api/v1/judgements/{task_id}")
        created = client.get(f"/api/v1/workorders/{_ORDER_ID}")

    assert task_id == _EVENT_ID
    assert viewed.status_code == 200
    assert viewed.json()["result"]["risk_score"] == 0.95
    assert created.status_code == 200
    assert created.json()["workorder_id"] == _ORDER_ID
    assert created.json()["status"] == "open"

    second = _app(settings, checkpoint)
    with TestClient(second) as client:
        fetched = client.get(f"/api/v1/workorders/{_ORDER_ID}")
        again = client.post(
            "/api/v1/workorders",
            json={
                "task_id": _EVENT_ID,
                "alarm_id": "alarm_000001",
                "risk_level": "high",
                "summary": "再次创建",
            },
        )
        report_order = client.get("/api/v1/workorders/wo-report-closed")

    assert fetched.status_code == 200
    assert fetched.json()["status"] == "open"
    assert fetched.json()["summary"] != "再次创建"
    assert again.status_code == 200
    assert again.json()["workorder_id"] == _ORDER_ID
    assert again.json()["summary"] == fetched.json()["summary"]
    assert report_order.status_code == 404


def test_report_orders_stay_in_memory(tmp_path) -> None:
    orders = seed_report_workorders()
    path = workorder_sqlite_path(tmp_path / "checkpoints.sqlite")
    store = SqliteWorkOrderStore(path)
    try:
        assert isinstance(orders, MemoryWorkOrderStore)
        assert [item.status for item in orders.list_workorders()] == ["closed", "open"]
        with pytest.raises(KeyError):
            store.get("wo-report-closed")
        with pytest.raises(KeyError):
            store.get("wo-report-open")
    finally:
        store.close()


def _app(settings: Settings, checkpoint):
    return create_app(
        settings,
        build_seed_repository(),
        open_model(settings),
        open_checkpointer(checkpoint),
    )
