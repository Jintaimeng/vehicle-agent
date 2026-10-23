from datetime import datetime
from typing import Protocol

from vehicle_agent.domain.models import Alarm, Device, DeviceMaintenance, Driver, Vehicle


class AlarmRepository(Protocol):
    """Read-only port for vehicle context, device status, and alarm history."""

    def get_vehicle(self, vehicle_id: str) -> Vehicle | None: ...

    def get_driver(self, driver_id: str) -> Driver | None: ...

    def get_device(self, device_id: str) -> Device: ...

    def get_maintenance(self, device_id: str) -> DeviceMaintenance: ...

    def find_vehicle_id(self, device_id: str) -> str | None: ...

    def list_recent_alarms(
        self,
        vehicle_id: str,
        *,
        since: datetime,
        alarm_type: str | None = None,
    ) -> list[Alarm]: ...
