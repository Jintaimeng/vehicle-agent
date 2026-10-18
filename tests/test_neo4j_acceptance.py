import os

import pytest

from test_graph import REPORT, _alarm, _run
from vehicle_agent.api.main import create_app
from vehicle_agent.config import Settings
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.knowledge.graph import Neo4jGraphIndex, seed_graph_index
from vehicle_agent.repositories.memory import build_seed_repository

pytestmark = pytest.mark.skipif(
    os.environ.get("VEHICLE_AGENT_RUN_NEO4J") != "1",
    reason="设置 VEHICLE_AGENT_RUN_NEO4J=1 后连接 deployments/docker-compose.yml 中的 Neo4j",
)


def test_live_neo4j_matches_memory_seed() -> None:
    index = Neo4jGraphIndex("bolt://127.0.0.1:7687", "neo4j", "vehicle-agent-local")
    try:
        memory = seed_graph_index()
        cases = [
            ("vehicle_001", "device_001"),
            ("vehicle_001", "device_003"),
            ("vehicle_002", "device_002"),
            ("vehicle_003", "device_003"),
            ("vehicle_003", "device_001"),
            ("vehicle_missing", "device_001"),
        ]
        for vehicle_id, device_id in cases:
            assert index.query(vehicle_id, device_id) == memory.query(vehicle_id, device_id)
        unrelated = index.query("vehicle_001", "device_003")
        assert all(fact.target_id != "camera_occlusion" for fact in unrelated)
        index.ensure_ready()
        assert index.query("vehicle_003", "device_003") == memory.query("vehicle_003", "device_003")
    finally:
        index.close()


def test_live_app_completes_high_risk_with_graph_evidence(tmp_path) -> None:
    settings = Settings(
        graph_backend="neo4j",
        max_retries=1,
        checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
    )
    app = create_app(
        settings,
        build_seed_repository(),
        FakeModelAdapter(REPORT),
        open_checkpointer(tmp_path / "checkpoints.sqlite"),
    )
    try:
        from fastapi.testclient import TestClient

        assert isinstance(app.state.graph_index, Neo4jGraphIndex)
        assert app.state.tools.get("query_vehicle_graph").name == "query_vehicle_graph"
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
        assert body["status"] == "completed"
        assert body["result"]["risk_score"] == 1.0
        assert any(item["type"] == "document" for item in evidence)
        assert any(item["type"] == "graph" for item in evidence)
        assert any(item["source"] == "drives:vehicle_001->driver_001" for item in evidence)
    finally:
        app.state.graph_index.close()


def test_live_vehicle_001_judgement_matches_memory_facts(tmp_path) -> None:
    index = Neo4jGraphIndex("bolt://127.0.0.1:7687", "neo4j", "vehicle-agent-local")
    try:
        _, values = _run(
            tmp_path,
            _alarm(
                vehicle_id="vehicle_001",
                driver_id="driver_001",
                device_id="device_001",
                speed_kmh=85,
                raw_confidence=0.9,
            ),
            graph=index,
        )
        expected = [
            item.model_dump()
            for item in seed_graph_index().query("vehicle_001", "device_001")
        ]
        documents = [item for item in values["result"]["evidence"] if item["type"] == "document"]
        relations = [item for item in values["result"]["evidence"] if item["type"] == "graph"]
        assert values["status"] == "completed"
        assert values["risk_score"] == 1.0
        assert values["graph_facts"] == expected
        assert documents[0]["source"] == "fatigue-rules"
        assert any(item["source"] == "installed_with:vehicle_001->device_001" for item in relations)
    finally:
        index.close()


def test_live_camera_occlusion_conflicts_with_the_same_speed_score(tmp_path) -> None:
    index = Neo4jGraphIndex("bolt://127.0.0.1:7687", "neo4j", "vehicle-agent-local")
    try:
        _, values = _run(
            tmp_path,
            _alarm(
                vehicle_id="vehicle_003",
                driver_id="driver_003",
                device_id="device_003",
                speed_kmh=85,
                raw_confidence=0.9,
            ),
            graph=index,
        )
        assert values["status"] == "awaiting_review"
        assert values["graph_status"] == "hit"
        assert values["regulation_status"] == "hit"
        assert values["risk_score"] == 0.8
        assert any(item["type"] == "document" for item in values["evidence"])
        assert any(
            item["type"] == "graph" and item["source"] == "has_fault:device_003->camera_occlusion"
            for item in values["evidence"]
        )
        assert "result" not in values
    finally:
        index.close()


def test_live_unknown_vehicle_has_no_graph_facts(tmp_path) -> None:
    index = Neo4jGraphIndex("bolt://127.0.0.1:7687", "neo4j", "vehicle-agent-local")
    try:
        _, values = _run(tmp_path, _alarm(vehicle_id="vehicle_missing"), graph=index)
        assert values["status"] == "awaiting_review"
        assert values["graph_status"] == "miss"
        assert values["graph_facts"] == []
        assert "result" not in values
    finally:
        index.close()


def test_live_connection_failure_degrades_without_failing_the_case(tmp_path) -> None:
    index = Neo4jGraphIndex("bolt://127.0.0.1:1", "neo4j", "vehicle-agent-local")
    try:
        _, values = _run(
            tmp_path,
            _alarm(),
            graph=index,
            max_retries=0,
            request_timeout_seconds=8,
        )
    finally:
        index.close()

    assert values["status"] == "awaiting_review"
    assert values["graph_status"] == "degraded"
    assert values["errors"][-1]["code"] == "tool_failed"
    assert "result" not in values
