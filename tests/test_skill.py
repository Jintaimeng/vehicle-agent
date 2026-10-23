import pytest

from test_graph import _alarm, _run
from test_vision import _FailingVision, _high_alarm
from vehicle_agent.config import Settings
from vehicle_agent.domain.models import JudgementResult
from vehicle_agent.graph.builder import build_graph
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.harness.policy import AllowApprovedWritesPolicy
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import (
    GetVehicleContextInput,
    RegisteredTool,
    ToolRegistry,
    VehicleContextOutput,
    build_vehicle_tools,
)
from vehicle_agent.repositories.memory import build_seed_repository
from vehicle_agent.skills.alarm_verification import (
    ALARM_VERIFICATION,
    check_alarm_verification_skill,
)

_QUERY_TOOLS = [
    "get_vehicle_context",
    "list_recent_alarms",
    "search_regulations",
    "query_vehicle_graph",
]
_VISION_TOOLS = ["analyze_image", "analyze_video"]
_WORKORDER_TOOLS = ["create_workorder", "get_workorder", "update_workorder"]


def _tool_names(values: dict) -> list[str]:
    return [
        event["name"]
        for event in values["trace"]
        if event["kind"] == "tool" and event["status"] == "ok"
    ]


def test_skill_lists_six_readonly_tools_and_four_cases() -> None:
    skill = ALARM_VERIFICATION

    assert skill.id == "alarm-verification"
    assert skill.trigger_alarm_type == "driver_fatigue"
    assert skill.tools == [*_QUERY_TOOLS, *_VISION_TOOLS]
    assert skill.output_fields == list(JudgementResult.model_fields)
    assert [case.id for case in skill.cases] == [
        "no_media_complete",
        "image_high_risk",
        "observation_conflict",
        "vision_degraded",
    ]
    assert all(name not in skill.tools for name in _WORKORDER_TOOLS)
    failed = skill.failures[0]
    degraded = skill.failures[1]
    assert failed.nodes == ["validate", "context", "history"]
    assert failed.marks_task_failed is True
    assert degraded.nodes == ["retrieve", "graph", "vision"]
    assert degraded.marks_task_failed is False
    assert degraded.task_status == "awaiting_review"


def test_skill_startup_rejects_a_drifted_graph() -> None:
    registry = build_vehicle_tools(build_seed_repository())
    order = [step.node for step in ALARM_VERIFICATION.steps]
    check_alarm_verification_skill(registry, order)

    with pytest.raises(RuntimeError, match="do not match"):
        check_alarm_verification_skill(registry, list(reversed(order)))

    with pytest.raises(RuntimeError, match="not registered"):
        check_alarm_verification_skill(ToolRegistry(), order)

    writable = ToolRegistry()
    writable.register(
        RegisteredTool(
            name="get_vehicle_context",
            description="查询车辆和驾驶员资料",
            input_model=GetVehicleContextInput,
            output_model=VehicleContextOutput,
            handler=lambda payload: VehicleContextOutput(vehicle=None, driver=None),
            readonly=False,
        )
    )
    with pytest.raises(RuntimeError, match="read-only"):
        check_alarm_verification_skill(writable, order)


def test_no_media_case_calls_only_query_tools(tmp_path) -> None:
    alarm = _alarm()
    _, values = _run(tmp_path, alarm)

    assert values["status"] == "completed"
    assert values["skill_id"] == "alarm-verification"
    assert values["result"]["risk_level"] == "medium"
    assert values["risk_score"] == 0.45
    assert _tool_names(values) == ALARM_VERIFICATION.tools_for(alarm.model_dump(mode="json"))
    assert _tool_names(values) == _QUERY_TOOLS


def test_image_high_risk_case_keeps_score_and_workorder(tmp_path) -> None:
    alarm = _high_alarm(image_id="image_closed_eye")
    _, values = _run(tmp_path, alarm)

    assert values["status"] == "completed"
    assert values["skill_id"] == "alarm-verification"
    assert values["risk_score"] == 1.0
    assert _tool_names(values) == [
        *ALARM_VERIFICATION.tools_for(alarm.model_dump(mode="json")),
        "create_workorder",
    ]
    assert "create_workorder" not in ALARM_VERIFICATION.tools


def test_observation_conflict_case_skips_workorder(tmp_path) -> None:
    _, matched = _run(
        tmp_path,
        _high_alarm(id="alarm_closed", image_id="image_closed_eye"),
    )
    _, mismatched = _run(
        tmp_path,
        _high_alarm(id="alarm_mismatch", image_id="image_closed_eye_mismatch"),
    )

    assert mismatched["skill_id"] == "alarm-verification"
    assert mismatched["status"] == "awaiting_review"
    assert mismatched["risk_score"] == matched["risk_score"] == 1.0
    assert "workorder_id" not in mismatched
    assert all(event["name"] != "create_workorder" for event in mismatched["trace"])


def test_vision_degraded_case_does_not_fail_the_task(tmp_path) -> None:
    _, values = _run(
        tmp_path,
        _alarm(image_id="image_closed_eye"),
        vision=_FailingVision(),
    )

    assert values["skill_id"] == "alarm-verification"
    assert values["status"] == "awaiting_review"
    assert values["status"] != "failed"
    assert values["vision_status"] == "degraded"
    assert values["errors"][-1]["code"] == "vision_unavailable"


def test_unmatched_alarm_type_stops_before_tools(tmp_path) -> None:
    alarm = _alarm().model_dump(mode="json")
    alarm["alarm_type"] = "phone_use"
    runtime = Settings(max_retries=1, checkpoint_path="data/test-checkpoints.sqlite")
    harness = Harness(
        runtime,
        FakeModelAdapter("{}"),
        build_vehicle_tools(build_seed_repository(), settings=runtime),
        policy=AllowApprovedWritesPolicy(),
    )
    graph = build_graph(harness, open_checkpointer(tmp_path / "checkpoints.sqlite"))
    config = {"configurable": {"thread_id": "alarm_other"}}
    graph.invoke({"task_id": "alarm_other", "alarm": alarm, "errors": []}, config)
    values = graph.get_state(config).values

    assert values["status"] == "failed"
    assert values["errors"][-1]["code"] == "skill_not_applicable"
    assert "skill_id" not in values
    assert values["trace"] == []
    assert graph.get_state(config).next == ()
