from datetime import UTC, datetime, timedelta

from vehicle_agent.domain.models import (
    Alarm,
    Device,
    DeviceMaintenance,
    Driver,
    RepairRecord,
    Vehicle,
)


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
        firmware: dict[str, str] | None = None,
        repairs: list[RepairRecord] | None = None,
        failing_maintenance_ids: set[str] | None = None,
    ) -> None:
        self._vehicles = vehicles
        self._drivers = drivers
        self._alarms = alarms
        self._devices = devices or {}
        self._firmware = firmware or {}
        self._repairs = repairs or []
        self._failing_vehicle_ids = failing_vehicle_ids or set()
        self._failing_device_ids = failing_device_ids or set()
        self._failing_maintenance_ids = failing_maintenance_ids or set()

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

    def get_maintenance(self, device_id: str) -> DeviceMaintenance:
        if device_id in self._failing_maintenance_ids:
            raise ConnectionError(f"maintenance store unavailable for {device_id}")
        if device_id not in self._devices:
            raise KeyError(f"unknown device: {device_id}")
        try:
            firmware = self._firmware[device_id]
        except KeyError as exc:
            raise KeyError(f"unknown device: {device_id}") from exc
        matched = [item for item in self._repairs if item.device_id == device_id]
        repair = max(matched, key=lambda item: item.opened_at) if matched else None
        return DeviceMaintenance(
            device_id=device_id,
            firmware_version=firmware,
            repair=repair,
        )

    def find_vehicle_id(self, device_id: str) -> str | None:
        for vehicle in self._vehicles.values():
            if vehicle.device_id == device_id:
                return vehicle.id
        return None

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


def build_seed_repository(
    failing_maintenance_ids: set[str] | None = None,
) -> MemoryAlarmRepository:
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
        "vehicle_004": Vehicle(
            id="vehicle_004",
            plate_number="粤B13579",
            enterprise_id="enterprise_001",
            vehicle_type="coach",
            device_id="device_004",
        ),
        "vehicle_005": Vehicle(
            id="vehicle_005",
            plate_number="粤B24680",
            enterprise_id="enterprise_001",
            vehicle_type="coach",
            device_id="device_005",
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
        "device_004": Device(
            id="device_004",
            online_status="online",
            last_heartbeat_at=now,
        ),
        "device_005": Device(
            id="device_005",
            online_status="online",
            last_heartbeat_at=now,
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
        *_burst("vehicle_004", "device_004", "alarm_004", now),
        *_burst("vehicle_005", "device_005", "alarm_005", now),
        _fatigue(
            "alarm_005_old",
            "vehicle_005",
            "device_005",
            now - timedelta(days=2),
        ),
    ]
    firmware = {
        "device_001": "1.4.0",
        "device_002": "1.4.0",
        "device_003": "1.2.0",
        "device_004": "1.3.0",
        "device_005": "1.4.0",
    }
    repairs = [
        RepairRecord(
            id="repair_003_closed",
            device_id="device_003",
            fault="camera_occlusion",
            status="closed",
            opened_at=datetime(2026, 10, 1, 10, 30, tzinfo=UTC),
            closed_at=datetime(2026, 10, 2, 10, 30, tzinfo=UTC),
        ),
        RepairRecord(
            id="repair_003_open",
            device_id="device_003",
            fault="camera_occlusion",
            status="open",
            opened_at=datetime(2026, 10, 6, 10, 30, tzinfo=UTC),
        ),
    ]
    return MemoryAlarmRepository(
        vehicles,
        drivers,
        history,
        failing_vehicle_ids={"vehicle_error"},
        devices=devices,
        failing_device_ids={"device_error"},
        firmware=firmware,
        repairs=repairs,
        failing_maintenance_ids=failing_maintenance_ids,
    )


def _fatigue(alarm_id: str, vehicle_id: str, device_id: str, alarm_time: datetime) -> Alarm:
    return Alarm(
        id=alarm_id,
        vehicle_id=vehicle_id,
        driver_id="driver_001",
        device_id=device_id,
        alarm_type="driver_fatigue",
        alarm_time=alarm_time,
        longitude=113.26,
        latitude=23.12,
        speed_kmh=70,
        raw_confidence=0.8,
    )


def _burst(vehicle_id: str, device_id: str, prefix: str, now: datetime) -> list[Alarm]:
    return [
        _fatigue(f"{prefix}_{index}", vehicle_id, device_id, now - timedelta(minutes=10 * index))
        for index in (1, 2, 3)
    ]
