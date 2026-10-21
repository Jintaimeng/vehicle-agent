from collections.abc import Callable
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from vehicle_agent.config import Settings
from vehicle_agent.harness.errors import HarnessTimeoutError, ModelCallError


class ModelResponse(BaseModel):
    content: str
    total_tokens: int = Field(ge=0)
    model: str


class ModelAdapter(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        timeout: float,
        endpoint: str = "text",
    ) -> ModelResponse: ...


class OpenAICompatibleModel:
    """Calls an OpenAI-compatible /chat/completions endpoint."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        vision_client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or httpx.Client(
            base_url=settings.model_base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.model_api_key}"},
            timeout=settings.request_timeout_seconds,
        )
        self._vision_client = vision_client or httpx.Client(
            base_url=settings.vlm_base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.vlm_api_key}"},
            timeout=settings.request_timeout_seconds,
        )

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        timeout: float,
        endpoint: str = "text",
    ) -> ModelResponse:
        client = self._vision_client if endpoint == "vision" else self._client
        try:
            response = client.post(
                "chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise HarnessTimeoutError("model request timed out") from exc
        except httpx.HTTPError as exc:
            raise ModelCallError("model request failed") from exc

        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
            tokens = int(payload.get("usage", {}).get("total_tokens", 0))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelCallError("model response is missing completion content") from exc
        return ModelResponse(content=content, total_tokens=tokens, model=model)


class FakeModelAdapter:
    """Deterministic model used by tests and the offline demo."""

    def __init__(
        self,
        content: str | Callable[[list[dict[str, Any]]], str] = (
            '{"summary":"ok","recommendations":[]}'
        ),
        *,
        tokens: int = 20,
    ) -> None:
        self._content = content
        self._tokens = tokens
        self.calls = 0

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        timeout: float,
        endpoint: str = "text",
    ) -> ModelResponse:
        del endpoint
        self.calls += 1
        content = self._content(messages) if callable(self._content) else self._content
        return ModelResponse(content=content, total_tokens=self._tokens, model=model)
