import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from threading import Lock
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from vehicle_agent.config import Settings
from vehicle_agent.harness.errors import (
    BudgetExceededError,
    HarnessError,
    HarnessTimeoutError,
    ModelCallError,
    OutputValidationError,
    ToolExecutionError,
)
from vehicle_agent.harness.model import ModelAdapter
from vehicle_agent.harness.policy import AllowReadOnlyPolicy, ToolPolicy
from vehicle_agent.harness.tools import ToolRegistry
from vehicle_agent.harness.tracing import TraceEvent, TraceRecorder

OutputT = TypeVar("OutputT", bound=BaseModel)


class ExecutionBudget:
    def __init__(self, settings: Settings) -> None:
        self.max_tool_calls = settings.max_tool_calls
        self.max_model_calls = settings.max_model_calls
        self.max_tokens = settings.max_tokens
        self.max_elapsed_seconds = settings.max_elapsed_seconds
        self.tool_calls = 0
        self.model_calls = 0
        self.tokens = 0
        self.started = time.monotonic()

    def ensure_tool_call(self) -> None:
        self._ensure_time()
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceededError("tool call budget exhausted")

    def ensure_model_call(self) -> None:
        self._ensure_time()
        if self.model_calls >= self.max_model_calls:
            raise BudgetExceededError("model call budget exhausted")
        if self.tokens >= self.max_tokens:
            raise BudgetExceededError("token budget exhausted")

    def _ensure_time(self) -> None:
        if time.monotonic() - self.started >= self.max_elapsed_seconds:
            raise BudgetExceededError("elapsed time budget exhausted")


class Harness:
    """Constraints shared by every model and tool call."""

    def __init__(
        self,
        settings: Settings,
        model: ModelAdapter,
        tools: ToolRegistry,
        tracer: TraceRecorder | None = None,
        policy: ToolPolicy | None = None,
        budget: ExecutionBudget | None = None,
    ) -> None:
        self.settings = settings
        self.model = model
        self.tools = tools
        self.tracer = tracer or TraceRecorder()
        self.policy = policy or AllowReadOnlyPolicy()
        self.budget = budget or ExecutionBudget(settings)
        self._custom_budget = budget is not None
        self._task_budgets: dict[str, ExecutionBudget] = {}
        self._budget_lock = Lock()

    def _budget_for(self, task_id: str) -> ExecutionBudget:
        with self._budget_lock:
            if task_id not in self._task_budgets:
                if not self._task_budgets and not self._custom_budget:
                    self.budget.started = time.monotonic()
                self._task_budgets[task_id] = (
                    self.budget if not self._task_budgets else ExecutionBudget(self.settings)
                )
            return self._task_budgets[task_id]

    def call_tool(self, name: str, arguments: dict, *, task_id: str, node: str) -> BaseModel:
        budget = self._budget_for(task_id)
        tool = self.tools.get(name)
        try:
            payload = tool.input_model.model_validate(arguments)
        except ValidationError as exc:
            self._record(task_id, node, "tool", name, 0, "error", "output_invalid")
            raise OutputValidationError("tool arguments do not match schema") from exc
        self.policy.allow(tool, payload)

        def operation() -> BaseModel:
            budget.ensure_tool_call()
            budget.tool_calls += 1
            started = time.perf_counter()
            try:
                result = _run_with_timeout(
                    lambda: tool.handler(payload),
                    self.settings.request_timeout_seconds,
                )
                output = tool.output_model.model_validate(result)
            except HarnessError:
                raise
            except Exception as exc:
                raise ToolExecutionError(str(exc)) from exc
            self._record(
                task_id,
                node,
                "tool",
                name,
                (time.perf_counter() - started) * 1000,
                "ok",
                None,
            )
            return output

        try:
            return self._retry(operation, retryable=_is_retryable)
        except HarnessError as exc:
            self._record(task_id, node, "tool", name, 0, "error", exc.code)
            raise

    def complete_structured(
        self,
        messages: list[dict[str, Any]],
        schema: type[OutputT],
        *,
        task_id: str,
        node: str,
        model_name: str | None = None,
        endpoint: str = "text",
        note_from: Callable[[OutputT], str] | None = None,
    ) -> OutputT:
        used_model = model_name or self.settings.model_name
        budget = self._budget_for(task_id)

        def operation() -> OutputT:
            budget.ensure_model_call()
            budget.model_calls += 1
            started = time.perf_counter()
            response = self.model.complete(
                messages,
                model=used_model,
                timeout=self.settings.request_timeout_seconds,
                endpoint=endpoint,
            )
            budget.tokens += response.total_tokens
            if budget.tokens > budget.max_tokens:
                raise BudgetExceededError("token budget exhausted")
            try:
                parsed = schema.model_validate(_parse_json_object(response.content))
            except (OutputValidationError, ValidationError) as exc:
                raise OutputValidationError("model output does not match schema") from exc
            note = note_from(parsed) if note_from else None
            self._record(
                task_id,
                node,
                "model",
                used_model,
                (time.perf_counter() - started) * 1000,
                "ok",
                None,
                tokens=response.total_tokens,
                note=note,
            )
            return parsed

        try:
            return self._retry(operation, retryable=_is_retryable)
        except HarnessError as exc:
            self._record(
                task_id,
                node,
                "model",
                used_model,
                0,
                "error",
                exc.code,
            )
            raise

    def _retry(self, operation, *, retryable) -> BaseModel:
        attempts = self.settings.max_retries + 1
        for attempt in range(attempts):
            try:
                return operation()
            except HarnessError as exc:
                if attempt == attempts - 1 or not retryable(exc):
                    raise
        raise AssertionError("retry loop exited without a result")

    def _record(
        self,
        task_id: str,
        node: str,
        kind: str,
        name: str,
        latency_ms: float,
        status: str,
        error_code: str | None,
        tokens: int | None = None,
        note: str | None = None,
    ) -> None:
        self.tracer.record(
            TraceEvent(
                task_id=task_id,
                node=node,
                kind=kind,
                name=name,
                latency_ms=latency_ms,
                status=status,
                error_code=error_code,
                tokens=tokens,
                note=note,
            )
        )


def _is_retryable(error: HarnessError) -> bool:
    return isinstance(error, (ToolExecutionError, ModelCallError, HarnessTimeoutError))


def _parse_json_object(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OutputValidationError("model output is not valid JSON") from exc
    if not isinstance(value, dict):
        raise OutputValidationError("model output must be a JSON object")
    return value


def _run_with_timeout(operation, timeout: float):
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(operation)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeoutError as exc:
        raise HarnessTimeoutError("tool execution timed out") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
