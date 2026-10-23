from typing import Literal

from pydantic import BaseModel, Field

from vehicle_agent.domain.models import Alarm, JudgementResult
from vehicle_agent.harness.tools import ToolRegistry

SkillNode = Literal[
    "validate",
    "context",
    "history",
    "retrieve",
    "graph",
    "vision",
    "score",
    "review",
    "report",
]
FailureEffect = Literal["fail", "degrade"]


class SkillStep(BaseModel):
    node: SkillNode
    tool: str | None = None
    tools: list[str] = Field(default_factory=list)
    on_failure: FailureEffect | None = None


class SkillFailure(BaseModel):
    nodes: list[SkillNode]
    task_status: Literal["failed", "awaiting_review"]
    marks_task_failed: bool


class EvalCase(BaseModel):
    id: Literal[
        "no_media_complete",
        "image_high_risk",
        "observation_conflict",
        "vision_degraded",
    ]
    description: str


class AlarmVerificationSkill(BaseModel):
    id: Literal["alarm-verification"]
    trigger_alarm_type: Literal["driver_fatigue"]
    input_fields: list[str]
    steps: list[SkillStep]
    tools: list[str]
    output_fields: list[str]
    failures: list[SkillFailure]
    cases: list[EvalCase]

    def matches(self, alarm_type: str) -> bool:
        return alarm_type == self.trigger_alarm_type

    def tools_for(self, alarm: dict) -> list[str]:
        selected: list[str] = []
        for step in self.steps:
            if step.tool:
                selected.append(step.tool)
            elif step.node == "vision":
                if alarm.get("video_id"):
                    selected.append("analyze_video")
                elif alarm.get("image_id"):
                    selected.append("analyze_image")
        return selected


def _steps() -> list[SkillStep]:
    return [
        SkillStep(node="validate", on_failure="fail"),
        SkillStep(node="context", tool="get_vehicle_context", on_failure="fail"),
        SkillStep(node="history", tool="list_recent_alarms", on_failure="fail"),
        SkillStep(node="retrieve", tool="search_regulations", on_failure="degrade"),
        SkillStep(node="graph", tool="query_vehicle_graph", on_failure="degrade"),
        SkillStep(
            node="vision",
            tools=["analyze_video", "analyze_image"],
            on_failure="degrade",
        ),
        SkillStep(node="score"),
        SkillStep(node="review"),
        SkillStep(node="report"),
    ]


ALARM_VERIFICATION = AlarmVerificationSkill(
    id="alarm-verification",
    trigger_alarm_type="driver_fatigue",
    input_fields=list(Alarm.model_fields),
    steps=_steps(),
    tools=[
        "get_vehicle_context",
        "list_recent_alarms",
        "search_regulations",
        "query_vehicle_graph",
        "analyze_image",
        "analyze_video",
    ],
    output_fields=list(JudgementResult.model_fields),
    failures=[
        SkillFailure(
            nodes=["validate", "context", "history"],
            task_status="failed",
            marks_task_failed=True,
        ),
        SkillFailure(
            nodes=["retrieve", "graph", "vision"],
            task_status="awaiting_review",
            marks_task_failed=False,
        ),
    ],
    cases=[
        EvalCase(id="no_media_complete", description="无图中风险完成，只调用四个查询工具"),
        EvalCase(id="image_high_risk", description="闭眼高风险完成，查询后调用图片检测"),
        EvalCase(id="observation_conflict", description="观察不支持检测类别时进入复核且不建单"),
        EvalCase(id="vision_degraded", description="检测失败时降级进入复核，整单不失败"),
    ],
)

_WORKORDER_TOOLS = ("create_workorder", "get_workorder", "update_workorder")


def check_alarm_verification_skill(registry: ToolRegistry, node_order: list[str]) -> None:
    skill = ALARM_VERIFICATION
    if [step.node for step in skill.steps] != list(node_order):
        raise RuntimeError("alarm-verification steps do not match the judgement graph")
    declared: list[str] = []
    for step in skill.steps:
        if step.tool:
            declared.append(step.tool)
        declared.extend(step.tools)
    if set(declared) != set(skill.tools) or len(declared) != len(skill.tools):
        raise RuntimeError("alarm-verification steps and tool list diverged")
    for name in skill.tools:
        if name in _WORKORDER_TOOLS:
            raise RuntimeError(f"alarm-verification must not include {name}")
        try:
            tool = registry.get(name)
        except KeyError as exc:
            raise RuntimeError(f"alarm-verification tool is not registered: {name}") from exc
        if not tool.readonly:
            raise RuntimeError(f"alarm-verification tool must be read-only: {name}")
