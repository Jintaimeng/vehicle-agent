import hmac
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from vehicle_agent.config import Settings
from vehicle_agent.domain.models import Alarm, Detection, HumanReview, JudgementResult
from vehicle_agent.graph.builder import build_graph
from vehicle_agent.graph.checkpoint import open_checkpointer
from vehicle_agent.harness.model import (
    FIXTURE_REPORT,
    FakeModelAdapter,
    ModelAdapter,
    OpenAICompatibleModel,
)
from vehicle_agent.harness.policy import AllowApprovedWritesPolicy
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.ingest import (
    JudgementCompleted,
    MemoryAlarmTopic,
    alarm_event_from_payload,
    consume_vehicle_alarms,
    publish_judgement_completed,
)
from vehicle_agent.knowledge.graph import Neo4jGraphIndex, open_graph_index
from vehicle_agent.knowledge.index import QdrantRegulationIndex, open_regulation_index
from vehicle_agent.knowledge.video import open_video_timeline
from vehicle_agent.knowledge.vision import open_vision_index, store_image, store_video
from vehicle_agent.mcp.client import open_registered_tools
from vehicle_agent.repositories.base import AlarmRepository
from vehicle_agent.repositories.memory import build_seed_repository
from vehicle_agent.workorders import (
    CreateWorkOrderInput,
    SqliteWorkOrderStore,
    WorkOrder,
    workorder_sqlite_path,
)


class VisionImageView(BaseModel):
    image_id: str
    detections: list[Detection]


class VisionVideoView(BaseModel):
    video_id: str
    frame_timestamps: list[float]
    event_formed: bool
    keyframe_timestamp_s: float | None = None


class WorkOrderPatch(BaseModel):
    status: str = Field(min_length=1)
    comment: str = ""


class TaskView(BaseModel):
    task_id: str
    status: str
    result: JudgementResult | None = None
    errors: list[dict] = []
    human_review: HumanReview | None = None
    workorder_id: str | None = None
    interrupts: list[dict] = []


def create_app(
    settings: Settings,
    repository: AlarmRepository,
    model: ModelAdapter,
    checkpointer,
    graph_index=None,
    regulation_index=None,
    vision_index=None,
    video_timeline=None,
) -> FastAPI:
    index = _graph_index(settings) if graph_index is None else graph_index
    regulations = _regulation_index(settings) if regulation_index is None else regulation_index
    vision = (
        open_vision_index(settings.vision_backend, weights=settings.yolo_weights)
        if vision_index is None
        else vision_index
    )
    timeline = (
        open_video_timeline(settings.video_backend, vision, Path(settings.vision_store_dir))
        if video_timeline is None
        else video_timeline
    )
    orders = SqliteWorkOrderStore(workorder_sqlite_path(settings.checkpoint_path))
    tools, mcp_session = open_registered_tools(
        settings, repository, regulations, index, vision, timeline, workorders=orders
    )
    ingest_ready = threading.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stop = threading.Event()
        worker: threading.Thread | None = None
        if settings.ingest == "kafka":
            worker = threading.Thread(
                target=_serve_alarm_ingest,
                args=(settings.kafka_bootstrap, graph, stop, ingest_ready),
                name="vehicle-alarm-ingest",
                daemon=True,
            )
            worker.start()
        try:
            yield
        finally:
            stop.set()
            if worker is not None:
                worker.join(timeout=2)
            if mcp_session is not None:
                mcp_session.close()
            orders.close()
            _close_index(index)
            _close_index(regulations)

    graph = build_graph(
        Harness(settings, model, tools, policy=AllowApprovedWritesPolicy()),
        checkpointer,
    )
    app = FastAPI(title="Vehicle Guard AI", version="0.1.0", lifespan=lifespan)
    app.state.vision_index = vision
    app.state.video_timeline = timeline
    app.state.graph = graph
    app.state.graph_index = index
    app.state.regulation_index = regulations
    app.state.tools = tools
    app.state.mcp_session = mcp_session
    app.state.ingest_ready = ingest_ready
    app.state.model_probe_client = None

    @app.middleware("http")
    async def require_api_token(request: Request, call_next):
        if settings.api_token and request.url.path.startswith("/api/"):
            expected = f"Bearer {settings.api_token}"
            provided = request.headers.get("authorization", "")
            if not hmac.compare_digest(provided, expected):
                return JSONResponse(
                    {"code": "unauthorized", "message": "unauthorized"},
                    status_code=401,
                )
        return await call_next(request)

    @app.get("/health", response_model=None)
    def health() -> JSONResponse:
        if settings.ingest == "kafka" and not ingest_ready.is_set():
            return JSONResponse({"status": "starting"}, status_code=503)
        if settings.model_backend == "openai" and not _text_model_ready(
            settings, app.state.model_probe_client
        ):
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            body = exc.detail
        else:
            body = {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.post("/api/v1/vision/images", response_model=VisionImageView)
    async def analyze_image(
        image_id: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
    ) -> VisionImageView:
        content = await file.read()
        try:
            store_image(Path(settings.vision_store_dir), image_id, content)
            detections = vision.analyze(image_id, content)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "vision_unavailable", "message": str(exc)},
            ) from exc
        return VisionImageView(image_id=image_id, detections=detections)

    @app.post("/api/v1/vision/videos", response_model=VisionVideoView)
    async def analyze_video(
        video_id: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
        speed_kmh: Annotated[float, Form()] = 85,
    ) -> VisionVideoView:
        content = await file.read()
        try:
            store_video(Path(settings.vision_store_dir), video_id, content)
            outcome = timeline.analyze(video_id, speed_kmh)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "vision_unavailable", "message": str(exc)},
            ) from exc
        keyframe = outcome.event.keyframe_timestamp_s if outcome.event is not None else None
        return VisionVideoView(
            video_id=video_id,
            frame_timestamps=[frame.timestamp_s for frame in outcome.frames],
            event_formed=outcome.event is not None,
            keyframe_timestamp_s=keyframe,
        )

    @app.post("/api/v1/workorders", response_model=WorkOrder)
    def create_workorder(payload: CreateWorkOrderInput) -> WorkOrder:
        try:
            return orders.create(
                task_id=payload.task_id,
                alarm_id=payload.alarm_id,
                risk_level=payload.risk_level,
                summary=payload.summary,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_workorder", "message": str(exc)},
            ) from exc

    @app.get("/api/v1/workorders/{workorder_id}", response_model=WorkOrder)
    def get_workorder(workorder_id: str) -> WorkOrder:
        try:
            return orders.get(workorder_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": str(exc)},
            ) from exc

    @app.patch("/api/v1/workorders/{workorder_id}", response_model=WorkOrder)
    def update_workorder(workorder_id: str, payload: WorkOrderPatch) -> WorkOrder:
        try:
            return orders.update(workorder_id, payload.status, payload.comment)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": str(exc)},
            ) from exc

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
        open_model(settings),
        open_checkpointer(settings.checkpoint_path),
        graph_index=index,
        regulation_index=regulations,
    )


def _text_model_ready(settings: Settings, client: httpx.Client | None = None) -> bool:
    """GET /models. A non-200 response or a transport error means the model is down."""

    owns_client = client is None
    http = client or httpx.Client(
        base_url=settings.model_base_url.rstrip("/") + "/",
        headers={"Authorization": f"Bearer {settings.model_api_key}"},
        timeout=2,
    )
    try:
        response = http.get("models")
        return response.status_code == 200
    except httpx.HTTPError:
        return False
    finally:
        if owns_client:
            http.close()


def open_model(settings: Settings) -> ModelAdapter:
    """Use the fixed demo summary offline, or the OpenAI-compatible endpoint."""

    if settings.model_backend == "fixture":
        return FakeModelAdapter(FIXTURE_REPORT)
    return OpenAICompatibleModel(settings)


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

    settings = Settings()
    uvicorn.run(build_default_app(), host=settings.host, port=settings.port)


def _config(task_id: str) -> dict:
    return {"configurable": {"thread_id": task_id}}


def judge_consumed_alarm(graph, alarm: Alarm, event_id: str, send=None) -> str:
    """Invoke the current graph once. An existing checkpoint for this event is kept."""

    if graph.get_state(_config(event_id)).values:
        return event_id
    graph.invoke(_initial_state(event_id, alarm), _config(event_id))
    if send is not None:
        values = graph.get_state(_config(event_id)).values
        result = values["result"]
        publish_judgement_completed(
            JudgementCompleted(
                event_id=event_id,
                task_id=values["task_id"],
                risk_score=result["risk_score"],
                requires_human_review=result["requires_human_review"],
                workorder_id=values.get("workorder_id"),
            ),
            send,
        )
    return event_id


def ingest_alarm_payload(
    payload: bytes,
    graph,
    topic: MemoryAlarmTopic,
    judged: dict[str, str],
    send=None,
) -> str:
    """Parse one vehicle.alarm record and judge it unless that event was already seen."""

    event = alarm_event_from_payload(payload)

    def judge(alarm: Alarm) -> str:
        return judge_consumed_alarm(graph, alarm, event.event_id, send)

    return consume_vehicle_alarms([payload], judge, topic, judged)


def handle_alarm_payload(
    payload: bytes,
    graph,
    topic: MemoryAlarmTopic,
    judged: dict[str, str],
    send=None,
) -> str | None:
    """Skip a bad record. The caller still commits the offset."""

    try:
        return ingest_alarm_payload(payload, graph, topic, judged, send)
    except ValueError:
        return None


_INGEST_RETRY_SECONDS = 1.0


def _serve_alarm_ingest(
    bootstrap: str,
    graph,
    stop: threading.Event,
    ready: threading.Event,
) -> None:
    """Keep polling vehicle.alarm. A broker that is not up yet is tried again."""

    from vehicle_agent.kafka_alarm import produce_judgement_completed, serve_vehicle_alarms

    topic = MemoryAlarmTopic()
    judged: dict[str, str] = {}

    def send(completed_topic: str, payload: bytes) -> None:
        produce_judgement_completed(bootstrap, completed_topic, payload)

    def handle(payload: bytes) -> None:
        handle_alarm_payload(payload, graph, topic, judged, send)

    while not stop.is_set():
        ready.clear()
        try:
            serve_vehicle_alarms(bootstrap, handle, stop.is_set, on_ready=ready.set)
        except Exception:
            ready.clear()
            if stop.wait(_INGEST_RETRY_SECONDS):
                return
            continue
        return


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
        workorder_id=values.get("workorder_id"),
        interrupts=interrupts,
    )


if __name__ == "__main__":
    main()
