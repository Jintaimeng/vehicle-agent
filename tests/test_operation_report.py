from datetime import UTC, datetime, timedelta

import pytest

from vehicle_agent.domain.models import Alarm
from vehicle_agent.harness.tools import build_vehicle_tools
from vehicle_agent.repositories.memory import MemoryAlarmRepository, build_seed_repository
from vehicle_agent.skills.alarm_verification import ALARM_VERIFICATION
from vehicle_agent.skills.operation_report import OPERATION_REPORT, build_operation_report
from vehicle_agent.workorders import GetWorkOrderInput, MemoryWorkOrderStore, seed_report_workorders

AS_OF = datetime(2026, 10, 9, 10, 30, tzinfo=UTC)


def test_day_and_week_reports_use_seed_orders() -> None:
    repository = build_seed_repository()
    orders = seed_report_workorders()

    day = build_operation_report(repository, orders, "day")
    week = build_operation_report(repository, orders, "week")

    assert day.status == "completed"
    assert day.skill_id == "operation-report"
    assert day.alarm_count == 19
    assert day.previous_count == 4
    assert day.trend == "rising"
    assert day.effect == "improved"
    assert day.closed_count == 1
    assert day.open_count == 0
    assert day.recommendation == "保持已关闭工单的处置。"
    assert any("趋势上升" in item.description for item in day.evidence)
    assert week.alarm_count == 24
    assert week.previous_count == 0
    assert week.trend == "rising"
    assert week.effect == "pending"
    assert week.open_count == 1
    assert week.closed_count == 1
    assert week.recommendation == "先关闭未完成工单。"
    assert any("未关闭 1 张工单" in item.description for item in week.evidence)
    assert [item.status for item in orders.list_workorders()] == ["closed", "open"]


def test_trend_falls_or_stays_flat_without_orders() -> None:
    orders = MemoryWorkOrderStore()
    falling = build_operation_report(
        _repository([_alarm("older", AS_OF - timedelta(days=2))]),
        orders,
        "day",
    )
    flat = build_operation_report(
        _repository(
            [
                _alarm("older", AS_OF - timedelta(days=2)),
                _alarm("newer", AS_OF - timedelta(hours=1)),
            ]
        ),
        orders,
        "day",
    )

    assert falling.trend == "falling"
    assert falling.alarm_count == 0
    assert falling.previous_count == 1
    assert falling.effect == "no_disposition"
    assert falling.recommendation == "本窗口没有工单。"
    assert flat.trend == "flat"
    assert flat.alarm_count == 1
    assert flat.previous_count == 1
    assert flat.effect == "no_disposition"


class _BrokenAlarms:
    def __init__(self, inner: MemoryAlarmRepository) -> None:
        self._inner = inner

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def list_alarms_between(self, *, start, end, include_end: bool) -> list:
        raise ConnectionError("alarms unavailable")


class _BrokenOrders(MemoryWorkOrderStore):
    def list_workorders(self) -> list:
        raise ConnectionError("orders unavailable")


def test_read_failures_do_not_guess_effect() -> None:
    repository = build_seed_repository()
    orders = _CountingOrders()
    alarms_failed = build_operation_report(_BrokenAlarms(repository), orders, "day")
    orders_failed = build_operation_report(repository, _BrokenOrders(), "week")

    assert alarms_failed.status == "degraded"
    assert alarms_failed.trend is None
    assert alarms_failed.effect is None
    assert alarms_failed.recommendation is None
    assert alarms_failed.evidence == []
    assert orders.calls == 0
    assert orders_failed.status == "degraded"
    assert orders_failed.effect is None
    assert orders_failed.recommendation is None
    assert orders_failed.alarm_count == 24
    assert orders_failed.previous_count == 0
    assert orders_failed.trend == "rising"
    assert any("本窗口有 24 次疲劳报警" in item.description for item in orders_failed.evidence)


class _CountingOrders:
    def __init__(self) -> None:
        self.calls = 0

    def list_workorders(self) -> list:
        self.calls += 1
        return []


def test_report_does_not_add_tools_or_seed_judgement_orders() -> None:
    registry = build_vehicle_tools(build_seed_repository())

    assert OPERATION_REPORT.tools == []
    assert [tool.name for tool in registry.tools()] == [
        "get_vehicle_context",
        "list_recent_alarms",
        "get_device_status",
        "get_device_maintenance",
        "search_regulations",
        "query_vehicle_graph",
        "analyze_image",
        "analyze_video",
        "create_workorder",
        "get_workorder",
        "update_workorder",
    ]
    assert list(ALARM_VERIFICATION.tools) == [
        "get_vehicle_context",
        "list_recent_alarms",
        "search_regulations",
        "query_vehicle_graph",
        "analyze_image",
        "analyze_video",
    ]
    assert len(MemoryWorkOrderStore()) == 0
    with pytest.raises(KeyError, match="workorder not found"):
        registry.get("get_workorder").handler(
            GetWorkOrderInput(workorder_id="wo-report-closed")
        )


def _repository(alarms: list[Alarm]) -> MemoryAlarmRepository:
    return MemoryAlarmRepository({}, {}, alarms)


def _alarm(alarm_id: str, alarm_time: datetime) -> Alarm:
    return Alarm(
        id=alarm_id,
        vehicle_id="vehicle_001",
        driver_id="driver_001",
        device_id="device_001",
        alarm_type="driver_fatigue",
        alarm_time=alarm_time,
        longitude=113.26,
        latitude=23.12,
        speed_kmh=70,
        raw_confidence=0.8,
    )
