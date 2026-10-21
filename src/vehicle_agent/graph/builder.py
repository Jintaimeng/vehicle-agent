from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from vehicle_agent.graph.nodes import make_nodes
from vehicle_agent.graph.state import JudgementState, Route
from vehicle_agent.harness.runtime import Harness
from vehicle_agent.knowledge.video import VideoTimeline, packaged_video_timeline
from vehicle_agent.knowledge.vision import (
    ObservationSource,
    VisionIndex,
    open_observation,
    open_vision_index,
)


def build_graph(
    harness: Harness,
    checkpointer: SqliteSaver,
    vision_index: VisionIndex | None = None,
    video_timeline: VideoTimeline | None = None,
    observation: ObservationSource | None = None,
):
    index = vision_index or open_vision_index(
        harness.settings.vision_backend,
        weights=harness.settings.yolo_weights,
    )
    timeline = video_timeline if video_timeline is not None else packaged_video_timeline(index)
    observer = observation if observation is not None else open_observation(harness)
    nodes = make_nodes(harness, index, timeline, observer)
    graph = StateGraph(JudgementState)
    graph.add_node("validate", nodes["validate"])
    graph.add_node("context", nodes["context"])
    graph.add_node("history", nodes["history"])
    graph.add_node("retrieve", nodes["retrieve"])
    graph.add_node("graph", nodes["graph"])
    graph.add_node("vision", nodes["vision"])
    graph.add_node("score", nodes["score"])
    graph.add_node("review", nodes["review"])
    graph.add_node("report", nodes["report"])

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
