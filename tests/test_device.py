from datetime import UTC, datetime, timedelta

import pytest

from test_graph import _alarm, _run
from vehicle_agent.config import Settings
from vehicle_agent.harness.errors import ToolExecutionError
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import build_vehicle_tools
from vehicle_agent.repositories.memory import build_seed_repository
from vehicle_agent.skills.alarm_verification import ALARM_VERIFICATION

NOW = datetime(2026, 10, 9, 10, 30, tzinfo=UTC)
_QUERY_TOOLS = [
    "get_vehicle_context",
    "list_recent_alarms",
    "search_regulations",
    "query_vehicle_graph",
]


def _harness() -> Harness:
    return Harness(
        Settings(max_retries=1, checkpoint_path="data/test-checkpoints.sqlite"),
        FakeModelAdapter("{}"),
        build_vehicle_tools(build_seed_repository()),
    )


def test_seed_devices_are_online_or_offline() -> None:
    repository = build_seed_repository()

    online = repository.get_device("device_001")
    offline = repository.get_device("device_003")

    assert online.online_status == "online"
    assert online.last_heartbeat_at == NOW
    assert repository.get_device("device_002").online_status == "online"
    assert offline.online_status == "offline"
    assert offline.last_heartbeat_at == NOW - timedelta(days=2)


def test_unknown_and_failing_devices_are_tool_errors() -> None:
    harness = _harness()

    with pytest.raises(ToolExecutionError, match="unknown device"):
        harness.call_tool(
            "get_device_status",
            {"device_id": "device_missing"},
            task_id="task_missing",
            node="context",
        )
    with pytest.raises(ToolExecutionError, match="device store unavailable"):
        harness.call_tool(
            "get_device_status",
            {"device_id": "device_error"},
            task_id="task_error",
            node="context",
        )


def test_high_speed_judgement_does_not_query_device_status(tmp_path) -> None:
    _, values = _run(
        tmp_path,
        _alarm(vehicle_id="vehicle_001", driver_id="driver_001", speed_kmh=85, raw_confidence=0.9),
    )
    names = [event["name"] for event in values["trace"] if event["kind"] == "tool"]

    assert values["risk_score"] == 1.0
    assert names[:4] == _QUERY_TOOLS
    assert "get_device_status" not in names
    assert "get_device_status" not in ALARM_VERIFICATION.tools
