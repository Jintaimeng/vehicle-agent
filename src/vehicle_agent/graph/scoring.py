from vehicle_agent.domain.models import Alarm, AlarmValidity, Driver, Evidence, RiskLevel, Vehicle


def score_fatigue(
    alarm: Alarm,
    vehicle: Vehicle | None,
    driver: Driver | None,
    history: list[Alarm],
    *,
    review_confidence_threshold: float,
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

    risk_score = min(1.0, risk_score)
    confidence = min(0.95, confidence)
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
        or driver is None,
        "evidence": [item.model_dump() for item in evidence],
    }
