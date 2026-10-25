import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("VEHICLE_AGENT_RUN_KAFKA") != "1",
    reason="设置 VEHICLE_AGENT_RUN_KAFKA=1 后连接 deployments/docker-compose.yml 中的 Kafka",
)


def test_live_kafka_duplicate_publish_keeps_one_alarm() -> None:
    from vehicle_agent.ingest import MemoryAlarmTopic, alarm_event_from_payload, fatigue_alarm_event
    from vehicle_agent.kafka_alarm import exchange_vehicle_alarm

    event = fatigue_alarm_event()
    payloads = exchange_vehicle_alarm(event)
    topic = MemoryAlarmTopic()
    first = topic.deliver(alarm_event_from_payload(payloads[0]))
    repeat = topic.deliver(alarm_event_from_payload(payloads[1]))

    assert len(payloads) == 2
    assert payloads[0] == payloads[1]
    assert first is repeat
    assert [item.id for item in topic.alarms()] == ["alarm_000001"]
    assert first.speed_kmh == 82.5


def test_live_kafka_duplicate_device_status_keeps_one_device() -> None:
    from vehicle_agent.ingest import (
        MemoryDeviceStatusTopic,
        device_status_event,
        device_status_from_payload,
    )
    from vehicle_agent.kafka_alarm import exchange_device_status

    event = device_status_event()
    payloads = exchange_device_status(event)
    topic = MemoryDeviceStatusTopic()
    first = topic.deliver(device_status_from_payload(payloads[0]))
    repeat = topic.deliver(device_status_from_payload(payloads[1]))

    assert len(payloads) == 2
    assert payloads[0] == payloads[1]
    assert first is repeat
    assert [item.id for item in topic.devices()] == ["device_001"]
    assert first.online_status == "online"


def test_live_kafka_alarm_is_judged_once(tmp_path) -> None:
    from test_graph import _run
    from vehicle_agent.ingest import consume_vehicle_alarms, fatigue_alarm_event
    from vehicle_agent.kafka_alarm import exchange_vehicle_alarm

    event = fatigue_alarm_event()
    payloads = exchange_vehicle_alarm(event)
    scores: list[float] = []

    def judge(alarm):
        _graph, values = _run(tmp_path, alarm, task_id=event.event_id)
        scores.append(values["risk_score"])
        return event.event_id

    task_id = consume_vehicle_alarms(payloads, judge)

    assert len(payloads) == 2
    assert task_id == "evt_20261009_000001"
    assert scores == [0.95]


def test_live_kafka_alarm_smoke_writes_one_workorder(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from vehicle_agent.api.main import create_app, handle_alarm_payload, open_model
    from vehicle_agent.config import Settings
    from vehicle_agent.graph.checkpoint import open_checkpointer
    from vehicle_agent.ingest import MemoryAlarmTopic, fatigue_alarm_event
    from vehicle_agent.kafka_alarm import exchange_vehicle_alarm
    from vehicle_agent.repositories.memory import build_seed_repository

    event = fatigue_alarm_event()
    payloads = exchange_vehicle_alarm(event)
    checkpoint = tmp_path / "checkpoints.sqlite"
    settings = Settings(
        model_backend="fixture",
        checkpoint_path=str(checkpoint),
        max_retries=1,
    )
    app = create_app(
        settings,
        build_seed_repository(),
        open_model(settings),
        open_checkpointer(checkpoint),
    )
    graph = app.state.graph
    invokes = {"count": 0}
    original = graph.invoke

    def invoke(*args, **kwargs):
        invokes["count"] += 1
        return original(*args, **kwargs)

    graph.invoke = invoke
    topic = MemoryAlarmTopic()
    judged: dict[str, str] = {}
    with TestClient(app) as client:
        task_ids = [
            handle_alarm_payload(payload, graph, topic, judged) for payload in payloads
        ]
        viewed = client.get(f"/api/v1/judgements/{event.event_id}")
        order = client.get("/api/v1/workorders/wo-evt_20261009_000001")

    assert task_ids == [event.event_id, event.event_id]
    assert invokes["count"] == 1
    assert viewed.json()["result"]["risk_score"] == 0.95
    assert order.json()["workorder_id"] == "wo-evt_20261009_000001"
    assert order.json()["status"] == "open"
