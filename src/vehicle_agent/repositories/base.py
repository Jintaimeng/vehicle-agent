from datetime import datetime
from typing import Protocol

from vehicle_agent.domain.models import Alarm, Driver, Vehicle


class AlarmRepository(Protocol):
    """Read-only port for vehicle context and alarm history."""

    def get_vehicle(self, vehicle_id: str) -> Vehicle | None: ...

    def get_driver(self, driver_id: str) -> Driver | None: ...

    def list_recent_alarms(
        self,
        vehicle_id: str,
        *,
        since: datetime,
        alarm_type: str | None = None,
    ) -> list[Alarm]: ...
