import os

import pytest
from fastapi.testclient import TestClient

from test_graph import REPORT, _alarm
from vehicle_agent.api.main import create_app
from vehicle_agent.config import Settings
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.repositories.memory import build_seed_repository

pytestmark = pytest.mark.skipif(
    os.environ.get("VEHICLE_AGENT_RUN_MCP") != "1",
    reason="设置 VEHICLE_AGENT_RUN_MCP=1 后通过 stdio 启动 MCP 服务进程",
)


def _app(tmp_path):
    settings = Settings(
        tool_backend="mcp",
        mcp_transport="stdio",
        max_retries=1,
        checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
    )
    return create_app(
        settings,
        build_seed_repository(),
        FakeModelAdapter(REPORT),
        open_checkpointer(tmp_path / "checkpoints.sqlite"),
    )


def test_stdio_high_risk_judgement_uses_mcp(tmp_path) -> None:
    app = _app(tmp_path)
    try:
        created = TestClient(app).post(
            "/api/v1/judgements",
            json=_alarm(
                vehicle_id="vehicle_001",
                driver_id="driver_001",
                device_id="device_001",
                speed_kmh=85,
                raw_confidence=0.9,
            ).model_dump(mode="json"),
        )
        assert created.status_code == 200
        body = created.json()
        evidence = body["result"]["evidence"]
        documents = [item for item in evidence if item["type"] == "document"]
        assert body["status"] == "completed"
        assert body["result"]["risk_score"] == 1.0
        assert documents[0]["source"] == "fatigue-rules"
        assert "第2节" in documents[0]["description"]
        assert any(item["type"] == "graph" for item in evidence)
        assert app.state.mcp_session.calls == [
            "get_vehicle_context",
            "list_recent_alarms",
            "search_regulations",
            "query_vehicle_graph",
        ]
    finally:
        app.state.mcp_session.close()


def test_stdio_camera_occlusion_still_conflicts(tmp_path) -> None:
    app = _app(tmp_path)
    try:
        created = TestClient(app).post(
            "/api/v1/judgements",
            json=_alarm(
                vehicle_id="vehicle_003",
                driver_id="driver_003",
                device_id="device_003",
                speed_kmh=85,
                raw_confidence=0.9,
            ).model_dump(mode="json"),
        )
        assert created.status_code == 200
        body = created.json()
        values = app.state.graph.get_state(
            {"configurable": {"thread_id": body["task_id"]}}
        ).values
        assert body["status"] == "awaiting_review"
        assert values["risk_score"] == 0.8
        assert values["regulation_status"] == "hit"
        assert values["graph_status"] == "hit"
        assert any(
            item["type"] == "graph" and item["source"] == "has_fault:device_003->camera_occlusion"
            for item in values["evidence"]
        )
        assert "result" not in values
    finally:
        app.state.mcp_session.close()
