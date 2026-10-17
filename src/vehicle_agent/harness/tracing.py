import json
from typing import Literal

from pydantic import BaseModel


class TraceEvent(BaseModel):
    task_id: str
    node: str
    kind: Literal["tool", "model"]
    name: str
    latency_ms: float
    status: Literal["ok", "error"]
    error_code: str | None = None
    tokens: int | None = None


class TraceRecorder:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def record(self, event: TraceEvent) -> None:
        self.events.append(event)

    def as_json(self) -> str:
        return json.dumps([event.model_dump() for event in self.events], ensure_ascii=False)
