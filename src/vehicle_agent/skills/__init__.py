"""Declarative skills. The judgement graph remains the executor."""

from vehicle_agent.skills.alarm_verification import (
    ALARM_VERIFICATION,
    check_alarm_verification_skill,
)
from vehicle_agent.skills.device_diagnosis import (
    DEVICE_DIAGNOSIS,
    check_device_diagnosis_skill,
    diagnose_device,
)

__all__ = [
    "ALARM_VERIFICATION",
    "DEVICE_DIAGNOSIS",
    "check_alarm_verification_skill",
    "check_device_diagnosis_skill",
    "diagnose_device",
]
