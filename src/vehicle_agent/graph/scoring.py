from typing import Literal

from vehicle_agent.domain.models import (
    Alarm,
    AlarmValidity,
    Detection,
    Driver,
    Evidence,
    RiskLevel,
    Vehicle,
)
from vehicle_agent.knowledge.graph import GraphFact
from vehicle_agent.knowledge.index import RegulationSnippet

RegulationStatus = Literal["hit", "miss", "degraded"]
GraphStatus = Literal["hit", "miss", "degraded"]
VisionStatus = Literal["skipped", "hit", "miss", "degraded"]
VlmStatus = Literal["skipped", "hit", "degraded"]
_HIT_BONUS = 0.05
_MISS_PENALTY = 0.2
_CONFLICT_FAULT = "camera_occlusion"
_FATIGUE_LABELS = frozenset({"closed_eye", "yawn"})
_VISION_CONFIDENCE = 0.6


def score_fatigue(
    alarm: Alarm,
    vehicle: Vehicle | None,
    driver: Driver | None,
    history: list[Alarm],
    *,
    review_confidence_threshold: float,
    regulations: list[RegulationSnippet] | None = None,
    regulation_status: RegulationStatus = "miss",
    graph_facts: list[GraphFact] | None = None,
    graph_status: GraphStatus = "miss",
    detections: list[Detection] | None = None,
    vision_status: VisionStatus = "skipped",
    vlm_status: VlmStatus = "skipped",
    visible_facts: list[str] | None = None,
) -> dict:
    """Deterministic risk score. The model is not allowed to override these fields."""

    prior_alarms = [item for item in history if item.id != alarm.id]
    risk_score = 0.25
    evidence: list[Evidence] = []

    if alarm.speed_kmh >= 80:
        risk_score += 0.35
        evidence.append(
            Evidence(
                type="telemetry",
                source="alarm",
                description=f"车速 {alarm.speed_kmh:.0f} km/h，高于高速行驶阈值",
            )
        )
    elif alarm.speed_kmh >= 60:
        risk_score += 0.2
        evidence.append(
            Evidence(
                type="telemetry",
                source="alarm",
                description=f"车速 {alarm.speed_kmh:.0f} km/h",
            )
        )

    if alarm.raw_confidence >= 0.8:
        risk_score += 0.2
    elif alarm.raw_confidence >= 0.6:
        risk_score += 0.1

    if len(prior_alarms) >= 2:
        risk_score += 0.25
        evidence.append(
            Evidence(
                type="history",
                source="alarm_history",
                description=f"7日内已有 {len(prior_alarms)} 次疲劳驾驶报警",
            )
        )
    elif len(prior_alarms) == 1:
        risk_score += 0.1
        evidence.append(
            Evidence(
                type="history",
                source="alarm_history",
                description="7日内已有 1 次疲劳驾驶报警",
            )
        )

    confidence = 0.35 + alarm.raw_confidence * 0.3 + 0.1
    if vehicle is not None and driver is not None:
        confidence += 0.25
        evidence.append(
            Evidence(
                type="context",
                source="vehicle",
                description=f"车辆 {vehicle.plate_number}，驾驶员 {driver.name}",
            )
        )
    else:
        evidence.append(
            Evidence(
                type="context",
                source="vehicle",
                description="缺少车辆或驾驶员资料",
            )
        )

    matched = regulations or []
    if regulation_status == "hit" and matched:
        confidence += _HIT_BONUS
        for item in matched:
            evidence.append(
                Evidence(
                    type="document",
                    source=item.document_id,
                    description=f"{item.title} {item.section}：{item.excerpt}",
                )
            )
    else:
        confidence -= _MISS_PENALTY
        missing = (
            "法规检索不可用，缺少法规依据"
            if regulation_status == "degraded"
            else "缺少与该报警类型匹配的法规依据"
        )
        evidence.append(Evidence(type="document", source="regulations", description=missing))

    facts = graph_facts or []
    conflict = regulation_status == "hit" and any(
        item.relation == "has_fault" and item.target_id == _CONFLICT_FAULT for item in facts
    )
    if graph_status == "hit" and facts and not conflict:
        confidence += _HIT_BONUS
        evidence.extend(_graph_evidence(facts))
    elif conflict:
        confidence -= _MISS_PENALTY
        evidence.extend(_graph_evidence(facts))
        evidence.append(
            Evidence(
                type="graph",
                source="vehicle_graph",
                description="法规摘录要求按疲劳驾驶处置，但图谱显示设备存在摄像头遮挡故障，证据冲突",
            )
        )
    else:
        confidence -= _MISS_PENALTY
        missing_graph = (
            "图谱查询不可用，缺少车辆关系依据"
            if graph_status == "degraded"
            else "缺少与该车辆匹配的图谱关系"
        )
        evidence.append(Evidence(type="graph", source="vehicle_graph", description=missing_graph))

    found = detections or []
    facts = [item for item in (visible_facts or []) if item]
    vision_conflict = False
    if vision_status == "degraded":
        confidence -= _MISS_PENALTY
        evidence.append(
            Evidence(type="vision", source="vision", description="图片检测不可用，缺少画面依据")
        )
    elif vision_status == "miss":
        confidence -= _MISS_PENALTY
        evidence.append(
            Evidence(type="vision", source="vision", description="图片未检出目标，缺少画面依据")
        )
    elif vision_status == "hit":
        evidence.extend(_vision_evidence(found, facts))
        fatigue_hit = any(
            item.label in _FATIGUE_LABELS and item.confidence >= _VISION_CONFIDENCE
            for item in found
        )
        if fatigue_hit:
            confidence += _HIT_BONUS
        vision_conflict = regulation_status == "hit" and any(
            item.label == _CONFLICT_FAULT and item.confidence >= _VISION_CONFIDENCE
            for item in found
        )
        if vision_conflict:
            confidence -= _MISS_PENALTY
            evidence.append(
                Evidence(
                    type="vision",
                    source="vision",
                    description="法规摘录要求按疲劳驾驶处置，但画面检测到摄像头遮挡，证据冲突",
                )
            )

    risk_score = min(1.0, risk_score)
    confidence = min(0.95, max(0.0, confidence))
    if risk_score >= 0.75:
        risk_level: RiskLevel = "high"
    elif risk_score >= 0.45:
        risk_level = "medium"
    else:
        risk_level = "low"
    if vehicle is None or driver is None:
        alarm_validity: AlarmValidity = "unknown"
    elif confidence >= 0.7 and risk_score >= 0.55:
        alarm_validity = "likely_true"
    else:
        alarm_validity = "uncertain"

    return {
        "risk_score": risk_score,
        "risk_level": risk_level,
        "confidence": confidence,
        "alarm_validity": alarm_validity,
        "requires_human_review": confidence < review_confidence_threshold
        or vehicle is None
        or driver is None
        or regulation_status != "hit"
        or graph_status != "hit"
        or conflict
        or vision_status in {"miss", "degraded"}
        or vlm_status == "degraded"
        or vision_conflict,
        "evidence": [item.model_dump() for item in evidence],
    }


def _vision_evidence(detections: list[Detection], facts: list[str]) -> list[Evidence]:
    suffix = f"；可见事实：{'；'.join(facts)}" if facts else ""
    evidence: list[Evidence] = []
    for item in detections:
        box = ", ".join(f"{value:.2f}" for value in item.bbox)
        evidence.append(
            Evidence(
                type="vision",
                source=item.model_version,
                description=f"{item.label} 置信度 {item.confidence:.2f}，框 [{box}]{suffix}",
            )
        )
    return evidence


def _graph_evidence(facts: list[GraphFact]) -> list[Evidence]:
    return [
        Evidence(
            type="graph",
            source=f"{item.relation}:{item.source_id}->{item.target_id}",
            description=item.description,
        )
        for item in facts
    ]
