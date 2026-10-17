import re
from pathlib import Path
from typing import Protocol

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


class MarkdownRegulationIndex:
    """Match packaged Markdown sections by alarm type. No vector search."""

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

    def search(self, alarm_type: str) -> list[RegulationSnippet]:
        return [snippet for alarm_types, snippet in self._documents if alarm_type in alarm_types]


def packaged_regulation_index() -> MarkdownRegulationIndex:
    return MarkdownRegulationIndex.from_directory(Path(__file__).resolve().parent / "regulations")


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
