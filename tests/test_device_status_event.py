from datetime import UTC, datetime

import pytest

from vehicle_agent.ingest import (
    DeviceStatusEvent,
    MemoryAlarmTopic,
    MemoryDeviceStatusTopic,
    device_status_event,
    fatigue_alarm_event,
)
from vehicle_agent.repositories.memory import build_seed_repository

_HEARTBEAT = datetime(2026, 10, 9, 10, 30, tzinfo=UTC)


def test_device_status_event_matches_the_fixed_sample() -> None:
    event = device_status_event()

    assert event.event_id == "evt_20261009_000002"
    assert event.device_id == "device_001"
    assert event.online_status == "online"
    assert event.last_heartbeat_at == _HEARTBEAT
    assert event.schema_version == "1.0"


def test_deliver_keeps_one_device_per_event_id() -> None:
    topic = MemoryDeviceStatusTopic()
    event = device_status_event()
    repository = build_seed_repository()
    seeded = repository.get_device("device_001")

    first = topic.deliver(event)
    repeat = topic.deliver(event.model_copy(update={"online_status": "offline"}))
    second = topic.deliver(
        event.model_copy(
            update={
                "event_id": "evt_20261009_000003",
                "device_id": "device_002",
                "online_status": "offline",
            }
        )
    )
    alarm = MemoryAlarmTopic().deliver(fatigue_alarm_event())

    assert topic.name == "device.status"
    assert first is repeat
    assert [item.id for item in topic.devices()] == ["device_001", "device_002"]
    assert first.online_status == "online"
    assert first.last_heartbeat_at == _HEARTBEAT
    assert second.id == "device_002"
    assert second.online_status == "offline"
    assert repository.get_device("device_001") is seeded
    assert seeded is not first
    assert repository.get_device("device_003").online_status == "offline"
    assert alarm.speed_kmh == 82.5
    assert alarm.id == "alarm_000001"


def test_invalid_status_events_leave_the_inbox_empty() -> None:
    topic = MemoryDeviceStatusTopic()
    sample = device_status_event().model_dump()

    blank = dict(sample)
    blank["event_id"] = "   "
    wrong_version = dict(sample)
    wrong_version["schema_version"] = "2.0"
    other_status = dict(sample)
    other_status["online_status"] = "sleeping"

    with pytest.raises(ValueError):
        DeviceStatusEvent.model_validate(blank)
    with pytest.raises(ValueError):
        DeviceStatusEvent.model_validate(wrong_version)
    with pytest.raises(ValueError):
        DeviceStatusEvent.model_validate(other_status)
    assert topic.devices() == []
