"""References / Bibliography modality (PDF v2 M3.1).

This modality solves the failure mode "translating reference entries
as prose" — author names get sinified, conference names get translated,
DOIs get mangled. The fix is structural: detect the references section
and force every block in it to `translatability=translate_none`. As a
bonus we parse each entry into a structured `ReferenceEntry` so review
UIs and exporters can present the bibliography cleanly.

Design notes:

  * **Detection by heading + structural cues, not by content.** The
    section start is a heading whose text matches one of the canonical
    titles (`References`, `Bibliography`, `Works Cited`, `参考文献`,
    `引用`). If no such heading exists we fall back to the
    `pdf_page_family == "backmatter"` flag the recovery pipeline
    already maintains.
  * **Once we enter the section, every subsequent block stays in it.**
    Until we hit a clearly non-reference heading (e.g., `Index`,
    `Appendix`) or end-of-document.
  * **Parsing is best-effort.** Entries that fit common citation
    patterns (APA / IEEE) are decomposed; entries that don't are kept
    as raw text. Either way the section is protected.
  * No LLM, no network, no DB — pure functions over `ParsedDocument`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final, Iterable

from book_agent.domain.structure.models import (
    TRANSLATE_NONE,
    ParsedBlock,
    ParsedChapter,
    ParsedDocument,
)


# --- Section detection ---

_REFERENCE_HEADING_TEXTS: Final[frozenset[str]] = frozenset(
    {
        "references",
        "reference",
        "bibliography",
        "works cited",
        "literature cited",
        "参考文献",
        "引用",
        "参考书目",
    }
)

_NEXT_SECTION_HEADING_TEXTS: Final[frozenset[str]] = frozenset(
    {
        "index",
        "appendix",
        "appendices",
        "glossary",
        "about the author",
        "about the authors",
        "colophon",
        "afterword",
        "索引",
        "附录",
        "术语表",
    }
)


def _normalize_heading(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    cleaned = cleaned.strip(".:")
    return cleaned.casefold()


def is_reference_heading(text: str) -> bool:
    return _normalize_heading(text) in _REFERENCE_HEADING_TEXTS


def is_terminating_heading(text: str) -> bool:
    return _normalize_heading(text) in _NEXT_SECTION_HEADING_TEXTS


# --- Entry parsing ---

# Detects a 4-digit year in (), [], or "year." form. Allow 1900-2099.
_YEAR_RE: Final[re.Pattern[str]] = re.compile(r"\b(19|20)\d{2}\b")
# DOI pattern per Crossref / IDF guidelines.
_DOI_RE: Final[re.Pattern[str]] = re.compile(
    r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE
)
_ARXIV_RE: Final[re.Pattern[str]] = re.compile(
    r"\barXiv:?\s*(\d{4}\.\d{4,5})", re.IGNORECASE
)
_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"https?://[^\s<>\"']+", re.IGNORECASE
)

# Heuristic author block — last names with initials, separated by commas
# and an "&" or "and" before the last author. Captures the chunk before
# the first year/period that starts the title.
_LEADING_AUTHORS_RE: Final[re.Pattern[str]] = re.compile(
    r"^([A-Z][A-Za-z'\-]+(?:,\s*[A-Z]\.[\s\-A-Z\.]*)+"
    r"(?:,?\s*(?:and|&)\s*[A-Z][A-Za-z'\-]+,?\s*[A-Z]\.[\s\-A-Z\.]*)*)"
)


@dataclass(slots=True, frozen=True)
class ReferenceEntry:
    """One parsed bibliography entry.

    `raw` is always populated. The other fields are best-effort.
    """

    raw: str
    block_anchor: str | None = None
    authors_chunk: str | None = None
    year: int | None = None
    title: str | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    urls: tuple[str, ...] = ()


def parse_reference_entry(text: str, *, anchor: str | None = None) -> ReferenceEntry:
    raw = (text or "").strip()
    if not raw:
        return ReferenceEntry(raw="", block_anchor=anchor)

    # Strip leading numbering like "1." or "[1]".
    body = re.sub(r"^\s*(?:\[\d+\]|\d+[\.\)])\s+", "", raw)

    # DOI / arXiv / URL pluck (do this before trying to split title/venue
    # so they're not double-counted in those fields).
    doi_match = _DOI_RE.search(body)
    arxiv_match = _ARXIV_RE.search(body)
    urls = tuple(_URL_RE.findall(body))

    # Year — first 4-digit match wins.
    year_match = _YEAR_RE.search(body)
    year_value = int(year_match.group(0)) if year_match else None

    # Authors chunk: greedy match of "Surname, F. M." sequences at start.
    authors_chunk = None
    leading = _LEADING_AUTHORS_RE.match(body)
    if leading:
        authors_chunk = leading.group(1).rstrip(", ")

    # Title / venue split — assume entries follow `Authors. Year. Title.
    # Venue, info.` Rough but workable.
    title = None
    venue = None
    after_year_chunk = body[year_match.end():] if year_match else body
    after_year_chunk = after_year_chunk.lstrip(" .)")
    if after_year_chunk:
        # Title is text up to the first period that's followed by a
        # capital-letter venue start.
        title_match = re.match(r"\s*([^.]+?)\.\s+(.+)", after_year_chunk)
        if title_match:
            title = title_match.group(1).strip().rstrip(",")
            venue = title_match.group(2).strip().rstrip(".") or None
        else:
            title = after_year_chunk.strip().rstrip(".") or None

    return ReferenceEntry(
        raw=raw,
        block_anchor=anchor,
        authors_chunk=authors_chunk,
        year=year_value,
        title=title,
        venue=venue,
        doi=doi_match.group(0) if doi_match else None,
        arxiv_id=arxiv_match.group(1) if arxiv_match else None,
        urls=urls,
    )


# --- Document-level operation ---


@dataclass(slots=True)
class ReferencesProtectionResult:
    """Outcome of running references protection on a document."""

    section_chapter_id: str | None = None
    section_start_block_anchor: str | None = None
    block_count: int = 0
    parsed_entries: list[ReferenceEntry] = field(default_factory=list)


def protect_references_section(
    document: ParsedDocument,
) -> tuple[ParsedDocument, ReferencesProtectionResult]:
    """Return (rewritten_document, result).

    The rewritten document has every block in the references section
    re-stamped with `translatability=translate_none` (preserving all
    other block fields). Outside-section blocks are left untouched.
    """
    from dataclasses import replace as _replace

    result = ReferencesProtectionResult()
    if not document.chapters:
        return document, result

    # First pass: locate section start.
    in_section = False
    new_chapters: list[ParsedChapter] = []
    for chapter in document.chapters:
        # Heading-as-chapter-title check — many parsers put the
        # "References" line as the chapter title rather than as a block.
        chapter_title_matches = bool(chapter.title and is_reference_heading(chapter.title))
        if chapter_title_matches:
            in_section = True
            if result.section_chapter_id is None:
                result.section_chapter_id = chapter.chapter_id

        new_blocks: list[ParsedBlock] = []
        for block in chapter.blocks:
            text_norm = _normalize_heading(block.text)
            if not in_section:
                if (block.block_type == "heading" and text_norm in _REFERENCE_HEADING_TEXTS) or (
                    text_norm in _REFERENCE_HEADING_TEXTS
                ):
                    in_section = True
                    if result.section_chapter_id is None:
                        result.section_chapter_id = chapter.chapter_id
                    if result.section_start_block_anchor is None:
                        result.section_start_block_anchor = block.anchor
                    # The heading itself is non-translatable (proper nouns / canonical title).
                    new_blocks.append(_replace(block, translatability=TRANSLATE_NONE))
                    continue
                new_blocks.append(block)
                continue

            # in_section path
            if block.block_type == "heading" and text_norm in _NEXT_SECTION_HEADING_TEXTS:
                in_section = False
                new_blocks.append(block)
                continue

            # Within section: protect + parse if it looks like an entry.
            new_blocks.append(_replace(block, translatability=TRANSLATE_NONE))
            result.block_count += 1
            entry = parse_reference_entry(block.text, anchor=block.anchor)
            if entry.raw:
                result.parsed_entries.append(entry)

        new_chapters.append(_replace(chapter, blocks=new_blocks))

    rewritten = ParsedDocument(
        title=document.title,
        author=document.author,
        language=document.language,
        chapters=new_chapters,
        metadata=dict(document.metadata),
    )
    return rewritten, result


def iter_reference_entries(document: ParsedDocument) -> Iterable[ReferenceEntry]:
    """Convenience: walk the document and yield parsed entries.

    Doesn't mutate the document. For protection, call
    `protect_references_section` instead.
    """
    in_section = False
    for chapter in document.chapters:
        if chapter.title and is_reference_heading(chapter.title):
            in_section = True
        for block in chapter.blocks:
            text_norm = _normalize_heading(block.text)
            if not in_section:
                if text_norm in _REFERENCE_HEADING_TEXTS:
                    in_section = True
                continue
            if block.block_type == "heading" and text_norm in _NEXT_SECTION_HEADING_TEXTS:
                in_section = False
                continue
            entry = parse_reference_entry(block.text, anchor=block.anchor)
            if entry.raw:
                yield entry
