import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel

_FIELD = re.compile(r"^-\s+([a-z_]+):\s*(.+?)\s*$")
_EXCERPT_LIMIT = 180


class RegulationSnippet(BaseModel):
    document_id: str
    title: str
    section: str
    excerpt: str


class RegulationIndex(Protocol):
    def search(self, alarm_type: str) -> list[RegulationSnippet]: ...


REGULATION_QUERIES = {
    "driver_fatigue": "行驶中疲劳驾驶报警应就近停车休息",
    "device_offline": "设备短时间离线或视频缺失先核对供电",
    "phone_use": "行驶中手持电话应语音警告并记录",
    "speeding": "超过路段限速应提示驾驶员减速",
    "camera_occlusion": "摄像头画面被遮挡先核对镜头和供电",
}


class MarkdownRegulationIndex:
    """Rank packaged Markdown sections with the shared hybrid retrieval."""

    def __init__(self, documents: list[tuple[set[str], RegulationSnippet]]) -> None:
        self._documents = documents

    @classmethod
    def from_directory(cls, root: Path) -> "MarkdownRegulationIndex":
        paths = sorted(root.glob("*.md"))
        if not paths:
            raise FileNotFoundError(f"no regulation documents in {root}")
        documents: list[tuple[set[str], RegulationSnippet]] = []
        for path in paths:
            documents.extend(parse_regulation_document(path.read_text(encoding="utf-8")))
        return cls(documents)

    def search(
        self,
        alarm_type: str,
        query: str | None = None,
        embedder: Callable[[str], list[float]] | None = None,
    ) -> list[RegulationSnippet]:
        matched = [
            snippet for alarm_types, snippet in self._documents if alarm_type in alarm_types
        ]
        return _rank_snippets(matched, _regulation_query(alarm_type, query), embedder)


def packaged_regulation_index() -> MarkdownRegulationIndex:
    return MarkdownRegulationIndex.from_directory(Path(__file__).resolve().parent / "regulations")


def packaged_regulation_entries() -> list[tuple[frozenset[str], RegulationSnippet]]:
    return [
        (frozenset(alarm_types), snippet)
        for alarm_types, snippet in packaged_regulation_index()._documents
    ]


REGULATION_COLLECTION = "regulations"
_SCROLL_LIMIT = 64


def regulation_point_id(document_id: str, section: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}\n{section}"))


def regulation_query_filter(alarm_type: str):
    """Fixed payload filter. The model does not choose the query."""

    from qdrant_client.models import FieldCondition, Filter, MatchValue

    return Filter(
        must=[FieldCondition(key="alarm_types", match=MatchValue(value=alarm_type))]
    )


class QdrantRegulationIndex:
    """Same hybrid ranking as the Markdown index, with Qdrant cosine search."""

    def __init__(self, url: str, *, client=None) -> None:
        self._url = url
        self._client = client
        self._owns_client = client is None
        self._ready = False

    def search(self, alarm_type: str, query: str | None = None) -> list[RegulationSnippet]:
        from vehicle_agent.knowledge.hybrid import regulation_vector

        self.ensure_ready()
        phrase = _regulation_query(alarm_type, query)
        query_filter = regulation_query_filter(alarm_type)
        self._client.query_points(
            collection_name=REGULATION_COLLECTION,
            query=regulation_vector(phrase),
            query_filter=query_filter,
            limit=_SCROLL_LIMIT,
            with_payload=True,
        )
        snippets: list[RegulationSnippet] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=REGULATION_COLLECTION,
                scroll_filter=query_filter,
                limit=_SCROLL_LIMIT,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            snippets.extend(_snippet_from_payload(point.payload) for point in points)
            if offset is None:
                break
        return _rank_snippets(snippets, phrase)

    def ensure_ready(self) -> None:
        from vehicle_agent.knowledge.hybrid import VECTOR_SIZE, regulation_text, regulation_vector

        client = self._require_client()
        if self._ready:
            return
        from qdrant_client.models import Distance, PointStruct, VectorParams

        if client.collection_exists(REGULATION_COLLECTION):
            if _collection_vector_size(client) != VECTOR_SIZE:
                client.delete_collection(REGULATION_COLLECTION)
        if not client.collection_exists(REGULATION_COLLECTION):
            client.create_collection(
                collection_name=REGULATION_COLLECTION,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
        client.upsert(
            collection_name=REGULATION_COLLECTION,
            points=[
                PointStruct(
                    id=regulation_point_id(snippet.document_id, snippet.section),
                    vector=regulation_vector(regulation_text(snippet)),
                    payload={
                        "document_id": snippet.document_id,
                        "title": snippet.title,
                        "section": snippet.section,
                        "excerpt": snippet.excerpt,
                        "alarm_types": sorted(alarm_types),
                    },
                )
                for alarm_types, snippet in packaged_regulation_entries()
            ],
        )
        self._ready = True

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None
            self._ready = False

    def _require_client(self):
        if self._client is None:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(url=self._url, timeout=5)
        return self._client


def open_regulation_index(
    backend: Literal["markdown", "qdrant"],
    *,
    url: str,
) -> RegulationIndex:
    if backend == "markdown":
        return packaged_regulation_index()
    if backend == "qdrant":
        return QdrantRegulationIndex(url)
    raise ValueError(f"unsupported regulation backend: {backend}")


def _regulation_query(alarm_type: str, query: str | None) -> str:
    if query is not None:
        return query
    return REGULATION_QUERIES.get(alarm_type, alarm_type)


def _rank_snippets(
    snippets: list[RegulationSnippet],
    query: str,
    embedder: Callable[[str], list[float]] | None = None,
) -> list[RegulationSnippet]:
    from vehicle_agent.knowledge.hybrid import hybrid_rank

    return hybrid_rank(snippets, query, embedder)


def _collection_vector_size(client) -> int | None:
    info = client.get_collection(REGULATION_COLLECTION)
    vectors = info.config.params.vectors
    if hasattr(vectors, "size"):
        return vectors.size
    named = next(iter(vectors.values()))
    return named.size


def _snippet_from_payload(payload: dict) -> RegulationSnippet:
    return RegulationSnippet(
        document_id=payload["document_id"],
        title=payload["title"],
        section=payload["section"],
        excerpt=payload["excerpt"],
    )


def parse_regulation_document(text: str) -> list[tuple[set[str], RegulationSnippet]]:
    sections: list[tuple[set[str], RegulationSnippet]] = []
    heading: str | None = None
    fields: dict[str, str] = {}
    body: list[str] = []

    def flush() -> None:
        nonlocal heading, fields, body
        if heading is None:
            return
        alarm_types = {
            item.strip() for item in fields.get("alarm_types", "").split(",") if item.strip()
        }
        excerpt = " ".join(line.strip() for line in body if line.strip())
        if len(excerpt) > _EXCERPT_LIMIT:
            excerpt = excerpt[: _EXCERPT_LIMIT - 1] + "…"
        if not fields.get("document_id") or not fields.get("title") or not fields.get("section"):
            raise ValueError(f"regulation section {heading} is missing required fields")
        if not alarm_types or not excerpt:
            raise ValueError(f"regulation section {heading} is missing alarm types or body")
        sections.append(
            (
                alarm_types,
                RegulationSnippet(
                    document_id=fields["document_id"],
                    title=fields["title"],
                    section=fields["section"],
                    excerpt=excerpt,
                ),
            )
        )
        fields = {}
        body = []

    for line in text.splitlines():
        if line.startswith("# "):
            flush()
            heading = line[2:].strip()
            continue
        if heading is None:
            continue
        if not line.strip() and not body:
            continue
        match = _FIELD.match(line)
        if match and not body:
            fields[match.group(1)] = match.group(2)
            continue
        body.append(line)
    flush()
    return sections
