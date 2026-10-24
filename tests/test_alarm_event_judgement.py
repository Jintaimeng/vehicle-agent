import json
from datetime import UTC, datetime

import pytest

from test_graph import _run
from vehicle_agent.ingest import (
    MemoryAlarmTopic,
    MemoryDeviceStatusTopic,
    alarm_event_payload,
    alarm_from_published_event,
    device_status_event,
    fatigue_alarm_event,
    publish_vehicle_alarm,
)
from vehicle_agent.repositories.memory import build_seed_repository

_TOOLS = [
    "get_vehicle_context",
    "list_recent_alarms",
    "search_regulations",
    "query_vehicle_graph",
    "create_workorder",
]


def test_published_alarm_uses_the_existing_graph(tmp_path) -> None:
    sent: list[tuple[str, bytes]] = []
    repository = build_seed_repository()
    before = _alarm_ids(repository)
    publish_vehicle_alarm(
        fatigue_alarm_event(),
        lambda topic, payload: sent.append((topic, payload)),
    )
    inbox = MemoryAlarmTopic()
    alarm = alarm_from_published_event(sent[0][1], inbox)
    slower = json.loads(sent[0][1])
    slower["speed_kmh"] = 10
    repeat = alarm_from_published_event(json.dumps(slower).encode("utf-8"), inbox)

    _, first = _run(tmp_path / "first", alarm)
    _, second = _run(tmp_path / "second", repeat)

    assert alarm.speed_kmh == 82.5
    assert alarm.raw_confidence == 0.71
    assert alarm.image_id is None
    assert alarm.video_id is None
    assert repeat is alarm
    assert first["risk_score"] == 0.95
    assert first["risk_level"] == "high"
    assert first["status"] == "completed"
    assert first["result"]["requires_human_review"] is False
    assert first["vision_status"] == "skipped"
    assert first["detections"] == []
    assert _tool_names(first) == _TOOLS
    assert second["risk_score"] == 0.95
    assert _alarm_ids(repository) == before
    assert MemoryDeviceStatusTopic().name == "device.status"
    assert device_status_event().event_id == "evt_20261009_000002"


def test_invalid_published_alarm_does_not_start_judgement(tmp_path) -> None:
    started = False

    def judge(payload: bytes) -> None:
        nonlocal started
        alarm = alarm_from_published_event(payload)
        started = True
        _run(tmp_path, alarm)

    with pytest.raises(ValueError):
        judge(b"not-json")
    assert started is False
    assert alarm_event_payload(fatigue_alarm_event())


def _tool_names(values: dict) -> list[str]:
    return [item["name"] for item in values["trace"] if item["kind"] == "tool"]


def _alarm_ids(repository) -> list[str]:
    alarms = repository.list_alarms_between(
        start=datetime(2000, 1, 1, tzinfo=UTC),
        end=datetime(2100, 1, 1, tzinfo=UTC),
        include_end=True,
    )
    return [item.id for item in alarms]
