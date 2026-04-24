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

    Mirrors `block_rules.translatability_for_block` exactly so DocIR and the
    DB-backed ProtectedPolicy enum cannot disagree. The decision order is:

      1. explicit `translatable=False` in metadata → TRANSLATE_NONE
         (role/page-family flags set this when the block lives outside
          translatable regions; we honour the parser's verdict)
      2. block type inherently non-translatable (code/table/figure/
         equation/image) → TRANSLATE_NONE, regardless of any explicit
         `translatable=True` — because a CODE block staying prose is
         never correct, even in a translatable region.
      3. role or page-family flagged (legacy path without explicit key) →
         TRANSLATE_NONE
      4. otherwise → TRANSLATE_ALL
    """
    md = metadata or {}

    # (1) Explicit False is authoritative — parser knows this block sits in
    # a non-translatable region (header/footer/toc/backmatter).
    if md.get("translatable") is False:
        return TRANSLATE_NONE

    # (2) Block-type protection ALWAYS applies, even when the parser has
    # stamped translatable=True on an otherwise-translatable region. This
    # is the invariant block_rules.translatability_for_block encodes.
    if block_type in _NON_TRANSLATABLE_BLOCK_TYPES:
        return TRANSLATE_NONE

    # (3) Legacy path: no explicit translatable key — infer from role/family.
    if "translatable" not in md:
        role = md.get("pdf_block_role")
        if isinstance(role, str) and role in _NON_TRANSLATABLE_PDF_ROLES:
            return TRANSLATE_NONE
        family = md.get("pdf_page_family")
        if isinstance(family, str) and family in _NON_TRANSLATABLE_PAGE_FAMILIES:
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
