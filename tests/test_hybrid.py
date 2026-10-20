import json
from pathlib import Path

from vehicle_agent.knowledge.hybrid import fuse_scores, hybrid_rank
from vehicle_agent.knowledge.index import (
    RegulationSnippet,
    packaged_regulation_entries,
    packaged_regulation_index,
)

_EVAL_PATH = Path(__file__).resolve().parents[1] / "src/vehicle_agent/knowledge/retrieval_eval.json"


def test_rerank_promotes_the_labeled_snippet_when_ranks_disagree() -> None:
    gold = RegulationSnippet(document_id="z-gold", title="标注", section="第1节", excerpt="目标")
    decoy = RegulationSnippet(document_id="a-decoy", title="干扰", section="第1节", excerpt="其他")
    other = RegulationSnippet(document_id="m-other", title="旁支", section="第1节", excerpt="中间")
    ranked = fuse_scores(
        [gold, decoy, other],
        vector_scores={
            ("a-decoy", "第1节"): 0.99,
            ("m-other", "第1节"): 0.5,
            ("z-gold", "第1节"): 0.2,
        },
        keyword_scores={
            ("z-gold", "第1节"): 0.9,
            ("m-other", "第1节"): 0.4,
            ("a-decoy", "第1节"): 0.01,
        },
    )

    assert ranked[0].document_id == "z-gold"


def test_packaged_eval_recall_is_exact() -> None:
    evaluation = json.loads(_EVAL_PATH.read_text(encoding="utf-8"))
    labeled = [case for case in evaluation["cases"] if case["relevant"]]
    unlabeled = [case for case in evaluation["cases"] if not case["relevant"]]

    assert _recall(labeled, 1) == _ratio(evaluation["recall_at_1"])
    assert _recall(labeled, 3) == _ratio(evaluation["recall_at_3"])
    packaged = {
        (snippet.document_id, snippet.section)
        for _alarm_types, snippet in packaged_regulation_entries()
    }
    for case in unlabeled:
        top = _rank_case(case)[0]
        assert (top.document_id, top.section) in packaged
        assert {"document_id": top.document_id, "section": top.section} not in case["relevant"]


def test_fixed_fatigue_query_keeps_the_driving_section_first() -> None:
    snippets = packaged_regulation_index().search("driver_fatigue")

    assert snippets[0].document_id == "fatigue-rules"
    assert snippets[0].section == "第2节 行驶中的报警"
    assert snippets[1].document_id == "fatigue-training"


def _rank_case(case: dict) -> list[RegulationSnippet]:
    if case["alarm_type"] is None:
        snippets = [snippet for _alarm_types, snippet in packaged_regulation_entries()]
        return hybrid_rank(snippets, case["query"])
    return packaged_regulation_index().search(case["alarm_type"], query=case["query"])


def _recall(cases: list[dict], limit: int) -> float:
    found = 0
    relevant = 0
    for case in cases:
        expected = {(item["document_id"], item["section"]) for item in case["relevant"]}
        ranked = {(item.document_id, item.section) for item in _rank_case(case)[:limit]}
        found += len(expected & ranked)
        relevant += len(expected)
    return found / relevant


def _ratio(parts: list[int]) -> float:
    numerator, denominator = parts
    return numerator / denominator
