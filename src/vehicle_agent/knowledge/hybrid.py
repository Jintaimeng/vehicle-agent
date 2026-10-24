import hashlib
import math
from collections.abc import Callable

from vehicle_agent.knowledge.index import RegulationSnippet

VECTOR_SIZE = 128
RRF_K = 60
RERANK_LIMIT = 5
VECTOR_WEIGHT = 0.5
KEYWORD_WEIGHT = 0.5
Embedder = Callable[[str], list[float]]
_NEURAL_LAYER: tuple[list[list[float]], list[float]] | None = None


def regulation_text(snippet: RegulationSnippet) -> str:
    return f"{snippet.title}\n{snippet.section}\n{snippet.excerpt}"


def regulation_tokens(text: str) -> list[str]:
    characters = [character.casefold() for character in text if not character.isspace()]
    return ["".join(characters[index : index + 2]) for index in range(len(characters) - 1)]


def regulation_vector(text: str) -> list[float]:
    """Hash character bigrams into a fixed cosine vector. No embedding model."""

    vector = [0.0] * VECTOR_SIZE
    for token in regulation_tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:2], "big") % VECTOR_SIZE
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[bucket] += sign
    return _normalize(vector)


def neural_embed(text: str) -> list[float]:
    """One fixed linear layer and tanh over the character-hash vector."""

    hashed = regulation_vector(text)
    weights, bias = _neural_layer()
    activated = []
    for row, bias_value in zip(weights, bias, strict=True):
        total = bias_value + sum(
            weight * value for weight, value in zip(row, hashed, strict=True)
        )
        activated.append(math.tanh(total))
    return _normalize(activated)


def keyword_overlap(query: str, text: str) -> float:
    query_tokens = regulation_tokens(query)
    if not query_tokens:
        return 0.0
    document_tokens = set(regulation_tokens(text))
    return sum(1 for token in query_tokens if token in document_tokens) / len(query_tokens)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    return sum(one * other for one, other in zip(left, right, strict=True))


def hybrid_rank(
    snippets: list[RegulationSnippet],
    query: str,
    embedder: Embedder | None = None,
) -> list[RegulationSnippet]:
    if not snippets:
        return []
    texts = {_snippet_key(snippet): regulation_text(snippet) for snippet in snippets}
    embed = regulation_vector if embedder is None else embedder
    query_vector = embed(query)
    _require_vector(query_vector)
    document_vectors = {key: embed(text) for key, text in texts.items()}
    for vector in document_vectors.values():
        if len(vector) != len(query_vector):
            raise ValueError("embedder vectors must share one length")
    vector_scores = {
        key: cosine_similarity(query_vector, vector) for key, vector in document_vectors.items()
    }
    keyword_scores = {key: keyword_overlap(query, text) for key, text in texts.items()}
    return fuse_scores(snippets, vector_scores=vector_scores, keyword_scores=keyword_scores)


def fuse_scores(
    snippets: list[RegulationSnippet],
    *,
    vector_scores: dict[tuple[str, str], float],
    keyword_scores: dict[tuple[str, str], float],
) -> list[RegulationSnippet]:
    """Fuse the two rankings with RRF, then rerank the head by a fixed weighted score."""

    by_key = {_snippet_key(snippet): snippet for snippet in snippets}
    vector_order = sorted(by_key, key=lambda key: (-vector_scores[key], key))
    keyword_order = sorted(by_key, key=lambda key: (-keyword_scores[key], key))
    fused = dict.fromkeys(by_key, 0.0)
    for rank, key in enumerate(vector_order, start=1):
        fused[key] += 1.0 / (RRF_K + rank)
    for rank, key in enumerate(keyword_order, start=1):
        fused[key] += 1.0 / (RRF_K + rank)
    ordered = sorted(by_key, key=lambda key: (-fused[key], key))
    head, tail = ordered[:RERANK_LIMIT], ordered[RERANK_LIMIT:]
    reranked = sorted(
        head,
        key=lambda key: (
            -(VECTOR_WEIGHT * vector_scores[key] + KEYWORD_WEIGHT * keyword_scores[key]),
            key,
        ),
    )
    return [by_key[key] for key in reranked + tail]


def _snippet_key(snippet: RegulationSnippet) -> tuple[str, str]:
    return (snippet.document_id, snippet.section)


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _require_vector(vector: list[float]) -> None:
    if len(vector) < 1:
        raise ValueError("embedder vector must contain at least one value")


def _neural_layer() -> tuple[list[list[float]], list[float]]:
    global _NEURAL_LAYER
    if _NEURAL_LAYER is None:
        weights = [
            [_layer_value(f"w:{row}:{column}") for column in range(VECTOR_SIZE)]
            for row in range(VECTOR_SIZE)
        ]
        bias = [_layer_value(f"b:{row}") for row in range(VECTOR_SIZE)]
        _NEURAL_LAYER = (weights, bias)
    return _NEURAL_LAYER


def _layer_value(label: str) -> float:
    digest = hashlib.sha256(label.encode("utf-8")).digest()
    return digest[0] / 127.5 - 1
