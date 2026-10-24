"""Declarative skills. The judgement graph remains the executor."""

from vehicle_agent.skills.alarm_root_cause import (
    ALARM_ROOT_CAUSE,
    check_alarm_root_cause_skill,
    trace_alarm_cause,
)
from vehicle_agent.skills.alarm_verification import (
    ALARM_VERIFICATION,
    check_alarm_verification_skill,
)
from vehicle_agent.skills.device_diagnosis import (
    DEVICE_DIAGNOSIS,
    check_device_diagnosis_skill,
    diagnose_device,
)
from vehicle_agent.skills.vehicle_risk_profile import (
    VEHICLE_RISK_PROFILE,
    check_vehicle_risk_profile_skill,
    profile_vehicle,
)

__all__ = [
    "ALARM_ROOT_CAUSE",
    "ALARM_VERIFICATION",
    "DEVICE_DIAGNOSIS",
    "VEHICLE_RISK_PROFILE",
    "check_alarm_root_cause_skill",
    "check_alarm_verification_skill",
    "check_device_diagnosis_skill",
    "check_vehicle_risk_profile_skill",
    "diagnose_device",
    "profile_vehicle",
    "trace_alarm_cause",
]
