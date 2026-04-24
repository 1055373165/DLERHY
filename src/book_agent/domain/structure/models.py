from dataclasses import dataclass, field
from typing import Any, Final


# DocIR Translatability protocol (PDF v2, M1).
# See tasks/pdf-pipeline-v2.md §M1.4. These constants are deliberately plain
# strings (not Enum members) so ParsedBlock can stay frozen+slots and so the
# values survive round-tripping through JSON/DB without custom codecs.
TRANSLATE_ALL: Final[str] = "translate_all"
TRANSLATE_PROSE_ONLY: Final[str] = "translate_prose_only"
TRANSLATE_NONE: Final[str] = "translate_none"

TRANSLATABILITY_VALUES: Final[frozenset[str]] = frozenset(
    {TRANSLATE_ALL, TRANSLATE_PROSE_ONLY, TRANSLATE_NONE}
)


# DocIR Provenance — where a block's text came from.
PROVENANCE_TEXT_LAYER: Final[str] = "text_layer"
PROVENANCE_OCR: Final[str] = "ocr"
PROVENANCE_VLM: Final[str] = "vlm"
PROVENANCE_HYBRID: Final[str] = "hybrid"

PROVENANCE_VALUES: Final[frozenset[str]] = frozenset(
    {PROVENANCE_TEXT_LAYER, PROVENANCE_OCR, PROVENANCE_VLM, PROVENANCE_HYBRID}
)


# Block types that must never be translated as prose. Mirrors the decision
# in `book_agent.domain.block_rules.protected_policy_for_block` but lives at
# the parser-side (DocIR) layer so ParsedBlock can carry the verdict without
# a circular import through the DB domain models.
_NON_TRANSLATABLE_BLOCK_TYPES: Final[frozenset[str]] = frozenset(
    {"code", "table", "figure", "equation", "image"}
)

_NON_TRANSLATABLE_PDF_ROLES: Final[frozenset[str]] = frozenset(
    {"header", "footer", "toc_entry"}
)

_NON_TRANSLATABLE_PAGE_FAMILIES: Final[frozenset[str]] = frozenset({"backmatter"})


def derive_translatability(
    block_type: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Return the DocIR `translatability` value for a parser-side block.

    The logic intentionally mirrors `block_rules.protected_policy_for_block`
    so DocIR and the DB-backed ProtectedPolicy enum never disagree. Parsers
    call this when constructing ParsedBlock; downstream consumers (bootstrap,
    translator gating) can trust the field without re-deriving.
    """
    md = metadata or {}

    # Explicit override wins (PDF parser sets metadata["translatable"]).
    if "translatable" in md:
        return TRANSLATE_ALL if bool(md["translatable"]) else TRANSLATE_NONE

    role = md.get("pdf_block_role")
    if isinstance(role, str) and role in _NON_TRANSLATABLE_PDF_ROLES:
        return TRANSLATE_NONE

    family = md.get("pdf_page_family")
    if isinstance(family, str) and family in _NON_TRANSLATABLE_PAGE_FAMILIES:
        return TRANSLATE_NONE

    if block_type in _NON_TRANSLATABLE_BLOCK_TYPES:
        return TRANSLATE_NONE

    return TRANSLATE_ALL


@dataclass(slots=True, frozen=True)
class ParsedBlock:
    block_type: str
    text: str
    source_path: str
    ordinal: int
    anchor: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    parse_confidence: float | None = None
    translatability: str = TRANSLATE_ALL
    provenance: str = PROVENANCE_TEXT_LAYER
    confidence_breakdown: dict[str, Any] = field(default_factory=dict)
    style_hints: dict[str, Any] = field(default_factory=dict)


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
