from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from vehicle_agent.domain.models import Alarm, DeviceMaintenance, Evidence
from vehicle_agent.harness.errors import ToolExecutionError
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import AlarmHistoryOutput, ToolRegistry
from vehicle_agent.repositories.base import AlarmRepository
from vehicle_agent.skills.alarm_verification import ALARM_VERIFICATION

ProfileSource = Literal[
    "device_fault",
    "high_speed_fatigue",
    "driver_fatigue",
    "no_recent_risk",
]
ProfileTrend = Literal["rising", "falling", "flat"]
ProfileStepName = Literal["alarms", "maintenance", "decide"]
_SAMPLE_AS_OF = datetime(2026, 10, 9, 10, 30, tzinfo=UTC)
_WINDOW_DAYS = 7
_RECENT_DAYS = 1
_HIGH_SPEED = 80
_FATIGUE_COUNT = 2
_TREND_TEXT = {"rising": "上升", "falling": "下降", "flat": "持平"}
_RECOMMENDATIONS = {
    "device_fault": "先处理未关闭的维修。",
    "high_speed_fatigue": "复核高速疲劳报警。",
    "driver_fatigue": "复核近期疲劳报警。",
    "no_recent_risk": "保持例行监控。",
}


class ProfileStep(BaseModel):
    name: ProfileStepName
    tool: str | None = None
    on_failure: Literal["degrade"] | None = None


class VehicleRiskProfile(BaseModel):
    skill_id: Literal["vehicle-risk-profile"]
    vehicle_id: str
    status: Literal["completed", "degraded"]
    source: ProfileSource | None = None
    trend: ProfileTrend | None = None
    alarm_count: int | None = None
    recent_count: int | None = None
    recommendation: str | None = None
    evidence: list[Evidence]


class VehicleRiskProfileSkill(BaseModel):
    id: Literal["vehicle-risk-profile"]
    input_fields: list[str]
    steps: list[ProfileStep]
    tools: list[str]
    output_fields: list[str]


VEHICLE_RISK_PROFILE = VehicleRiskProfileSkill(
    id="vehicle-risk-profile",
    input_fields=["vehicle_id"],
    steps=[
        ProfileStep(name="alarms", tool="list_recent_alarms", on_failure="degrade"),
        ProfileStep(name="maintenance", tool="get_device_maintenance", on_failure="degrade"),
        ProfileStep(name="decide"),
    ],
    tools=["list_recent_alarms", "get_device_maintenance"],
    output_fields=list(VehicleRiskProfile.model_fields),
)


def check_vehicle_risk_profile_skill(registry: ToolRegistry) -> None:
    skill = VEHICLE_RISK_PROFILE
    declared = [step.tool for step in skill.steps if step.tool]
    if declared != skill.tools:
        raise RuntimeError("vehicle-risk-profile steps and tool list diverged")
    for name in skill.tools:
        try:
            tool = registry.get(name)
        except KeyError as exc:
            raise RuntimeError(f"vehicle-risk-profile tool is not registered: {name}") from exc
        if not tool.readonly:
            raise RuntimeError(f"vehicle-risk-profile tool must be read-only: {name}")
    if "get_device_maintenance" in ALARM_VERIFICATION.tools:
        raise RuntimeError("alarm-verification must not call get_device_maintenance")


def profile_vehicle(
    harness: Harness,
    repository: AlarmRepository,
    vehicle_id: str,
) -> VehicleRiskProfile:
    """Summarize one vehicle from alarm history and an open repair."""

    vehicle = repository.get_vehicle(vehicle_id)
    if vehicle is None:
        raise KeyError(f"unknown vehicle: {vehicle_id}")

    task_id = f"profile-{vehicle_id}"
    try:
        history = harness.call_tool(
            "list_recent_alarms",
            {"vehicle_id": vehicle_id, "as_of": _SAMPLE_AS_OF, "within_days": _WINDOW_DAYS},
            task_id=task_id,
            node="alarms",
        )
    except ToolExecutionError:
        return _degraded(vehicle_id)
    if not isinstance(history, AlarmHistoryOutput):
        return _degraded(vehicle_id)

    matched = [item for item in history.alarms if item.vehicle_id == vehicle_id]
    recent_since = _SAMPLE_AS_OF - timedelta(days=_RECENT_DAYS)
    recent_count = len([item for item in matched if item.alarm_time >= recent_since])
    alarm_count = len(matched)
    earlier_count = alarm_count - recent_count
    trend = _trend(recent_count, earlier_count)
    evidence = [
        _alarm_evidence(vehicle_id, alarm_count, recent_count),
        _trend_evidence(vehicle_id, trend),
    ]
    try:
        maintenance = harness.call_tool(
            "get_device_maintenance",
            {"device_id": vehicle.device_id},
            task_id=task_id,
            node="maintenance",
        )
    except ToolExecutionError:
        return _degraded(vehicle_id, evidence, alarm_count, recent_count)
    if not isinstance(maintenance, DeviceMaintenance):
        return _degraded(vehicle_id, evidence, alarm_count, recent_count)

    source = _source(matched, maintenance, alarm_count)
    return VehicleRiskProfile(
        skill_id=VEHICLE_RISK_PROFILE.id,
        vehicle_id=vehicle_id,
        status="completed",
        source=source,
        trend=trend,
        alarm_count=alarm_count,
        recent_count=recent_count,
        recommendation=_RECOMMENDATIONS[source],
        evidence=[*evidence, _maintenance_evidence(maintenance)],
    )


def _source(
    alarms: list[Alarm],
    maintenance: DeviceMaintenance,
    alarm_count: int,
) -> ProfileSource:
    repair = maintenance.repair
    if repair is not None and repair.status == "open":
        return "device_fault"
    if any(item.speed_kmh >= _HIGH_SPEED for item in alarms):
        return "high_speed_fatigue"
    if alarm_count >= _FATIGUE_COUNT:
        return "driver_fatigue"
    return "no_recent_risk"


def _trend(recent_count: int, earlier_count: int) -> ProfileTrend:
    if recent_count > earlier_count:
        return "rising"
    if recent_count < earlier_count:
        return "falling"
    return "flat"


def _degraded(
    vehicle_id: str,
    evidence: list[Evidence] | None = None,
    alarm_count: int | None = None,
    recent_count: int | None = None,
) -> VehicleRiskProfile:
    return VehicleRiskProfile(
        skill_id=VEHICLE_RISK_PROFILE.id,
        vehicle_id=vehicle_id,
        status="degraded",
        alarm_count=alarm_count,
        recent_count=recent_count,
        evidence=evidence or [],
    )


def _alarm_evidence(vehicle_id: str, alarm_count: int, recent_count: int) -> Evidence:
    return Evidence(
        type="history",
        source="alarm_history",
        description=(
            f"车辆 {vehicle_id} 在 7 日内有 {alarm_count} 次疲劳报警，其中 1 日内 {recent_count} 次"
        ),
    )


def _trend_evidence(vehicle_id: str, trend: ProfileTrend) -> Evidence:
    return Evidence(
        type="history",
        source="alarm_history",
        description=f"车辆 {vehicle_id} 的报警趋势{_TREND_TEXT[trend]}",
    )


def _maintenance_evidence(item: DeviceMaintenance) -> Evidence:
    if item.repair is not None and item.repair.status == "open":
        description = f"设备 {item.device_id} 有未关闭维修"
    else:
        description = f"设备 {item.device_id} 无未关闭维修"
    return Evidence(type="context", source="maintenance", description=description)
