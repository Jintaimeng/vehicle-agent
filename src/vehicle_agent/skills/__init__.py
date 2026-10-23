"""Declarative skills. The judgement graph remains the executor."""

from vehicle_agent.skills.alarm_verification import (
    ALARM_VERIFICATION,
    check_alarm_verification_skill,
)

__all__ = ["ALARM_VERIFICATION", "check_alarm_verification_skill"]
