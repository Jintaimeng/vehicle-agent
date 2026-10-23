from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from vehicle_agent.domain.models import Device, DeviceMaintenance, Evidence
from vehicle_agent.harness.errors import ToolExecutionError
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import AlarmHistoryOutput, QueryVehicleGraphOutput, ToolRegistry
from vehicle_agent.knowledge.graph import GraphFact
from vehicle_agent.repositories.base import AlarmRepository
from vehicle_agent.skills.alarm_verification import ALARM_VERIFICATION

DiagnosisCause = Literal[
    "offline",
    "camera_occlusion",
    "frequent_alarms",
    "missing_video",
    "no_device_fault",
]
DiagnosisStepName = Literal["status", "graph", "alarms", "maintenance", "decide"]
MaintenanceStatus = Literal["hit", "miss", "degraded"]
AlarmCountStatus = Literal["hit", "miss", "degraded"]
VideoStatus = Literal["hit", "miss", "degraded"]
_OCCLUSION = "camera_occlusion"
_FREQUENT_COUNT = 3
_MISSING_VIDEO_COUNT = 2
_SAMPLE_AS_OF = datetime(2026, 10, 9, 10, 30, tzinfo=UTC)


class DiagnosisStep(BaseModel):
    name: DiagnosisStepName
    tool: str | None = None
    on_failure: Literal["degrade"] | None = None


class DiagnosisFailure(BaseModel):
    step: Literal["status", "graph"]
    status: Literal["degraded"]
    guesses_cause: Literal[False] = False


class DiagnosisCase(BaseModel):
    id: Literal[
        "offline_occlusion",
        "online_occlusion",
        "online_clear",
        "status_degraded",
    ]
    description: str


class DeviceDiagnosis(BaseModel):
    skill_id: Literal["device-diagnosis"]
    device_id: str
    status: Literal["completed", "degraded"]
    cause: DiagnosisCause | None = None
    alarm_status: AlarmCountStatus | None = None
    video_status: VideoStatus | None = None
    maintenance_status: MaintenanceStatus | None = None
    evidence: list[Evidence]


class DeviceDiagnosisSkill(BaseModel):
    id: Literal["device-diagnosis"]
    input_fields: list[str]
    steps: list[DiagnosisStep]
    tools: list[str]
    output_fields: list[str]
    failures: list[DiagnosisFailure]
    cases: list[DiagnosisCase]


DEVICE_DIAGNOSIS = DeviceDiagnosisSkill(
    id="device-diagnosis",
    input_fields=["device_id"],
    steps=[
        DiagnosisStep(name="status", tool="get_device_status", on_failure="degrade"),
        DiagnosisStep(name="graph", tool="query_vehicle_graph", on_failure="degrade"),
        DiagnosisStep(name="alarms", tool="list_recent_alarms"),
        DiagnosisStep(name="maintenance", tool="get_device_maintenance"),
        DiagnosisStep(name="decide"),
    ],
    tools=[
        "get_device_status",
        "query_vehicle_graph",
        "list_recent_alarms",
        "get_device_maintenance",
    ],
    output_fields=list(DeviceDiagnosis.model_fields),
    failures=[
        DiagnosisFailure(step="status", status="degraded"),
        DiagnosisFailure(step="graph", status="degraded"),
    ],
    cases=[
        DiagnosisCase(id="offline_occlusion", description="离线且有遮挡时原因是离线"),
        DiagnosisCase(id="online_occlusion", description="在线且有遮挡时原因是遮挡"),
        DiagnosisCase(id="online_clear", description="在线且没有遮挡故障"),
        DiagnosisCase(id="status_degraded", description="设备读取失败时降级且不猜测原因"),
    ],
)


def check_device_diagnosis_skill(registry: ToolRegistry) -> None:
    skill = DEVICE_DIAGNOSIS
    declared = [step.tool for step in skill.steps if step.tool]
    if declared != skill.tools:
        raise RuntimeError("device-diagnosis steps and tool list diverged")
    for name in skill.tools:
        try:
            tool = registry.get(name)
        except KeyError as exc:
            raise RuntimeError(f"device-diagnosis tool is not registered: {name}") from exc
        if not tool.readonly:
            raise RuntimeError(f"device-diagnosis tool must be read-only: {name}")
    for name in ("get_device_status", "get_device_maintenance"):
        if name in ALARM_VERIFICATION.tools:
            raise RuntimeError(f"alarm-verification must not call {name}")


def diagnose_device(
    harness: Harness,
    repository: AlarmRepository,
    device_id: str,
) -> DeviceDiagnosis:
    """Read status and graph facts, then assign one cause in code."""

    task_id = f"diagnosis-{device_id}"
    try:
        device = harness.call_tool(
            "get_device_status",
            {"device_id": device_id},
            task_id=task_id,
            node="status",
        )
    except ToolExecutionError as exc:
        if "unknown device" in str(exc):
            raise
        return _degraded(device_id)
    if not isinstance(device, Device):
        return _degraded(device_id)

    vehicle_id = repository.find_vehicle_id(device_id)
    if vehicle_id is None:
        return _degraded(device_id, evidence=[_status_evidence(device)])

    try:
        graph = harness.call_tool(
            "query_vehicle_graph",
            {"vehicle_id": vehicle_id, "device_id": device_id},
            task_id=task_id,
            node="graph",
        )
    except ToolExecutionError:
        return _degraded(device_id, evidence=[_status_evidence(device)])
    if not isinstance(graph, QueryVehicleGraphOutput):
        return _degraded(device_id, evidence=[_status_evidence(device)])

    occlusion = [
        fact
        for fact in graph.facts
        if fact.relation == "has_fault" and fact.target_id == _OCCLUSION
    ]
    evidence = [_status_evidence(device), *_graph_evidence(occlusion)]
    prior: DiagnosisCause | None
    if device.online_status == "offline":
        prior = "offline"
    elif occlusion:
        prior = "camera_occlusion"
    else:
        prior = None
    try:
        history = harness.call_tool(
            "list_recent_alarms",
            {"vehicle_id": vehicle_id, "as_of": _SAMPLE_AS_OF, "within_days": 1},
            task_id=task_id,
            node="alarms",
        )
    except ToolExecutionError:
        if prior is None:
            return _degraded(device_id, evidence=evidence)
        return _with_maintenance(
            harness,
            device_id,
            task_id,
            cause=prior,
            evidence=evidence,
            alarm_status="degraded",
            video_status="degraded",
        )
    if not isinstance(history, AlarmHistoryOutput):
        if prior is None:
            return _degraded(device_id, evidence=evidence)
        return _with_maintenance(
            harness,
            device_id,
            task_id,
            cause=prior,
            evidence=evidence,
            alarm_status="degraded",
            video_status="degraded",
        )
    matched = [item for item in history.alarms if item.device_id == device_id]
    count = len(matched)
    missing = len([item for item in matched if not item.video_id])
    evidence.append(_alarm_evidence(device_id, count))
    evidence.append(_missing_video_evidence(device_id, missing))
    alarm_status: AlarmCountStatus = "hit" if count >= _FREQUENT_COUNT else "miss"
    video_status: VideoStatus = "hit" if _missing_video(count, missing) else "miss"
    if prior is not None:
        cause = prior
    elif count >= _FREQUENT_COUNT:
        cause = "frequent_alarms"
    elif _missing_video(count, missing):
        cause = "missing_video"
    else:
        cause = "no_device_fault"
    return _with_maintenance(
        harness,
        device_id,
        task_id,
        cause=cause,
        evidence=evidence,
        alarm_status=alarm_status,
        video_status=video_status,
    )


def _with_maintenance(
    harness: Harness,
    device_id: str,
    task_id: str,
    *,
    cause: DiagnosisCause,
    evidence: list[Evidence],
    alarm_status: AlarmCountStatus,
    video_status: VideoStatus,
) -> DeviceDiagnosis:
    try:
        maintenance = harness.call_tool(
            "get_device_maintenance",
            {"device_id": device_id},
            task_id=task_id,
            node="maintenance",
        )
    except ToolExecutionError:
        return DeviceDiagnosis(
            skill_id=DEVICE_DIAGNOSIS.id,
            device_id=device_id,
            status="completed",
            cause=cause,
            alarm_status=alarm_status,
            video_status=video_status,
            maintenance_status="degraded",
            evidence=evidence,
        )
    if not isinstance(maintenance, DeviceMaintenance):
        return DeviceDiagnosis(
            skill_id=DEVICE_DIAGNOSIS.id,
            device_id=device_id,
            status="completed",
            cause=cause,
            alarm_status=alarm_status,
            video_status=video_status,
            maintenance_status="degraded",
            evidence=evidence,
        )
    return DeviceDiagnosis(
        skill_id=DEVICE_DIAGNOSIS.id,
        device_id=device_id,
        status="completed",
        cause=cause,
        alarm_status=alarm_status,
        video_status=video_status,
        maintenance_status="hit" if maintenance.repair is not None else "miss",
        evidence=[*evidence, *_maintenance_evidence(maintenance)],
    )


def _degraded(device_id: str, evidence: list[Evidence] | None = None) -> DeviceDiagnosis:
    return DeviceDiagnosis(
        skill_id=DEVICE_DIAGNOSIS.id,
        device_id=device_id,
        status="degraded",
        cause=None,
        evidence=evidence or [],
    )


def _missing_video(count: int, missing: int) -> bool:
    return count >= _MISSING_VIDEO_COUNT and missing == count


def _alarm_evidence(device_id: str, count: int) -> Evidence:
    return Evidence(
        type="history",
        source="alarm_history",
        description=f"设备 {device_id} 在 1 日内有 {count} 次报警",
    )


def _missing_video_evidence(device_id: str, missing: int) -> Evidence:
    return Evidence(
        type="history",
        source="alarm_history",
        description=f"设备 {device_id} 在 1 日内有 {missing} 次报警缺少视频",
    )


def _status_evidence(device: Device) -> Evidence:
    state = "离线" if device.online_status == "offline" else "在线"
    heartbeat = device.last_heartbeat_at.isoformat().replace("+00:00", "Z")
    return Evidence(
        type="context",
        source="device",
        description=f"设备 {device.id} {state}，最后心跳 {heartbeat}",
    )


def _maintenance_evidence(item: DeviceMaintenance) -> list[Evidence]:
    rows = [
        Evidence(
            type="context",
            source="maintenance",
            description=f"设备 {item.device_id} 固件 {item.firmware_version}",
        )
    ]
    if item.repair is None:
        rows.append(
            Evidence(
                type="context",
                source="maintenance",
                description=f"设备 {item.device_id} 无维修记录",
            )
        )
        return rows
    state = "未关闭" if item.repair.status == "open" else "已关闭"
    rows.append(
        Evidence(
            type="context",
            source="maintenance",
            description=f"最近维修 {item.repair.id} {state}，故障 {item.repair.fault}",
        )
    )
    return rows


def _graph_evidence(facts: list[GraphFact]) -> list[Evidence]:
    return [
        Evidence(
            type="graph",
            source=f"{item.relation}:{item.source_id}->{item.target_id}",
            description=item.description,
        )
        for item in facts
    ]
