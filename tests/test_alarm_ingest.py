import json
import threading
from datetime import UTC, datetime

import pytest

from test_graph import REPORT, _run, _settings
from vehicle_agent.api.main import (
    _serve_alarm_ingest,
    handle_alarm_payload,
    ingest_alarm_payload,
)
from vehicle_agent.config import Settings
from vehicle_agent.graph.builder import build_graph
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.harness.policy import AllowApprovedWritesPolicy
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import build_vehicle_tools
from vehicle_agent.ingest import (
    MemoryAlarmTopic,
    MemoryDeviceStatusTopic,
    alarm_event_payload,
    consume_vehicle_alarms,
    device_status_event,
    device_status_payload,
    fatigue_alarm_event,
    publish_vehicle_alarm,
)
from vehicle_agent.repositories.memory import build_seed_repository

_EVENT_ID = "evt_20261009_000001"
_TOOLS = [
    "get_vehicle_context",
    "list_recent_alarms",
    "search_regulations",
    "query_vehicle_graph",
    "create_workorder",
]


def test_consumed_alarm_enters_the_existing_graph_once(tmp_path) -> None:
    sent: list[tuple[str, bytes]] = []
    repository = build_seed_repository()
    before = _alarm_ids(repository)
    publish_vehicle_alarm(
        fatigue_alarm_event(),
        lambda topic, payload: sent.append((topic, payload)),
    )
    slower = json.loads(sent[0][1])
    slower["speed_kmh"] = 10
    calls: list = []
    held: dict = {}

    def judge(alarm):
        calls.append(alarm)
        graph, values = _run(tmp_path, alarm, task_id=_EVENT_ID)
        held["graph"] = graph
        held["values"] = values
        return _EVENT_ID

    inbox = MemoryAlarmTopic()
    task_id = consume_vehicle_alarms(
        [sent[0][1], json.dumps(slower).encode("utf-8")],
        judge,
        inbox,
    )
    values = held["values"]
    stored = held["graph"].get_state({"configurable": {"thread_id": task_id}}).values

    assert sent[0][0] == "vehicle.alarm"
    assert len(calls) == 1
    assert calls[0].speed_kmh == 82.5
    assert calls[0].image_id is None
    assert calls[0].video_id is None
    assert inbox.alarms()[0].speed_kmh == 82.5
    assert task_id == _EVENT_ID
    assert values["risk_score"] == 0.95
    assert values["risk_level"] == "high"
    assert values["status"] == "completed"
    assert values["result"]["requires_human_review"] is False
    assert values["vision_status"] == "skipped"
    assert values["detections"] == []
    assert _tool_names(values) == _TOOLS
    assert stored["risk_score"] == 0.95
    assert stored["task_id"] == _EVENT_ID
    assert _alarm_ids(repository) == before


def test_invalid_alarm_does_not_start_judgement() -> None:
    called = False

    def judge(_alarm) -> str:
        nonlocal called
        called = True
        return _EVENT_ID

    with pytest.raises(ValueError):
        consume_vehicle_alarms([b"not-json"], judge)
    assert called is False
    assert handle_alarm_payload(b"not-json", None, MemoryAlarmTopic(), {}) is None


def test_device_status_is_not_consumed() -> None:
    called = False

    def judge(_alarm) -> str:
        nonlocal called
        called = True
        return _EVENT_ID

    payload = device_status_payload(device_status_event())
    with pytest.raises(ValueError):
        consume_vehicle_alarms([payload], judge)
    assert called is False
    assert handle_alarm_payload(payload, None, MemoryAlarmTopic(), {}) is None
    assert MemoryDeviceStatusTopic().name == "device.status"


def test_completed_event_publishes_once(tmp_path) -> None:
    sent: list[tuple[str, bytes]] = []

    def send(topic: str, payload: bytes) -> None:
        sent.append((topic, payload))

    event = fatigue_alarm_event()
    alarm = MemoryAlarmTopic().deliver(event)
    ready, _values = _run(tmp_path, alarm, task_id=event.event_id)
    payload = alarm_event_payload(event)
    task_id = ingest_alarm_payload(payload, ready, MemoryAlarmTopic(), {}, send)

    assert task_id == _EVENT_ID
    assert sent == []

    graph = _idle_graph(tmp_path / "fresh")
    judged: dict[str, str] = {}
    ingest_alarm_payload(payload, graph, MemoryAlarmTopic(), judged, send)
    ingest_alarm_payload(payload, graph, MemoryAlarmTopic(), judged, send)

    assert len(sent) == 1
    topic, body = sent[0]
    assert topic == "judgement.completed"
    assert json.loads(body) == {
        "event_id": _EVENT_ID,
        "task_id": _EVENT_ID,
        "risk_score": 0.95,
        "requires_human_review": False,
        "workorder_id": f"wo-{_EVENT_ID}",
        "schema_version": "1.0",
    }
    invalid: list[tuple[str, bytes]] = []
    assert (
        handle_alarm_payload(
            b"not-json",
            graph,
            MemoryAlarmTopic(),
            {},
            lambda completed_topic, completed: invalid.append((completed_topic, completed)),
        )
        is None
    )
    assert invalid == []


def test_ingest_thread_publishes_one_completion(tmp_path, monkeypatch) -> None:
    graph = _idle_graph(tmp_path / "thread")
    produced: list[tuple[str, str, bytes]] = []
    payload = alarm_event_payload(fatigue_alarm_event())

    def serve(_bootstrap: str, handle, _stop, on_ready=None) -> None:
        del on_ready
        handle(payload)
        handle(payload)

    def produce(bootstrap: str, topic: str, body: bytes) -> None:
        produced.append((bootstrap, topic, body))

    monkeypatch.setattr("vehicle_agent.kafka_alarm.serve_vehicle_alarms", serve)
    monkeypatch.setattr("vehicle_agent.kafka_alarm.produce_judgement_completed", produce)
    _serve_alarm_ingest("kafka:29092", graph, threading.Event(), threading.Event())

    assert len(produced) == 1
    bootstrap, topic, body = produced[0]
    assert bootstrap == "kafka:29092"
    assert topic == "judgement.completed"
    assert json.loads(body) == {
        "event_id": _EVENT_ID,
        "task_id": _EVENT_ID,
        "risk_score": 0.95,
        "requires_human_review": False,
        "workorder_id": f"wo-{_EVENT_ID}",
        "schema_version": "1.0",
    }


def test_existing_checkpoint_skips_a_second_invoke(tmp_path) -> None:
    event = fatigue_alarm_event()
    alarm = MemoryAlarmTopic().deliver(event)
    graph, _values = _run(tmp_path, alarm, task_id=event.event_id)

    def fail(*_args, **_kwargs):
        raise AssertionError("checkpoint should skip invoke")

    graph.invoke = fail
    task_id = ingest_alarm_payload(
        alarm_event_payload(event),
        graph,
        MemoryAlarmTopic(),
        {},
    )

    assert task_id == _EVENT_ID


def test_alarm_ingest_retries_until_the_broker_accepts(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []
    ready = threading.Event()

    def serve(bootstrap: str, _handle, _stop, on_ready=None) -> None:
        calls.append((bootstrap, ready.is_set()))
        if on_ready is not None:
            on_ready()
        if len(calls) == 1:
            raise OSError("broker down")

    monkeypatch.setattr("vehicle_agent.kafka_alarm.serve_vehicle_alarms", serve)
    monkeypatch.setattr("vehicle_agent.api.main._INGEST_RETRY_SECONDS", 0)
    stop = threading.Event()

    _serve_alarm_ingest("kafka:29092", object(), stop, ready)

    assert calls == [("kafka:29092", False), ("kafka:29092", False)]
    assert ready.is_set()


def test_ingest_defaults_stay_off() -> None:
    settings = Settings()

    assert settings.ingest == "off"
    assert settings.kafka_bootstrap == "127.0.0.1:9092"


def _idle_graph(directory):
    directory.mkdir()
    runtime = _settings(checkpoint_path=str(directory / "checkpoints.sqlite"))
    harness = Harness(
        runtime,
        FakeModelAdapter(REPORT),
        build_vehicle_tools(build_seed_repository(), settings=runtime),
        policy=AllowApprovedWritesPolicy(),
    )
    return build_graph(harness, open_checkpointer(directory / "checkpoints.sqlite"))


def _tool_names(values: dict) -> list[str]:
    return [item["name"] for item in values["trace"] if item["kind"] == "tool"]


def _alarm_ids(repository) -> list[str]:
    alarms = repository.list_alarms_between(
        start=datetime(2000, 1, 1, tzinfo=UTC),
        end=datetime(2100, 1, 1, tzinfo=UTC),
        include_end=True,
    )
    return [item.id for item in alarms]
