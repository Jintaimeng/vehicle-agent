from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TypeVar

from pydantic import BaseModel, Field

from vehicle_agent.domain.models import Alarm, Driver, Vehicle
from vehicle_agent.repositories.base import AlarmRepository

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class RegisteredTool:
    def __init__(
        self,
        name: str,
        description: str,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
        handler: Callable[[BaseModel], BaseModel],
        *,
        readonly: bool = True,
    ) -> None:
        self.name = name
        self.description = description
        self.input_model = input_model
        self.output_model = output_model
        self.handler = handler
        self.readonly = readonly


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc


class GetVehicleContextInput(BaseModel):
    vehicle_id: str
    driver_id: str


class VehicleContextOutput(BaseModel):
    vehicle: Vehicle | None
    driver: Driver | None


class ListRecentAlarmsInput(BaseModel):
    vehicle_id: str
    as_of: datetime
    within_days: int = Field(default=7, ge=1, le=30)
    alarm_type: str | None = None


class AlarmHistoryOutput(BaseModel):
    alarms: list[Alarm]


def build_vehicle_tools(repository: AlarmRepository) -> ToolRegistry:
    registry = ToolRegistry()

    def get_vehicle_context(payload: GetVehicleContextInput) -> VehicleContextOutput:
        return VehicleContextOutput(
            vehicle=repository.get_vehicle(payload.vehicle_id),
            driver=repository.get_driver(payload.driver_id),
        )

    def list_recent_alarms(payload: ListRecentAlarmsInput) -> AlarmHistoryOutput:
        since = payload.as_of - timedelta(days=payload.within_days)
        alarms = repository.list_recent_alarms(
            payload.vehicle_id,
            since=since,
            alarm_type=payload.alarm_type,
        )
        return AlarmHistoryOutput(alarms=alarms)

    registry.register(
        RegisteredTool(
            name="get_vehicle_context",
            description="查询车辆和驾驶员资料",
            input_model=GetVehicleContextInput,
            output_model=VehicleContextOutput,
            handler=get_vehicle_context,
        )
    )
    registry.register(
        RegisteredTool(
            name="list_recent_alarms",
            description="查询车辆近期报警",
            input_model=ListRecentAlarmsInput,
            output_model=AlarmHistoryOutput,
            handler=list_recent_alarms,
        )
    )
    return registry
