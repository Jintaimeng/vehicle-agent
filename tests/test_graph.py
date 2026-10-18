import json
from datetime import UTC, datetime

from langgraph.types import Command

from vehicle_agent.config import Settings
from vehicle_agent.domain.models import Alarm
from vehicle_agent.graph.builder import build_graph
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import FakeModelAdapter
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import build_vehicle_tools
from vehicle_agent.repositories.memory import build_seed_repository

REPORT = json.dumps(
    {
        "summary": "根据车速、历史报警和车辆资料完成研判。",
        "recommendations": ["通知企业安全管理员复核"],
    },
    ensure_ascii=False,
)


def _settings(**overrides) -> Settings:
    values = {"max_retries": 1, "checkpoint_path": "data/test-checkpoints.sqlite"}
    values.update(overrides)
    return Settings(**values)


def _alarm(**overrides) -> Alarm:
    payload = {
        "id": "alarm_current",
        "vehicle_id": "vehicle_002",
        "driver_id": "driver_002",
        "device_id": "device_002",
        "alarm_type": "driver_fatigue",
        "alarm_time": datetime(2026, 10, 9, 10, 30, tzinfo=UTC),
        "longitude": 113.26,
        "latitude": 23.12,
        "speed_kmh": 40,
        "raw_confidence": 0.8,
    }
    payload.update(overrides)
    return Alarm(**payload)


def _run(
    tmp_path,
    alarm: Alarm,
    *,
    content: str = REPORT,
    regulations=None,
    graph=None,
    **settings,
):
    harness = Harness(
        _settings(**settings),
        FakeModelAdapter(content),
        build_vehicle_tools(build_seed_repository(), regulations, graph),
    )
    graph = build_graph(harness, open_checkpointer(tmp_path / "checkpoints.sqlite"))
    config = {"configurable": {"thread_id": alarm.id}}
    graph.invoke(
        {"task_id": alarm.id, "alarm": alarm.model_dump(mode="json"), "errors": []},
        config,
    )
    return graph, graph.get_state(config).values


def test_normal_alarm_completes_without_review(tmp_path) -> None:
    _, values = _run(tmp_path, _alarm())

    assert values["status"] == "completed"
    assert values["result"]["risk_level"] == "medium"
    assert values["result"]["requires_human_review"] is False
    assert values["result"]["summary"].startswith("根据车速")


def test_high_risk_alarm_is_scored_by_rules(tmp_path) -> None:
    _, values = _run(
        tmp_path,
        _alarm(vehicle_id="vehicle_001", driver_id="driver_001", speed_kmh=85, raw_confidence=0.9),
    )

    assert values["status"] == "completed"
    assert values["result"]["risk_level"] == "high"
    assert values["result"]["alarm_validity"] == "likely_true"
    assert values["result"]["requires_human_review"] is False
    assert values["risk_score"] == 1.0


def test_missing_vehicle_waits_for_human_review(tmp_path) -> None:
    graph, values = _run(tmp_path, _alarm(vehicle_id="vehicle_missing"))

    assert values["status"] == "awaiting_review"
    assert values["alarm_validity"] == "unknown"
    assert graph.get_state({"configurable": {"thread_id": "alarm_current"}}).next == ("review",)


def test_tool_failure_stops_the_workflow(tmp_path) -> None:
    _, values = _run(tmp_path, _alarm(vehicle_id="vehicle_error"))

    assert values["status"] == "failed"
    assert values["errors"][-1]["code"] == "tool_failed"
    assert "result" not in values


def test_invalid_model_output_is_recorded(tmp_path) -> None:
    _, values = _run(tmp_path, _alarm(), content="not-json")

    assert values["status"] == "failed"
    assert values["errors"][-1]["code"] == "output_invalid"


def test_model_budget_exhaustion_is_recorded(tmp_path) -> None:
    _, values = _run(tmp_path, _alarm(), max_model_calls=0)

    assert values["status"] == "failed"
    assert values["errors"][-1]["code"] == "budget_exceeded"


def test_checkpoint_resume_continues_after_review(tmp_path) -> None:
    settings = _settings()
    repository_tools = build_vehicle_tools(build_seed_repository())
    first = Harness(settings, FakeModelAdapter(REPORT), repository_tools)
    checkpointer = open_checkpointer(tmp_path / "checkpoints.sqlite")
    graph = build_graph(first, checkpointer)
    alarm = _alarm(vehicle_id="vehicle_missing")
    config = {"configurable": {"thread_id": alarm.id}}
    graph.invoke(
        {"task_id": alarm.id, "alarm": alarm.model_dump(mode="json"), "errors": []},
        config,
    )

    restored = build_graph(
        Harness(settings, FakeModelAdapter(REPORT), build_vehicle_tools(build_seed_repository())),
        checkpointer,
    )
    assert restored.get_state(config).next == ("review",)

    restored.invoke(
        Command(resume={"reviewer": "ops", "decision": "confirm", "comment": "证据不足，人工确认"}),
        config,
    )
    values = restored.get_state(config).values

    assert values["status"] == "completed"
    assert values["human_review"]["decision"] == "confirm"
    assert values["result"]["requires_human_review"] is True


class _EmptyRegulations:
    def search(self, alarm_type: str) -> list:
        return []


class _FailingRegulations:
    def search(self, alarm_type: str) -> list:
        raise RuntimeError("regulation index unavailable")


def test_regulation_hit_adds_document_evidence(tmp_path) -> None:
    _, values = _run(
        tmp_path,
        _alarm(vehicle_id="vehicle_001", driver_id="driver_001", speed_kmh=85, raw_confidence=0.9),
    )

    documents = [item for item in values["result"]["evidence"] if item["type"] == "document"]
    assert values["status"] == "completed"
    assert values["regulation_status"] == "hit"
    assert values["risk_score"] == 1.0
    assert documents[0]["source"] == "fatigue-rules"
    assert "第2节" in documents[0]["description"]


def test_regulation_miss_requests_review(tmp_path) -> None:
    _, values = _run(tmp_path, _alarm(), regulations=_EmptyRegulations())

    assert values["status"] == "awaiting_review"
    assert values["regulation_status"] == "miss"
    assert values["requires_human_review"] is True
    assert values["risk_level"] == "medium"
    assert any(
        "缺少与该报警类型匹配的法规依据" in item["description"] for item in values["evidence"]
    )
    assert "result" not in values


def test_regulation_failure_degrades_to_review(tmp_path) -> None:
    _, values = _run(tmp_path, _alarm(), regulations=_FailingRegulations())

    assert values["status"] == "awaiting_review"
    assert values["regulation_status"] == "degraded"
    assert values["errors"][-1]["code"] == "tool_failed"
    assert any("法规检索不可用" in item["description"] for item in values["evidence"])
    assert "result" not in values


def test_regulation_budget_exhaustion_degrades_to_review(tmp_path) -> None:
    _, values = _run(tmp_path, _alarm(), max_tool_calls=2)

    assert values["status"] == "awaiting_review"
    assert values["regulation_status"] == "degraded"
    assert values["errors"][-1]["code"] == "budget_exceeded"
    assert "result" not in values


class _EmptyGraph:
    def query(self, vehicle_id: str, device_id: str) -> list:
        return []


class _FailingGraph:
    def query(self, vehicle_id: str, device_id: str) -> list:
        raise RuntimeError("vehicle graph unavailable")


def test_graph_hit_adds_relationship_evidence(tmp_path) -> None:
    _, values = _run(
        tmp_path,
        _alarm(vehicle_id="vehicle_001", driver_id="driver_001", speed_kmh=85, raw_confidence=0.9),
    )

    documents = [item for item in values["result"]["evidence"] if item["type"] == "document"]
    relations = [item for item in values["result"]["evidence"] if item["type"] == "graph"]
    assert values["status"] == "completed"
    assert values["graph_status"] == "hit"
    assert values["risk_score"] == 1.0
    assert documents[0]["source"] == "fatigue-rules"
    assert any(item["source"] == "drives:vehicle_001->driver_001" for item in relations)
    assert any(item["source"] == "installed_with:vehicle_001->device_001" for item in relations)


def test_graph_miss_requests_review(tmp_path) -> None:
    _, values = _run(tmp_path, _alarm(), graph=_EmptyGraph())

    assert values["status"] == "awaiting_review"
    assert values["graph_status"] == "miss"
    assert values["regulation_status"] == "hit"
    assert values["requires_human_review"] is True
    assert values["risk_score"] == 0.45
    assert values["risk_level"] == "medium"
    assert any("缺少与该车辆匹配的图谱关系" in item["description"] for item in values["evidence"])
    assert "result" not in values


def test_graph_failure_degrades_to_review(tmp_path) -> None:
    _, values = _run(tmp_path, _alarm(), graph=_FailingGraph())

    assert values["status"] == "awaiting_review"
    assert values["graph_status"] == "degraded"
    assert values["errors"][-1]["code"] == "tool_failed"
    assert any("图谱查询不可用" in item["description"] for item in values["evidence"])
    assert "result" not in values


def test_graph_budget_exhaustion_degrades_to_review(tmp_path) -> None:
    _, values = _run(tmp_path, _alarm(), max_tool_calls=3)

    assert values["status"] == "awaiting_review"
    assert values["regulation_status"] == "hit"
    assert values["graph_status"] == "degraded"
    assert values["errors"][-1]["code"] == "budget_exceeded"
    assert "result" not in values


def test_device_fault_conflicts_with_fatigue_rule(tmp_path) -> None:
    _, values = _run(
        tmp_path,
        _alarm(
            vehicle_id="vehicle_003",
            driver_id="driver_003",
            device_id="device_003",
            speed_kmh=85,
            raw_confidence=0.9,
        ),
    )

    assert values["status"] == "awaiting_review"
    assert values["graph_status"] == "hit"
    assert values["regulation_status"] == "hit"
    assert values["requires_human_review"] is True
    assert values["risk_score"] == 0.8
    documents = [item for item in values["evidence"] if item["type"] == "document"]
    assert documents[0]["source"] == "fatigue-rules"
    assert any(
        item["type"] == "graph" and item["source"] == "has_fault:device_003->camera_occlusion"
        for item in values["evidence"]
    )
    assert any("证据冲突" in item["description"] for item in values["evidence"])
    assert "result" not in values
