import pytest

from test_graph import _alarm, _run
from vehicle_agent.api.main import create_app
from vehicle_agent.config import Settings
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.knowledge.index import (
    REGULATION_COLLECTION,
    REGULATION_VECTOR,
    MarkdownRegulationIndex,
    QdrantRegulationIndex,
    packaged_regulation_entries,
    packaged_regulation_index,
    regulation_query_filter,
)
from vehicle_agent.repositories.memory import build_seed_repository


class _FakeClient:
    def __init__(self) -> None:
        self.points: dict[str, dict] = {}
        self.upsert_ids: list[list[str]] = []
        self.scrolls: list = []
        self.create_calls = 0
        self.exists = False

    def collection_exists(self, name: str) -> bool:
        assert name == REGULATION_COLLECTION
        return self.exists

    def create_collection(self, collection_name: str, vectors_config) -> None:
        assert collection_name == REGULATION_COLLECTION
        assert vectors_config.size == len(REGULATION_VECTOR)
        assert vectors_config.distance.value == "Cosine"
        self.exists = True
        self.create_calls += 1

    def upsert(self, collection_name: str, points) -> None:
        assert collection_name == REGULATION_COLLECTION
        ids = []
        for point in points:
            assert point.vector == REGULATION_VECTOR
            self.points[point.id] = point.payload
            ids.append(point.id)
        self.upsert_ids.append(ids)

    def scroll(self, collection_name: str, scroll_filter, limit: int, offset, **_kwargs):
        assert collection_name == REGULATION_COLLECTION
        assert limit == 64
        self.scrolls.append(scroll_filter)
        alarm_type = scroll_filter.must[0].match.value
        hits = [
            _Point(payload)
            for payload in self.points.values()
            if alarm_type in payload["alarm_types"]
        ]
        return hits, None

    def close(self) -> None:
        return None


class _Point:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


class _FailingScroll(_FakeClient):
    def scroll(self, collection_name: str, scroll_filter, limit: int, offset, **_kwargs):
        raise RuntimeError("query failed")


def _index(client: _FakeClient | None = None) -> tuple[QdrantRegulationIndex, _FakeClient]:
    stored = client or _FakeClient()
    return QdrantRegulationIndex("http://127.0.0.1:6333", client=stored), stored


def test_fake_client_matches_markdown_and_records_the_filter() -> None:
    index, client = _index()
    markdown = packaged_regulation_index()

    for alarm_type in ("driver_fatigue", "device_offline", "phone_use"):
        assert index.search(alarm_type) == markdown.search(alarm_type)

    assert client.scrolls
    assert client.scrolls[0] == regulation_query_filter("driver_fatigue")
    assert client.scrolls[1] == regulation_query_filter("device_offline")
    assert index.search("phone_use") == []


def test_upsert_keeps_the_same_point_ids() -> None:
    index, client = _index()
    index.ensure_ready()
    index._ready = False
    index.ensure_ready()

    assert client.create_calls == 1
    assert len(client.points) == len(packaged_regulation_entries())
    assert client.upsert_ids[0] == client.upsert_ids[1]
    assert client.upsert_ids == [client.upsert_ids[0], client.upsert_ids[0]]


def test_client_errors_propagate() -> None:
    index, _client = _index(_FailingScroll())

    with pytest.raises(RuntimeError, match="query failed"):
        index.search("driver_fatigue")


def test_markdown_backend_keeps_the_markdown_index(tmp_path) -> None:
    app = create_app(
        Settings(
            regulation_backend="markdown",
            checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
        ),
        build_seed_repository(),
        FakeModelAdapter("{}"),
        open_checkpointer(tmp_path / "checkpoints.sqlite"),
    )

    assert isinstance(app.state.regulation_index, MarkdownRegulationIndex)
    assert app.state.tools.get("search_regulations").name == "search_regulations"


def test_qdrant_backend_installs_qdrant_index_without_connecting(tmp_path) -> None:
    app = create_app(
        Settings(
            regulation_backend="qdrant",
            qdrant_url="http://127.0.0.1:1",
            checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
        ),
        build_seed_repository(),
        FakeModelAdapter("{}"),
        open_checkpointer(tmp_path / "checkpoints.sqlite"),
    )

    assert isinstance(app.state.regulation_index, QdrantRegulationIndex)
    assert app.state.regulation_index._client is None
    tool = app.state.tools.get("search_regulations")
    assert tool.name == "search_regulations"
    assert set(tool.input_model.model_fields) == {"alarm_type"}


def test_qdrant_query_failure_degrades_the_judgement(tmp_path) -> None:
    index, _client = _index(_FailingScroll())
    _, values = _run(tmp_path, _alarm(), regulations=index, max_retries=0)

    assert values["status"] == "awaiting_review"
    assert values["regulation_status"] == "degraded"
    assert values["errors"][-1]["code"] == "tool_failed"
    assert "result" not in values
