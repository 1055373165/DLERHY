from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class ParsedBlock:
    block_type: str
    text: str
    source_path: str
    ordinal: int
    anchor: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    parse_confidence: float | None = None


@dataclass(slots=True, frozen=True)
class ParsedChapter:
    chapter_id: str
    href: str
    title: str | None
    blocks: list[ParsedBlock]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ParsedDocument:
    title: str | None
    author: str | None
    language: str | None
    chapters: list[ParsedChapter]
    metadata: dict[str, Any] = field(default_factory=dict)
