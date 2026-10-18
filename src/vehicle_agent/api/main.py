from contextlib import asynccontextmanager
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
from vehicle_agent.knowledge.graph import Neo4jGraphIndex, open_graph_index
from vehicle_agent.knowledge.index import QdrantRegulationIndex, open_regulation_index
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
    graph_index=None,
    regulation_index=None,
) -> FastAPI:
    index = _graph_index(settings) if graph_index is None else graph_index
    regulations = (
        _regulation_index(settings) if regulation_index is None else regulation_index
    )
    tools = build_vehicle_tools(repository, regulations, index)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        _close_index(index)
        _close_index(regulations)

    graph = build_graph(Harness(settings, model, tools), checkpointer)
    app = FastAPI(title="Vehicle Guard AI", version="0.1.0", lifespan=lifespan)
    app.state.graph = graph
    app.state.graph_index = index
    app.state.regulation_index = regulations
    app.state.tools = tools

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
    index = _graph_index(settings)
    regulations = _regulation_index(settings)
    if isinstance(index, Neo4jGraphIndex):
        index.ensure_ready()
    if isinstance(regulations, QdrantRegulationIndex):
        regulations.ensure_ready()
    return create_app(
        settings,
        build_seed_repository(),
        OpenAICompatibleModel(settings),
        open_checkpointer(settings.checkpoint_path),
        graph_index=index,
        regulation_index=regulations,
    )


def _graph_index(settings: Settings):
    return open_graph_index(
        settings.graph_backend,
        uri=settings.neo4j_uri,
        user=settings.neo4j_user,
        password=settings.neo4j_password,
    )


def _regulation_index(settings: Settings):
    return open_regulation_index(settings.regulation_backend, url=settings.qdrant_url)


def _close_index(index) -> None:
    close = getattr(index, "close", None)
    if callable(close):
        close()


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
