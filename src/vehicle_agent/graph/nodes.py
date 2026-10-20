import json
from collections.abc import Callable

from langgraph.types import interrupt
from pydantic import BaseModel, ValidationError

from vehicle_agent.domain.models import (
    Alarm,
    Detection,
    Driver,
    HumanReview,
    JudgementResult,
    Vehicle,
)
from vehicle_agent.graph.scoring import score_fatigue
from vehicle_agent.graph.state import JudgementState
from vehicle_agent.harness.errors import HarnessError
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.knowledge.graph import GraphFact
from vehicle_agent.knowledge.index import RegulationSnippet
from vehicle_agent.knowledge.vision import VisionIndex


class ReportDraft(BaseModel):
    summary: str
    recommendations: list[str]


class VisionReview(BaseModel):
    visible_facts: list[str]


def make_nodes(
    harness: Harness, vision_index: VisionIndex
) -> dict[str, Callable[[JudgementState], dict]]:
    settings = harness.settings

    def traced(update: dict) -> dict:
        update["trace"] = [event.model_dump() for event in harness.tracer.events]
        return update

    def failed(state: JudgementState, code: str, message: str) -> dict:
        errors = [*state.get("errors", []), {"code": code, "message": message}]
        return traced({"status": "failed", "errors": errors})

    def validate(state: JudgementState) -> dict:
        try:
            alarm = Alarm.model_validate(state["alarm"])
        except ValidationError as exc:
            return failed(state, "invalid_alarm", str(exc))
        return traced({"status": "running", "alarm": alarm.model_dump(mode="json")})

    def load_context(state: JudgementState) -> dict:
        alarm = Alarm.model_validate(state["alarm"])
        try:
            context = harness.call_tool(
                "get_vehicle_context",
                {"vehicle_id": alarm.vehicle_id, "driver_id": alarm.driver_id},
                task_id=state["task_id"],
                node="context",
            )
        except HarnessError as exc:
            return failed(state, exc.code, str(exc))
        vehicle = context.vehicle.model_dump() if context.vehicle else None
        driver = context.driver.model_dump() if context.driver else None
        return traced({"vehicle_context": vehicle, "driver_context": driver})

    def load_history(state: JudgementState) -> dict:
        alarm = Alarm.model_validate(state["alarm"])
        try:
            history = harness.call_tool(
                "list_recent_alarms",
                {
                    "vehicle_id": alarm.vehicle_id,
                    "as_of": alarm.alarm_time.isoformat(),
                    "within_days": 7,
                    "alarm_type": alarm.alarm_type,
                },
                task_id=state["task_id"],
                node="history",
            )
        except HarnessError as exc:
            return failed(state, exc.code, str(exc))
        return traced({"history": [item.model_dump(mode="json") for item in history.alarms]})

    def retrieve(state: JudgementState) -> dict:
        alarm = Alarm.model_validate(state["alarm"])
        try:
            found = harness.call_tool(
                "search_regulations",
                {"alarm_type": alarm.alarm_type},
                task_id=state["task_id"],
                node="retrieve",
            )
        except HarnessError as exc:
            errors = [*state.get("errors", []), {"code": exc.code, "message": str(exc)}]
            return traced({"regulations": [], "regulation_status": "degraded", "errors": errors})
        snippets = [item.model_dump() for item in found.snippets]
        status = "hit" if snippets else "miss"
        return traced({"regulations": snippets, "regulation_status": status})

    def query_graph(state: JudgementState) -> dict:
        alarm = Alarm.model_validate(state["alarm"])
        try:
            found = harness.call_tool(
                "query_vehicle_graph",
                {"vehicle_id": alarm.vehicle_id, "device_id": alarm.device_id},
                task_id=state["task_id"],
                node="graph",
            )
        except HarnessError as exc:
            errors = [*state.get("errors", []), {"code": exc.code, "message": str(exc)}]
            return traced({"graph_facts": [], "graph_status": "degraded", "errors": errors})
        facts = [item.model_dump() for item in found.facts]
        status = "hit" if facts else "miss"
        return traced({"graph_facts": facts, "graph_status": status})

    def analyze_vision(state: JudgementState) -> dict:
        alarm = Alarm.model_validate(state["alarm"])
        if not alarm.image_id:
            return traced(
                {
                    "detections": [],
                    "vision_status": "skipped",
                    "vlm_status": "skipped",
                    "visible_facts": [],
                }
            )
        try:
            detections = vision_index.analyze(alarm.image_id)
        except Exception as exc:
            errors = [*state.get("errors", []), {"code": "vision_unavailable", "message": str(exc)}]
            return traced(
                {
                    "detections": [],
                    "vision_status": "degraded",
                    "vlm_status": "skipped",
                    "visible_facts": [],
                    "errors": errors,
                }
            )
        payload = [item.model_dump() for item in detections]
        if not detections:
            return traced(
                {
                    "detections": [],
                    "vision_status": "miss",
                    "vlm_status": "skipped",
                    "visible_facts": [],
                }
            )
        try:
            review = harness.complete_structured(
                _vision_messages(alarm.image_id, payload),
                VisionReview,
                task_id=state["task_id"],
                node="vision",
            )
        except HarnessError as exc:
            errors = [*state.get("errors", []), {"code": exc.code, "message": str(exc)}]
            return traced(
                {
                    "detections": payload,
                    "vision_status": "hit",
                    "vlm_status": "degraded",
                    "visible_facts": [],
                    "errors": errors,
                }
            )
        return traced(
            {
                "detections": payload,
                "vision_status": "hit",
                "vlm_status": "hit",
                "visible_facts": review.visible_facts,
            }
        )

    def score(state: JudgementState) -> dict:
        alarm = Alarm.model_validate(state["alarm"])
        vehicle = None
        if state.get("vehicle_context"):
            vehicle = Vehicle.model_validate(state["vehicle_context"])
        driver = None
        if state.get("driver_context"):
            driver = Driver.model_validate(state["driver_context"])
        history = [Alarm.model_validate(item) for item in state.get("history", [])]
        regulations = [
            RegulationSnippet.model_validate(item) for item in state.get("regulations", [])
        ]
        graph_facts = [GraphFact.model_validate(item) for item in state.get("graph_facts", [])]
        detections = [Detection.model_validate(item) for item in state.get("detections", [])]
        scored = score_fatigue(
            alarm,
            vehicle,
            driver,
            history,
            review_confidence_threshold=settings.review_confidence_threshold,
            regulations=regulations,
            regulation_status=state.get("regulation_status", "miss"),
            graph_facts=graph_facts,
            graph_status=state.get("graph_status", "miss"),
            detections=detections,
            vision_status=state.get("vision_status", "skipped"),
            vlm_status=state.get("vlm_status", "skipped"),
            visible_facts=state.get("visible_facts", []),
        )
        status = "awaiting_review" if scored["requires_human_review"] else "ready_to_report"
        return traced({**scored, "status": status})

    def review(state: JudgementState) -> dict:
        decision = interrupt(
            {
                "task_id": state["task_id"],
                "reason": "low_confidence",
                "confidence": state.get("confidence"),
            }
        )
        try:
            parsed = HumanReview.model_validate(decision)
        except ValidationError as exc:
            return failed(state, "invalid_review", str(exc))
        return traced({"human_review": parsed.model_dump(), "status": "reviewed"})

    def report(state: JudgementState) -> dict:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是车辆监控研判助手。只根据给定证据编写 summary 和 recommendations。"
                    "输出 JSON 对象，字段为 summary 和 recommendations。"
                    "不要编造未提供的证据。没有 document 证据时，不要引用法规条款。"
                    "没有 graph 证据时，不要编造车辆、驾驶员、设备或故障关系。"
                    "没有 vision 证据时，不要编造检测框或画面事实。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "alarm": state.get("alarm"),
                        "risk_level": state.get("risk_level"),
                        "alarm_validity": state.get("alarm_validity"),
                        "confidence": state.get("confidence"),
                        "evidence": state.get("evidence", []),
                        "regulations": state.get("regulations", []),
                        "graph_facts": state.get("graph_facts", []),
                        "detections": state.get("detections", []),
                        "visible_facts": state.get("visible_facts", []),
                        "human_review": state.get("human_review"),
                        "prompt_version": settings.prompt_version,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            draft = harness.complete_structured(
                messages,
                ReportDraft,
                task_id=state["task_id"],
                node="report",
            )
        except HarnessError as exc:
            return failed(state, exc.code, str(exc))

        result = JudgementResult(
            alarm_validity=state["alarm_validity"],
            risk_level=state["risk_level"],
            confidence=state["confidence"],
            risk_score=state["risk_score"],
            summary=draft.summary,
            evidence=state.get("evidence", []),
            recommendations=draft.recommendations,
            requires_human_review=state["requires_human_review"],
        )
        return traced({"result": result.model_dump(), "status": "completed"})

    return {
        "validate": validate,
        "context": load_context,
        "history": load_history,
        "retrieve": retrieve,
        "graph": query_graph,
        "vision": analyze_vision,
        "score": score,
        "review": review,
        "report": report,
    }


def _vision_messages(image_id: str, detections: list[dict]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是画面复核助手。只根据给定检测框描述可见事实。"
                "输出 JSON 对象，字段为 visible_facts。"
                "不要推测身份，不要修改检测类别、置信度或风险分。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"image_id": image_id, "detections": detections},
                ensure_ascii=False,
            ),
        },
    ]
