import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import BaseModel

from vehicle_agent.config import Settings
from vehicle_agent.harness.errors import (
    BudgetExceededError,
    HarnessTimeoutError,
    OutputValidationError,
    PolicyDeniedError,
    ToolExecutionError,
)
from vehicle_agent.harness.model import FakeModelAdapter, OpenAICompatibleModel
from vehicle_agent.harness.policy import AllowReadOnlyPolicy
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import RegisteredTool, ToolRegistry, build_vehicle_tools
from vehicle_agent.repositories.memory import build_seed_repository


class ReportDraft(BaseModel):
    summary: str
    recommendations: list[str]


def _settings(**overrides) -> Settings:
    values = {
        "max_retries": 1,
        "request_timeout_seconds": 0.2,
        "max_tool_calls": 4,
        "max_model_calls": 2,
        "max_tokens": 100,
    }
    values.update(overrides)
    return Settings(**values)


def _harness(model, settings=None, tools=None) -> Harness:
    return Harness(
        settings or _settings(),
        model,
        tools or build_vehicle_tools(build_seed_repository()),
    )


def test_tool_call_validates_schema_and_records_trace() -> None:
    harness = _harness(FakeModelAdapter())
    result = harness.call_tool(
        "get_vehicle_context",
        {"vehicle_id": "vehicle_001", "driver_id": "driver_001"},
        task_id="task_1",
        node="context",
    )

    assert result.vehicle.plate_number == "粤B12345"
    assert result.driver.name == "张三"
    assert harness.tracer.events[0].status == "ok"
    assert json.loads(harness.tracer.as_json())[0]["name"] == "get_vehicle_context"


def test_recent_alarm_tool_returns_seeded_history() -> None:
    harness = _harness(FakeModelAdapter())
    result = harness.call_tool(
        "list_recent_alarms",
        {
            "vehicle_id": "vehicle_001",
            "as_of": datetime(2026, 10, 9, 10, 30, tzinfo=UTC).isoformat(),
            "alarm_type": "driver_fatigue",
        },
        task_id="task_1",
        node="history",
    )

    assert [alarm.id for alarm in result.alarms] == ["alarm_hist_002", "alarm_hist_001"]


def test_tool_failure_retries_then_raises() -> None:
    calls = {"count": 0}

    class InputModel(BaseModel):
        vehicle_id: str

    class OutputModel(BaseModel):
        ok: bool

    def fail(_: InputModel) -> OutputModel:
        calls["count"] += 1
        raise ConnectionError("database unavailable")

    registry = ToolRegistry()
    registry.register(
        RegisteredTool("flaky", "flaky tool", InputModel, OutputModel, fail)
    )
    harness = _harness(FakeModelAdapter(), tools=registry)

    with pytest.raises(ToolExecutionError):
        harness.call_tool("flaky", {"vehicle_id": "vehicle_001"}, task_id="task_1", node="context")

    assert calls["count"] == 2
    assert harness.tracer.events[-1].error_code == "tool_failed"


def test_write_tool_is_denied_by_default_policy() -> None:
    class InputModel(BaseModel):
        value: str

    class OutputModel(BaseModel):
        value: str

    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            "create_workorder",
            "create a work order",
            InputModel,
            OutputModel,
            lambda payload: payload,
            readonly=False,
        )
    )
    harness = Harness(_settings(), FakeModelAdapter(), registry, policy=AllowReadOnlyPolicy())

    with pytest.raises(PolicyDeniedError):
        harness.call_tool("create_workorder", {"value": "high"}, task_id="task_1", node="report")


def test_invalid_model_output_is_rejected_without_retry() -> None:
    model = FakeModelAdapter("not-json")
    harness = _harness(model)

    with pytest.raises(OutputValidationError):
        harness.complete_structured(
            [{"role": "user", "content": "judge"}],
            ReportDraft,
            task_id="task_1",
            node="report",
        )

    assert model.calls == 1
    assert harness.tracer.events[-1].error_code == "output_invalid"


def test_model_budget_is_enforced_before_the_call() -> None:
    model = FakeModelAdapter()
    harness = _harness(model, settings=_settings(max_model_calls=0))

    with pytest.raises(BudgetExceededError):
        harness.complete_structured(
            [{"role": "user", "content": "judge"}],
            ReportDraft,
            task_id="task_1",
            node="report",
        )

    assert model.calls == 0
    assert harness.tracer.events[-1].error_code == "budget_exceeded"


def test_tool_timeout_is_classified() -> None:
    class InputModel(BaseModel):
        value: str

    class OutputModel(BaseModel):
        value: str

    def slow(_: InputModel) -> OutputModel:
        import time

        time.sleep(0.3)
        return OutputModel(value="late")

    registry = ToolRegistry()
    registry.register(RegisteredTool("slow", "slow tool", InputModel, OutputModel, slow))
    harness = _harness(FakeModelAdapter(), settings=_settings(max_retries=0), tools=registry)

    with pytest.raises(HarnessTimeoutError):
        harness.call_tool("slow", {"value": "x"}, task_id="task_1", node="context")


def test_openai_compatible_adapter_parses_completion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content.decode())
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"summary":"ok","recommendations":["check"]}',
                        }
                    }
                ],
                "usage": {"total_tokens": 11},
            },
        )

    client = httpx.Client(
        base_url="http://model.test/v1/",
        transport=httpx.MockTransport(handler),
    )
    response = OpenAICompatibleModel(_settings(), client=client).complete(
        [{"role": "user", "content": "judge"}],
        model="qwen",
        timeout=1,
    )

    assert response.total_tokens == 11
    assert "summary" in response.content
