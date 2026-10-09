"""The book's own table of contents (a PDF's bookmarks) against the headings a reading copy shows.

A reading copy's outline is its headings. A PDF book sets small run-in titles under its numbered
sections ("core definition: autonomous control flow" under "1.2.2 What is an LLM agent?"); read as
headings at the x.y.z level they crowd the outline with entries the book's contents never list. A
run-in title stays in the text, in bold, and only what the book's outline lists (or numbers) is a
heading.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# "1.2", "1.2.1", "A.3": a numbered section.
_SECTION_NUMBER_RE = re.compile(r"^\s*(?:\d{1,3}|[A-Z])(?:\.\d{1,3})+\.?(?=\s|$)")
# "1.2.1": a section two levels under its chapter.
_SUBSECTION_NUMBER_RE = re.compile(r"^\s*(?:\d{1,3}|[A-Z])\.\d{1,3}\.\d{1,3}\.?(?=\s|$)")
# A sentence leading into a list ("This hybrid approach offers several advantages:").
_LEAD_IN_RE = re.compile(r"[:：]\s*$")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")


@dataclass(frozen=True, slots=True)
class BookOutlineEntry:
    level: int
    title: str
    page: int | None = None


def outline_entries(document_metadata: Mapping[str, Any] | None) -> list[BookOutlineEntry]:
    """The PDF's bookmarks as the parser stored them (none for other sources)."""
    evidence = (document_metadata or {}).get("pdf_page_evidence")
    raw = evidence.get("pdf_outline_entries") if isinstance(evidence, Mapping) else None
    entries: list[BookOutlineEntry] = []
    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, Mapping):
            continue
        title = " ".join(str(item.get("title") or "").split())
        if not title:
            continue
        try:
            level = int(item.get("level") or 1)
        except (TypeError, ValueError):
            level = 1
        page = item.get("page_number")
        entries.append(BookOutlineEntry(level, title, page if isinstance(page, int) else None))
    return entries


def title_key(text: str | None) -> str:
    """A title compared without its section number, case, punctuation or spacing."""
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = _SECTION_NUMBER_RE.sub("", normalized, count=1)
    normalized = re.sub(r"[^\w\s]", " ", normalized.casefold())
    return " ".join(normalized.split())


# A bookmark points at its section's page; a heading parsed a page or two off is still that section.
_PAGE_TOLERANCE = 2


@dataclass(frozen=True, slots=True)
class OutlineIndex:
    """The outline's titles (compared by ``title_key``) and the pages each is listed at."""

    pages_by_key: Mapping[str, tuple[int | None, ...]]

    def lists(self, text: str | None, page: int | None = None) -> bool:
        """Whether the outline lists this title, at this page when both pages are known
        ("implementation" in chapter 6 is not "8.5.3 Implementation")."""
        pages = self.pages_by_key.get(title_key(text))
        if not pages:
            return False
        if page is None:
            return True
        return any(listed is None or abs(listed - page) <= _PAGE_TOLERANCE for listed in pages)


def outline_index(entries: Iterable[BookOutlineEntry]) -> OutlineIndex:
    pages: dict[str, list[int | None]] = {}
    for entry in entries:
        key = title_key(entry.title)
        if key:
            pages.setdefault(key, []).append(entry.page)
    return OutlineIndex({key: tuple(values) for key, values in pages.items()})


def is_numbered_section(text: str | None) -> bool:
    return bool(_SECTION_NUMBER_RE.match(text or ""))


def has_numbered_subsections(heading_texts: Iterable[str | None]) -> bool:
    """Whether a chapter numbers its sections two levels down ("1.2.1"): its run-in titles sit below them."""
    return any(_SUBSECTION_NUMBER_RE.match(text or "") for text in heading_texts)


def is_lead_in_sentence(text: str | None) -> bool:
    """A sentence ending in a colon that leads into a list or code, not a title."""
    normalized = " ".join((text or "").split())
    return bool(_LEAD_IN_RE.search(normalized)) and len(_WORD_RE.findall(normalized)) >= 4


# A run-in title is a few words; more, or a line that runs on ("…implement similar func-"), is body text.
_RUN_IN_MAX_WORDS = 8
_RUNS_ON_RE = re.compile(r"[-‐,;]\s*$")


def reads_as_prose(text: str | None) -> bool:
    """Body text parsed as a heading: a long line, or one cut off mid-sentence."""
    normalized = " ".join((text or "").split())
    return len(_WORD_RE.findall(normalized)) > _RUN_IN_MAX_WORDS or bool(_RUNS_ON_RE.search(normalized))


def is_run_in_heading(
    text: str | None,
    level: int | None,
    *,
    numbered_chapter: bool,
    outline: OutlineIndex,
    page: int | None = None,
) -> bool:
    """A heading below the book's numbered sections that its contents do not list: a run-in title.

    Only in a chapter that numbers its sections down to x.y.z (an unnumbered book's headings are its
    structure), only at the x.y.z level or deeper (a chapter's "Summary" stays), and never what the
    book's outline lists.
    """
    if not numbered_chapter or is_numbered_section(text):
        return False
    if not isinstance(level, int) or level < 3:
        return False
    return not outline.lists(text, page)


@dataclass(frozen=True, slots=True)
class OutlineMismatch:
    kind: str  # "missing": the book lists it, the reading copy has no such heading; "extra": the reverse
    title: str
    page: int | None = None


def compare_sections(
    book_sections: Sequence[BookOutlineEntry],
    reading_copy_headings: Sequence[tuple[str, int | None, int | None]],
    *,
    outline: OutlineIndex | None = None,
) -> list[OutlineMismatch]:
    """The reading copy's headings against the book's sections.

    ``book_sections``: the book's numbered sections in the part compared; ``reading_copy_headings``:
    (source text, page, heading level) of every heading the reading copy shows there, in order;
    ``outline``: the whole outline, to tell a listed unnumbered title. Titles are compared without
    their numbers (a book may renumber a section its outline lists).

    "missing": a section the book lists that has no heading. "extra": a numbered heading the book does
    not list. "unlisted": an unnumbered heading at the x.y.z depth or below the book does not list
    (a run-in title in the reading copy's outline).
    """
    outline = outline or outline_index(book_sections)
    shown = {title_key(text) for text, _page, _level in reading_copy_headings}
    listed = {title_key(entry.title) for entry in book_sections}
    mismatches = [
        OutlineMismatch("missing", entry.title, entry.page)
        for entry in book_sections
        if title_key(entry.title) and title_key(entry.title) not in shown
    ]
    for text, page, level in reading_copy_headings:
        key = title_key(text)
        if not key:
            continue
        title = " ".join(text.split())
        if is_numbered_section(text):
            if key not in listed:
                mismatches.append(OutlineMismatch("extra", title, page))
        elif isinstance(level, int) and level >= 3 and not outline.lists(text, page):
            mismatches.append(OutlineMismatch("unlisted", title, page))
    return mismatches


def numbered_section_entries(entries: Sequence[BookOutlineEntry]) -> list[BookOutlineEntry]:
    """The outline's numbered sections ("1.2 …", "1.2.1 …"), the entries a reading copy must have as headings."""
    return [entry for entry in entries if is_numbered_section(entry.title)]
