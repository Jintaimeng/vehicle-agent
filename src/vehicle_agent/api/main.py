from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from langgraph.types import Command
from pydantic import BaseModel

from vehicle_agent.config import Settings
from vehicle_agent.domain.models import Alarm, HumanReview, JudgementResult
from vehicle_agent.graph.builder import build_graph
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import ModelAdapter, OpenAICompatibleModel
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.harness.tools import build_vehicle_tools
from vehicle_agent.repositories.base import AlarmRepository
from vehicle_agent.repositories.memory import build_seed_repository


class TaskView(BaseModel):
    task_id: str
    status: str
    result: JudgementResult | None = None
    errors: list[dict] = []
    human_review: HumanReview | None = None
    interrupts: list[dict] = []


def create_app(
    settings: Settings,
    repository: AlarmRepository,
    model: ModelAdapter,
    checkpointer,
) -> FastAPI:
    graph = build_graph(Harness(settings, model, build_vehicle_tools(repository)), checkpointer)
    app = FastAPI(title="Vehicle Guard AI", version="0.1.0")
    app.state.graph = graph

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            body = exc.detail
        else:
            body = {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.post("/api/v1/judgements", response_model=TaskView)
    def create_judgement(alarm: Alarm) -> TaskView:
        task_id = f"task_{uuid4().hex}"
        graph.invoke(_initial_state(task_id, alarm), _config(task_id))
        return _view(graph, task_id)

    @app.get("/api/v1/judgements/{task_id}", response_model=TaskView)
    def get_judgement(task_id: str) -> TaskView:
        return _require(graph, task_id)

    @app.post("/api/v1/judgements/{task_id}/review", response_model=TaskView)
    def submit_review(task_id: str, review: HumanReview) -> TaskView:
        snapshot = _require_snapshot(graph, task_id)
        if "review" not in snapshot.next:
            raise HTTPException(
                status_code=409,
                detail={"code": "review_not_allowed", "message": "task is not waiting for review"},
            )
        graph.invoke(Command(resume=review.model_dump()), _config(task_id))
        return _view(graph, task_id)

    return app


def build_default_app() -> FastAPI:
    settings = Settings()
    return create_app(
        settings,
        build_seed_repository(),
        OpenAICompatibleModel(settings),
        open_checkpointer(settings.checkpoint_path),
    )


def main() -> None:
    import uvicorn

    uvicorn.run(build_default_app(), host="127.0.0.1", port=8000)


def _config(task_id: str) -> dict:
    return {"configurable": {"thread_id": task_id}}


def _initial_state(task_id: str, alarm: Alarm) -> dict:
    return {"task_id": task_id, "alarm": alarm.model_dump(mode="json"), "errors": []}


def _require(graph, task_id: str) -> TaskView:
    _require_snapshot(graph, task_id)
    return _view(graph, task_id)


def _require_snapshot(graph, task_id: str):
    snapshot = graph.get_state(_config(task_id))
    if not snapshot.values:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "task not found"},
        )
    return snapshot


def _view(graph, task_id: str) -> TaskView:
    snapshot = graph.get_state(_config(task_id))
    values = snapshot.values
    interrupts = []
    for item in snapshot.interrupts:
        value = getattr(item, "value", item)
        if isinstance(value, dict):
            interrupts.append(value)
    return TaskView(
        task_id=task_id,
        status=values.get("status", "unknown"),
        result=values.get("result"),
        errors=values.get("errors", []),
        human_review=values.get("human_review"),
        interrupts=interrupts,
    )


if __name__ == "__main__":
    main()
