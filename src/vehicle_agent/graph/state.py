from typing import Literal, TypedDict

from vehicle_agent.domain.models import AlarmValidity, RiskLevel


class JudgementState(TypedDict, total=False):
    task_id: str
    alarm: dict
    vehicle_context: dict | None
    driver_context: dict | None
    history: list[dict]
    regulations: list[dict]
    regulation_status: Literal["hit", "miss", "degraded"]
    graph_facts: list[dict]
    graph_status: Literal["hit", "miss", "degraded"]
    detections: list[dict]
    vision_status: Literal["skipped", "hit", "miss", "degraded"]
    vlm_status: Literal["skipped", "hit", "degraded"]
    vision_agreement: Literal["match", "conflict"]
    visible_facts: list[str]
    video_start_s: float
    video_end_s: float
    keyframe_timestamp_s: float
    risk_score: float
    risk_level: RiskLevel
    alarm_validity: AlarmValidity
    confidence: float
    requires_human_review: bool
    evidence: list[dict]
    result: dict | None
    human_review: dict | None
    errors: list[dict]
    status: str
    trace: list[dict]


Route = Literal[
    "context", "history", "retrieve", "graph", "vision", "score", "review", "report", "end"
]
