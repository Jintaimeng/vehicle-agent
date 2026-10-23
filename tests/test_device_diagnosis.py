from datetime import UTC, datetime

import pytest

from vehicle_agent.config import Settings
from vehicle_agent.harness.errors import ToolExecutionError
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import build_vehicle_tools
from vehicle_agent.knowledge.graph import seed_graph_index
from vehicle_agent.repositories.memory import MemoryAlarmRepository, build_seed_repository
from vehicle_agent.skills.alarm_verification import ALARM_VERIFICATION
from vehicle_agent.skills.device_diagnosis import DEVICE_DIAGNOSIS, diagnose_device

NOW = datetime(2026, 10, 9, 10, 30, tzinfo=UTC)


class _BrokenGraph:
    def query(self, vehicle_id: str, device_id: str) -> list:
        raise ConnectionError("graph unavailable")


def _harness(graph=None) -> tuple[Harness, MemoryAlarmRepository]:
    repository = build_seed_repository()
    harness = Harness(
        Settings(max_retries=0, checkpoint_path="data/test-checkpoints.sqlite"),
        FakeModelAdapter("{}"),
        build_vehicle_tools(repository, graph=graph),
    )
    return harness, repository


def test_offline_device_keeps_occlusion_as_evidence() -> None:
    harness, repository = _harness()

    result = diagnose_device(harness, repository, "device_003")
    descriptions = [item.description for item in result.evidence]

    assert result.status == "completed"
    assert result.cause == "offline"
    assert result.skill_id == "device-diagnosis"
    assert any("离线" in item and "2026-10-07T10:30:00Z" in item for item in descriptions)
    assert any("摄像头遮挡" in item for item in descriptions)
    assert any("1 日内有 0 次报警" in item for item in descriptions)
    assert any("固件 1.2.0" in item for item in descriptions)
    assert any("repair_003_open" in item and "未关闭" in item for item in descriptions)
    assert all("repair_003_closed" not in item for item in descriptions)
    assert result.alarm_status == "miss"
    assert result.maintenance_status == "hit"
    assert [event.name for event in harness.tracer.events] == [
        "get_device_status",
        "query_vehicle_graph",
        "list_recent_alarms",
        "get_device_maintenance",
    ]


def test_online_occluded_device_is_camera_occlusion() -> None:
    harness, repository = _harness()
    facts = seed_graph_index().query("vehicle_004", "device_004")

    result = diagnose_device(harness, repository, "device_004")

    assert ("has_fault", "camera_occlusion") in [
        (item.relation, item.target_id) for item in facts
    ]
    assert repository.get_device("device_004").last_heartbeat_at == NOW
    assert result.cause == "camera_occlusion"
    assert result.alarm_status == "hit"
    assert result.maintenance_status == "miss"
    assert any(
        item.type == "graph" and "摄像头遮挡" in item.description for item in result.evidence
    )
    assert any("1 日内有 3 次报警" in item.description for item in result.evidence)
    assert any("固件 1.3.0" in item.description for item in result.evidence)
    assert any("无维修记录" in item.description for item in result.evidence)


def test_online_clear_device_has_no_fault() -> None:
    harness, repository = _harness()

    result = diagnose_device(harness, repository, "device_001")

    assert result.cause == "no_device_fault"
    assert result.alarm_status == "miss"
    assert result.maintenance_status == "miss"
    assert all(item.type != "graph" for item in result.evidence)
    assert any("1 日内有 0 次报警" in item.description for item in result.evidence)
    assert any("固件 1.4.0" in item.description for item in result.evidence)
    assert any("无维修记录" in item.description for item in result.evidence)


def test_device_and_graph_failures_do_not_guess() -> None:
    harness, repository = _harness()
    broken, broken_repository = _harness(_BrokenGraph())

    failed = diagnose_device(harness, repository, "device_error")
    graph_failed = diagnose_device(broken, broken_repository, "device_001")

    assert failed.status == "degraded"
    assert failed.cause is None
    assert failed.maintenance_status is None
    assert "get_device_maintenance" not in [event.name for event in harness.tracer.events]
    assert "list_recent_alarms" not in [event.name for event in harness.tracer.events]
    assert graph_failed.status == "degraded"
    assert graph_failed.cause is None
    assert "get_device_maintenance" not in [event.name for event in broken.tracer.events]
    assert "list_recent_alarms" not in [event.name for event in broken.tracer.events]
    with pytest.raises(ToolExecutionError, match="unknown device"):
        diagnose_device(harness, repository, "device_missing")


def test_maintenance_failure_keeps_the_cause() -> None:
    repository = build_seed_repository(failing_maintenance_ids={"device_001"})
    harness = Harness(
        Settings(max_retries=0, checkpoint_path="data/test-checkpoints.sqlite"),
        FakeModelAdapter("{}"),
        build_vehicle_tools(repository),
    )

    result = diagnose_device(harness, repository, "device_001")
    descriptions = [item.description for item in result.evidence]

    assert result.status == "completed"
    assert result.cause == "no_device_fault"
    assert result.maintenance_status == "degraded"
    assert all("固件" not in item and "维修" not in item for item in descriptions)


def test_frequent_online_device_counts_only_the_last_day() -> None:
    harness, repository = _harness()

    result = diagnose_device(harness, repository, "device_005")

    assert result.status == "completed"
    assert result.cause == "frequent_alarms"
    assert result.alarm_status == "hit"
    assert any("1 日内有 3 次报警" in item.description for item in result.evidence)


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


def test_alarm_history_failure_keeps_known_causes() -> None:
    repository = build_seed_repository()
    broken = _BrokenHistory(repository)
    harness = Harness(
        Settings(max_retries=0, checkpoint_path="data/test-checkpoints.sqlite"),
        FakeModelAdapter("{}"),
        build_vehicle_tools(broken),
    )

    undecided = diagnose_device(harness, broken, "device_001")
    undecided_names = [event.name for event in harness.tracer.events]
    known = diagnose_device(harness, broken, "device_003")
    known_names = [event.name for event in harness.tracer.events][len(undecided_names) :]

    assert undecided.status == "degraded"
    assert undecided.cause is None
    assert "get_device_maintenance" not in undecided_names
    assert known.status == "completed"
    assert known.cause == "offline"
    assert known.alarm_status == "degraded"
    assert all("次报警" not in item.description for item in known.evidence)
    assert "get_device_maintenance" in known_names


def test_diagnosis_tools_stay_out_of_alarm_verification() -> None:
    assert DEVICE_DIAGNOSIS.tools == [
        "get_device_status",
        "query_vehicle_graph",
        "list_recent_alarms",
        "get_device_maintenance",
    ]
    assert "get_device_status" not in ALARM_VERIFICATION.tools
    assert "get_device_maintenance" not in ALARM_VERIFICATION.tools
    assert "query_vehicle_graph" in ALARM_VERIFICATION.tools
    assert "list_recent_alarms" in ALARM_VERIFICATION.tools
    assert len(ALARM_VERIFICATION.tools) == 6
