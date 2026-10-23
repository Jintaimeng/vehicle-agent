from datetime import UTC, datetime, timedelta

from vehicle_agent.domain.models import Alarm, Device, Driver, Vehicle


class MemoryAlarmRepository:
    """In-memory repository with a fixed sample set for repeatable demos and tests."""

    def __init__(
        self,
        vehicles: dict[str, Vehicle],
        drivers: dict[str, Driver],
        alarms: list[Alarm],
        failing_vehicle_ids: set[str] | None = None,
        devices: dict[str, Device] | None = None,
        failing_device_ids: set[str] | None = None,
    ) -> None:
        self._vehicles = vehicles
        self._drivers = drivers
        self._alarms = alarms
        self._devices = devices or {}
        self._failing_vehicle_ids = failing_vehicle_ids or set()
        self._failing_device_ids = failing_device_ids or set()

    def get_vehicle(self, vehicle_id: str) -> Vehicle | None:
        self._raise_if_unavailable(vehicle_id)
        return self._vehicles.get(vehicle_id)

    def get_driver(self, driver_id: str) -> Driver | None:
        return self._drivers.get(driver_id)

    def get_device(self, device_id: str) -> Device:
        if device_id in self._failing_device_ids:
            raise ConnectionError(f"device store unavailable for {device_id}")
        try:
            return self._devices[device_id]
        except KeyError as exc:
            raise KeyError(f"unknown device: {device_id}") from exc

    def list_recent_alarms(
        self,
        vehicle_id: str,
        *,
        since: datetime,
        alarm_type: str | None = None,
    ) -> list[Alarm]:
        self._raise_if_unavailable(vehicle_id)
        matched = []
        for alarm in self._alarms:
            if alarm.vehicle_id != vehicle_id or alarm.alarm_time < since:
                continue
            if alarm_type is not None and alarm.alarm_type != alarm_type:
                continue
            matched.append(alarm)
        return sorted(matched, key=lambda item: item.alarm_time)

    def _raise_if_unavailable(self, vehicle_id: str) -> None:
        if vehicle_id in self._failing_vehicle_ids:
            raise ConnectionError(f"vehicle store unavailable for {vehicle_id}")


def build_seed_repository() -> MemoryAlarmRepository:
    now = datetime(2026, 10, 9, 10, 30, tzinfo=UTC)
    vehicles = {
        "vehicle_001": Vehicle(
            id="vehicle_001",
            plate_number="粤B12345",
            enterprise_id="enterprise_001",
            vehicle_type="coach",
            device_id="device_001",
        ),
        "vehicle_002": Vehicle(
            id="vehicle_002",
            plate_number="粤B54321",
            enterprise_id="enterprise_001",
            vehicle_type="truck",
            device_id="device_002",
        ),
        "vehicle_003": Vehicle(
            id="vehicle_003",
            plate_number="粤B67890",
            enterprise_id="enterprise_001",
            vehicle_type="coach",
            device_id="device_003",
        ),
    }
    drivers = {
        "driver_001": Driver(
            id="driver_001",
            name="张三",
            license_type="A1",
            enterprise_id="enterprise_001",
        ),
        "driver_002": Driver(
            id="driver_002",
            name="李四",
            license_type="A2",
            enterprise_id="enterprise_001",
        ),
        "driver_003": Driver(
            id="driver_003",
            name="王五",
            license_type="A1",
            enterprise_id="enterprise_001",
        ),
    }
    devices = {
        "device_001": Device(
            id="device_001",
            online_status="online",
            last_heartbeat_at=now,
        ),
        "device_002": Device(
            id="device_002",
            online_status="online",
            last_heartbeat_at=now,
        ),
        "device_003": Device(
            id="device_003",
            online_status="offline",
            last_heartbeat_at=now - timedelta(days=2),
        ),
    }
    history = [
        Alarm(
            id="alarm_hist_001",
            vehicle_id="vehicle_001",
            driver_id="driver_001",
            device_id="device_001",
            alarm_type="driver_fatigue",
            alarm_time=now - timedelta(days=2),
            longitude=113.26,
            latitude=23.12,
            speed_kmh=78,
            raw_confidence=0.82,
        ),
        Alarm(
            id="alarm_hist_002",
            vehicle_id="vehicle_001",
            driver_id="driver_001",
            device_id="device_001",
            alarm_type="driver_fatigue",
            alarm_time=now - timedelta(days=5),
            longitude=113.27,
            latitude=23.13,
            speed_kmh=81,
            raw_confidence=0.77,
        ),
    ]
    return MemoryAlarmRepository(
        vehicles,
        drivers,
        history,
        failing_vehicle_ids={"vehicle_error"},
        devices=devices,
        failing_device_ids={"device_error"},
    )
