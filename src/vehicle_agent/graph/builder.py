from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from vehicle_agent.graph.nodes import make_nodes
from vehicle_agent.graph.state import JudgementState, Route
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.knowledge.vision import ObservationSource, open_observation
from vehicle_agent.skills.alarm_verification import check_alarm_verification_skill
from vehicle_agent.skills.device_diagnosis import check_device_diagnosis_skill
from vehicle_agent.skills.vehicle_risk_profile import check_vehicle_risk_profile_skill

_NODE_ORDER = [
    "validate",
    "context",
    "history",
    "retrieve",
    "graph",
    "vision",
    "score",
    "review",
    "report",
]


def build_graph(
    harness: Harness,
    checkpointer: SqliteSaver,
    observation: ObservationSource | None = None,
):
    check_alarm_verification_skill(harness.tools, _NODE_ORDER)
    check_device_diagnosis_skill(harness.tools)
    check_vehicle_risk_profile_skill(harness.tools)
    observer = observation if observation is not None else open_observation(harness)
    nodes = make_nodes(harness, observer)
    graph = StateGraph(JudgementState)
    for name in _NODE_ORDER:
        graph.add_node(name, nodes[name])

    graph.add_edge(START, "validate")
    graph.add_conditional_edges("validate", _after_validate, {"context": "context", "end": END})
    graph.add_conditional_edges("context", _after_context, {"history": "history", "end": END})
    graph.add_conditional_edges("history", _after_history, {"retrieve": "retrieve", "end": END})
    graph.add_edge("retrieve", "graph")
    graph.add_edge("graph", "vision")
    graph.add_edge("vision", "score")
    graph.add_conditional_edges("score", _after_score, {"review": "review", "report": "report"})
    graph.add_edge("review", "report")
    graph.add_edge("report", END)
    return graph.compile(checkpointer=checkpointer)


def _after_validate(state: JudgementState) -> Route:
    return "end" if state.get("status") == "failed" else "context"


def _after_context(state: JudgementState) -> Route:
    return "end" if state.get("status") == "failed" else "history"


def _after_history(state: JudgementState) -> Route:
    return "end" if state.get("status") == "failed" else "retrieve"


def _after_score(state: JudgementState) -> Route:
    return "review" if state.get("requires_human_review") else "report"
