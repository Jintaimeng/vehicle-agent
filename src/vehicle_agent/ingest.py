from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from vehicle_agent.domain.models import Alarm

_TOPIC = "vehicle.alarm"


class AlarmEvent(BaseModel):
    """One vehicle.alarm payload, matching the documented fatigue example."""

    event_id: str
    alarm_id: str
    alarm_type: Literal["driver_fatigue"]
    vehicle_id: str
    driver_id: str
    device_id: str
    occurred_at: datetime
    longitude: float
    latitude: float
    speed_kmh: float = Field(ge=0)
    media_ids: list[str]
    source_confidence: float = Field(ge=0, le=1)
    schema_version: Literal["1.0"]

    @field_validator("event_id")
    @classmethod
    def event_id_is_present(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("event_id must not be blank")
        return cleaned


def fatigue_alarm_event() -> AlarmEvent:
    """The section 12.2 fatigue alarm, kept out of the seeded repository."""

    return AlarmEvent(
        event_id="evt_20261009_000001",
        alarm_id="alarm_000001",
        alarm_type="driver_fatigue",
        vehicle_id="vehicle_001",
        driver_id="driver_001",
        device_id="device_001",
        occurred_at=datetime(2026, 10, 9, 10, 30, tzinfo=UTC),
        longitude=113.2644,
        latitude=23.1291,
        speed_kmh=82.5,
        media_ids=["media_001"],
        source_confidence=0.71,
        schema_version="1.0",
    )


class MemoryAlarmTopic:
    """In-memory vehicle.alarm log. One event_id becomes one alarm."""

    def __init__(self) -> None:
        self.name = _TOPIC
        self._log: list[AlarmEvent] = []
        self._alarms: dict[str, Alarm] = {}

    def deliver(self, event: AlarmEvent) -> Alarm:
        self._log.append(event)
        existing = self._alarms.get(event.event_id)
        if existing is not None:
            return existing
        alarm = Alarm(
            id=event.alarm_id,
            vehicle_id=event.vehicle_id,
            driver_id=event.driver_id,
            device_id=event.device_id,
            alarm_type=event.alarm_type,
            alarm_time=event.occurred_at,
            longitude=event.longitude,
            latitude=event.latitude,
            speed_kmh=event.speed_kmh,
            raw_confidence=event.source_confidence,
        )
        self._alarms[event.event_id] = alarm
        return alarm

    def alarms(self) -> list[Alarm]:
        return list(self._alarms.values())
