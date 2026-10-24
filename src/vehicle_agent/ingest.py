import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from vehicle_agent.domain.models import Alarm, Device

_TOPIC = "vehicle.alarm"
_STATUS_TOPIC = "device.status"


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


def alarm_event_payload(event: AlarmEvent) -> bytes:
    """Section 12.2 JSON. Coordinates sit under location, and time ends in Z."""

    body = {
        "event_id": event.event_id,
        "alarm_id": event.alarm_id,
        "alarm_type": event.alarm_type,
        "vehicle_id": event.vehicle_id,
        "driver_id": event.driver_id,
        "device_id": event.device_id,
        "occurred_at": event.occurred_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "location": {"longitude": event.longitude, "latitude": event.latitude},
        "speed_kmh": event.speed_kmh,
        "media_ids": list(event.media_ids),
        "source_confidence": event.source_confidence,
        "schema_version": event.schema_version,
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def alarm_event_from_payload(payload: bytes) -> AlarmEvent:
    """Parse a section 12.2 payload. Invalid input raises ValueError before delivery."""

    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("alarm payload is not json") from exc
    if not isinstance(raw, dict):
        raise ValueError("alarm payload is not json")
    location = raw.get("location")
    if not isinstance(location, dict) or "longitude" not in location or "latitude" not in location:
        raise ValueError("alarm payload is missing location")
    occurred_at = raw.get("occurred_at")
    if not isinstance(occurred_at, str):
        raise ValueError("alarm payload is missing occurred_at")
    if occurred_at.endswith("Z"):
        occurred_at = occurred_at[:-1] + "+00:00"
    try:
        return AlarmEvent(
            event_id=raw["event_id"],
            alarm_id=raw["alarm_id"],
            alarm_type=raw["alarm_type"],
            vehicle_id=raw["vehicle_id"],
            driver_id=raw["driver_id"],
            device_id=raw["device_id"],
            occurred_at=datetime.fromisoformat(occurred_at),
            longitude=location["longitude"],
            latitude=location["latitude"],
            speed_kmh=raw["speed_kmh"],
            media_ids=raw["media_ids"],
            source_confidence=raw["source_confidence"],
            schema_version=raw["schema_version"],
        )
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise ValueError("alarm payload is invalid") from exc


def publish_vehicle_alarm(event: AlarmEvent, send: Callable[[str, bytes], None]) -> None:
    """Hand one vehicle.alarm payload to the caller-supplied sender."""

    send(_TOPIC, alarm_event_payload(event))


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


def alarm_from_published_event(payload: bytes, topic: MemoryAlarmTopic | None = None) -> Alarm:
    """Parse one vehicle.alarm payload and return the inbox alarm for that event."""

    inbox = MemoryAlarmTopic() if topic is None else topic
    return inbox.deliver(alarm_event_from_payload(payload))


class DeviceStatusEvent(BaseModel):
    """One device.status payload. It does not update the seeded device table."""

    event_id: str
    device_id: str
    online_status: Literal["online", "offline"]
    last_heartbeat_at: datetime
    schema_version: Literal["1.0"]

    @field_validator("event_id")
    @classmethod
    def event_id_is_present(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("event_id must not be blank")
        return cleaned


def device_status_event() -> DeviceStatusEvent:
    """A fixed device_001 heartbeat, kept out of the seeded repository."""

    return DeviceStatusEvent(
        event_id="evt_20261009_000002",
        device_id="device_001",
        online_status="online",
        last_heartbeat_at=datetime(2026, 10, 9, 10, 30, tzinfo=UTC),
        schema_version="1.0",
    )


def device_status_payload(event: DeviceStatusEvent) -> bytes:
    """JSON for one device heartbeat. The timestamp ends in Z."""

    body = {
        "event_id": event.event_id,
        "device_id": event.device_id,
        "online_status": event.online_status,
        "last_heartbeat_at": event.last_heartbeat_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": event.schema_version,
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def device_status_from_payload(payload: bytes) -> DeviceStatusEvent:
    """Parse a device.status payload. Invalid input raises ValueError before delivery."""

    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("device status payload is not json") from exc
    if not isinstance(raw, dict):
        raise ValueError("device status payload is not json")
    heartbeat = raw.get("last_heartbeat_at")
    if not isinstance(heartbeat, str):
        raise ValueError("device status payload is missing last_heartbeat_at")
    if heartbeat.endswith("Z"):
        heartbeat = heartbeat[:-1] + "+00:00"
    try:
        return DeviceStatusEvent(
            event_id=raw["event_id"],
            device_id=raw["device_id"],
            online_status=raw["online_status"],
            last_heartbeat_at=datetime.fromisoformat(heartbeat),
            schema_version=raw["schema_version"],
        )
    except (KeyError, TypeError, ValidationError, ValueError) as exc:
        raise ValueError("device status payload is invalid") from exc


def publish_device_status(event: DeviceStatusEvent, send: Callable[[str, bytes], None]) -> None:
    """Hand one device.status payload to the caller-supplied sender."""

    send(_STATUS_TOPIC, device_status_payload(event))


class MemoryDeviceStatusTopic:
    """In-memory device.status log. One event_id becomes one device."""

    def __init__(self) -> None:
        self.name = _STATUS_TOPIC
        self._log: list[DeviceStatusEvent] = []
        self._devices: dict[str, Device] = {}

    def deliver(self, event: DeviceStatusEvent) -> Device:
        self._log.append(event)
        existing = self._devices.get(event.event_id)
        if existing is not None:
            return existing
        device = Device(
            id=event.device_id,
            online_status=event.online_status,
            last_heartbeat_at=event.last_heartbeat_at,
        )
        self._devices[event.event_id] = device
        return device

    def devices(self) -> list[Device]:
        return list(self._devices.values())
