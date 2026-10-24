from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from vehicle_agent.domain.models import Evidence
from vehicle_agent.harness.tools import ToolRegistry
from vehicle_agent.repositories.base import AlarmRepository
from vehicle_agent.skills.alarm_verification import ALARM_VERIFICATION
from vehicle_agent.workorders import MemoryWorkOrderStore

ReportPeriod = Literal["day", "week"]
ReportTrend = Literal["rising", "falling", "flat"]
ReportEffect = Literal["pending", "improved", "no_disposition"]
ReportStepName = Literal["alarms", "orders", "decide"]
_SAMPLE_AS_OF = datetime(2026, 10, 9, 10, 30, tzinfo=UTC)
_PERIOD_DAYS = {"day": 1, "week": 7}
_TREND_TEXT = {"rising": "上升", "falling": "下降", "flat": "持平"}
_RECOMMENDATIONS = {
    "pending": "先关闭未完成工单。",
    "improved": "保持已关闭工单的处置。",
    "no_disposition": "本窗口没有工单。",
}
_REGISTERED_TOOLS = [
    "get_vehicle_context",
    "list_recent_alarms",
    "get_device_status",
    "get_device_maintenance",
    "search_regulations",
    "query_vehicle_graph",
    "analyze_image",
    "analyze_video",
    "create_workorder",
    "get_workorder",
    "update_workorder",
]
_VERIFICATION_TOOLS = [
    "get_vehicle_context",
    "list_recent_alarms",
    "search_regulations",
    "query_vehicle_graph",
    "analyze_image",
    "analyze_video",
]


class ReportStep(BaseModel):
    name: ReportStepName
    tool: str | None = None
    on_failure: Literal["degrade"] | None = None


class OperationReport(BaseModel):
    skill_id: Literal["operation-report"]
    period: ReportPeriod
    status: Literal["completed", "degraded"]
    alarm_count: int | None = None
    previous_count: int | None = None
    trend: ReportTrend | None = None
    closed_count: int | None = None
    open_count: int | None = None
    effect: ReportEffect | None = None
    recommendation: str | None = None
    evidence: list[Evidence]


class OperationReportSkill(BaseModel):
    id: Literal["operation-report"]
    input_fields: list[str]
    steps: list[ReportStep]
    tools: list[str]
    output_fields: list[str]


OPERATION_REPORT = OperationReportSkill(
    id="operation-report",
    input_fields=["period"],
    steps=[
        ReportStep(name="alarms", on_failure="degrade"),
        ReportStep(name="orders", on_failure="degrade"),
        ReportStep(name="decide"),
    ],
    tools=[],
    output_fields=list(OperationReport.model_fields),
)


def check_operation_report_skill(registry: ToolRegistry) -> None:
    skill = OPERATION_REPORT
    declared = [step.tool for step in skill.steps if step.tool]
    if declared != skill.tools:
        raise RuntimeError("operation-report steps and tool list diverged")
    names = [tool.name for tool in registry.tools()]
    if names != _REGISTERED_TOOLS:
        raise RuntimeError("registered tools changed")
    if list(ALARM_VERIFICATION.tools) != _VERIFICATION_TOOLS:
        raise RuntimeError("alarm-verification tools changed")


def build_operation_report(
    repository: AlarmRepository,
    workorders: MemoryWorkOrderStore,
    period: ReportPeriod,
) -> OperationReport:
    """Count alarms in one window, then describe work orders linked to them."""

    length = timedelta(days=_PERIOD_DAYS[period])
    current_start = _SAMPLE_AS_OF - length
    try:
        current = repository.list_alarms_between(
            start=current_start,
            end=_SAMPLE_AS_OF,
            include_end=True,
        )
        previous = repository.list_alarms_between(
            start=_SAMPLE_AS_OF - (length * 2),
            end=current_start,
            include_end=False,
        )
    except Exception:
        return _degraded(period)

    alarm_count = len(current)
    previous_count = len(previous)
    trend = _trend(alarm_count, previous_count)
    evidence = [_alarm_evidence(alarm_count, previous_count, trend)]
    try:
        orders = workorders.list_workorders()
    except Exception:
        return _degraded(period, evidence, alarm_count, previous_count, trend)

    alarm_ids = {item.id for item in current}
    linked = [item for item in orders if item.alarm_id in alarm_ids]
    closed_count = len([item for item in linked if item.status == "closed"])
    open_count = len([item for item in linked if item.status == "open"])
    effect = _effect(open_count, closed_count)
    return OperationReport(
        skill_id=OPERATION_REPORT.id,
        period=period,
        status="completed",
        alarm_count=alarm_count,
        previous_count=previous_count,
        trend=trend,
        closed_count=closed_count,
        open_count=open_count,
        effect=effect,
        recommendation=_RECOMMENDATIONS[effect],
        evidence=[*evidence, _order_evidence(closed_count, open_count)],
    )


def _trend(alarm_count: int, previous_count: int) -> ReportTrend:
    if alarm_count > previous_count:
        return "rising"
    if alarm_count < previous_count:
        return "falling"
    return "flat"


def _effect(open_count: int, closed_count: int) -> ReportEffect:
    if open_count >= 1:
        return "pending"
    if closed_count >= 1:
        return "improved"
    return "no_disposition"


def _degraded(
    period: ReportPeriod,
    evidence: list[Evidence] | None = None,
    alarm_count: int | None = None,
    previous_count: int | None = None,
    trend: ReportTrend | None = None,
) -> OperationReport:
    return OperationReport(
        skill_id=OPERATION_REPORT.id,
        period=period,
        status="degraded",
        alarm_count=alarm_count,
        previous_count=previous_count,
        trend=trend,
        evidence=evidence or [],
    )


def _alarm_evidence(alarm_count: int, previous_count: int, trend: ReportTrend) -> Evidence:
    return Evidence(
        type="history",
        source="alarm_history",
        description=(
            f"本窗口有 {alarm_count} 次疲劳报警，上一窗口有 {previous_count} 次，"
            f"趋势{_TREND_TEXT[trend]}"
        ),
    )


def _order_evidence(closed_count: int, open_count: int) -> Evidence:
    return Evidence(
        type="context",
        source="workorder",
        description=f"已关闭 {closed_count} 张工单，未关闭 {open_count} 张工单",
    )
