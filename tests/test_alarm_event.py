from datetime import UTC, datetime

import pytest

from vehicle_agent.ingest import AlarmEvent, MemoryAlarmTopic, fatigue_alarm_event
from vehicle_agent.repositories.memory import build_seed_repository


def test_fatigue_alarm_event_matches_the_documented_sample() -> None:
    event = fatigue_alarm_event()

    assert event.event_id == "evt_20261009_000001"
    assert event.alarm_id == "alarm_000001"
    assert event.alarm_type == "driver_fatigue"
    assert event.vehicle_id == "vehicle_001"
    assert event.driver_id == "driver_001"
    assert event.device_id == "device_001"
    assert event.occurred_at == datetime(2026, 10, 9, 10, 30, tzinfo=UTC)
    assert event.longitude == 113.2644
    assert event.latitude == 23.1291
    assert event.speed_kmh == 82.5
    assert event.media_ids == ["media_001"]
    assert event.source_confidence == 0.71
    assert event.schema_version == "1.0"


def test_deliver_keeps_one_alarm_per_event_id() -> None:
    topic = MemoryAlarmTopic()
    event = fatigue_alarm_event()
    repository = build_seed_repository()
    before = _alarm_ids(repository)

    first = topic.deliver(event)
    repeat = topic.deliver(event.model_copy(update={"speed_kmh": 10}))
    second = topic.deliver(
        event.model_copy(update={"event_id": "evt_20261009_000002", "alarm_id": "alarm_000002"})
    )

    assert topic.name == "vehicle.alarm"
    assert first is repeat
    assert [item.id for item in topic.alarms()] == ["alarm_000001", "alarm_000002"]
    assert first.speed_kmh == 82.5
    assert first.raw_confidence == 0.71
    assert first.image_id is None
    assert first.video_id is None
    assert first.source == "device"
    assert first.status == "open"
    assert second.id == "alarm_000002"
    assert _alarm_ids(repository) == before
    assert repository.get_alarm("alarm_000001") is None


def test_invalid_events_leave_the_inbox_empty() -> None:
    topic = MemoryAlarmTopic()
    sample = fatigue_alarm_event().model_dump()

    blank = dict(sample)
    blank["event_id"] = "   "
    wrong_version = dict(sample)
    wrong_version["schema_version"] = "2.0"
    other_type = dict(sample)
    other_type["alarm_type"] = "speeding"

    with pytest.raises(ValueError):
        AlarmEvent.model_validate(blank)
    with pytest.raises(ValueError):
        AlarmEvent.model_validate(wrong_version)
    with pytest.raises(ValueError):
        AlarmEvent.model_validate(other_type)
    assert topic.alarms() == []


def _alarm_ids(repository) -> list[str]:
    alarms = repository.list_alarms_between(
        start=datetime(2000, 1, 1, tzinfo=UTC),
        end=datetime(2100, 1, 1, tzinfo=UTC),
        include_end=True,
    )
    return [item.id for item in alarms]
