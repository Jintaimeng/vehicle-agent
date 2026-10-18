import pytest

from test_graph import _alarm, _run
from vehicle_agent.api.main import create_app
from vehicle_agent.config import Settings
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.knowledge.graph import (
    CONSTRAINT_STATEMENTS,
    VEHICLE_GRAPH_QUERY,
    MemoryGraphIndex,
    Neo4jGraphIndex,
    seed_graph_index,
)
from vehicle_agent.repositories.memory import build_seed_repository


class _RecordingSession:
    def __init__(self, rows_by_query: dict[tuple[str, str], list[dict]]) -> None:
        self.rows_by_query = rows_by_query
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def run(self, query: str, parameters: dict | None = None, **kwargs):
        params = parameters if parameters is not None else kwargs
        self.calls.append((query, params))
        if query == VEHICLE_GRAPH_QUERY:
            key = (params["vehicle_id"], params["device_id"])
            return self.rows_by_query.get(key, [])
        return []


class _RecordingDriver:
    def __init__(self, rows_by_query: dict[tuple[str, str], list[dict]]) -> None:
        self.session_obj = _RecordingSession(rows_by_query)

    def session(self) -> _RecordingSession:
        return self.session_obj


class _FailingRead:
    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False

    def run(self, query: str, parameters: dict | None = None, **_kwargs):
        if query == VEHICLE_GRAPH_QUERY:
            raise RuntimeError("query failed")
        return []


def _rows_for(vehicle_id: str, device_id: str) -> list[dict]:
    return [
        {"relation": fact.relation, "source_id": fact.source_id, "target_id": fact.target_id}
        for fact in seed_graph_index().query(vehicle_id, device_id)
    ]


def _index(rows: dict[tuple[str, str], list[dict]]) -> tuple[Neo4jGraphIndex, _RecordingSession]:
    driver = _RecordingDriver(rows)
    index = Neo4jGraphIndex(
        "bolt://127.0.0.1:7687",
        "neo4j",
        "vehicle-agent-local",
        driver=driver,
    )
    return index, driver.session_obj


def test_fake_driver_maps_rows_to_the_same_facts_as_memory() -> None:
    cases = {
        ("vehicle_001", "device_001"): _rows_for("vehicle_001", "device_001"),
        ("vehicle_003", "device_003"): _rows_for("vehicle_003", "device_003"),
        ("vehicle_missing", "device_001"): [],
        ("vehicle_001", "device_003"): _rows_for("vehicle_001", "device_003"),
    }
    index, session = _index(cases)
    memory = seed_graph_index()

    for vehicle_id, device_id in cases:
        assert index.query(vehicle_id, device_id) == memory.query(vehicle_id, device_id)

    read_calls = [item for item in session.calls if item[0] == VEHICLE_GRAPH_QUERY]
    assert read_calls
    for (vehicle_id, device_id), (_query, params) in zip(cases, read_calls, strict=True):
        assert params == {"vehicle_id": vehicle_id, "device_id": device_id}
    assert "$device_id" in VEHICLE_GRAPH_QUERY
    unrelated = index.query("vehicle_001", "device_003")
    assert all(fact.target_id != "camera_occlusion" for fact in unrelated)


def test_seed_statements_are_idempotent_merge_and_constraints() -> None:
    index, session = _index({})
    index.ensure_ready()
    index._ready = False
    index.ensure_ready()

    statements = [query for query, _params in session.calls]
    assert statements[: len(CONSTRAINT_STATEMENTS)] == list(CONSTRAINT_STATEMENTS)
    assert all("IF NOT EXISTS" in statement for statement in CONSTRAINT_STATEMENTS)
    merges = [statement for statement in statements if statement not in CONSTRAINT_STATEMENTS]
    assert merges
    assert all("MERGE" in statement for statement in merges)
    assert all(not statement.strip().startswith("CREATE (") for statement in statements)
    assert statements == statements[: len(statements) // 2] * 2


def test_driver_errors_propagate() -> None:
    index = Neo4jGraphIndex(
        "bolt://127.0.0.1:7687",
        "neo4j",
        "vehicle-agent-local",
        driver=_FailingRead(),
    )

    with pytest.raises(RuntimeError, match="query failed"):
        index.query("vehicle_001", "device_001")


def test_memory_backend_keeps_the_memory_index(tmp_path) -> None:
    app = create_app(
        Settings(graph_backend="memory", checkpoint_path=str(tmp_path / "checkpoints.sqlite")),
        build_seed_repository(),
        FakeModelAdapter("{}"),
        open_checkpointer(tmp_path / "checkpoints.sqlite"),
    )

    assert isinstance(app.state.graph_index, MemoryGraphIndex)
    assert app.state.tools.get("query_vehicle_graph").name == "query_vehicle_graph"


def test_neo4j_backend_installs_neo4j_index_without_connecting(tmp_path) -> None:
    app = create_app(
        Settings(
            graph_backend="neo4j",
            neo4j_uri="bolt://127.0.0.1:1",
            checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
        ),
        build_seed_repository(),
        FakeModelAdapter("{}"),
        open_checkpointer(tmp_path / "checkpoints.sqlite"),
    )

    assert isinstance(app.state.graph_index, Neo4jGraphIndex)
    assert app.state.graph_index._driver is None
    tool = app.state.tools.get("query_vehicle_graph")
    assert tool.name == "query_vehicle_graph"
    assert set(tool.input_model.model_fields) == {"vehicle_id", "device_id"}


def test_neo4j_query_failure_degrades_the_judgement(tmp_path) -> None:
    index = Neo4jGraphIndex(
        "bolt://127.0.0.1:7687",
        "neo4j",
        "vehicle-agent-local",
        driver=_FailingRead(),
    )
    _, values = _run(tmp_path, _alarm(), graph=index, max_retries=0)

    assert values["status"] == "awaiting_review"
    assert values["graph_status"] == "degraded"
    assert values["errors"][-1]["code"] == "tool_failed"
    assert "result" not in values
