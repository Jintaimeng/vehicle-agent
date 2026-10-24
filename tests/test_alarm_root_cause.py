from datetime import datetime

import pytest

from vehicle_agent.config import Settings
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import build_vehicle_tools
from vehicle_agent.repositories.memory import MemoryAlarmRepository, build_seed_repository
from vehicle_agent.skills.alarm_root_cause import ALARM_ROOT_CAUSE, trace_alarm_cause
from vehicle_agent.skills.alarm_verification import ALARM_VERIFICATION


def _harness(
    repository=None,
    graph=None,
    regulations=None,
) -> tuple[Harness, MemoryAlarmRepository]:
    repository = build_seed_repository() if repository is None else repository
    harness = Harness(
        Settings(max_retries=0, checkpoint_path="data/test-checkpoints.sqlite"),
        FakeModelAdapter("{}"),
        build_vehicle_tools(repository, regulations=regulations, graph=graph),
    )
    return harness, repository


def test_occlusion_beats_repeated_alarms() -> None:
    harness, repository = _harness()

    occluded = trace_alarm_cause(harness, repository, "alarm_004_1")
    repeated = trace_alarm_cause(harness, repository, "alarm_hist_001")

    assert occluded.status == "completed"
    assert occluded.skill_id == "alarm-root-cause-analysis"
    assert occluded.cause == "camera_occlusion"
    assert occluded.related_count == 2
    assert occluded.recommendation == "先核对镜头遮挡，再决定是否按疲劳处置。"
    assert any("有摄像头遮挡故障" in item.description for item in occluded.evidence)
    assert repeated.cause == "repeated_driver_fatigue"
    assert repeated.related_count == 1
    assert repeated.recommendation == "对照近期同类报警复核。"
    assert any("另有 1 次同类报警" in item.description for item in repeated.evidence)
    assert harness.budget.model_calls == 0


def test_single_alarm_uses_regulation_until_snippets_are_empty() -> None:
    harness, repository = _harness()
    empty_harness, empty_repository = _harness(regulations=_EmptyRegulations())

    ruled = trace_alarm_cause(harness, repository, "alarm_011_1")
    unexplained = trace_alarm_cause(empty_harness, empty_repository, "alarm_011_1")

    assert ruled.cause == "driver_fatigue"
    assert ruled.related_count == 0
    assert ruled.recommendation == "按疲劳驾驶规则处置。"
    assert any(item.type == "document" and "疲劳" in item.description for item in ruled.evidence)
    assert any("无摄像头遮挡故障" in item.description for item in ruled.evidence)
    assert unexplained.status == "completed"
    assert unexplained.cause == "unexplained"
    assert unexplained.recommendation == "补充法规或图谱证据后再判断。"
    assert all(item.type != "document" for item in unexplained.evidence)
    assert any("另有 0 次同类报警" in item.description for item in unexplained.evidence)


class _BrokenGraph:
    def query(self, vehicle_id: str, device_id: str) -> list:
        raise ConnectionError("graph unavailable")


class _EmptyRegulations:
    def search(self, alarm_type: str) -> list:
        return []


class _BrokenRegulations:
    def search(self, alarm_type: str) -> list:
        raise ConnectionError("regulations unavailable")


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


def test_tool_failures_do_not_guess() -> None:
    repository = build_seed_repository()
    graph_harness, _ = _harness(graph=_BrokenGraph())
    regulation_harness, _ = _harness(regulations=_BrokenRegulations())
    broken = _BrokenHistory(repository)
    history_harness, _ = _harness(repository=broken)

    graph_failed = trace_alarm_cause(graph_harness, repository, "alarm_011_1")
    regulation_failed = trace_alarm_cause(regulation_harness, repository, "alarm_011_1")
    history_failed = trace_alarm_cause(history_harness, broken, "alarm_011_1")

    assert graph_failed.status == "degraded"
    assert graph_failed.cause is None
    assert graph_failed.recommendation is None
    assert graph_failed.evidence == []
    assert "search_regulations" not in [event.name for event in graph_harness.tracer.events]
    assert "list_recent_alarms" not in [event.name for event in graph_harness.tracer.events]
    assert regulation_failed.status == "degraded"
    assert regulation_failed.cause is None
    assert regulation_failed.recommendation is None
    assert any("无摄像头遮挡故障" in item.description for item in regulation_failed.evidence)
    assert all(item.type != "document" for item in regulation_failed.evidence)
    assert "list_recent_alarms" not in [event.name for event in regulation_harness.tracer.events]
    assert history_failed.status == "degraded"
    assert history_failed.cause is None
    assert history_failed.related_count is None
    assert any(item.type == "graph" for item in history_failed.evidence)
    assert any(item.type == "document" for item in history_failed.evidence)
    with pytest.raises(KeyError, match="unknown alarm"):
        trace_alarm_cause(graph_harness, repository, "alarm_missing")


def test_root_cause_does_not_add_tools() -> None:
    assert ALARM_ROOT_CAUSE.tools == [
        "query_vehicle_graph",
        "search_regulations",
        "list_recent_alarms",
    ]
    assert ALARM_VERIFICATION.tools == [
        "get_vehicle_context",
        "list_recent_alarms",
        "search_regulations",
        "query_vehicle_graph",
        "analyze_image",
        "analyze_video",
    ]
    assert len(ALARM_VERIFICATION.tools) == 6
