from datetime import datetime

import pytest

from vehicle_agent.config import Settings
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import build_vehicle_tools
from vehicle_agent.repositories.memory import MemoryAlarmRepository, build_seed_repository
from vehicle_agent.skills.alarm_verification import ALARM_VERIFICATION
from vehicle_agent.skills.vehicle_risk_profile import (
    VEHICLE_RISK_PROFILE,
    profile_vehicle,
)


def _harness() -> tuple[Harness, MemoryAlarmRepository]:
    repository = build_seed_repository()
    harness = Harness(
        Settings(max_retries=0, checkpoint_path="data/test-checkpoints.sqlite"),
        FakeModelAdapter("{}"),
        build_vehicle_tools(repository),
    )
    return harness, repository


def test_profiles_cover_speed_fatigue_fault_and_clear() -> None:
    harness, repository = _harness()

    high_speed = profile_vehicle(harness, repository, "vehicle_001")
    fatigue = profile_vehicle(harness, repository, "vehicle_005")
    fault = profile_vehicle(harness, repository, "vehicle_003")
    clear = profile_vehicle(harness, repository, "vehicle_002")

    assert high_speed.status == "completed"
    assert high_speed.skill_id == "vehicle-risk-profile"
    assert high_speed.source == "high_speed_fatigue"
    assert high_speed.trend == "falling"
    assert high_speed.alarm_count == 2
    assert high_speed.recent_count == 0
    assert high_speed.recommendation == "复核高速疲劳报警。"
    assert fatigue.source == "driver_fatigue"
    assert fatigue.trend == "rising"
    assert fatigue.alarm_count == 4
    assert fatigue.recent_count == 3
    assert fatigue.recommendation == "复核近期疲劳报警。"
    assert fault.source == "device_fault"
    assert fault.trend == "flat"
    assert fault.alarm_count == 0
    assert fault.recommendation == "先处理未关闭的维修。"
    assert any("未关闭维修" in item.description for item in fault.evidence)
    assert clear.source == "no_recent_risk"
    assert clear.trend == "flat"
    assert clear.alarm_count == 0
    assert clear.recommendation == "保持例行监控。"
    assert [event.name for event in harness.tracer.events] == [
        "list_recent_alarms",
        "get_device_maintenance",
        "list_recent_alarms",
        "get_device_maintenance",
        "list_recent_alarms",
        "get_device_maintenance",
        "list_recent_alarms",
        "get_device_maintenance",
    ]


def test_open_repair_beats_high_speed_and_one_alarm_is_not_fatigue() -> None:
    harness, repository = _harness()

    fault = profile_vehicle(harness, repository, "vehicle_012")
    single = profile_vehicle(harness, repository, "vehicle_011")

    assert fault.source == "device_fault"
    assert fault.trend == "rising"
    assert fault.alarm_count == 1
    assert any("未关闭维修" in item.description for item in fault.evidence)
    assert single.source == "no_recent_risk"
    assert single.trend == "rising"
    assert single.alarm_count == 1
    assert single.recent_count == 1
    assert any("无未关闭维修" in item.description for item in single.evidence)


class _BrokenHistory:
    def __init__(self, inner: MemoryAlarmRepository) -> None:
        self._inner = inner

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def list_recent_alarms(
        self,
        vehicle_id: str,
        *,
        since: datetime,
        alarm_type: str | None = None,
    ) -> list:
        raise ConnectionError("alarm history unavailable")


def test_history_and_maintenance_failures_do_not_guess() -> None:
    repository = build_seed_repository()
    broken = _BrokenHistory(repository)
    history_harness = Harness(
        Settings(max_retries=0, checkpoint_path="data/test-checkpoints.sqlite"),
        FakeModelAdapter("{}"),
        build_vehicle_tools(broken),
    )
    maintenance_repository = build_seed_repository(failing_maintenance_ids={"device_002"})
    maintenance_harness = Harness(
        Settings(max_retries=0, checkpoint_path="data/test-checkpoints.sqlite"),
        FakeModelAdapter("{}"),
        build_vehicle_tools(maintenance_repository),
    )

    history_failed = profile_vehicle(history_harness, broken, "vehicle_002")
    maintenance_failed = profile_vehicle(
        maintenance_harness,
        maintenance_repository,
        "vehicle_002",
    )

    assert history_failed.status == "degraded"
    assert history_failed.source is None
    assert history_failed.trend is None
    assert history_failed.recommendation is None
    assert history_failed.evidence == []
    assert "get_device_maintenance" not in [event.name for event in history_harness.tracer.events]
    assert maintenance_failed.status == "degraded"
    assert maintenance_failed.source is None
    assert maintenance_failed.recommendation is None
    assert maintenance_failed.alarm_count == 0
    assert any("7 日内有 0 次疲劳报警" in item.description for item in maintenance_failed.evidence)
    with pytest.raises(KeyError, match="unknown vehicle"):
        profile_vehicle(history_harness, repository, "vehicle_missing")


def test_profile_tools_stay_out_of_alarm_verification() -> None:
    assert VEHICLE_RISK_PROFILE.tools == ["list_recent_alarms", "get_device_maintenance"]
    assert "get_device_maintenance" not in ALARM_VERIFICATION.tools
    assert len(ALARM_VERIFICATION.tools) == 6
