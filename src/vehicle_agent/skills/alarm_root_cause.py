from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from vehicle_agent.domain.models import Evidence
from vehicle_agent.harness.errors import ToolExecutionError
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import (
    AlarmHistoryOutput,
    QueryVehicleGraphOutput,
    SearchRegulationsOutput,
    ToolRegistry,
)
from vehicle_agent.knowledge.graph import GraphFact
from vehicle_agent.knowledge.index import RegulationSnippet
from vehicle_agent.repositories.base import AlarmRepository
from vehicle_agent.skills.alarm_verification import ALARM_VERIFICATION

RootCause = Literal[
    "camera_occlusion",
    "repeated_driver_fatigue",
    "driver_fatigue",
    "unexplained",
]
RootCauseStepName = Literal["graph", "regulations", "history", "decide"]
_SAMPLE_AS_OF = datetime(2026, 10, 9, 10, 30, tzinfo=UTC)
_WINDOW_DAYS = 7
_OCCLUSION = "camera_occlusion"
_RECOMMENDATIONS = {
    "camera_occlusion": "先核对镜头遮挡，再决定是否按疲劳处置。",
    "repeated_driver_fatigue": "对照近期同类报警复核。",
    "driver_fatigue": "按疲劳驾驶规则处置。",
    "unexplained": "补充法规或图谱证据后再判断。",
}


class RootCauseStep(BaseModel):
    name: RootCauseStepName
    tool: str | None = None
    on_failure: Literal["degrade"] | None = None


class AlarmRootCause(BaseModel):
    skill_id: Literal["alarm-root-cause-analysis"]
    alarm_id: str
    status: Literal["completed", "degraded"]
    cause: RootCause | None = None
    related_count: int | None = None
    recommendation: str | None = None
    evidence: list[Evidence]


class AlarmRootCauseSkill(BaseModel):
    id: Literal["alarm-root-cause-analysis"]
    input_fields: list[str]
    steps: list[RootCauseStep]
    tools: list[str]
    output_fields: list[str]


ALARM_ROOT_CAUSE = AlarmRootCauseSkill(
    id="alarm-root-cause-analysis",
    input_fields=["alarm_id"],
    steps=[
        RootCauseStep(name="graph", tool="query_vehicle_graph", on_failure="degrade"),
        RootCauseStep(name="regulations", tool="search_regulations", on_failure="degrade"),
        RootCauseStep(name="history", tool="list_recent_alarms", on_failure="degrade"),
        RootCauseStep(name="decide"),
    ],
    tools=["query_vehicle_graph", "search_regulations", "list_recent_alarms"],
    output_fields=list(AlarmRootCause.model_fields),
)


def check_alarm_root_cause_skill(registry: ToolRegistry) -> None:
    skill = ALARM_ROOT_CAUSE
    declared = [step.tool for step in skill.steps if step.tool]
    if declared != skill.tools:
        raise RuntimeError("alarm-root-cause-analysis steps and tool list diverged")
    for name in skill.tools:
        try:
            tool = registry.get(name)
        except KeyError as exc:
            raise RuntimeError(f"alarm-root-cause-analysis tool is not registered: {name}") from exc
        if not tool.readonly:
            raise RuntimeError(f"alarm-root-cause-analysis tool must be read-only: {name}")
    if ALARM_VERIFICATION.tools != [
        "get_vehicle_context",
        "list_recent_alarms",
        "search_regulations",
        "query_vehicle_graph",
        "analyze_image",
        "analyze_video",
    ]:
        raise RuntimeError("alarm-verification tools changed")


def trace_alarm_cause(
    harness: Harness,
    repository: AlarmRepository,
    alarm_id: str,
) -> AlarmRootCause:
    """Read graph, regulation, and peer alarms, then assign one cause in code."""

    alarm = repository.get_alarm(alarm_id)
    if alarm is None:
        raise KeyError(f"unknown alarm: {alarm_id}")

    task_id = f"root-cause-{alarm_id}"
    try:
        graph = harness.call_tool(
            "query_vehicle_graph",
            {"vehicle_id": alarm.vehicle_id, "device_id": alarm.device_id},
            task_id=task_id,
            node="graph",
        )
    except ToolExecutionError:
        return _degraded(alarm_id)
    if not isinstance(graph, QueryVehicleGraphOutput):
        return _degraded(alarm_id)

    evidence = [_graph_evidence(alarm.device_id, graph.facts)]
    try:
        regulations = harness.call_tool(
            "search_regulations",
            {"alarm_type": alarm.alarm_type},
            task_id=task_id,
            node="regulations",
        )
    except ToolExecutionError:
        return _degraded(alarm_id, evidence)
    if not isinstance(regulations, SearchRegulationsOutput):
        return _degraded(alarm_id, evidence)
    if regulations.snippets:
        evidence.append(_document_evidence(regulations.snippets[0]))

    try:
        history = harness.call_tool(
            "list_recent_alarms",
            {
                "vehicle_id": alarm.vehicle_id,
                "as_of": _SAMPLE_AS_OF,
                "within_days": _WINDOW_DAYS,
                "alarm_type": alarm.alarm_type,
            },
            task_id=task_id,
            node="history",
        )
    except ToolExecutionError:
        return _degraded(alarm_id, evidence)
    if not isinstance(history, AlarmHistoryOutput):
        return _degraded(alarm_id, evidence)

    related_count = len(
        [
            item
            for item in history.alarms
            if item.id != alarm.id and item.alarm_type == alarm.alarm_type
        ]
    )
    evidence.append(_history_evidence(alarm_id, related_count))
    cause = _cause(graph.facts, related_count, regulations.snippets)
    return AlarmRootCause(
        skill_id=ALARM_ROOT_CAUSE.id,
        alarm_id=alarm_id,
        status="completed",
        cause=cause,
        related_count=related_count,
        recommendation=_RECOMMENDATIONS[cause],
        evidence=evidence,
    )


def _cause(
    facts: list[GraphFact],
    related_count: int,
    snippets: list[RegulationSnippet],
) -> RootCause:
    if any(item.relation == "has_fault" and item.target_id == _OCCLUSION for item in facts):
        return "camera_occlusion"
    if related_count >= 1:
        return "repeated_driver_fatigue"
    if snippets:
        return "driver_fatigue"
    return "unexplained"


def _degraded(alarm_id: str, evidence: list[Evidence] | None = None) -> AlarmRootCause:
    return AlarmRootCause(
        skill_id=ALARM_ROOT_CAUSE.id,
        alarm_id=alarm_id,
        status="degraded",
        evidence=evidence or [],
    )


def _graph_evidence(device_id: str, facts: list[GraphFact]) -> Evidence:
    occluded = any(item.relation == "has_fault" and item.target_id == _OCCLUSION for item in facts)
    if occluded:
        description = f"设备 {device_id} 有摄像头遮挡故障"
    else:
        description = f"设备 {device_id} 无摄像头遮挡故障"
    return Evidence(type="graph", source="vehicle_graph", description=description)


def _document_evidence(snippet: RegulationSnippet) -> Evidence:
    return Evidence(
        type="document",
        source=snippet.document_id,
        description=f"{snippet.title} {snippet.section}",
    )


def _history_evidence(alarm_id: str, related_count: int) -> Evidence:
    return Evidence(
        type="history",
        source="alarm_history",
        description=f"报警 {alarm_id} 在 7 日内另有 {related_count} 次同类报警",
    )
