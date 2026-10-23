from contextlib import asynccontextmanager

from test_graph import REPORT, _alarm, _run
from vehicle_agent.config import Settings
from vehicle_agent.graph.builder import build_graph
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.errors import PolicyDeniedError, ToolExecutionError
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.harness.policy import AllowApprovedWritesPolicy
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import (
    AnalyzeImageInput,
    GetDeviceMaintenanceInput,
    GetDeviceStatusInput,
    SearchRegulationsInput,
    build_vehicle_tools,
)
from vehicle_agent.mcp.client import McpToolSession, build_mcp_client_tools
from vehicle_agent.repositories.memory import build_seed_repository

TOOL_NAMES = (
    "get_vehicle_context",
    "list_recent_alarms",
    "search_regulations",
    "query_vehicle_graph",
    "analyze_image",
    "analyze_video",
    "get_device_status",
    "get_device_maintenance",
    "create_workorder",
    "get_workorder",
    "update_workorder",
)


class _FailingRegulations:
    def search(self, alarm_type: str) -> list:
        raise RuntimeError("regulation index unavailable")


class _FailingGraph:
    def query(self, vehicle_id: str, device_id: str) -> list:
        raise RuntimeError("vehicle graph unavailable")


@asynccontextmanager
async def _broken_connector():
    raise ConnectionError("mcp session is not ready")
    yield None


def _session(
    role: str = "monitor",
    regulations=None,
    graph=None,
    vision=None,
    timeline=None,
) -> tuple[McpToolSession, object]:
    registry = build_vehicle_tools(
        build_seed_repository(),
        regulations,
        graph,
        vision,
        timeline,
    )
    session = McpToolSession.open_memory(registry, role=role)
    return session, registry


def test_listed_schemas_match_the_registered_models() -> None:
    session, registry = _session()
    try:
        listed = {tool.name: tool for tool in session.list_tools().tools}
        assert set(listed) == set(TOOL_NAMES)
        for tool in registry.tools():
            remote = listed[tool.name]
            assert remote.description == tool.description
            assert remote.input_schema == tool.input_model.model_json_schema()
            assert remote.annotations is not None
            assert remote.annotations.read_only_hint is tool.readonly
            assert remote.annotations.idempotent_hint is True
    finally:
        session.close()


def test_search_regulations_matches_the_local_registry() -> None:
    session, registry = _session()
    try:
        remote = session.call("search_regulations", {"alarm_type": "driver_fatigue"})
        local = registry.get("search_regulations").handler(
            SearchRegulationsInput(alarm_type="driver_fatigue")
        )
        assert remote == local.model_dump(mode="json")
        assert remote["snippets"][0]["document_id"] == "fatigue-rules"
    finally:
        session.close()


def test_analyze_image_matches_the_local_registry() -> None:
    session, registry = _session()
    try:
        remote = session.call("analyze_image", {"image_id": "image_closed_eye"})
        local = registry.get("analyze_image").handler(
            AnalyzeImageInput(image_id="image_closed_eye")
        )
        assert remote == local.model_dump(mode="json")
        assert remote["detections"][0]["label"] == "closed_eye"
    finally:
        session.close()


def test_device_status_matches_the_local_registry() -> None:
    session, registry = _session()
    try:
        remote = session.call("get_device_status", {"device_id": "device_003"})
        local = registry.get("get_device_status").handler(
            GetDeviceStatusInput(device_id="device_003")
        )
        assert remote == local.model_dump(mode="json")
        assert remote["online_status"] == "offline"
    finally:
        session.close()


def test_device_maintenance_matches_the_local_registry() -> None:
    session, registry = _session()
    try:
        remote = session.call("get_device_maintenance", {"device_id": "device_003"})
        local = registry.get("get_device_maintenance").handler(
            GetDeviceMaintenanceInput(device_id="device_003")
        )
        tool = registry.get("get_device_maintenance")
        assert tool.readonly is True
        assert remote == local.model_dump(mode="json")
        assert remote["firmware_version"] == "1.2.0"
        assert remote["repair"]["id"] == "repair_003_open"
        assert remote["repair"]["status"] == "open"
    finally:
        session.close()


def test_unknown_tool_is_a_tool_error() -> None:
    session, _registry = _session()
    try:
        try:
            session.call("missing_tool", {})
        except ToolExecutionError as exc:
            assert "unknown tool: missing_tool" in str(exc)
        else:
            raise AssertionError("missing tool was accepted")
    finally:
        session.close()


def test_wrong_role_is_denied_without_retry() -> None:
    session, registry = _session(role="guest")
    tools = build_mcp_client_tools(session, registry)
    harness = Harness(
        Settings(max_retries=3, checkpoint_path="data/test-checkpoints.sqlite"),
        FakeModelAdapter(REPORT),
        tools,
    )
    try:
        try:
            harness.call_tool(
                "search_regulations",
                {"alarm_type": "driver_fatigue"},
                task_id="task_role",
                node="retrieve",
            )
        except PolicyDeniedError:
            pass
        else:
            raise AssertionError("guest role was allowed")
        assert session.calls == ["search_regulations"]
        assert harness.tracer.events[-1].error_code == "policy_denied"
    finally:
        session.close()


def test_mcp_judgement_matches_local_scoring(tmp_path) -> None:
    session, registry = _session()
    tools = build_mcp_client_tools(session, registry)
    alarm = _alarm(
        vehicle_id="vehicle_001",
        driver_id="driver_001",
        device_id="device_001",
        speed_kmh=85,
        raw_confidence=0.9,
    )
    try:
        graph = build_graph(
            Harness(
                Settings(max_retries=1, checkpoint_path=str(tmp_path / "checkpoints.sqlite")),
                FakeModelAdapter(REPORT),
                tools,
                policy=AllowApprovedWritesPolicy(),
            ),
            open_checkpointer(tmp_path / "checkpoints.sqlite"),
        )
        config = {"configurable": {"thread_id": alarm.id}}
        graph.invoke(
            {"task_id": alarm.id, "alarm": alarm.model_dump(mode="json"), "errors": []},
            config,
        )
        values = graph.get_state(config).values
    finally:
        session.close()

    documents = [item for item in values["result"]["evidence"] if item["type"] == "document"]
    relations = [item for item in values["result"]["evidence"] if item["type"] == "graph"]
    tool_events = [
        event["name"]
        for event in values["trace"]
        if event["kind"] == "tool" and event["status"] == "ok"
    ]
    assert values["status"] == "completed"
    assert values["risk_score"] == 1.0
    assert documents[0]["source"] == "fatigue-rules"
    assert "第2节" in documents[0]["description"]
    assert relations
    assert tool_events == [
        "get_vehicle_context",
        "list_recent_alarms",
        "search_regulations",
        "query_vehicle_graph",
        "create_workorder",
    ]
    assert values["workorder_id"] == f"wo-{alarm.id}"
    assert session.calls == tool_events


def test_regulation_failure_retries_then_degrades(tmp_path) -> None:
    session, registry = _session(regulations=_FailingRegulations())
    tools = build_mcp_client_tools(session, registry)
    alarm = _alarm()
    try:
        graph = build_graph(
            Harness(
                Settings(max_retries=1, checkpoint_path=str(tmp_path / "checkpoints.sqlite")),
                FakeModelAdapter(REPORT),
                tools,
            ),
            open_checkpointer(tmp_path / "checkpoints.sqlite"),
        )
        config = {"configurable": {"thread_id": alarm.id}}
        graph.invoke(
            {"task_id": alarm.id, "alarm": alarm.model_dump(mode="json"), "errors": []},
            config,
        )
        values = graph.get_state(config).values
    finally:
        session.close()

    assert values["status"] == "awaiting_review"
    assert values["regulation_status"] == "degraded"
    assert values["errors"][-1]["code"] == "tool_failed"
    assert values["status"] != "failed"
    assert session.calls.count("search_regulations") == 2
    assert "result" not in values


def test_graph_failure_degrades(tmp_path) -> None:
    session, registry = _session(graph=_FailingGraph())
    tools = build_mcp_client_tools(session, registry)
    alarm = _alarm()
    try:
        graph = build_graph(
            Harness(
                Settings(max_retries=1, checkpoint_path=str(tmp_path / "checkpoints.sqlite")),
                FakeModelAdapter(REPORT),
                tools,
            ),
            open_checkpointer(tmp_path / "checkpoints.sqlite"),
        )
        config = {"configurable": {"thread_id": alarm.id}}
        graph.invoke(
            {"task_id": alarm.id, "alarm": alarm.model_dump(mode="json"), "errors": []},
            config,
        )
        values = graph.get_state(config).values
    finally:
        session.close()

    assert values["status"] == "awaiting_review"
    assert values["graph_status"] == "degraded"
    assert values["errors"][-1]["code"] == "tool_failed"
    assert session.calls.count("query_vehicle_graph") == 2
    assert "result" not in values


def test_session_startup_failure_stops_at_context(tmp_path) -> None:
    session = McpToolSession(role="monitor", connector=_broken_connector)
    tools = build_mcp_client_tools(session)
    alarm = _alarm()
    try:
        graph = build_graph(
            Harness(
                Settings(max_retries=1, checkpoint_path=str(tmp_path / "checkpoints.sqlite")),
                FakeModelAdapter(REPORT),
                tools,
            ),
            open_checkpointer(tmp_path / "checkpoints.sqlite"),
        )
        config = {"configurable": {"thread_id": alarm.id}}
        graph.invoke(
            {"task_id": alarm.id, "alarm": alarm.model_dump(mode="json"), "errors": []},
            config,
        )
        values = graph.get_state(config).values
    finally:
        session.close()

    assert values["status"] == "failed"
    assert values["errors"][-1]["code"] == "tool_failed"
    assert values["trace"][-1]["node"] == "context"
    assert "risk_score" not in values
    assert session.calls == ["get_vehicle_context", "get_vehicle_context"]


class _FailingVision:
    def analyze(self, image_id: str, content: bytes | None = None) -> list:
        raise RuntimeError("vision index unavailable")


def test_mcp_image_judgement_matches_local(tmp_path) -> None:
    alarm = _alarm(
        id="alarm_mcp_image",
        vehicle_id="vehicle_001",
        driver_id="driver_001",
        device_id="device_001",
        speed_kmh=85,
        raw_confidence=0.9,
        image_id="image_closed_eye",
    )
    _, local = _run(tmp_path, alarm)
    session, registry = _session()
    tools = build_mcp_client_tools(session, registry)
    try:
        graph = build_graph(
            Harness(
                Settings(max_retries=1, checkpoint_path=str(tmp_path / "mcp.sqlite")),
                FakeModelAdapter(REPORT),
                tools,
                policy=AllowApprovedWritesPolicy(),
            ),
            open_checkpointer(tmp_path / "mcp.sqlite"),
        )
        config = {"configurable": {"thread_id": alarm.id}}
        graph.invoke(
            {"task_id": alarm.id, "alarm": alarm.model_dump(mode="json"), "errors": []},
            config,
        )
        values = graph.get_state(config).values
    finally:
        session.close()

    assert values["status"] == local["status"] == "completed"
    assert values["risk_score"] == local["risk_score"] == 1.0
    assert values["visible_facts"] == local["visible_facts"]
    assert values["workorder_id"] == local["workorder_id"] == f"wo-{alarm.id}"
    assert "analyze_image" in session.calls
    assert "create_workorder" in session.calls


def test_mcp_detector_failure_retries_then_reviews(tmp_path) -> None:
    session, registry = _session(vision=_FailingVision())
    tools = build_mcp_client_tools(session, registry)
    alarm = _alarm(id="alarm_mcp_vision_down", image_id="image_closed_eye")
    try:
        graph = build_graph(
            Harness(
                Settings(max_retries=1, checkpoint_path=str(tmp_path / "mcp.sqlite")),
                FakeModelAdapter(REPORT),
                tools,
            ),
            open_checkpointer(tmp_path / "mcp.sqlite"),
        )
        config = {"configurable": {"thread_id": alarm.id}}
        graph.invoke(
            {"task_id": alarm.id, "alarm": alarm.model_dump(mode="json"), "errors": []},
            config,
        )
        values = graph.get_state(config).values
    finally:
        session.close()

    assert values["status"] == "awaiting_review"
    assert values["vision_status"] == "degraded"
    assert values["errors"][-1]["code"] == "vision_unavailable"
    assert values["status"] != "failed"
    assert session.calls.count("analyze_image") == 2
    assert "result" not in values


def test_local_backend_still_scores_without_mcp(tmp_path) -> None:
    _, values = _run(
        tmp_path,
        _alarm(vehicle_id="vehicle_001", driver_id="driver_001", speed_kmh=85, raw_confidence=0.9),
        tool_backend="local",
    )
    assert values["status"] == "completed"
    assert values["risk_score"] == 1.0
