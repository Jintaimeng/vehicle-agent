import json
from datetime import UTC, datetime

import pytest

from vehicle_agent.ingest import (
    MemoryAlarmTopic,
    alarm_event_from_payload,
    alarm_event_payload,
    device_status_event,
    fatigue_alarm_event,
    publish_vehicle_alarm,
)
from vehicle_agent.repositories.memory import build_seed_repository


def test_fatigue_alarm_payload_matches_section_12_2() -> None:
    event = fatigue_alarm_event()
    payload = alarm_event_payload(event)
    body = json.loads(payload)

    assert body == {
        "event_id": "evt_20261009_000001",
        "alarm_id": "alarm_000001",
        "alarm_type": "driver_fatigue",
        "vehicle_id": "vehicle_001",
        "driver_id": "driver_001",
        "device_id": "device_001",
        "occurred_at": "2026-10-09T10:30:00Z",
        "location": {"longitude": 113.2644, "latitude": 23.1291},
        "speed_kmh": 82.5,
        "media_ids": ["media_001"],
        "source_confidence": 0.71,
        "schema_version": "1.0",
    }
    assert "longitude" not in body
    assert payload.endswith(b"}")
    assert b"2026-10-09T10:30:00Z" in payload
    assert alarm_event_from_payload(payload) == event


def test_publish_once_and_duplicate_delivery_keeps_one_alarm() -> None:
    sent: list[tuple[str, bytes]] = []
    repository = build_seed_repository()
    before = _alarm_ids(repository)

    publish_vehicle_alarm(
        fatigue_alarm_event(),
        lambda topic, payload: sent.append((topic, payload)),
    )

    topic = MemoryAlarmTopic()
    event = alarm_event_from_payload(sent[0][1])
    first = topic.deliver(event)
    repeat = topic.deliver(event)

    assert sent == [("vehicle.alarm", alarm_event_payload(fatigue_alarm_event()))]
    assert first is repeat
    assert [item.id for item in topic.alarms()] == ["alarm_000001"]
    assert first.speed_kmh == 82.5
    assert _alarm_ids(repository) == before
    assert device_status_event().event_id == "evt_20261009_000002"


def test_invalid_payloads_are_rejected_before_delivery() -> None:
    topic = MemoryAlarmTopic()

    with pytest.raises(ValueError):
        alarm_event_from_payload(b"not-json")
    with pytest.raises(ValueError):
        alarm_event_from_payload(b'{"event_id": "evt_20261009_000001"}')
    assert topic.alarms() == []


def _alarm_ids(repository) -> list[str]:
    alarms = repository.list_alarms_between(
        start=datetime(2000, 1, 1, tzinfo=UTC),
        end=datetime(2100, 1, 1, tzinfo=UTC),
        include_end=True,
    )
    return [item.id for item in alarms]
