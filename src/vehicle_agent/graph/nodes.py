import json
from collections.abc import Callable

from langgraph.types import interrupt
from pydantic import BaseModel, ValidationError

from vehicle_agent.domain.models import Alarm, Driver, HumanReview, JudgementResult, Vehicle
from vehicle_agent.graph.scoring import score_fatigue
from vehicle_agent.graph.state import JudgementState
from vehicle_agent.harness.errors import HarnessError
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.knowledge.index import RegulationSnippet


class ReportDraft(BaseModel):
    summary: str
    recommendations: list[str]


def make_nodes(harness: Harness) -> dict[str, Callable[[JudgementState], dict]]:
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
        scored = score_fatigue(
            alarm,
            vehicle,
            driver,
            history,
            review_confidence_threshold=settings.review_confidence_threshold,
            regulations=regulations,
            regulation_status=state.get("regulation_status", "miss"),
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
        "score": score,
        "review": review,
        "report": report,
    }
