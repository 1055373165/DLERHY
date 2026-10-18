"""Parse health: rule checks on a parsed book, run before any money is spent on translating it.

Each check stands for a parse problem a real book has had (docs/agent-upgrade/08-pilot-quality-gate.md §4):

========================  ========  ==================================================================
check                     severity  what fails it
========================  ========  ==================================================================
chapters_vs_outline       blocking  a chapter-level outline entry with no chapter, or a chapter the
                                    outline does not have (contents lines turned into chapters, the
                                    chapters under "Part N" folded into the part)
chapter_titles            blocking  a chapter titled References/Index/... that holds body chapters;
                                    a page number in a chapter title
front_matter_sections     major     a top-level front-matter outline entry that is not its own section
stray_glyphs              blocking  blocks starting with a symbol-font "¡", holding U+FFFD or
                                    private-use characters
pseudo_headings           major     "headings" set in a monospace font or starting with "#"
line_break_hyphens        major     words still broken by a line-end hyphen ("pro-vider"), by the
                                    book's own evidence
figure_regions            blocking  a figure crop that takes in page furniture or its caption, or cuts
                                    through a path or text line (``figure_region_problems``)
image_fragments           major     image blocks that are hairline slivers or have nothing to show
caption_folds             blocking  running prose that export would fold into a figure
duplicated_labels         major     a figure's own labels also kept as body paragraphs
headings_as_text          major     a section heading kept as a body paragraph: a short line set in the
                                    book's heading type, numbered like the chapter's sections
                                    ("1.2.1 What is an LLM?") or a section label ("Summary")
figure_residue            major     a figure's own text kept as a code, table or equation block
                                    ("| The | teacher |", a chart's legend): inside the figure, no
                                    caption of its own, not a code listing or a data grid
========================  ========  ==================================================================

The checks read parsed data only (blocks, chapters, the PDF outline and, for figure regions, the
PDF page), never a model. An EPUB or a web page gets the checks that do not need pages or an outline.

Thresholds: a check passes when its count is at most ``max(max_count, max_ratio * denominator)``.
They say what a reader would accept, not what today's parser achieves: a book's known residuals are
recorded as that book's upper bounds in the corpus manifest (tests/corpus/manifest.json), which
tests/test_parse_health_corpus.py holds the parser to.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from book_agent.domain.structure.artifact_grouping import resolve_artifact_group_context_ids
from book_agent.domain.structure.geometry import bleeds_off_page

BLOCKING = "blocking"
MAJOR = "major"
MINOR = "minor"
SEVERITY_ORDER = (BLOCKING, MAJOR, MINOR)

MAX_SAMPLES = 5

Bbox = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class HealthBlock:
    """What the checks need of a block. Attribute names follow the ORM ``Block`` so export helpers accept it."""

    id: str
    chapter_id: str
    ordinal: int
    block_type: str
    source_text: str
    source_span_json: dict[str, Any] = field(default_factory=dict)

    @property
    def role(self) -> str | None:
        role = self.source_span_json.get("pdf_block_role")
        return str(role) if role else None

    @property
    def page(self) -> int | None:
        page = self.source_span_json.get("source_page_start")
        return int(page) if isinstance(page, int) else None

    @property
    def bbox(self) -> Bbox | None:
        region = _first_region(self.source_span_json)
        return region[1] if region else None

    @property
    def region_page(self) -> int | None:
        region = _first_region(self.source_span_json)
        return region[0] if region else self.page


@dataclass(frozen=True, slots=True)
class HealthChapter:
    id: str
    ordinal: int
    title: str
    page_start: int | None = None
    page_end: int | None = None


@dataclass(frozen=True, slots=True)
class OutlineEntry:
    level: int
    title: str
    page: int


@dataclass(slots=True)
class Sample:
    text: str
    page: int | None = None
    block_id: str | None = None
    chapter_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class Threshold:
    max_count: int = 0
    max_ratio: float | None = None
    # Why the threshold is not zero, when it is not.
    note: str | None = None

    def allowed(self, denominator: int) -> int:
        if self.max_ratio is None:
            return self.max_count
        return max(self.max_count, int(self.max_ratio * denominator))


@dataclass(slots=True)
class CheckResult:
    name: str
    severity: str
    count: int
    denominator: int
    allowed: int
    passed: bool
    samples: list[Sample] = field(default_factory=list)
    breakdown: dict[str, int] = field(default_factory=dict)
    threshold_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["samples"] = [{key: value for key, value in asdict(sample).items() if value is not None} for sample in self.samples]
        return payload


THRESHOLDS: dict[str, Threshold] = {
    "chapters_vs_outline": Threshold(0),
    "chapter_titles": Threshold(0),
    "front_matter_sections": Threshold(0),
    "stray_glyphs": Threshold(0, 0.002, "one text block in 500 may carry an odd glyph (the RAG book had 502 in 2,369)"),
    "pseudo_headings": Threshold(0, 0.02, "one heading in 50 may be a bold code comment"),
    "line_break_hyphens": Threshold(
        5, 0.0002, "a few words per 10,000 may stay split where the book gives no evidence either way"
    ),
    # Every cut, swallowed or missing figure is one a reader sees: no tolerance.
    "figure_regions": Threshold(0),
    "image_fragments": Threshold(0, 0.02, "one picture in 50 may be a hairline rule set as an image"),
    "caption_folds": Threshold(0),
    "duplicated_labels": Threshold(3, 0.02, "a label or two kept as text is noise; one text block in 50 is a pattern"),
    # A lost heading drops the section from the reader's contents; figure text in the flow is junk.
    "headings_as_text": Threshold(0),
    "figure_residue": Threshold(0),
}

SEVERITIES: dict[str, str] = {
    "chapters_vs_outline": BLOCKING,
    "chapter_titles": BLOCKING,
    "front_matter_sections": MAJOR,
    "stray_glyphs": BLOCKING,
    "pseudo_headings": MAJOR,
    "line_break_hyphens": MAJOR,
    "figure_regions": BLOCKING,
    "image_fragments": MAJOR,
    "caption_folds": BLOCKING,
    "duplicated_labels": MAJOR,
    "headings_as_text": MAJOR,
    "figure_residue": MAJOR,
}

PDF_ONLY_CHECKS = frozenset(
    {
        "chapters_vs_outline",
        "front_matter_sections",
        "line_break_hyphens",
        "figure_regions",
        "image_fragments",
        "duplicated_labels",
        "headings_as_text",
        "figure_residue",
    }
)


def _result(name: str, count: int, denominator: int, samples: list[Sample], breakdown: dict[str, int] | None = None) -> CheckResult:
    threshold = THRESHOLDS[name]
    allowed = threshold.allowed(denominator)
    return CheckResult(
        name=name,
        severity=SEVERITIES[name],
        count=count,
        denominator=denominator,
        allowed=allowed,
        passed=count <= allowed,
        samples=samples[:MAX_SAMPLES],
        breakdown=dict(breakdown or {}),
        threshold_note=threshold.note if threshold.max_count or threshold.max_ratio else None,
    )


def _first_region(metadata: dict[str, Any]) -> tuple[int | None, Bbox] | None:
    source_bbox = metadata.get("source_bbox_json")
    if not isinstance(source_bbox, dict):
        return None
    regions = source_bbox.get("regions")
    if not isinstance(regions, list) or not regions or not isinstance(regions[0], dict):
        return None
    bbox = regions[0].get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        values = tuple(float(value) for value in bbox)
    except (TypeError, ValueError):
        return None
    page = regions[0].get("page_number")
    return (int(page) if isinstance(page, int) else None, values)  # type: ignore[return-value]


def _snippet(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _norm_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title or "").casefold()
    text = re.sub(r"\.{2,}\s*\d*\s*$", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Structure: chapters against the outline


# "Chapter One" spells its number out (A Complete Guide To Volume Price Analysis).
_NUMBER_WORD = (
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty)"
)
_PART_RE = re.compile(rf"^\s*part\s+(?:\d+|[ivxlc]+|{_NUMBER_WORD})\b", re.IGNORECASE)
_NUMBERED_CHAPTER_RE = re.compile(
    rf"^\s*(?:chapter\s+)?\d{{1,2}}(?:\s|:|\.\s)|^\s*chapter\s+{_NUMBER_WORD}\b|^\s*appendix\b", re.IGNORECASE
)
_BODY_START_RE = re.compile(
    rf"^\s*(?:part|chapter)\s+(?:\d+|[ivxlc]+|{_NUMBER_WORD})\b|^\s*\d{{1,2}}(?:\s|:|\.\s)", re.IGNORECASE
)
_COVER_RE = re.compile(r"\bcover\b(?!\s+illustration)", re.IGNORECASE)


def _ignorable_entry(entry: OutlineEntry, index: int, document_title: str | None) -> bool:
    """The book's own title entry and its covers are not sections a reader needs."""
    title = _norm_title(entry.title)
    if _COVER_RE.search(entry.title):
        return True
    if index == 0 and entry.page <= 1:
        return True
    return bool(document_title) and title == _norm_title(document_title or "")


def _title_matches(chapter: HealthChapter, entry: OutlineEntry) -> bool:
    """Same title, and not a contents line naming the entry pages before it starts."""
    title = _norm_title(entry.title)
    if not title or _norm_title(chapter.title) != title:
        return False
    return chapter.page_start is None or chapter.page_start >= entry.page - 2


def _chapter_for(entry: OutlineEntry, chapters: Sequence[HealthChapter]) -> HealthChapter | None:
    for chapter in chapters:
        if chapter.page_start == entry.page or _title_matches(chapter, entry):
            return chapter
    return None


def split_outline(outline: Sequence[OutlineEntry], document_title: str | None = None) -> tuple[list[OutlineEntry], list[OutlineEntry]]:
    """(front-matter entries, chapter-level entries after them).

    Front matter is the top-level entries before the first part or numbered chapter. Chapter-level
    entries are the other top-level entries plus the numbered chapters nested under "Part N".
    """
    if not outline:
        return [], []
    top = min(entry.level for entry in outline)
    front: list[OutlineEntry] = []
    body: list[OutlineEntry] = []
    in_body = False
    under_part = False
    for index, entry in enumerate(outline):
        if entry.level == top:
            under_part = bool(_PART_RE.match(entry.title))
            if not in_body and _BODY_START_RE.match(entry.title):
                in_body = True
            if _ignorable_entry(entry, index, document_title):
                continue
            (body if in_body else front).append(entry)
        elif entry.level == top + 1 and under_part and _NUMBERED_CHAPTER_RE.match(entry.title):
            in_body = True
            body.append(entry)
    return front, body


def is_extra_chapter(chapter: HealthChapter, outline: Sequence[OutlineEntry]) -> bool:
    """A chapter no bookmark starts or names: a contents line turned chapter, say. The parse's own
    "Front Matter" chapter, the pages before the first bookmark (title page, copyright, praise), is
    none; a contents line before the first bookmark still is."""
    if not outline:
        return False
    outline_pages = {entry.page for entry in outline}
    if chapter.page_start in outline_pages or any(_title_matches(chapter, entry) for entry in outline):
        return False
    leading_front_matter = (
        " ".join((chapter.title or "").split()).casefold() == "front matter"
        and chapter.page_start is not None
        and chapter.page_start < min(outline_pages)
    )
    return not leading_front_matter


def check_chapters_vs_outline(
    chapters: Sequence[HealthChapter], outline: Sequence[OutlineEntry], document_title: str | None = None
) -> CheckResult | None:
    if not outline:
        return None
    _front, expected = split_outline(outline, document_title)
    samples: list[Sample] = []
    missing = [entry for entry in expected if _chapter_for(entry, chapters) is None]
    for entry in missing:
        samples.append(Sample(text=_snippet(entry.title), page=entry.page, detail="missing_chapter"))
    extra = [chapter for chapter in chapters if is_extra_chapter(chapter, outline)]
    for chapter in extra:
        samples.append(Sample(text=_snippet(chapter.title), page=chapter.page_start, chapter_id=chapter.id, detail="extra_chapter"))
    # Extra chapters first: a contents line turned chapter is the more telling sample.
    samples.sort(key=lambda sample: sample.detail != "extra_chapter")
    return _result(
        "chapters_vs_outline",
        len(missing) + len(extra),
        len(expected),
        samples,
        {"missing_chapter": len(missing), "extra_chapter": len(extra)},
    )


def check_front_matter_sections(
    chapters: Sequence[HealthChapter], outline: Sequence[OutlineEntry], document_title: str | None = None
) -> CheckResult | None:
    if not outline:
        return None
    front, _body = split_outline(outline, document_title)
    missing = [entry for entry in front if _chapter_for(entry, chapters) is None]
    samples = [Sample(text=_snippet(entry.title), page=entry.page, detail="not_its_own_section") for entry in missing]
    return _result("front_matter_sections", len(missing), len(front), samples)


_BACK_MATTER_TITLES = frozenset(
    {
        "references", "bibliography", "index", "glossary", "notes", "endnotes", "contents",
        "table of contents", "brief contents", "further reading", "sources", "works cited",
    }
)
_CHAPTER_HEADING_RE = re.compile(r"^\s*(?:(?:chapter|part)\s+(?:\d+|[ivxlc]+)|\d{1,2})\s+[A-Za-z]", re.IGNORECASE)
_TITLE_PAGE_NUMBER_RE = re.compile(r"(?:\.{2,}|\s)\s*(\d{1,4})\s*$")
_NUMBERED_WORD_RE = re.compile(
    r"\b(?:chapter|part|appendix|section|step|lesson|unit|version|level|phase|figure|table|listing|volume|book|day|week)\s*$",
    re.IGNORECASE,
)
_URLISH_RE = re.compile(r"https?://|www\.|doi\.org|\bet al\b", re.IGNORECASE)


def _is_prose(text: str) -> bool:
    words = text.split()
    return len(words) >= 25 and not _URLISH_RE.search(text) and text.rstrip().endswith((".", "?", "!", ".”", '."'))


def title_page_number(title: str) -> str | None:
    """The page number printed after a title ("Part 2 Creating RAG systems.......31"), if any."""
    text = " ".join((title or "").split())
    match = _TITLE_PAGE_NUMBER_RE.search(text)
    if not match:
        return None
    head = text[: match.start()].rstrip(". ")
    if "..." in text[match.start() - 3 : match.end()] or ".." in text:
        return match.group(1)
    if len(head.split()) < 2 or _NUMBERED_WORD_RE.search(head):
        return None
    return match.group(1)


def check_chapter_titles(chapters: Sequence[HealthChapter], blocks: Sequence[HealthBlock]) -> CheckResult:
    blocks_by_chapter: dict[str, list[HealthBlock]] = defaultdict(list)
    for block in blocks:
        blocks_by_chapter[block.chapter_id].append(block)
    samples: list[Sample] = []
    breakdown: Counter[str] = Counter()
    for chapter in chapters:
        number = title_page_number(chapter.title)
        if number is not None:
            breakdown["page_number_in_title"] += 1
            samples.append(Sample(text=_snippet(chapter.title), page=chapter.page_start, chapter_id=chapter.id, detail="page_number_in_title"))
            continue
        if _norm_title(chapter.title) not in _BACK_MATTER_TITLES:
            continue
        chapter_blocks = blocks_by_chapter.get(chapter.id, [])
        # A contents page lists chapter titles by design; a chapter's own opening heading has no page number.
        chapter_headings = [
            block
            for block in chapter_blocks
            if block.block_type == "heading"
            and _CHAPTER_HEADING_RE.match(block.source_text or "")
            and title_page_number(block.source_text) is None
            and "contents" not in _norm_title(chapter.title)
        ]
        prose = [block for block in chapter_blocks if block.block_type == "paragraph" and _is_prose(block.source_text or "")]
        if chapter_headings or len(prose) >= 20:
            breakdown["body_under_back_matter_title"] += 1
            evidence = chapter_headings[0].source_text if chapter_headings else prose[0].source_text
            samples.append(
                Sample(
                    text=_snippet(f"{chapter.title}: {evidence}"),
                    page=chapter.page_start,
                    chapter_id=chapter.id,
                    detail="body_under_back_matter_title",
                )
            )
    # Several chapters under one title: a section name (every chapter "Summary") was taken for the
    # chapter's title, as an EPUB whose TOC lists each chapter's sections after it once did.
    by_title: dict[str, list[HealthChapter]] = defaultdict(list)
    for chapter in chapters:
        if _norm_title(chapter.title):
            by_title[_norm_title(chapter.title)].append(chapter)
    for title, same in by_title.items():
        if len(same) >= 3:
            breakdown["repeated_title"] += len(same)
            samples.append(
                Sample(
                    text=_snippet(f"{len(same)} 章都叫「{same[0].title}」"),
                    page=same[0].page_start,
                    chapter_id=same[0].id,
                    detail="repeated_title",
                )
            )
    # Body chapters under a back-matter title lose the reader whole chapters: those samples first.
    samples.sort(key=lambda sample: sample.detail != "body_under_back_matter_title")
    return _result("chapter_titles", sum(breakdown.values()), len(chapters), samples, dict(breakdown))


# ---------------------------------------------------------------------------
# Text


def _is_private_use(char: str) -> bool:
    code = ord(char)
    return 0xE000 <= code <= 0xF8FF or code >= 0xF0000


def _glyph_problem(text: str) -> str | None:
    stripped = (text or "").lstrip()
    if stripped.startswith("¡"):
        return "symbol_font_bullet"
    if "�" in stripped:
        return "replacement_character"
    if any(_is_private_use(char) for char in stripped):
        return "private_use_character"
    return None


_TEXT_BLOCK_TYPES = frozenset({"paragraph", "heading", "list_item", "caption", "footnote", "quote", "table"})


def _text_blocks(blocks: Iterable[HealthBlock]) -> list[HealthBlock]:
    return [
        block
        for block in blocks
        if block.block_type in _TEXT_BLOCK_TYPES and block.role not in {"header", "footer"} and (block.source_text or "").strip()
    ]


def check_stray_glyphs(blocks: Sequence[HealthBlock]) -> CheckResult:
    text_blocks = _text_blocks(blocks)
    samples: list[Sample] = []
    breakdown: Counter[str] = Counter()
    for block in text_blocks:
        problem = _glyph_problem(block.source_text)
        if problem is None:
            continue
        breakdown[problem] += 1
        samples.append(Sample(text=_snippet(block.source_text), page=block.page, block_id=block.id, detail=problem))
    return _result("stray_glyphs", sum(breakdown.values()), len(text_blocks), samples, dict(breakdown))


_MONOSPACE_FONT_RE = re.compile(
    r"mono|courier|consol|menlo|inconsolata|sourcecode|source\s*code|firacode|lettergothic|andale|lucidaconsole|"
    r"typewriter|\bcode\b|ocr[ab]",
    re.IGNORECASE,
)


def is_monospace_font(name: str) -> bool:
    return bool(_MONOSPACE_FONT_RE.search(name or ""))


def set_wholly_in_code_face(font_names: Iterable[object] | None) -> bool:
    """Every font of a block is a monospace face: the book prints it as code (or program output)."""
    names = [str(name) for name in font_names or () if name]
    return bool(names) and all(is_monospace_font(name) for name in names)


def check_pseudo_headings(blocks: Sequence[HealthBlock]) -> CheckResult:
    headings = [block for block in blocks if block.block_type == "heading"]
    samples: list[Sample] = []
    breakdown: Counter[str] = Counter()
    for block in headings:
        text = (block.source_text or "").strip()
        fonts = [str(name) for name in block.source_span_json.get("pdf_font_names") or [] if name]
        if text.startswith("#"):
            reason = "hash_prefix"
        elif fonts and all(is_monospace_font(name) for name in fonts):
            reason = "monospace_font"
        else:
            continue
        breakdown[reason] += 1
        samples.append(Sample(text=_snippet(text), page=block.page, block_id=block.id, detail=reason))
    return _result("pseudo_headings", sum(breakdown.values()), len(headings), samples, dict(breakdown))


_WORD_RE = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")
_HYPHEN_PAIR_RE = re.compile(r"(?<![A-Za-z0-9\-./@\\_])([A-Za-z]{2,})-([a-z]{2,})(?![A-Za-z0-9\-@\\_]|\.[A-Za-z0-9])")
_DANGLING_HYPHEN_RE = re.compile(r"(?<![A-Za-z0-9\-./@\\_])([A-Za-z]{2,})[-\u00ad]\s+([a-z]{2,})\b")
# Overlapping pairs: "an evalua tion" must test "evalua tion", not only "an evalua".
_SPACE_PAIR_RE = re.compile(r"(?<![A-Za-z0-9\-./@\\_])([A-Za-z]{2,})(?=\s+([a-z]{2,})\b)")
_SUSPENDED_HYPHEN_NEXT = frozenset({"and", "or", "to", "nor", "versus", "vs"})
_PROSE_BLOCK_TYPES = frozenset({"paragraph", "list_item", "caption", "footnote", "quote", "heading"})


def check_line_break_hyphens(blocks: Sequence[HealthBlock], inline_counts: Counter[str] | None = None) -> CheckResult:
    """Words a line break still splits, judged by how the book itself writes them.

    * ``soft_hyphen``: a soft hyphen (U+00AD) left in the text;
    * ``hyphen_space``: "decision- making", a line-end hyphen followed by a space
      ("pre- and post-training" is a suspended hyphen, not a break);
    * ``broken_word``: "pro-vider", where the book writes "provider" and never writes "pro-vider"
      inside a line. ``inline_counts`` is ``hyphenation.inline_word_counts`` over the PDF's lines:
      the words each line holds whole. Without it, the book's parsed text is the evidence;
    * ``split_word``: "evalua tion", a word cut in two where neither piece is ever a word of its own
      ("lay out" is two words: "out" stands alone elsewhere).
    """
    prose = [block for block in _text_blocks(blocks) if block.block_type in _PROSE_BLOCK_TYPES]
    counts: Counter[str] = Counter()
    word_total = 0
    for block in prose:
        for match in _WORD_RE.finditer(block.source_text):
            counts[match.group(0).casefold()] += 1
            word_total += 1

    def written_inline(token: str) -> bool:
        if inline_counts is not None:
            return inline_counts.get(token, 0) > 0
        return False

    # A space-split pair is a broken word only when a piece never stands alone anywhere else.
    split_pairs: list[tuple[HealthBlock, str, str, str]] = []
    left_uses: Counter[str] = Counter()
    right_uses: Counter[str] = Counter()
    for block in prose:
        for match in _SPACE_PAIR_RE.finditer(block.source_text):
            left, right = match.group(1).casefold(), match.group(2).casefold()
            if counts.get(left + right, 0) or (inline_counts and inline_counts.get(left + right, 0)):
                split_pairs.append((block, left, right, f"{match.group(1)} {match.group(2)}"))
                left_uses[left] += 1
                right_uses[right] += 1

    samples: list[Sample] = []
    breakdown: Counter[str] = Counter()

    def flag(kind: str, block: HealthBlock, excerpt: str) -> None:
        breakdown[kind] += 1
        samples.append(Sample(text=_snippet(excerpt, 60), page=block.page, block_id=block.id, detail=kind))

    for block in prose:
        text = block.source_text
        if "\u00ad" in text:
            at = text.index("\u00ad")
            flag("soft_hyphen", block, text[max(0, at - 20) : at + 20])
        for match in _DANGLING_HYPHEN_RE.finditer(text):
            if match.group(2).casefold() not in _SUSPENDED_HYPHEN_NEXT:
                flag("hyphen_space", block, match.group(0))
        for match in _HYPHEN_PAIR_RE.finditer(text):
            left, right = match.group(1), match.group(2)
            token = f"{left}-{right}".casefold()
            joined = (left + right).casefold()
            if written_inline(token):
                continue
            if inline_counts is not None:
                broken = bool(inline_counts.get(joined, 0) or counts.get(joined, 0))
            else:
                broken = counts.get(joined, 0) >= max(1, counts.get(token, 0))
            if broken:
                flag("broken_word", block, match.group(0))
    for block, left, right, excerpt in split_pairs:
        if counts[left] <= left_uses[left] and counts[right] <= right_uses[right]:
            flag("split_word", block, excerpt)
    return _result("line_break_hyphens", sum(breakdown.values()), word_total, samples, dict(breakdown))


# ---------------------------------------------------------------------------
# Figures and images

_PICTURE_TYPES = frozenset({"image", "figure"})
SLIVER_MAX_SIDE_PT = 4.0


def picture_blocks(blocks: Iterable[HealthBlock]) -> list[HealthBlock]:
    return [block for block in blocks if block.block_type in _PICTURE_TYPES]


def image_fragment_kind(bbox: Bbox | None, page_size: tuple[float, float] | None = None) -> str | None:
    """Why an image block's region cannot hold a picture: ``empty`` or ``sliver``; None when it can.

    Small but square pictures (an inline icon) are pictures; a strip under 4pt is a hairline or the
    edge of a tiled image.
    """
    if bbox is None:
        return "empty"
    x0, y0, x1, y1 = bbox
    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0:
        return "empty"
    if page_size is not None:
        page_width, page_height = page_size
        if x1 <= 0 or y1 <= 0 or x0 >= page_width or y0 >= page_height:
            return "empty"
    if min(width, height) < SLIVER_MAX_SIDE_PT:
        return "sliver"
    return None


ICON_MAX_SIDE_PT = 24.0


def is_inline_icon(bbox: Bbox | None) -> bool:
    """A picture small enough to sit in a line of text: an icon, not a figure with a crop to check."""
    return bbox is not None and max(bbox[2] - bbox[0], bbox[3] - bbox[1]) < ICON_MAX_SIDE_PT


def check_image_fragments(blocks: Sequence[HealthBlock], page_sizes: dict[int, tuple[float, float]] | None = None) -> CheckResult:
    pictures = picture_blocks(blocks)
    samples: list[Sample] = []
    breakdown: Counter[str] = Counter()
    for block in pictures:
        page = block.region_page
        kind = image_fragment_kind(block.bbox, (page_sizes or {}).get(page) if page is not None else None)
        if kind is None:
            continue
        breakdown[kind] += 1
        bbox = block.bbox
        samples.append(
            Sample(
                text=f"{bbox[2] - bbox[0]:.1f}×{bbox[3] - bbox[1]:.1f}pt" if bbox else "no region",
                page=page,
                block_id=block.id,
                detail=kind,
            )
        )
    return _result("image_fragments", sum(breakdown.values()), len(pictures), samples, dict(breakdown))


def _area(bbox: Bbox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _overlap(a: Bbox, b: Bbox) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


_LABEL_BLOCK_TYPES = frozenset({"paragraph", "list_item", "heading"})


FULL_PAGE_AREA_RATIO = 0.7


def is_page_background(bbox: Bbox | None, page_size: tuple[float, float] | None) -> bool:
    """A picture that is its page (a cover) or the page's background: most of the page, or printed to two opposite edges.

    It is not a figure: nothing on it is a figure label and its region has no crop to check.
    """
    if bbox is None or page_size is None:
        return False
    return _area(bbox) >= FULL_PAGE_AREA_RATIO * page_size[0] * page_size[1] or bleeds_off_page(bbox, *page_size)


def check_duplicated_labels(blocks: Sequence[HealthBlock], page_sizes: dict[int, tuple[float, float]] | None = None) -> CheckResult:
    """Text blocks lying inside a figure's region: the figure's labels, kept a second time as text.

    Text over a page-background picture (a cover's panel) is the page's own text.
    """
    figures_by_page: dict[int, list[HealthBlock]] = defaultdict(list)
    for block in picture_blocks(blocks):
        if block.region_page is None or image_fragment_kind(block.bbox) is not None:
            continue
        if is_page_background(block.bbox, (page_sizes or {}).get(block.region_page)):
            continue
        figures_by_page[block.region_page].append(block)
    samples: list[Sample] = []
    count = 0
    pages: set[int] = set()
    for block in blocks:
        if block.block_type not in _LABEL_BLOCK_TYPES or block.role in {"header", "footer", "caption"}:
            continue
        page, bbox = block.region_page, block.bbox
        if page is None or bbox is None or _area(bbox) <= 0 or page not in figures_by_page:
            continue
        if any(_overlap(bbox, figure.bbox) >= 0.6 * _area(bbox) for figure in figures_by_page[page] if figure.bbox):
            count += 1
            pages.add(page)
            samples.append(Sample(text=_snippet(block.source_text), page=page, block_id=block.id, detail="label_as_text"))
    text_blocks = sum(1 for block in blocks if block.block_type in _LABEL_BLOCK_TYPES)
    return _result("duplicated_labels", count, text_blocks, samples, {"pages": len(pages)})


# ---------------------------------------------------------------------------
# Headings kept as text, figure text kept as code or tables (shared with the pilot QA)

# "1.2 Title", "1.2.3 Title": the chapter's number, then one or two section numbers.
_SECTION_HEADING_RE = re.compile(r"^\s*(\d{1,2})((?:\.\d{1,2}){1,2})\.?\s+(?=[A-Z“\"'(])")
# Section labels a book sets as headings without a number.
SECTION_LABELS = frozenset({"summary", "chapter summary", "this chapter covers"})
_CHAPTER_NUMBER_RE = re.compile(r"^\s*(?:chapter\s+)?(\d{1,2})\b", re.IGNORECASE)
_HEADING_BLOCK_TYPES = frozenset({"paragraph", "list_item", "caption", "quote"})


def chapter_number(title: str | None) -> int | None:
    match = _CHAPTER_NUMBER_RE.match(title or "")
    return int(match.group(1)) if match else None


@dataclass(frozen=True, slots=True)
class HeadingStyle:
    """The type a book sets its section headings in, learned from its numbered headings."""

    heading_fonts: frozenset[str]
    body_font: str | None


@dataclass(frozen=True, slots=True)
class StyledText:
    block_type: str
    source_text: str
    font_names: tuple[str, ...]


def heading_style(blocks: Iterable[StyledText]) -> HeadingStyle | None:
    """The fonts of the book's numbered section headings, less its body font; None without evidence."""
    heading_fonts: set[str] = set()
    body_chars: Counter[str] = Counter()
    numbered = 0
    for block in blocks:
        fonts = [str(name) for name in block.font_names if name]
        if not fonts:
            continue
        text = " ".join((block.source_text or "").split())
        if block.block_type == "heading" and _SECTION_HEADING_RE.match(text):
            heading_fonts.update(fonts)
            numbered += 1
        elif block.block_type == "paragraph" and len(text) >= 120:
            for name in fonts:
                body_chars[name] += len(text) // len(fonts)
    if numbered < 2 or not heading_fonts:
        return None
    body_font = body_chars.most_common(1)[0][0] if body_chars else None
    return HeadingStyle(frozenset(heading_fonts - ({body_font} if body_font else set())), body_font)


def section_heading_level(
    text: str, font_names: Sequence[str], chapter_no: int | None, style: HeadingStyle | None
) -> int | None:
    """The level of a section heading kept as text, or None when the block is not one.

    Needs the page's evidence: a short standalone line, set wholly in the book's heading type
    (none of its body font), numbered like the sections of its chapter ("1.2.1 ..." in chapter 1)
    or a section label ("Summary"). A list item "1. Install ..." or a sentence starting with a
    number fails the numbering, and body text fails the type.
    """
    if style is None or not style.heading_fonts:
        return None
    normalized = " ".join((text or "").split())
    if not normalized or len(normalized) > 90 or len(normalized.split()) > 14 or normalized.endswith((".", ",", ";", ":")):
        return None
    fonts = {str(name) for name in font_names or () if name}
    if not fonts or not fonts <= style.heading_fonts:
        return None
    match = _SECTION_HEADING_RE.match(normalized)
    if match is not None:
        if chapter_no is None or int(match.group(1)) != chapter_no:
            return None
        return 1 + match.group(2).count(".")
    if normalized.casefold() in SECTION_LABELS:
        return 2
    return None


def _styled(block: HealthBlock) -> StyledText:
    return StyledText(block.block_type, block.source_text or "", tuple(block.source_span_json.get("pdf_font_names") or ()))


def check_headings_as_text(chapters: Sequence[HealthChapter], blocks: Sequence[HealthBlock]) -> CheckResult:
    style = heading_style(_styled(block) for block in blocks)
    number_by_chapter = {chapter.id: chapter_number(chapter.title) for chapter in chapters}
    samples: list[Sample] = []
    count = 0
    candidates = 0
    for block in blocks:
        if block.block_type not in _HEADING_BLOCK_TYPES or block.role in {"header", "footer"}:
            continue
        candidates += 1
        level = section_heading_level(
            block.source_text, block.source_span_json.get("pdf_font_names") or (), number_by_chapter.get(block.chapter_id), style
        )
        if level is None:
            continue
        count += 1
        samples.append(Sample(text=_snippet(block.source_text), page=block.page, block_id=block.id, detail=f"heading_level={level}"))
    return _result("headings_as_text", count, candidates, samples)


_RESIDUE_BLOCK_TYPES = frozenset({"code", "table", "equation"})
# A word split over cells ("C | | laude Sonnet 4").
_BROKEN_WORD_RE = re.compile(r"(?:^|\|)\s*[A-Za-z]\s*(?:\|\s*)+[a-z]{2,}")
_TOKEN_RE = re.compile(r"\w+")


def block_regions(metadata: dict[str, Any]) -> list[tuple[int, Bbox]]:
    source_bbox = metadata.get("source_bbox_json")
    regions = source_bbox.get("regions") if isinstance(source_bbox, dict) else None
    found: list[tuple[int, Bbox]] = []
    for region in regions or []:
        if isinstance(region, dict) and isinstance(region.get("page_number"), int) and len(region.get("bbox") or []) == 4:
            x0, y0, x1, y1 = (float(value) for value in region["bbox"])
            found.append((region["page_number"], (x0, y0, x1, y1)))
    return found


def is_data_grid(text: str) -> bool:
    """Three or more "|" rows of three or more columns, mostly filled: a table's data, not a diagram's labels."""
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in (text or "").splitlines()
        if line.strip().startswith("|")
    ]
    if len(rows) < 3:
        return False
    cells = [cell for row in rows for cell in row]
    return max(len(row) for row in rows) >= 3 and sum(1 for cell in cells if cell) >= 0.6 * len(cells)


def figure_residue_kind(
    block_type: str,
    text: str,
    regions: Sequence[tuple[int, Bbox]],
    font_names: Sequence[str],
    figures_by_page: dict[int, list[Bbox]],
    *,
    has_caption: bool = False,
) -> str | None:
    """"garbage" or "labels" when a code, table or equation block is a figure's own text; else None.

    The block lies inside a figure's region (70% of each piece, with 8pt of slack), has no caption
    of its own, is not set in a code font (a listing) and is not a data grid. "garbage" is what a
    reader sees as broken: a word split over cells ("C | laude") or tokens repeated over and over
    ("The teacher The teacher").
    """
    if block_type not in _RESIDUE_BLOCK_TYPES or has_caption or not regions:
        return None
    if any(is_monospace_font(str(name)) for name in font_names or ()):
        return None
    if block_type == "table" and is_data_grid(text):
        return None
    for page, bbox in regions:
        padded = [(box[0] - 8.0, box[1] - 8.0, box[2] + 8.0, box[3] + 8.0) for box in figures_by_page.get(page) or []]
        if _area(bbox) <= 0 or not any(_overlap(bbox, box) >= 0.7 * _area(bbox) for box in padded):
            return None
    tokens = [token.casefold() for token in _TOKEN_RE.findall(text or "")]
    repeated = len(tokens) >= 4 and len(set(tokens)) * 2 <= len(tokens)
    if repeated or _BROKEN_WORD_RE.search(text or ""):
        return "garbage"
    return "labels"


def figure_regions_by_page(
    blocks: Iterable[HealthBlock], page_sizes: dict[int, tuple[float, float]] | None = None
) -> dict[int, list[Bbox]]:
    regions: dict[int, list[Bbox]] = defaultdict(list)
    for block in picture_blocks(blocks):
        for page, bbox in block_regions(block.source_span_json):
            if image_fragment_kind(bbox) is None and not is_page_background(bbox, (page_sizes or {}).get(page)):
                regions[page].append(bbox)
    return regions


def check_figure_residue(blocks: Sequence[HealthBlock], page_sizes: dict[int, tuple[float, float]] | None = None) -> CheckResult:
    figures = figure_regions_by_page(blocks, page_sizes)
    samples: list[Sample] = []
    breakdown: Counter[str] = Counter()
    candidates = [block for block in blocks if block.block_type in _RESIDUE_BLOCK_TYPES]
    for block in candidates:
        kind = figure_residue_kind(
            block.block_type,
            block.source_text or "",
            block_regions(block.source_span_json),
            block.source_span_json.get("pdf_font_names") or (),
            figures,
            has_caption=bool(block.source_span_json.get("linked_caption_text")),
        )
        if kind is None:
            continue
        breakdown[kind] += 1
        samples.append(Sample(text=_snippet(block.source_text), page=block.page, block_id=block.id, detail=kind))
    return _result("figure_residue", sum(breakdown.values()), len(candidates), samples, dict(breakdown))


FigureProblemFinder = Callable[[int, Bbox, list[Bbox]], list[Any]]


_FIGURE_CAPTION_RE = re.compile(r"^\s*fig(?:ure|\.)\s*[A-Z]?\d+(?:[.\-]\d+)*\b", re.IGNORECASE)


def check_figure_regions(
    blocks: Sequence[HealthBlock],
    find_problems: FigureProblemFinder,
    page_sizes: dict[int, tuple[float, float]] | None = None,
) -> CheckResult:
    """Every figure region against its page: ``find_problems(page, bbox, furniture)`` is ``figure_region_problems``.

    Furniture is the running headers, folios and footers the parser recognised on that page. A
    full-page picture (a cover) or a page-background panel is its page and is not checked
    (``is_page_background``). A "Figure N" caption no figure claims is a figure the parser found
    no region for at all (``no_region``).
    """
    furniture: dict[int, list[Bbox]] = defaultdict(list)
    for block in blocks:
        if block.role in {"header", "footer"} and block.region_page is not None and block.bbox is not None:
            furniture[block.region_page].append(block.bbox)

    def full_page(block: HealthBlock) -> bool:
        return is_page_background(block.bbox, (page_sizes or {}).get(int(block.region_page or 0)))

    # A cover is a full-page picture; a publisher's logo or badge printed on it is the cover's, not a figure.
    cover_pages = {int(block.region_page) for block in picture_blocks(blocks) if block.region_page is not None and full_page(block)}
    figures = [
        block
        for block in picture_blocks(blocks)
        if block.region_page is not None
        and image_fragment_kind(block.bbox) is None
        and not full_page(block)
        and int(block.region_page) not in cover_pages
        and not is_inline_icon(block.bbox)
    ]
    samples: list[Sample] = []
    orphan_samples: list[Sample] = []
    breakdown: Counter[str] = Counter()
    count = 0
    # A caption no picture claims still has its figure when the page holds a picture no caption claims.
    # (A link to a block that is not a caption claims nothing.)
    caption_ids = {block.id for block in blocks if block.block_type == "caption"}
    unclaimed_picture_pages = {
        block.region_page
        for block in picture_blocks(blocks)
        if str(block.source_span_json.get("linked_caption_block_id")) not in caption_ids
        and image_fragment_kind(block.bbox) is None
        and not is_inline_icon(block.bbox)
    }
    claimed_captions = {str(block.source_span_json.get("linked_caption_block_id")) for block in picture_blocks(blocks)}
    for block in blocks:
        if block.block_type != "caption" or not _FIGURE_CAPTION_RE.match(block.source_text or ""):
            continue
        if block.source_span_json.get("caption_for_block_id") or block.id in claimed_captions:
            continue
        if block.page in unclaimed_picture_pages:
            continue
        count += 1
        breakdown["no_region"] += 1
        orphan_samples.append(Sample(text=_snippet(block.source_text), page=block.page, block_id=block.id, detail="no_region"))
    for block in figures:
        problems = find_problems(int(block.region_page), block.bbox, furniture.get(int(block.region_page), []))  # type: ignore[arg-type]
        if not problems:
            continue
        count += 1
        kinds = sorted({str(getattr(problem, "kind", problem)) for problem in problems})
        breakdown.update(kinds)
        texts = [str(getattr(problem, "text", "") or "") for problem in problems if getattr(problem, "text", "")]
        samples.append(
            Sample(
                text=_snippet("; ".join(texts[:3]) or ", ".join(kinds)),
                page=block.region_page,
                block_id=block.id,
                detail=",".join(kinds),
            )
        )
    return _result("figure_regions", count, len(figures), samples + orphan_samples, dict(breakdown))


_SENTENCE_END_RE = re.compile(r"[.!?][\"”’)]?\s+(?=[A-Z])")


def check_caption_folds(blocks: Sequence[HealthBlock], *, academic_paper: bool = False) -> CheckResult:
    """Running prose that export would render inside a figure or table instead of the body.

    Uses the export's own grouping (``resolve_artifact_group_context_ids``). A short note that
    names the artifact ("Table 1 reports ...") is legitimate context; three sentences or more,
    or over 60 words, is body text.
    """
    by_chapter: dict[str, list[HealthBlock]] = defaultdict(list)
    for block in blocks:
        by_chapter[block.chapter_id].append(block)
    blocks_by_id = {block.id: block for block in blocks}
    samples: list[Sample] = []
    count = 0
    grouped = 0
    for chapter_blocks in by_chapter.values():
        groups = resolve_artifact_group_context_ids(list(chapter_blocks), academic_paper=academic_paper)
        for artifact_id, context_ids in groups.items():
            for context_id in context_ids:
                grouped += 1
                block = blocks_by_id.get(context_id)
                if block is None:
                    continue
                text = " ".join((block.source_text or "").split())
                sentences = len(_SENTENCE_END_RE.split(text)) if text else 0
                if sentences > 2 or len(text.split()) > 60:
                    count += 1
                    samples.append(Sample(text=_snippet(text), page=block.page, block_id=block.id, detail=f"folded_into:{artifact_id}"))
    return _result("caption_folds", count, grouped, samples)


# ---------------------------------------------------------------------------
# The report


def summarize(checks: Sequence[CheckResult]) -> dict[str, Any]:
    failed = [check for check in checks if not check.passed]
    counts = {severity: sum(1 for check in failed if check.severity == severity) for severity in SEVERITY_ORDER}
    if counts[BLOCKING]:
        status = "blocking"
    elif counts[MAJOR] or counts[MINOR]:
        status = "warnings"
    else:
        status = "passed"
    return {"status": status, "failed": counts}


def run_checks(
    *,
    is_pdf: bool,
    chapters: Sequence[HealthChapter],
    blocks: Sequence[HealthBlock],
    outline: Sequence[OutlineEntry] = (),
    document_title: str | None = None,
    page_sizes: dict[int, tuple[float, float]] | None = None,
    find_figure_problems: FigureProblemFinder | None = None,
    inline_counts: Counter[str] | None = None,
    academic_paper: bool = False,
) -> list[CheckResult]:
    """Every check that applies to this document, in the order of the design table."""
    results: list[CheckResult | None] = []
    if is_pdf:
        results.append(check_chapters_vs_outline(chapters, outline, document_title))
    results.append(check_chapter_titles(chapters, blocks))
    if is_pdf:
        results.append(check_front_matter_sections(chapters, outline, document_title))
    results.append(check_stray_glyphs(blocks))
    results.append(check_pseudo_headings(blocks))
    if is_pdf:
        results.append(check_line_break_hyphens(blocks, inline_counts))
        if find_figure_problems is not None:
            results.append(check_figure_regions(blocks, find_figure_problems, page_sizes))
        results.append(check_image_fragments(blocks, page_sizes))
    results.append(check_caption_folds(blocks, academic_paper=academic_paper))
    if is_pdf:
        results.append(check_duplicated_labels(blocks, page_sizes))
        results.append(check_headings_as_text(chapters, blocks))
        results.append(check_figure_residue(blocks, page_sizes))
    return [result for result in results if result is not None]
