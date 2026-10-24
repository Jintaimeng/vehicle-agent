import math

import pytest

from vehicle_agent.knowledge.hybrid import (
    VECTOR_SIZE,
    hybrid_rank,
    neural_embed,
    regulation_vector,
)
from vehicle_agent.knowledge.index import (
    REGULATION_QUERIES,
    RegulationSnippet,
    packaged_regulation_index,
)

_QUERY = REGULATION_QUERIES["driver_fatigue"]


def test_neural_embed_is_a_unit_vector() -> None:
    first = neural_embed(_QUERY)
    second = neural_embed(_QUERY)

    assert len(first) == VECTOR_SIZE
    assert first == second
    assert first != regulation_vector(_QUERY)
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1)


def test_default_fatigue_search_stays_on_the_driving_section() -> None:
    snippets = packaged_regulation_index().search("driver_fatigue")

    assert snippets[0].document_id == "fatigue-rules"
    assert snippets[0].section == "第2节 行驶中的报警"


def test_embedder_replaces_the_hash_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(text: str) -> list[float]:
        del text
        raise AssertionError("regulation_vector should not run")

    monkeypatch.setattr("vehicle_agent.knowledge.hybrid.regulation_vector", fail)
    query = "qq"
    snippet_a = RegulationSnippet(document_id="a", title="甲", section="第1节", excerpt="zz")
    snippet_b = RegulationSnippet(document_id="b", title="乙", section="第1节", excerpt="yy")

    def embed(text: str) -> list[float]:
        if text == query or text.endswith("yy"):
            return [1.0, 0.0]
        return [0.0, 1.0]

    ranked = hybrid_rank([snippet_a, snippet_b], query, embed)

    assert ranked[0].document_id == "b"


def test_embedder_rejects_empty_or_mismatched_vectors() -> None:
    snippet = RegulationSnippet(document_id="a", title="甲", section="第1节", excerpt="zz")

    with pytest.raises(ValueError):
        hybrid_rank([snippet], "qq", lambda text: [])

    def mismatch(text: str) -> list[float]:
        if text == "qq":
            return [1.0]
        return [1.0, 0.0]

    with pytest.raises(ValueError):
        hybrid_rank([snippet], "qq", mismatch)
