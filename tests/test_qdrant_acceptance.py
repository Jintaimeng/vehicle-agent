import os

import pytest

from test_graph import REPORT, _alarm, _run
from vehicle_agent.api.main import create_app
from vehicle_agent.config import Settings
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.knowledge.index import QdrantRegulationIndex, packaged_regulation_index
from vehicle_agent.repositories.memory import build_seed_repository

pytestmark = pytest.mark.skipif(
    os.environ.get("VEHICLE_AGENT_RUN_QDRANT") != "1",
    reason="设置 VEHICLE_AGENT_RUN_QDRANT=1 后连接 deployments/docker-compose.yml 中的 Qdrant",
)


class _UnknownAlarmQuery:
    """Read an alarm type that is not in the seeded collection."""

    def __init__(self, index: QdrantRegulationIndex) -> None:
        self._index = index

    def search(self, alarm_type: str):
        assert alarm_type == "driver_fatigue"
        return self._index.search("phone_use")


def test_live_qdrant_matches_markdown_seed() -> None:
    index = QdrantRegulationIndex("http://127.0.0.1:6333")
    try:
        markdown = packaged_regulation_index()
        for alarm_type in ("driver_fatigue", "device_offline", "phone_use"):
            assert index.search(alarm_type) == markdown.search(alarm_type)
        index.ensure_ready()
        assert index.search("driver_fatigue") == markdown.search("driver_fatigue")
    finally:
        index.close()


def test_live_app_completes_high_risk_with_document_evidence(tmp_path) -> None:
    settings = Settings(
        regulation_backend="qdrant",
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

        assert isinstance(app.state.regulation_index, QdrantRegulationIndex)
        assert app.state.tools.get("search_regulations").name == "search_regulations"
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
        assert any(item["source"] == "fatigue-rules" for item in evidence)
    finally:
        app.state.regulation_index.close()


def test_live_vehicle_001_judgement_matches_markdown_excerpts(tmp_path) -> None:
    index = QdrantRegulationIndex("http://127.0.0.1:6333")
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
            regulations=index,
        )
        expected = [
            item.model_dump() for item in packaged_regulation_index().search("driver_fatigue")
        ]
        documents = [item for item in values["result"]["evidence"] if item["type"] == "document"]
        relations = [item for item in values["result"]["evidence"] if item["type"] == "graph"]
        assert values["status"] == "completed"
        assert values["risk_score"] == 1.0
        assert values["regulations"] == expected
        assert documents[0]["source"] == "fatigue-rules"
        assert any(item["source"] == "installed_with:vehicle_001->device_001" for item in relations)
    finally:
        index.close()


def test_live_camera_occlusion_conflicts_with_the_same_speed_score(tmp_path) -> None:
    index = QdrantRegulationIndex("http://127.0.0.1:6333")
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
            regulations=index,
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


def test_live_unknown_alarm_type_requests_review(tmp_path) -> None:
    index = QdrantRegulationIndex("http://127.0.0.1:6333")
    try:
        assert index.search("phone_use") == []
        _, values = _run(tmp_path, _alarm(), regulations=_UnknownAlarmQuery(index))
        assert values["status"] == "awaiting_review"
        assert values["regulation_status"] == "miss"
        assert values["regulations"] == []
        assert "result" not in values
    finally:
        index.close()


def test_live_connection_failure_degrades_without_failing_the_case(tmp_path) -> None:
    index = QdrantRegulationIndex("http://127.0.0.1:1")
    try:
        _, values = _run(
            tmp_path,
            _alarm(),
            regulations=index,
            max_retries=0,
            request_timeout_seconds=8,
        )
    finally:
        index.close()

    assert values["status"] == "awaiting_review"
    assert values["regulation_status"] == "degraded"
    assert values["errors"][-1]["code"] == "tool_failed"
    assert "result" not in values
