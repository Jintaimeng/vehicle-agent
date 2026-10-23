from langgraph.types import Command

from test_graph import _alarm, _run
from vehicle_agent.config import Settings
from vehicle_agent.harness.errors import PolicyDeniedError
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.harness.policy import AllowApprovedWritesPolicy, AllowReadOnlyPolicy
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import build_vehicle_tools
from vehicle_agent.repositories.memory import build_seed_repository
from vehicle_agent.workorders import (
    CreateWorkOrderInput,
    GetWorkOrderInput,
    MemoryWorkOrderStore,
    UpdateWorkOrderInput,
)


def _high_alarm(**overrides):
    payload = {
        "id": "alarm_high",
        "vehicle_id": "vehicle_001",
        "driver_id": "driver_001",
        "device_id": "device_001",
        "speed_kmh": 85,
        "raw_confidence": 0.9,
    }
    payload.update(overrides)
    return _alarm(**payload)


def test_medium_risk_does_not_create_workorder(tmp_path) -> None:
    _, values = _run(tmp_path, _alarm())

    assert values["status"] == "completed"
    assert values["result"]["risk_level"] == "medium"
    assert "workorder_id" not in values
    assert all(event["name"] != "create_workorder" for event in values["trace"])


def test_high_risk_creates_one_idempotent_workorder(tmp_path) -> None:
    store = MemoryWorkOrderStore()
    alarm = _high_alarm()
    _, values = _run(tmp_path, alarm, workorders=store)

    assert values["status"] == "completed"
    assert values["risk_score"] == 1.0
    assert values["workorder_status"] == "created"
    assert values["workorder_id"] == "wo-alarm_high"
    assert len(store) == 1
    again = store.create(
        task_id=alarm.id,
        alarm_id=alarm.id,
        risk_level="high",
        summary="另一份摘要",
    )
    assert again.workorder_id == "wo-alarm_high"
    assert again.summary != "另一份摘要"
    assert len(store) == 1
    model_nodes = [
        event["node"]
        for event in values["trace"]
        if event["kind"] == "model" and event["status"] == "ok"
    ]
    assert model_nodes == ["report"]


def test_review_path_does_not_create_workorder(tmp_path) -> None:
    alarm = _alarm(id="alarm_review", vehicle_id="vehicle_missing")
    graph, waiting = _run(tmp_path, alarm)

    assert waiting["status"] == "awaiting_review"
    graph.invoke(
        Command(resume={"reviewer": "ops", "decision": "confirm", "comment": "人工确认"}),
        {"configurable": {"thread_id": alarm.id}},
    )
    done = graph.get_state({"configurable": {"thread_id": alarm.id}}).values

    assert done["status"] == "completed"
    assert done["human_review"]["decision"] == "confirm"
    assert "workorder_id" not in done
    assert all(event["name"] != "create_workorder" for event in done["trace"])


class _FailingWorkOrders:
    def create(self, task_id: str, alarm_id: str, risk_level: str, summary: str):
        raise RuntimeError("workorder store unavailable")


def test_create_failure_keeps_completed_result(tmp_path) -> None:
    _, values = _run(tmp_path, _high_alarm(id="alarm_store_down"), workorders=_FailingWorkOrders())

    assert values["status"] == "completed"
    assert values["risk_score"] == 1.0
    assert values["workorder_status"] == "degraded"
    assert "workorder_id" not in values
    assert values["errors"][-1]["code"] == "workorder_unavailable"
    assert "workorder store unavailable" in values["errors"][-1]["message"]
    assert values["result"]["summary"].startswith("根据车速")


def test_store_rejects_non_high_and_unknown_ids() -> None:
    store = MemoryWorkOrderStore()
    try:
        store.create("task_1", "alarm_1", "medium", "摘要")
    except ValueError as exc:
        assert "high" in str(exc)
    else:
        raise AssertionError("medium risk created a workorder")

    created = store.create("task_1", "alarm_1", "high", "摘要")
    updated = store.update(created.workorder_id, "closed", "已处置")
    assert updated.status == "closed"
    assert updated.comment == "已处置"
    assert store.get(created.workorder_id).status == "closed"
    try:
        store.get("wo-missing")
    except KeyError as exc:
        assert "wo-missing" in str(exc)
    else:
        raise AssertionError("missing workorder was returned")


def test_readonly_policy_denies_real_create_tool() -> None:
    registry = build_vehicle_tools(build_seed_repository())
    harness = Harness(
        Settings(max_retries=0, checkpoint_path="data/test-checkpoints.sqlite"),
        FakeModelAdapter("{}"),
        registry,
        policy=AllowReadOnlyPolicy(),
    )

    try:
        harness.call_tool(
            "create_workorder",
            {
                "task_id": "task_1",
                "alarm_id": "alarm_1",
                "risk_level": "high",
                "summary": "摘要",
            },
            task_id="task_1",
            node="report",
        )
    except PolicyDeniedError:
        pass
    else:
        raise AssertionError("readonly policy allowed create_workorder")

    approved = Harness(
        Settings(max_retries=0, checkpoint_path="data/test-checkpoints.sqlite"),
        FakeModelAdapter("{}"),
        registry,
        policy=AllowApprovedWritesPolicy(),
    )
    try:
        approved.call_tool(
            "update_workorder",
            {"workorder_id": "wo-task_1", "status": "closed", "comment": ""},
            task_id="task_1",
            node="report",
        )
    except PolicyDeniedError:
        pass
    else:
        raise AssertionError("approved policy allowed update_workorder")


def test_tool_handlers_match_the_store() -> None:
    store = MemoryWorkOrderStore()
    registry = build_vehicle_tools(build_seed_repository(), workorders=store)
    created = registry.get("create_workorder").handler(
        CreateWorkOrderInput(
            task_id="task_1",
            alarm_id="alarm_1",
            risk_level="high",
            summary="摘要",
        )
    )
    fetched = registry.get("get_workorder").handler(
        GetWorkOrderInput(workorder_id=created.workorder_id)
    )
    updated = registry.get("update_workorder").handler(
        UpdateWorkOrderInput(workorder_id=created.workorder_id, status="closed", comment="备注")
    )

    assert fetched.workorder_id == "wo-task_1"
    assert updated.status == "closed"
    assert updated.comment == "备注"
    assert registry.get("create_workorder").readonly is False
    assert registry.get("get_workorder").readonly is True
    assert registry.get("update_workorder").readonly is False
