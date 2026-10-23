from typing import Literal

from pydantic import BaseModel

from vehicle_agent.domain.models import Device, DeviceMaintenance, Evidence
from vehicle_agent.harness.errors import ToolExecutionError
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import QueryVehicleGraphOutput, ToolRegistry
from vehicle_agent.knowledge.graph import GraphFact
from vehicle_agent.repositories.base import AlarmRepository
from vehicle_agent.skills.alarm_verification import ALARM_VERIFICATION

DiagnosisCause = Literal["offline", "camera_occlusion", "no_device_fault"]
DiagnosisStepName = Literal["status", "graph", "maintenance", "decide"]
MaintenanceStatus = Literal["hit", "miss", "degraded"]
_OCCLUSION = "camera_occlusion"


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
        DiagnosisStep(name="maintenance", tool="get_device_maintenance"),
        DiagnosisStep(name="decide"),
    ],
    tools=["get_device_status", "query_vehicle_graph", "get_device_maintenance"],
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
    if device.online_status == "offline":
        cause: DiagnosisCause = "offline"
    elif occlusion:
        cause = "camera_occlusion"
    else:
        cause = "no_device_fault"
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
            maintenance_status="degraded",
            evidence=evidence,
        )
    if not isinstance(maintenance, DeviceMaintenance):
        return DeviceDiagnosis(
            skill_id=DEVICE_DIAGNOSIS.id,
            device_id=device_id,
            status="completed",
            cause=cause,
            maintenance_status="degraded",
            evidence=evidence,
        )
    return DeviceDiagnosis(
        skill_id=DEVICE_DIAGNOSIS.id,
        device_id=device_id,
        status="completed",
        cause=cause,
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
