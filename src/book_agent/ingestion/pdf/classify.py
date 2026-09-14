"""PDF page and heading classification: outlines, TOC, front/back matter, academic and book headings, appendices, footnotes, page layout."""


from __future__ import annotations

import re
from typing import Any

from book_agent.ingestion.pdf.models import (
    PdfFileProfile,
    PdfPage,
    _PageExtractionPlan,
    _PageLayoutAssessment,
    _PageRecoveryContext,
)
from book_agent.ingestion.text import (
    _ACADEMIC_BODY_STARTER_WORDS,
    _HEADING_PATTERN,
    _PAGE_NUMBER_PATTERN,
    _PROSE_CONTINUATION_STOPWORDS,
    _dense_toc_line_count,
    _is_broken_word_fragment,
    _is_name_like_token,
    _looks_like_academic_prose_lead,
    _looks_like_caption_text,
    _looks_like_code,
    _looks_like_sentence_prose_line,
    _looks_like_table,
    _normalize_multiline_text,
    _normalize_text,
    _roman_to_int,
)

_FOOTNOTE_PATTERN = re.compile(r"^(?:\d+|[*\u2020\u2021])(?:[.)]|\s)")
_TOC_ENTRY_PATTERN = re.compile(
    r"^(?P<title>.+?)(?:\s*\.{2,}\s*|\s{2,})(?P<page>\d+|[ivxlcdm]+)$",
    re.IGNORECASE,
)
_APPENDIX_HEADING_PATTERN = re.compile(r"^appendix\b", re.IGNORECASE)
_CHAPTER_PREFIX_PATTERN = re.compile(
    r"^(?:(chapter|part|appendix)\s+(?:\d+|[ivxlcdm]+)\b[:.\-]?\s*)",
    re.IGNORECASE,
)
_LEADING_SECTION_NUMBER_PATTERN = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*)|[ivxlcdm]+)[.):\-]?\s+",
    re.IGNORECASE,
)
_TOC_HEADING_TITLES = {"contents", "table of contents"}
_FRONTMATTER_HEADING_TITLES = {
    "preface",
    "foreword",
    "introduction",
    "acknowledgments",
    "acknowledgements",
    "about the author",
    "dedication",
    "prologue",
}
_FRONTMATTER_SIGNAL_TITLES = _FRONTMATTER_HEADING_TITLES.union(
    _TOC_HEADING_TITLES,
    {"brief contents", "about this book", "about the cover illustration"},
)
_REFERENCES_HEADING_TITLES = {
    "references",
    "bibliography",
    "works cited",
    "further reading",
    "notes",
}
_INDEX_HEADING_TITLES = {"index", "subject index", "name index", "general index"}
_BACKMATTER_HEADING_TITLES = {
    "upcoming titles",
    "more books",
    "more from manning",
    "other books you may enjoy",
    "about the author",
    "about this book",
    "reader services",
    "share your thoughts",
}
_OUTLINED_BOOK_FRONTMATTER_TITLES = {
    "acknowledgment",
    "acknowledgments",
    "acknowledgements",
    "dedication",
    "foreword",
    "introduction",
    "preface",
    "prologue",
}
_BOOK_SPECIAL_OUTLINE_TITLES = _FRONTMATTER_SIGNAL_TITLES.union(
    _REFERENCES_HEADING_TITLES,
    _INDEX_HEADING_TITLES,
    _BACKMATTER_HEADING_TITLES,
    {"copyright", "glossary", "colophon"},
)
_REFERENCE_YEAR_PATTERN = re.compile(r"(?:\(|\b)(?:19|20)\d{2}[a-z]?(?:\)|\b)")
_REFERENCE_CITATION_PATTERN = re.compile(r"\[\s*\d+\s*\]")
_INDEX_TRAILING_PAGES_PATTERN = re.compile(
    r"(?:\d+(?:[-\u2013]\d+)?)(?:,\s*\d+(?:[-\u2013]\d+)?)*$"
)
_INDEX_ALPHA_WORD_PATTERN = re.compile(r"[A-Za-z]{3,}")
_CHAPTER_INTRO_CUE_PATTERN = re.compile(r"^(?:this chapter covers|in this chapter\b)", re.IGNORECASE)
_CHAPTER_TITLE_BREAK_WORDS = {"after", "as", "in", "no", "now", "the", "this", "when", "while"}
_HEADING_CONTINUATION_START_WORDS = {
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "via",
    "with",
    "without",
}
_PROSE_CONTINUATION_START_WORDS = _HEADING_CONTINUATION_START_WORDS.union(
    {
        "because",
        "but",
        "that",
        "which",
        "who",
        "whose",
        "where",
        "when",
        "while",
    }
)
_MULTI_COLUMN_BLOCK_WIDTH_RATIO = 0.58
_BOOK_INLINE_HEADING_MAX_WORDS = 10
_CONTEXTUAL_IMAGE_LEGEND_START_WORDS = {"this", "these", "the"}
_TITLE_OCTAL_ESCAPE_PATTERN = re.compile(r"\\\d{3}")
_TITLE_SINGLE_LETTER_SEQUENCE_PATTERN = re.compile(r"\b(?:[A-Za-z]\s+){2,}[A-Za-z]\b")
_TITLE_UPPERCASE_SENTENCE_RESTART_PATTERN = re.compile(r"\b(?:[A-Z]\s+)(?:[A-Za-z]\s+){4,}[A-Za-z]\b")
_TITLE_CAPITAL_LEAD_FRAGMENT_PATTERN = re.compile(r"\b([A-Z])\s+([a-z]{2,})\b")
_APPENDIX_SHORT_LABEL_LEAD_PATTERN = re.compile(r"^[A-Z](?:[).:\-])?\s+[A-Z]")
_APPENDIX_SHORT_LABEL_PATTERN = re.compile(r"^([A-Z])(?:[).:\-])?\b")
_APPENDIX_SECTION_SUBHEADING_PATTERN = re.compile(r"^(?P<label>[A-Z]\.\d+(?:\.\d+)*)\s+(?P<title>.+)$")
_APPENDIX_LABEL_PATTERN = re.compile(
    r"^(?:appendix\s+(?:[A-Z]|\d+|[ivxlcdm]+)\b[:.\-]?\s*|[A-Z](?:\.\d+)?\s+)",
    re.IGNORECASE,
)
_APPENDIX_TITLE_ARTIFACT_MARKER_PATTERN = re.compile(r"\b(?:table|figure|section)\s+\d+\b", re.IGNORECASE)
_APPENDIX_TITLE_TRAILING_METADATA_PATTERN = re.compile(r"\s*\((?:priority|section)\b[^)]*\)\s*$", re.IGNORECASE)
_LEADING_PAGE_LABEL_PATTERN = re.compile(r"^(?:\d+|[ivxlcdm]+)\s+", re.IGNORECASE)
_APPENDIX_TITLE_BREAK_WORDS = {
    "all",
    "and",
    "describes",
    "documents",
    "each",
    "every",
    "lists",
    "provides",
    "reproduces",
    "serves",
    "shows",
    "summarizes",
    "that",
    "the",
    "these",
    "this",
    "used",
    "uses",
    "with",
}
_TITLE_FRAGMENT_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "our",
    "the",
    "that",
    "their",
    "these",
    "this",
    "those",
    "to",
    "up",
    "we",
    "when",
    "while",
    "with",
    "you",
    "your",
}
_BACKMATTER_WEB_CUE_PATTERN = re.compile(r"\b(?:www\.)?[A-Za-z0-9.-]+\.(?:com|org|io|ai|dev)\b", re.IGNORECASE)
_BACKMATTER_PRICE_CUE_PATTERN = re.compile(r"(?:[$\u00a3\u20ac]\s?\d|\b\d+\s+pages\b)", re.IGNORECASE)
_BACKMATTER_ISBN_CUE_PATTERN = re.compile(r"\bisbn(?:-1[03])?\b", re.IGNORECASE)
_BACKMATTER_PUBLISHER_CUE_PATTERN = re.compile(
    r"\b(?:manning|oreilly|o'reilly|packt|apress|pragmatic|wiley|leanpub)\b",
    re.IGNORECASE,
)
_REFERENCE_LOCATOR_PATTERN = re.compile(r"\b(?:https?://|doi\.org/|arxiv:)\S+", re.IGNORECASE)
_ACADEMIC_NUMBERED_SECTION_PATTERN = re.compile(r"\b\d+(?:\.\d+){0,2}\b")
_ACADEMIC_INLINE_HEADING_BOUNDARY_PATTERN = re.compile(r"[.!?;:]\s+$")
_ACADEMIC_HEADING_CONNECTOR_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "via",
    "with",
}
_ACADEMIC_HEADING_LOWERCASE_TOKENS = {
    "work",
    "wrong",
}
_ACADEMIC_HEADING_TAIL_STOPWORDS = {
    "additionally",
    "however",
    "instead",
    "moreover",
    "similarly",
    "therefore",
}
_ACADEMIC_HEADING_TAIL_NOUNS = {
    "attention",
    "architecture",
    "batching",
    "conclusion",
    "conclusions",
    "data",
    "evaluation",
    "experiments",
    "introduction",
    "methods",
    "model",
    "models",
    "results",
    "stacks",
    "training",
}
_ACADEMIC_SINGLE_TOKEN_HEADING_WORDS = {
    "abstract",
    "approach",
    "background",
    "conclusion",
    "conclusions",
    "discussion",
    "evaluation",
    "experiments",
    "introduction",
    "motivation",
    "overview",
    "results",
    "training",
}
_ACADEMIC_STANDALONE_HEADING_TITLES = {
    "abstract",
    "background",
    "conclusion",
    "conclusions",
    "discussion",
    "evaluation",
    "future work",
    "implementation details",
    "introduction",
    "method",
    "methods",
    "related work",
    "results",
    # NOTE: "training" and "model" used to be in this set but they
    # collided destructively with book prose ("Training LLMs is
    # expensive ..." was treated as standalone heading "Training" +
    # body "LLMs is expensive Training an LLM ...", clobbering the
    # callout title). They're rare as bare academic section titles
    # anyway — real papers say "Training Procedure" / "Model
    # Architecture". Drop them to avoid the collision.
}
_PAPER_TITLE_INSTITUTION_CUE_PATTERN = re.compile(
    r"\b(?:university|institute|department|school|college|laboratory|lab|centre|center)\b",
    re.IGNORECASE,
)
_ARXIV_LEAD_PATTERN = re.compile(
    r"^arxiv:\S+(?:\s+\[[^\]]+\])?(?:\s+\d{1,2}\s+[A-Za-z]{3}\s+\d{4})?\s+",
    re.IGNORECASE,
)
_BROKEN_REFERENCES_HEADING_PATTERN = re.compile(
    r"^(?:r\s*e\s*f\s*e\s*r\s*e\s*n\s*c\s*e\s*s|bibliography|works\s+cited|further\s+reading|notes)\b",
    re.IGNORECASE,
)


def _normalize_outline_title(text: str) -> str:
    title, _page_number = _split_toc_entry(text)
    return title.casefold()


def _normalize_outline_heading_text(text: str) -> str:
    return _normalize_intro_title_artifacts(_normalize_pdf_signal_text(_normalize_text(text)))


# Outline-title chapter-number extraction. Two accepted forms:
#   1. "Chapter N Title..." \u2014 explicit Chapter/Part/Appendix label.
#   2. "N Title..."         \u2014 bare-integer prefix used by Manning Press
#                             and other publishers that omit the literal
#                             word "chapter" in TOC entries.
# Section-style numbering ("1.1", "2.3.1") is intentionally REJECTED in
# both patterns because subsections are not chapters.
_BOOK_OUTLINE_CHAPTER_LABEL_RE = re.compile(
    r"^\s*chapter\s+(\d+)\b", re.IGNORECASE
)
# Bare integer + whitespace + non-digit. The lookahead `(?=\s)` ensures
# we don't match "1.1" (next char is "." not whitespace) or "1st" (next
# char is "s" not whitespace).
_BOOK_OUTLINE_CHAPTER_BARE_RE = re.compile(r"^\s*(\d+)(?=\s)")


def _looks_like_book_primary_outline_title(text: str) -> bool:
    normalized = _normalize_outline_heading_text(text)
    if not normalized:
        return False
    if _HEADING_PATTERN.match(normalized) or _APPENDIX_HEADING_PATTERN.match(normalized):
        return True
    # Books that omit "Chapter" in their outline (e.g. Manning's
    # "1 Big picture: What are LLMs?") would otherwise yield zero
    # primary outline entries, breaking the chapter-split code path.
    return _extract_book_main_chapter_number(normalized) is not None


def _should_keep_book_top_level_outline_title(text: str) -> bool:
    normalized = _normalize_outline_heading_text(text)
    if not normalized:
        return False
    lowered = normalized.casefold()
    if _looks_like_book_primary_outline_title(normalized):
        return True
    return lowered in _BOOK_SPECIAL_OUTLINE_TITLES


def _extract_book_main_chapter_number(text: str) -> int | None:
    """Return the chapter number for a book outline title.

    Accepts either ``"Chapter N Title\u2026"`` or the bare-integer form
    ``"N Title\u2026"``. Returns ``None`` for section-style numbering
    (``"1.1 \u2026"``), for titles without a Title-cased word following
    the number (e.g. lowercase frontmatter labels), or for any input
    that doesn't fit either pattern.
    """
    normalized = _normalize_outline_heading_text(text)
    if not normalized:
        return None

    match = _BOOK_OUTLINE_CHAPTER_LABEL_RE.match(normalized)
    if match is None:
        match = _BOOK_OUTLINE_CHAPTER_BARE_RE.match(normalized)
        if match is None:
            return None

    remainder = normalized[match.end():]
    normalized_remainder = remainder.lstrip(" .:_-)\u2013\u2014")
    # The character following the number+separator must start a real
    # title (uppercase letter). This rejects "1 introduction" (lowercase
    # frontmatter-ish) and "1." standalone entries.
    if not normalized_remainder or not normalized_remainder[:1].isupper():
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _looks_like_outlined_book_frontmatter_title(text: str | None) -> bool:
    normalized = _normalize_outline_heading_text(text or "")
    return normalized.casefold() in _OUTLINED_BOOK_FRONTMATTER_TITLES


def _looks_like_outlined_book_appendix_title(text: str | None) -> bool:
    normalized = _normalize_outline_heading_text(text or "")
    return bool(_APPENDIX_LABEL_PATTERN.match(normalized))


def _looks_like_outlined_book_glossary_title(text: str | None) -> bool:
    normalized = _normalize_outline_heading_text(text or "")
    return normalized.casefold() == "glossary"


def _should_start_outlined_book_top_level_chapter(title: str | None) -> bool:
    if _extract_book_main_chapter_number(title or "") is not None:
        return True
    if _looks_like_outlined_book_appendix_title(title):
        return True
    if _looks_like_outlined_book_glossary_title(title):
        return True
    return _looks_like_outlined_book_frontmatter_title(title)


def _parse_page_number(text: str) -> int | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    if normalized.isdigit():
        return int(normalized)
    return _roman_to_int(normalized)


def _split_toc_entry(text: str) -> tuple[str, int | None]:
    normalized = _normalize_text(text)
    match = _TOC_ENTRY_PATTERN.match(normalized)
    if not match:
        return normalized, None
    return _normalize_text(match.group("title")), _parse_page_number(match.group("page"))


def _strip_leading_page_label(text: str) -> str:
    return _LEADING_PAGE_LABEL_PATTERN.sub("", _normalize_text(text), count=1).strip()


def _collapse_spaced_title_artifacts(text: str) -> str:
    collapsed = _normalize_text(text)
    previous = None
    while previous != collapsed:
        previous = collapsed

        def _merge_leading_capital_fragment(match: re.Match[str]) -> str:
            left = match.group(1)
            right = match.group(2)
            if left == "A":
                prefix = collapsed[: match.start()].rstrip()
                previous_char = prefix[-1] if prefix else ""
                # Preserve article-like "A deep" at the start of a title or after punctuation.
                if not prefix or previous_char in ":;,.!?-–—(":
                    return f"{left} {right}"
            return f"{left}{right}"

        collapsed = _TITLE_CAPITAL_LEAD_FRAGMENT_PATTERN.sub(_merge_leading_capital_fragment, collapsed)
    return collapsed


def _normalize_intro_title_artifacts(text: str) -> str:
    normalized = _TITLE_OCTAL_ESCAPE_PATTERN.sub(" ", text or "")
    normalized = _TITLE_SINGLE_LETTER_SEQUENCE_PATTERN.sub(
        lambda match: "".join(match.group(0).split()),
        normalized,
    )
    normalized = _collapse_spaced_title_artifacts(normalized)
    previous = None
    while previous != normalized:
        previous = normalized
        tokens = normalized.split()
        merged_tokens: list[str] = []
        index = 0
        while index < len(tokens):
            if index + 1 < len(tokens):
                left = tokens[index]
                right = tokens[index + 1]
                left_clean = re.sub(r"[^A-Za-z]", "", left)
                right_clean = re.sub(r"[^A-Za-z]", "", right)
                left_title_fragment = bool(
                    left_clean
                    and left_clean[:1].isupper()
                    and left_clean[1:].islower()
                )
                if (
                    left_clean
                    and right_clean
                    and left_clean.casefold() not in _TITLE_FRAGMENT_STOPWORDS
                    and right_clean.casefold() not in _TITLE_FRAGMENT_STOPWORDS
                    and (
                        (
                            (
                                (1 <= len(left_clean) <= 2 and len(right_clean) >= 4)
                                or (1 <= len(left_clean) <= 3 and 1 <= len(right_clean) <= 2)
                                or (len(left_clean) >= 2 and len(right_clean) == 1)
                            )
                            and left_clean[:1].islower()
                            and right_clean[:1].islower()
                        )
                        or (
                            left_title_fragment
                            and right_clean[:1].islower()
                            and (len(left_clean) <= 2 or len(right_clean) == 1)
                        )
                    )
                ):
                    merged_tokens.append(left + right)
                    index += 2
                    continue
            merged_tokens.append(tokens[index])
            index += 1
        normalized = _normalize_text(" ".join(merged_tokens))
    return normalized


def _normalize_pdf_signal_text(text: str) -> str:
    return _normalize_text(re.sub(r"[\x00-\x1f]+", " ", text or ""))


def _normalize_paper_title_candidate(text: str) -> str:
    normalized = _normalize_intro_title_artifacts(_normalize_pdf_signal_text(text))
    previous = None
    while previous != normalized:
        previous = normalized
        tokens = normalized.split()
        merged_tokens: list[str] = []
        index = 0
        while index < len(tokens):
            if index + 1 < len(tokens):
                left = tokens[index]
                right = tokens[index + 1]
                left_clean = re.sub(r"[^A-Za-z]", "", left)
                right_clean = re.sub(r"[^A-Za-z]", "", right)
                if (
                    left_clean
                    and right_clean
                    and left_clean[:1].isupper()
                    and left_clean[1:].islower()
                    and right_clean[:1].islower()
                    and right_clean.casefold() not in _TITLE_FRAGMENT_STOPWORDS
                    and len(right_clean) <= 4
                ):
                    merged_tokens.append(left + right)
                    index += 2
                    continue
            merged_tokens.append(tokens[index])
            index += 1
        normalized = _normalize_text(" ".join(merged_tokens))
    return normalized


def _looks_like_paper_title(text: str) -> bool:
    normalized = _normalize_paper_title_candidate(text)
    if not normalized or "@" in normalized:
        return False
    words = normalized.split()
    if not 5 <= len(words) <= 24:
        return False
    if sum(1 for char in normalized if char.isdigit()) > 1:
        return False
    alpha_words = [re.sub(r"[^A-Za-z-]", "", word) for word in words]
    alpha_words = [word for word in alpha_words if word]
    if len(alpha_words) < 5:
        return False
    capitalized_count = sum(1 for word in alpha_words if word[:1].isupper())
    lowercase_count = sum(1 for word in alpha_words if word[:1].islower())
    return capitalized_count >= max(4, len(alpha_words) - 4) and lowercase_count <= max(4, len(alpha_words) // 2)


def _looks_like_visual_heading(text: str, line_count: int) -> bool:
    normalized = _normalize_multiline_text(text)
    compact = _normalize_text(normalized)
    if not compact or len(compact) > 160:
        return False
    if line_count > 4:
        return False
    if compact.endswith((".", "!", ";")):
        return False
    if re.search(r"[=∼≤≥\[\]{}|]", compact):
        return False
    if _looks_like_paper_title(compact):
        return True
    if _leading_academic_standalone_heading(compact) is not None:
        return True
    if re.match(r"^\d+(?:\.\d+){0,2}\s+[A-Z]", compact):
        return True

    words = compact.split()
    alpha_words = [re.sub(r"[^A-Za-z-]", "", word) for word in words]
    alpha_words = [word for word in alpha_words if word]
    if not alpha_words or len(alpha_words) > 14:
        return False

    titleish_words = 0
    lowercase_words = 0
    for word in alpha_words:
        lowered = word.casefold()
        if lowered in _ACADEMIC_HEADING_CONNECTOR_WORDS:
            continue
        if word[:1].isupper():
            titleish_words += 1
        elif word[:1].islower():
            lowercase_words += 1

    if len(alpha_words) == 1 and compact.casefold() in {"preface", "foreword", "index"}:
        return True
    if compact.endswith("?"):
        return titleish_words >= 2 and lowercase_words <= max(4, len(alpha_words) // 2)

    return titleish_words >= max(2, len(alpha_words) - 3) and lowercase_words <= 1


def _looks_like_author_affiliation_start(tokens: list[str], index: int) -> bool:
    if index + 2 >= len(tokens):
        return False
    if not _is_name_like_token(tokens[index]) or not _is_name_like_token(tokens[index + 1]):
        return False
    if not tokens[index + 2].isdigit():
        return False
    lookahead = tokens[index : index + 18]
    digit_count = sum(1 for token in lookahead if token.isdigit())
    lookahead_text = " ".join(lookahead)
    return bool(
        digit_count >= 2
        or "@" in lookahead_text
        or _PAPER_TITLE_INSTITUTION_CUE_PATTERN.search(lookahead_text)
    )


def _infer_first_page_paper_title_and_remainder(text: str) -> tuple[str, str] | None:
    normalized = _normalize_intro_title_artifacts(_strip_leading_page_label(_normalize_pdf_signal_text(text)))
    if not normalized:
        return None
    stripped_prefix = _ARXIV_LEAD_PATTERN.sub("", normalized, count=1)
    prefix_token_count = max(0, len(normalized.split()) - len(stripped_prefix.split()))
    full_tokens = stripped_prefix.split()
    if len(full_tokens) < 12:
        return None

    author_boundary: int | None = None
    for index in range(4, min(len(full_tokens) - 2, 40)):
        if not _looks_like_author_affiliation_start(full_tokens, index):
            continue
        title_candidate = _normalize_paper_title_candidate(" ".join(full_tokens[:index]))
        if _looks_like_paper_title(title_candidate):
            author_boundary = index
            break
    if author_boundary is None:
        return None

    title = _normalize_paper_title_candidate(" ".join(full_tokens[:author_boundary])).strip(" -:;,.")
    if not _looks_like_paper_title(title):
        return None

    original_tokens = normalized.split()
    remainder = _normalize_text(" ".join(original_tokens[prefix_token_count + author_boundary :]))
    if not remainder:
        return None
    return title, remainder


def _leading_reference_heading_and_remainder(text: str) -> tuple[str, str] | None:
    normalized = _strip_leading_page_label(_normalize_pdf_signal_text(text))
    if not normalized:
        return None
    lowered = normalized.casefold()
    downloaded_from_marker = "downloaded from "
    if downloaded_from_marker in lowered:
        download_index = lowered.find(downloaded_from_marker)
        normalized = normalized[download_index + len(downloaded_from_marker) :].lstrip()
        lowered = normalized.casefold()
    heading_text = _section_family_display_title("references")
    if lowered.startswith("references and notes"):
        heading_text = "References and Notes"
        remainder = _normalize_intro_title_artifacts(normalized[len("References and Notes") :]).strip(" -:;,.")
    else:
        match = _BROKEN_REFERENCES_HEADING_PATTERN.match(normalized)
        if match is None:
            return None
        remainder = _normalize_intro_title_artifacts(normalized[match.end() :]).strip(" -:;,.")
    if not remainder:
        return heading_text, ""
    first_remainder_line = next(
        (_normalize_text(line) for line in remainder.splitlines() if _normalize_text(line)),
        "",
    )
    if (
        remainder.startswith("[")
        or _looks_like_reference_entry(remainder)
        or bool(re.match(r"^(?:\[\s*\d+\s*\]|\d+[.)])\s+", first_remainder_line))
    ):
        return heading_text, remainder
    return None


def _section_family_display_title(section_family: str) -> str:
    return {
        "frontmatter": "Front Matter",
        "appendix": "Appendix",
        "references": "References",
        "index": "Index",
        "backmatter": "Back Matter",
    }.get(section_family, "Document")


def _looks_like_toc_heading(text: str) -> bool:
    return _normalize_text(text).casefold() in _TOC_HEADING_TITLES


def _page_family_for_heading(text: str, page_number: int) -> str | None:
    normalized = _normalize_text(text).casefold()
    if not normalized:
        return None
    if _APPENDIX_HEADING_PATTERN.match(normalized):
        return "appendix"
    if normalized in _REFERENCES_HEADING_TITLES:
        return "references"
    if normalized in _INDEX_HEADING_TITLES:
        return "index"
    if page_number <= 6 and normalized in _FRONTMATTER_HEADING_TITLES:
        return "frontmatter"
    return None


def _inline_page_family_heading(text: str, page_number: int) -> tuple[str, str | None] | None:
    stripped = _normalize_intro_title_artifacts(_strip_leading_page_label(_normalize_pdf_signal_text(text)))
    if not stripped:
        return None
    heading_family = _page_family_for_heading(stripped, page_number)
    if heading_family is not None:
        if heading_family == "appendix":
            return heading_family, re.sub(r"^appendix\b", "Appendix", stripped, count=1, flags=re.IGNORECASE)
        if heading_family in {"references", "index", "frontmatter"}:
            return heading_family, _section_family_display_title(heading_family)
        return heading_family, stripped

    reference_heading = _leading_reference_heading_and_remainder(stripped)
    if reference_heading is not None:
        return "references", reference_heading[0]

    lowered = stripped.casefold()
    for heading in sorted(_INDEX_HEADING_TITLES, key=len, reverse=True):
        if not lowered.startswith(f"{heading} "):
            continue
        tail = stripped[len(heading) :].lstrip()
        if not tail:
            return "index", heading.title()
        first_token = tail.split()[0]
        if _parse_page_number(first_token) is not None or any(char.isdigit() for char in tail[:18]):
            return "index", heading.title()
    return None


def _looks_like_titleish_backmatter_lead(text: str) -> bool:
    normalized = _strip_leading_page_label(text)
    if not normalized or len(normalized) > 72:
        return False
    words = normalized.split()
    if not 1 <= len(words) <= 8:
        return False
    alpha_words = [re.sub(r"[^A-Za-z]", "", word) for word in words]
    meaningful_words = [word for word in alpha_words if len(word) >= 2]
    if not meaningful_words:
        return False
    capitalized_count = sum(1 for word in words if word[:1].isupper())
    return capitalized_count >= max(1, len(meaningful_words) - 1)


def _detect_backmatter_cue(texts: list[str]) -> tuple[str, str] | None:
    substantive_texts = [
        _strip_leading_page_label(text)
        for text in texts
        if _strip_leading_page_label(text) and not _is_page_number_text(text)
    ][:6]
    if not substantive_texts:
        return None

    first_text = substantive_texts[0]
    first_lower = first_text.casefold()
    if first_lower in _BACKMATTER_HEADING_TITLES:
        return first_text, "heading_title"

    combined = " ".join(substantive_texts)
    signal_count = 0
    if _BACKMATTER_ISBN_CUE_PATTERN.search(combined):
        signal_count += 1
    if _BACKMATTER_PRICE_CUE_PATTERN.search(combined):
        signal_count += 1
    if _BACKMATTER_WEB_CUE_PATTERN.search(combined):
        signal_count += 1
    if _BACKMATTER_PUBLISHER_CUE_PATTERN.search(combined):
        signal_count += 1

    if signal_count >= 2 and _looks_like_titleish_backmatter_lead(first_text):
        return first_text, "marketing_signals"
    return None


def _looks_like_frontmatter_signal(text: str) -> bool:
    normalized = _normalize_text(text).casefold()
    if not normalized:
        return False
    stripped = re.sub(r"^(?:\d+|[ivxlcdm]+)\s+", "", normalized, count=1)
    return any(stripped.startswith(title) for title in _FRONTMATTER_SIGNAL_TITLES)


def _looks_like_title_page_metadata_signal(text: str) -> bool:
    normalized = _normalize_text(text).casefold()
    if not normalized:
        return False
    return any(
        cue in normalized
        for cue in (
            "available at",
            "version was published on",
            "leanpub",
            "copyright",
            "©",
        )
    )


def _looks_like_reference_entry(text: str) -> bool:
    normalized = _normalize_text(text)
    if len(normalized) < 24:
        return False
    lowered = normalized.casefold()
    compacted = lowered.replace(" ", "")
    citation_marker_count = len(_REFERENCE_CITATION_PATTERN.findall(normalized))
    year_count = len(_REFERENCE_YEAR_PATTERN.findall(normalized))
    has_reference_locator = bool(_REFERENCE_LOCATOR_PATTERN.search(normalized) or re.search(r"\bdoi\b", lowered))
    if has_reference_locator and compacted.startswith("references"):
        return True
    if has_reference_locator and normalized.lstrip().startswith("[") and (
        citation_marker_count >= 1 or year_count >= 1
    ):
        return True
    if has_reference_locator and year_count >= 1 and (
        citation_marker_count >= 1 or (normalized.count(".") >= 2 and "," in normalized and len(normalized) <= 320)
    ):
        return True
    if compacted.startswith("references") and (citation_marker_count >= 1 or year_count >= 1):
        return True
    if normalized.lstrip().startswith("[") and citation_marker_count >= 1 and year_count >= 1:
        return True
    return bool(
        len(normalized) <= 240
        and year_count >= 1
        and normalized.count(".") >= 2
        and "," in normalized
    )


def _looks_like_index_entry(text: str) -> bool:
    normalized = _normalize_text(text)
    match = _INDEX_TRAILING_PAGES_PATTERN.search(normalized)
    if len(normalized) < 6 or match is None:
        return False
    if _REFERENCE_YEAR_PATTERN.search(normalized):
        return False
    title_part = normalized[: match.start()].rstrip(",;: ")
    if not title_part or any(char in title_part for char in "\\[]{}=+/"):
        return False
    alpha_words = _INDEX_ALPHA_WORD_PATTERN.findall(title_part)
    if not alpha_words or len(title_part.split()) > 8:
        return False
    return max(len(word) for word in alpha_words) >= 3


def _looks_like_academic_heading_token(token: str) -> bool:
    stripped = token.strip("()[]{}.,;:").replace("\u2013", "-").replace("\u2014", "-")
    if not stripped:
        return False
    alpha_only = re.sub(r"[^A-Za-z]", "", stripped)
    if len(alpha_only) == 1:
        return False
    if stripped.isupper() and len(alpha_only) >= 2:
        return True
    if any(char.isdigit() for char in stripped):
        return False
    parts = [part for part in stripped.split("-") if part]
    if not parts:
        return False
    return all(
        part[:1].isupper() or (part.isupper() and len(part) >= 2)
        for part in parts
    )


def _leading_academic_standalone_heading(text: str) -> tuple[str, str] | None:
    normalized = _normalize_text(text)
    lowered = normalized.casefold()
    for heading in sorted(_ACADEMIC_STANDALONE_HEADING_TITLES, key=len, reverse=True):
        if lowered == heading:
            return heading.title(), ""
        prefix = f"{heading} "
        if not lowered.startswith(prefix):
            continue
        remainder = normalized[len(prefix):].strip()
        if not remainder:
            return heading.title(), ""
        if _looks_like_academic_prose_lead(remainder):
            return heading.title(), remainder
    return None


def _embedded_academic_abstract_segments(text: str) -> tuple[str, str, str] | None:
    normalized = _normalize_multiline_text(text)
    if not normalized:
        return None
    match = re.search(r"\b(?:ABSTRACT|Abstract)\b", normalized)
    if match is None:
        return None
    prefix = normalized[: match.start()].strip()
    remainder = normalized[match.end() :].strip(" -:;,.")
    if not remainder or not _looks_like_academic_prose_lead(remainder):
        return None
    if prefix:
        lowered_prefix = prefix.casefold()
        frontmatterish = any(
            cue in lowered_prefix
            for cue in (
                "@",
                "department",
                "university",
                "research",
                "institute",
                "laboratory",
                "college",
                "school",
                "arxiv:",
            )
        )
        if not frontmatterish and _looks_like_academic_prose_lead(prefix):
            return None
    return prefix, "Abstract", remainder


def _consume_academic_heading_title(candidate_text: str) -> tuple[str, str] | None:
    tokens = candidate_text.split()
    if not tokens:
        return None
    title_tokens: list[str] = []
    content_tokens = 0
    for index, token in enumerate(tokens):
        stripped = token.strip("()[]{}.,;:?!")
        lowered = stripped.casefold()
        remaining = " ".join(tokens[index:]).strip()
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        next_stripped = (next_token or "").strip("()[]{}.,;:?!")
        next_lowered = next_stripped.casefold()
        if lowered in _ACADEMIC_HEADING_CONNECTOR_WORDS and title_tokens:
            if next_token is not None and (
                _looks_like_academic_heading_token(next_stripped)
                or next_lowered in _ACADEMIC_HEADING_CONNECTOR_WORDS
                or next_lowered in _ACADEMIC_HEADING_LOWERCASE_TOKENS
            ):
                title_tokens.append(token)
                continue
        if (
            title_tokens
            and lowered in _ACADEMIC_BODY_STARTER_WORDS
            and (
                content_tokens >= 2
                or (content_tokens == 1 and title_tokens[0].strip("()[]{}.,;:").casefold() in _ACADEMIC_SINGLE_TOKEN_HEADING_WORDS)
            )
        ):
            break
        if title_tokens and lowered in _ACADEMIC_BODY_STARTER_WORDS and _looks_like_academic_prose_lead(remaining):
            break
        if title_tokens and lowered in _ACADEMIC_HEADING_LOWERCASE_TOKENS:
            title_tokens.append(token)
            content_tokens += 1
            if content_tokens >= 6:
                break
            continue
        if (
            title_tokens
            and stripped.islower()
            and not _is_broken_word_fragment(title_tokens[-1].strip("()[]{}.,;:?!"))
            and lowered not in _ACADEMIC_BODY_STARTER_WORDS
            and next_token is not None
            and (
                _looks_like_academic_heading_token(next_stripped)
                or next_lowered in _ACADEMIC_HEADING_CONNECTOR_WORDS
            )
        ):
            title_tokens.append(token)
            content_tokens += 1
            if content_tokens >= 6:
                break
            continue
        if _looks_like_academic_heading_token(stripped):
            prospective_remainder = " ".join(tokens[index + 1 :]).strip()
            if (
                title_tokens
                and content_tokens >= 1
                and prospective_remainder
                and _looks_like_academic_prose_lead(prospective_remainder)
                and lowered not in _ACADEMIC_HEADING_TAIL_NOUNS
                and not token.rstrip().endswith(("?", "!", ":"))
                and not _looks_like_academic_heading_token(next_stripped)
                and not _is_broken_word_fragment(stripped)
            ):
                break
            title_tokens.append(token)
            content_tokens += 1
            if content_tokens >= 6:
                break
            continue
        if content_tokens >= 2:
            break
        if content_tokens == 1 and title_tokens[0].strip("()[]{}.,;:").casefold() in _ACADEMIC_SINGLE_TOKEN_HEADING_WORDS:
            break
        return None

    if not title_tokens:
        return None
    heading_text = _normalize_intro_title_artifacts(" ".join(title_tokens))
    if not heading_text or len(heading_text) > 96:
        return None
    remainder = candidate_text[len(" ".join(title_tokens)) :].strip()
    return heading_text, remainder


def _looks_like_academic_body_continuation(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    tokens = normalized.split()
    if not tokens:
        return False

    first_token = tokens[0].strip("()[]{}.,;:")
    first_lowered = first_token.casefold()
    if first_lowered in _ACADEMIC_BODY_STARTER_WORDS:
        return True

    sample = tokens[:8]
    lowercaseish = 0
    uppercaseish = 0
    numericish = 0
    for token in sample:
        stripped = token.strip("()[]{}.,;:")
        if not stripped:
            continue
        alpha_only = re.sub(r"[^A-Za-z]", "", stripped)
        if any(char.isdigit() for char in stripped):
            numericish += 1
            continue
        if alpha_only and alpha_only.isupper() and len(alpha_only) >= 2:
            uppercaseish += 1
            continue
        if alpha_only and alpha_only[0].islower():
            lowercaseish += 1

    if uppercaseish + numericish >= 3 and lowercaseish <= 1:
        return False
    return lowercaseish >= 2


def _extend_broken_academic_heading_fragment(
    heading_text: str,
    remainder: str,
) -> tuple[str, str]:
    if not remainder:
        return heading_text, remainder
    match = re.match(r"^(?P<label>\d+(?:\.\d+){0,2})\s+(?P<title>.+)$", heading_text)
    if match is None:
        return heading_text, remainder

    title_tokens = match.group("title").split()
    if not title_tokens:
        return heading_text, remainder
    last_token = title_tokens[-1].strip("()[]{}.,;:")
    last_token_alpha_len = len(re.sub(r"[^A-Za-z]", "", last_token))
    remainder_tokens = remainder.split()
    if not remainder_tokens:
        return heading_text, remainder
    first_fragment = remainder_tokens[0].strip("()[]{}.,;:")
    first_fragment_alpha = re.sub(r"[^A-Za-z]", "", first_fragment)
    if (
        not last_token
        or not last_token[:1].isupper()
        or (
            last_token_alpha_len > 4
            and len(first_fragment_alpha) > 2
            and "-" not in last_token
        )
    ):
        return heading_text, remainder

    fragment_tokens: list[str] = []
    short_tail_only = last_token_alpha_len > 4 and "-" not in last_token
    long_fragment_consumed = False
    for token in remainder_tokens[:3]:
        stripped = token.strip("()[]{}.,;:")
        alpha_only = re.sub(r"[^A-Za-z]", "", stripped)
        if not alpha_only or not alpha_only[:1].islower():
            break
        if short_tail_only and len(alpha_only) > 2:
            break
        if long_fragment_consumed and len(alpha_only) > 2:
            break
        fragment_tokens.append(token)
        if len(alpha_only) >= 4:
            long_fragment_consumed = True
    if not fragment_tokens:
        return heading_text, remainder

    heading_tokens = heading_text.split()
    merged_fragment_tokens = [*fragment_tokens]
    if merged_fragment_tokens:
        heading_tokens[-1] = f"{heading_tokens[-1]}{merged_fragment_tokens[0]}"
        merged_fragment_tokens = merged_fragment_tokens[1:]
    consumed_prefix = " ".join(merged_fragment_tokens)
    extended_heading = _normalize_intro_title_artifacts(" ".join([*heading_tokens, consumed_prefix]).strip())
    if extended_heading == heading_text:
        return heading_text, remainder
    updated_remainder = " ".join(remainder_tokens[len(fragment_tokens) :]).strip()
    return _extend_academic_heading_trailing_token(
        extended_heading,
        updated_remainder,
        fragment_repaired=True,
    )


def _extend_academic_heading_trailing_token(
    heading_text: str,
    remainder: str,
    *,
    fragment_repaired: bool = False,
) -> tuple[str, str]:
    if not remainder or not fragment_repaired:
        return heading_text, remainder
    match = re.match(r"^(?P<label>\d+(?:\.\d+){0,2})\s+(?P<title>.+)$", heading_text)
    if match is None:
        return heading_text, remainder
    remainder_tokens = remainder.split()
    if not remainder_tokens:
        return heading_text, remainder

    next_token = remainder_tokens[0].strip("()[]{}.,;:")
    next_alpha = re.sub(r"[^A-Za-z]", "", next_token)
    if (
        not next_alpha
        or not next_alpha[:1].isupper()
        or next_alpha.casefold() in _ACADEMIC_BODY_STARTER_WORDS
        or next_alpha.casefold() in _ACADEMIC_HEADING_TAIL_STOPWORDS
        or next_alpha.casefold() not in _ACADEMIC_HEADING_TAIL_NOUNS
        or len(next_alpha) < 3
    ):
        return heading_text, remainder

    updated_remainder = " ".join(remainder_tokens[1:]).strip()
    if not updated_remainder or not _looks_like_academic_prose_lead(updated_remainder):
        return heading_text, remainder

    extended_heading = _normalize_intro_title_artifacts(f"{heading_text} {remainder_tokens[0]}")
    if not extended_heading or extended_heading == heading_text:
        return heading_text, remainder
    return extended_heading, updated_remainder


def _move_colon_heading_tail_into_body(
    heading_text: str,
    remainder: str,
) -> tuple[str, str]:
    words = heading_text.split()
    if len(words) < 4 or not words[-1].endswith(":") or not remainder:
        return heading_text, remainder

    trailing = words[-1]
    trailing_clean = trailing.rstrip(":").strip("()[]{}.,;:")
    if not trailing_clean or len(trailing_clean) <= 2:
        return heading_text, remainder

    candidate_heading = _normalize_intro_title_artifacts(" ".join(words[:-1]))
    candidate_body = _normalize_text(f"{trailing} {remainder}")
    if not candidate_heading or not _looks_like_academic_body_continuation(candidate_body):
        return heading_text, remainder
    return candidate_heading, candidate_body


def _clean_academic_heading_candidate(
    heading_text: str,
    remainder: str,
    heading_kind: str | None,
) -> tuple[str, str] | None:
    cleaned_heading = _normalize_intro_title_artifacts(heading_text)
    cleaned_remainder = _normalize_text(remainder)

    if heading_kind == "standalone" and cleaned_remainder and not _looks_like_academic_prose_lead(cleaned_remainder):
        return None

    if heading_kind == "numbered":
        cleaned_heading, cleaned_remainder = _extend_broken_academic_heading_fragment(
            cleaned_heading,
            cleaned_remainder,
        )
        cleaned_heading, cleaned_remainder = _move_colon_heading_tail_into_body(
            cleaned_heading,
            cleaned_remainder,
        )

    if not cleaned_heading or len(cleaned_heading) > 120:
        return None
    return cleaned_heading, cleaned_remainder


def _next_academic_inline_heading(text: str) -> tuple[int, str, str, dict[str, Any]] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    # Short blocks ("1 Map text to tokens (chapter 2).") trip the numbered
    # section pattern but are list items inside a figure or step list, not
    # academic section headings. Real "1 Introduction"-style sections come
    # from blocks that contain a full paragraph after the title.
    if len(normalized) < 60:
        return None
    leading = _leading_academic_standalone_heading(normalized)
    if leading is not None:
        heading_text, remainder = leading
        cleaned = _clean_academic_heading_candidate(heading_text, remainder, "standalone")
        if cleaned is not None:
            cleaned_heading, cleaned_remainder = cleaned
            return 0, cleaned_heading, cleaned_remainder, {"heading_kind": "standalone", "section_level": 1}

    for match in _ACADEMIC_NUMBERED_SECTION_PATTERN.finditer(normalized):
        start = match.start()
        prefix = normalized[:start]
        if prefix and not _ACADEMIC_INLINE_HEADING_BOUNDARY_PATTERN.search(prefix):
            continue
        label = match.group(0)
        after_start = match.end()
        if after_start >= len(normalized) or not normalized[after_start].isspace():
            continue
        candidate_tail = normalized[after_start:].lstrip()
        consumed = _consume_academic_heading_title(candidate_tail)
        if consumed is None:
            continue
        title_body, remainder = consumed
        # If the remainder starts with a continuation preposition like "to",
        # "of", "for", the input is almost certainly one list item ("3 Add
        # information to each embedding ...") that the heading detector
        # would have wrongly split. _leading_numbered_book_heading_and_remainder
        # already applies this guard; mirror it here so the academic-inline
        # path doesn't slip past it.
        remainder_tokens = remainder.split()
        if remainder_tokens:
            raw_first = re.sub(r"[^A-Za-z'-]", "", remainder_tokens[0])
            first_remainder = raw_first.casefold()
            # Real academic body usually starts with capitalized "The".
            # Lowercase "the" is a list-item continuation ("5 Apply the
            # unembedding layer ...") and should still be rejected.
            if first_remainder in (_HEADING_CONTINUATION_START_WORDS - {"the"}):
                continue
            if first_remainder == "the" and raw_first != "The":
                continue
        cleaned = _clean_academic_heading_candidate(f"{label} {title_body}", remainder, "numbered")
        if cleaned is None:
            continue
        heading_text, cleaned_remainder = cleaned
        return start, heading_text, cleaned_remainder, {"heading_kind": "numbered", "section_level": label.count(".") + 2}
    return None


def _page_has_centered_title_signal(page: "PdfPage") -> bool:
    ordered_blocks = sorted(page.blocks, key=lambda block: (round(block.bbox[1], 2), round(block.bbox[0], 2)))
    page_center = page.width / 2.0
    for block in ordered_blocks[:4]:
        text = _normalize_text(block.text)
        if len(text) < 12 or len(text) > 180:
            continue
        if block.bbox[1] > page.height * 0.22:
            continue
        block_width = block.bbox[2] - block.bbox[0]
        block_center = (block.bbox[0] + block.bbox[2]) / 2.0
        if block_width > page.width * 0.62:
            continue
        if abs(block_center - page_center) > page.width * 0.2:
            continue
        if _looks_like_reference_entry(text) or _looks_like_index_entry(text):
            continue
        return True
    return False


def _page_has_title_overlap_signal(page: "PdfPage", title: str | None) -> bool:
    normalized_title = _normalize_intro_title_artifacts(_normalize_pdf_signal_text(title or ""))
    if len(normalized_title) < 12:
        return False
    compact_title = re.sub(r"\s+", "", normalized_title).casefold()
    ordered_blocks = sorted(page.blocks, key=lambda block: (round(block.bbox[1], 2), round(block.bbox[0], 2)))
    for block in ordered_blocks:
        text = _normalize_intro_title_artifacts(_normalize_pdf_signal_text(block.text))
        if len(text) < 12:
            continue
        compact_text = re.sub(r"\s+", "", text).casefold()
        if _titles_overlap(text, normalized_title) or compact_text == compact_title:
            return True
        if len(compact_text) >= 12 and (compact_text in compact_title or compact_title in compact_text):
            return True
    return False


def _early_page_has_title_signal(
    pages: list["PdfPage"],
    title: str | None,
    *,
    window: int = 2,
) -> bool:
    if not pages:
        return False
    for page in pages[: max(window, 1)]:
        if _page_has_centered_title_signal(page) or _page_has_title_overlap_signal(page, title):
            return True
    return False


def _page_has_reference_signature(page: "PdfPage") -> bool:
    texts = [_normalize_text(block.text) for block in page.blocks if _normalize_text(block.text)]
    if not texts:
        return False
    if any(_leading_reference_heading_and_remainder(text) is not None for text in texts[:3]):
        return True
    if any(
        text.casefold().startswith(title)
        for text in texts[:3]
        for title in _REFERENCES_HEADING_TITLES
    ):
        return True
    return any(_looks_like_reference_entry(text) for text in texts)


def _trailing_reference_page_count(pages: list["PdfPage"]) -> int:
    count = 0
    for page in reversed(pages):
        if _page_has_reference_signature(page):
            count += 1
            continue
        if count > 0:
            break
    return count


def _looks_like_chapter_intro_cue(text: str) -> bool:
    return bool(_CHAPTER_INTRO_CUE_PATTERN.match(_normalize_text(text)))


def _contains_chapter_intro_cue(text: str) -> bool:
    normalized = _normalize_text(text)
    lowered = normalized.casefold()
    return _looks_like_chapter_intro_cue(normalized) or "this chapter covers" in lowered


def _contains_appendix_intro_cue(text: str) -> bool:
    normalized = _normalize_text(text)
    lowered = normalized.casefold()
    cue_index = lowered.find("this appendix")
    return cue_index != -1 and cue_index <= 240


def _has_appendix_title_lead(text: str) -> bool:
    normalized = _strip_leading_page_label(text)
    return bool(
        normalized
        and (
            normalized.casefold().startswith("appendix ")
            or _APPENDIX_SHORT_LABEL_LEAD_PATTERN.match(normalized)
        )
    )


def _infer_appendix_intro_title(text: str) -> str | None:
    normalized = _collapse_spaced_title_artifacts(text)
    lowered = normalized.casefold()
    cue_index = lowered.find("this appendix")
    has_appendix_lead = _has_appendix_title_lead(normalized)
    if cue_index == -1 or (cue_index > 240 and not has_appendix_lead):
        return None
    prefix = normalized[:cue_index].strip(" -:;,.")
    if not prefix:
        return None
    has_prefix_label = bool(
        prefix.casefold().startswith("appendix ")
        or _APPENDIX_SHORT_LABEL_LEAD_PATTERN.match(prefix)
    )
    if not has_prefix_label:
        tail = prefix[-120:]
        tail_match = re.search(
            r"([A-Z](?:\.\d+)?\s+(?:[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,7}))$",
            tail,
        )
        if tail_match is None:
            return None
        prefix = tail_match.group(1).strip()
    if not prefix:
        return None
    label_prefix: str | None = None
    appendix_match = re.match(r"^(appendix(?:\s+(?:[A-Z]|\d+|[ivxlcdm]+))?)\b", prefix, re.IGNORECASE)
    if appendix_match is not None:
        label_prefix = re.sub(
            r"^appendix\b",
            "Appendix",
            appendix_match.group(1),
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        short_label_match = _APPENDIX_SHORT_LABEL_PATTERN.match(prefix)
        if short_label_match is not None:
            label_prefix = f"Appendix {short_label_match.group(1)}"

    title_body = _APPENDIX_LABEL_PATTERN.sub("", prefix, count=1).strip()
    if prefix.casefold().startswith("appendix "):
        stripped_body = re.sub(r"^appendix\b[:.\-]?\s*", "", prefix, count=1, flags=re.IGNORECASE).strip(" -:;,.")
        if stripped_body:
            title_body = stripped_body
    if not title_body:
        return None
    artifact_match = _APPENDIX_TITLE_ARTIFACT_MARKER_PATTERN.search(title_body)
    if artifact_match is not None:
        title_body = title_body[: artifact_match.start()].strip(" -:;,.")
    title_body = _APPENDIX_TITLE_TRAILING_METADATA_PATTERN.sub("", title_body).strip(" -:;,.")
    if not title_body:
        return None
    words = title_body.split()
    if len(words) > 8:
        trimmed_words: list[str] = []
        for index, word in enumerate(words):
            cleaned = re.sub(r"[^A-Za-z]", "", word)
            lowered_word = cleaned.casefold()
            if index >= 2 and (
                lowered_word in _APPENDIX_TITLE_BREAK_WORDS
                or (cleaned and cleaned[:1].islower())
            ):
                break
            trimmed_words.append(word)
            if len(trimmed_words) >= 8:
                break
        title_body = " ".join(trimmed_words).strip(" -:;,.")
    if not title_body:
        return None
    word_count = len(title_body.split())
    if word_count < 2 or word_count > 8:
        return None
    if not title_body[:1].isupper():
        return None
    return f"{label_prefix} {title_body}".strip() if label_prefix else title_body


def _infer_appendix_subheading_title(text: str) -> str | None:
    candidate = _appendix_section_subheading_candidate(text)
    if candidate is None:
        return None
    label = str(candidate["label"])
    if label.count(".") != 1:
        return None
    return str(candidate["full_title"])


def _infer_appendix_nested_subheading_title(text: str) -> str | None:
    candidate = _appendix_section_subheading_candidate(text)
    if candidate is None:
        return None
    label = str(candidate["label"])
    if label.count(".") < 2:
        return None
    return str(candidate["full_title"])


def _appendix_section_subheading_candidate(text: str) -> dict[str, Any] | None:
    normalized = _collapse_spaced_title_artifacts(_strip_leading_page_label(text))
    if not normalized:
        return None
    match = _APPENDIX_SECTION_SUBHEADING_PATTERN.match(normalized)
    if match is None:
        return None
    title_body = match.group("title").strip(" -:;,.")
    if not title_body:
        return None
    artifact_match = _APPENDIX_TITLE_ARTIFACT_MARKER_PATTERN.search(title_body)
    if artifact_match is not None:
        title_body = title_body[: artifact_match.start()].strip(" -:;,.")
    title_body = _APPENDIX_TITLE_TRAILING_METADATA_PATTERN.sub("", title_body).strip(" -:;,.")
    if not title_body:
        return None
    words = title_body.split()
    trimmed_words: list[str] = []
    for index, word in enumerate(words):
        cleaned = re.sub(r"[^A-Za-z]", "", word)
        lowered_word = cleaned.casefold()
        if index >= 2 and (
            lowered_word in _APPENDIX_TITLE_BREAK_WORDS
            or (cleaned and cleaned[:1].islower())
        ):
            break
        trimmed_words.append(word)
        if len(trimmed_words) >= 8:
            break
    title_body = " ".join(trimmed_words).strip(" -:;,.")
    if not title_body:
        return None
    word_count = len(title_body.split())
    if word_count < 2 or word_count > 8:
        return None
    if not title_body[:1].isupper():
        return None
    label = match.group("label")
    return {
        "label": label,
        "depth": label.count("."),
        "title": title_body,
        "full_title": f"Appendix {label} {title_body}",
    }


def _trim_intro_title_tail(text: str) -> str:
    raw_text = _normalize_pdf_signal_text(text)
    restart_match = _TITLE_UPPERCASE_SENTENCE_RESTART_PATTERN.search(raw_text)
    if restart_match is not None:
        prefix = raw_text[: restart_match.start()].strip()
        if len(_normalize_intro_title_artifacts(prefix).split()) >= 2:
            raw_text = prefix

    normalized = _normalize_intro_title_artifacts(raw_text)
    if not normalized:
        return normalized
    words = normalized.split()
    if len(words) <= 4:
        return normalized
    for index, word in enumerate(words):
        cleaned = re.sub(r"[^A-Za-z]", "", word)
        lowered = cleaned.casefold()
        previous_word = words[index - 1] if index > 0 else ""
        if index >= 2 and lowered in _CHAPTER_TITLE_BREAK_WORDS:
            return " ".join(words[:index]).strip()
        if index >= 2 and len(cleaned) == 1 and cleaned.isupper() and cleaned not in {"A", "I"}:
            return " ".join(words[:index]).strip()
        if (
            index >= 5
            and cleaned
            and cleaned[:1].isupper()
            and not previous_word.rstrip(",;").endswith(":")
        ):
            return " ".join(words[:index]).strip()
    return normalized


def _infer_intro_page_title(block_texts: list[str]) -> str | None:
    normalized_pairs = [
        (text, _normalize_intro_title_artifacts(text))
        for text in block_texts
        if _normalize_intro_title_artifacts(text)
    ]
    if not normalized_pairs:
        return None
    if len(normalized_pairs) > 1 and _parse_page_number(normalized_pairs[0][1]) is not None:
        normalized_pairs = normalized_pairs[1:]
    if not normalized_pairs:
        return None
    raw_texts = [raw for raw, _normalized in normalized_pairs]
    normalized_texts = [normalized for _raw, normalized in normalized_pairs]

    if len(normalized_texts) == 1:
        title = _trim_intro_title_tail(raw_texts[0])
    else:
        prefix_parts = normalized_texts[:-1]
        suffix = _trim_intro_title_tail(raw_texts[-1])
        if suffix:
            prefix_parts.append(suffix)
        title = _normalize_text(" ".join(part for part in prefix_parts if part))
    if not title:
        return None
    stripped_title = _LEADING_SECTION_NUMBER_PATTERN.sub("", title).strip()
    if len(stripped_title.split()) >= 2:
        title = stripped_title
    word_count = len(title.split())
    if word_count < 2:
        return None
    if word_count > 14:
        title = " ".join(title.split()[:14])
    return title


def _title_variants(text: str) -> set[str]:
    normalized = _normalize_outline_title(text)
    if not normalized:
        return set()
    variants = {normalized}
    pending = [normalized]
    while pending:
        candidate = pending.pop()
        stripped_prefix = _CHAPTER_PREFIX_PATTERN.sub("", candidate).strip()
        stripped_number = _LEADING_SECTION_NUMBER_PATTERN.sub("", candidate).strip()
        for derived in (stripped_prefix, stripped_number):
            if derived and derived not in variants:
                variants.add(derived)
                pending.append(derived)
    return variants


def _titles_overlap(left: str, right: str) -> bool:
    left_variants = _title_variants(left)
    right_variants = _title_variants(right)
    return bool(left_variants and right_variants and left_variants.intersection(right_variants))


def _extract_footnote_marker(text: str) -> str | None:
    match = _FOOTNOTE_PATTERN.match(_normalize_text(text))
    if not match:
        return None
    marker = match.group(0).strip().rstrip(".)")
    return marker or None


def _body_contains_footnote_anchor(text: str, marker: str) -> bool:
    tail = _normalize_text(text)[-160:]
    if not tail:
        return False
    escaped_marker = re.escape(marker)
    attached_marker_pattern = ""
    if marker.isdigit():
        attached_marker_pattern = rf"|(?<=[A-Za-z]){escaped_marker}(?=(?:[\"'\]\)\u201d\u2019.,;:!?]|\s+[A-Z]|\s*$))"
    return bool(
        re.search(
            rf"(?:\[\s*{escaped_marker}\s*\]|\(\s*{escaped_marker}\s*\)|(?<!\w){escaped_marker}(?!\w))"
            rf"{attached_marker_pattern}"
            rf"(?=(?:[\"'\]\)\u201d\u2019.,;:!?])*\s*$|(?:\s+[A-Z]))",
            tail,
        )
    )


def _header_footer_signature(text: str) -> str:
    lowered = text.casefold()
    lowered = re.sub(r"\d+", "#", lowered)
    lowered = re.sub(r"[^a-z#]+", " ", lowered)
    return _normalize_text(lowered)


def _is_page_number_text(text: str) -> bool:
    return bool(_PAGE_NUMBER_PATTERN.fullmatch(_normalize_text(text).casefold()))


# Running-header text patterns the parser captures as paragraph blocks.
# Two book-typography conventions seen in production:
#   "22\nCHAPTER 2\nTokenizers: How large language models see the world"
#   "2.2 Language models see only tokens\n23"
#   "1.7 Why LLMs perform so well 11"
_BOOK_RUNNING_HEADER_PATTERN = re.compile(
    r"^(?:"
    r"\s*\d+\s*\n\s*CHAPTER\s+\d+\b[^\n]{0,80}"
    r"|\s*\d+(?:\.\d+){0,2}\s+[^\n]{1,80}\s*\n\s*\d{1,4}\s*$"
    r"|\s*\d+(?:\.\d+){0,2}\s+[^\n]{1,80}\s+\d{1,4}\s*$"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def _is_book_running_header_text(text: str) -> bool:
    if not text:
        return False
    s = text.strip()
    if len(s) > 200:
        return False
    return bool(_BOOK_RUNNING_HEADER_PATTERN.match(s))


def _looks_like_dense_toc_block(text: str, line_count: int) -> bool:
    if line_count < 2 and "\n" not in text:
        return False
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 1600:
        return False
    if _dense_toc_line_count(text) >= 1:
        return True
    page_token_hits = len(re.findall(r"\b(?:\d+|[ivxlcdm]+)\b", normalized, re.IGNORECASE))
    return text.count(".") >= 10 and page_token_hits >= 3


def _looks_like_heading_continuation_fragment(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 80:
        return False
    if _HEADING_PATTERN.match(normalized) or _LEADING_SECTION_NUMBER_PATTERN.match(normalized):
        return False
    lead = normalized.split(" ", 1)[0].casefold()
    if lead in _HEADING_CONTINUATION_START_WORDS:
        return True
    return normalized[:1].islower()


def _looks_like_prose_continuation_fragment(text: str) -> bool:
    normalized_lines = [_normalize_text(line) for line in text.splitlines() if _normalize_text(line)]
    if not normalized_lines:
        return False
    if _looks_like_code(text, len(normalized_lines)):
        return False
    if _looks_like_table(len(normalized_lines), normalized_lines):
        return False

    normalized = " ".join(normalized_lines)
    if len(normalized) < 48 or _looks_like_caption_text(normalized) or _HEADING_PATTERN.match(normalized):
        return False

    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized.casefold())
    if len(tokens) < 10:
        return False
    stopword_hits = sum(1 for token in tokens if token in _PROSE_CONTINUATION_STOPWORDS)
    sentence_punctuation = len(re.findall(r"[.!?](?:[\"'\)\]\u201d\u2019])?(?:\s|$)", normalized))
    lead = tokens[0] if tokens else ""
    continuation_lead = lead in _PROSE_CONTINUATION_START_WORDS or normalized[:1].islower()
    if not continuation_lead:
        return False
    return stopword_hits >= max(4, len(tokens) // 8) and (sentence_punctuation >= 1 or len(normalized_lines) >= 3)


def _looks_like_book_prose_fragment(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) < 24:
        return False
    if _looks_like_caption_text(normalized) or _looks_like_visual_heading(normalized, 1):
        return False
    if _looks_like_code(normalized, 1):
        return False
    if _looks_like_table(1, [normalized]):
        return False
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized.casefold())
    if len(tokens) < 6:
        return False
    stopword_hits = sum(1 for token in tokens if token in _PROSE_CONTINUATION_STOPWORDS)
    return stopword_hits >= max(3, len(tokens) // 4)


def _looks_like_inline_book_heading_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 96:
        return False
    if _looks_like_caption_text(normalized) or _HEADING_PATTERN.match(normalized):
        return False
    if _looks_like_code(normalized, 1):
        return False
    if _looks_like_table(1, [normalized]):
        return False
    token_count = len(re.findall(r"[A-Za-z][A-Za-z'-]*", normalized))
    if not 2 <= token_count <= _BOOK_INLINE_HEADING_MAX_WORDS:
        return False
    return _looks_like_visual_heading(normalized, 1)


def _looks_like_contextual_image_legend_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 96:
        return False
    if _looks_like_caption_text(normalized) or _looks_like_visual_heading(normalized, 1):
        return False
    if _looks_like_code(normalized, 1):
        return False
    if _looks_like_table(1, [normalized]):
        return False
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized.casefold())
    if not 3 <= len(tokens) <= 9:
        return False
    if tokens[0] not in _CONTEXTUAL_IMAGE_LEGEND_START_WORDS:
        return False
    return any(char.isdigit() for char in normalized)


def _book_heading_level(text: str, *, fallback: int | None = None) -> int | None:
    normalized = _normalize_text(text)
    if not normalized:
        return fallback
    if _CHAPTER_PREFIX_PATTERN.match(normalized) or _HEADING_PATTERN.match(normalized):
        return 1
    appendix_subheading = _appendix_section_subheading_candidate(normalized)
    if appendix_subheading is not None:
        return 2 + int(appendix_subheading.get("depth", 0) or 0)
    numbered = re.match(r"^(?P<label>\d+(?:\.\d+)*)\b", normalized)
    if numbered is not None:
        return max(2, len(numbered.group("label").split(".")))
    return fallback


def _looks_like_book_prose_lead(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    tokens = normalized.split()
    if not tokens:
        return False
    first = re.sub(r"[^A-Za-z'-]", "", tokens[0]).casefold()
    second = re.sub(r"[^A-Za-z'-]", "", tokens[1]).casefold() if len(tokens) > 1 else ""
    if first in _PROSE_CONTINUATION_STOPWORDS or first in _PROSE_CONTINUATION_START_WORDS:
        return True
    if second in _PROSE_CONTINUATION_STOPWORDS:
        return True
    if tokens[0].endswith(",") and second in _PROSE_CONTINUATION_STOPWORDS:
        return True
    return False


def _is_plausible_book_heading_candidate(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or normalized.endswith((",", ";", ":")):
        return False
    if any(token.endswith(",") for token in normalized.split()):
        return False
    return True


def _remainder_starts_fresh_sentence(remainder: str) -> bool:
    """True when ``remainder`` opens a fresh sentence after a heading.

    A real section heading is followed by a body sentence with its own
    subject, which begins with an uppercase letter. Unlike
    :func:`_looks_like_book_prose_lead` — which only accepts a body led
    by a known stopword — this accepts ANY ordinary capitalised lead
    ("Using", "Many", "Although", "For most people…"). Relying on the
    stopword list alone made the heading/body splitter reject the correct
    boundary and swallow the first body word into the title
    (e.g. "6.1.3 Improving code via formatting Using").

    A capitalised lead that happens to be a preposition/auxiliary
    ("For", "By", "To") is still a genuine sentence start — the parser's
    proper-noun-phrase guard (the "previous word is capitalised" check in
    the caller) is what prevents cutting inside a Title-Cased phrase.
    """
    text = (remainder or "").strip()
    if not text:
        return False
    first_char = text[0]
    return first_char.isalpha() and first_char.isupper()


def _leading_numbered_book_heading_and_remainder(text: str) -> tuple[str, str, int] | None:
    # Fast-path: respect the PDF's own newline-separated structure when the
    # raw block begins with "<section-number>\n<title-line>\n<body>". The
    # newline-collapsing normalize below otherwise forces token-by-token
    # boundary guessing, which mis-fires when the body opens with a word
    # that doubles as a heading-continuation stopword (e.g. "As mentioned
    # in chapter 1..."). The newline layout is far more reliable than the
    # stopword heuristic.
    if text:
        # Preserve newlines: only strip leading page label without collapsing
        # whitespace (the standard `_strip_leading_page_label` would flatten
        # the structure we depend on here).
        raw_lines_all = [line.strip() for line in (text or "").split("\n")]
        # Drop a leading lone page label (e.g. "24") if present.
        if raw_lines_all and re.fullmatch(r"\d{1,4}", raw_lines_all[0] or ""):
            raw_lines_all = raw_lines_all[1:]
        raw_lines = [line for line in raw_lines_all if line]
        if len(raw_lines) >= 3:
            first = raw_lines[0]
            section_match = re.fullmatch(r"(?:\d+(?:\.\d+)*)[.):\-]?", first)
            if section_match is not None:
                title_line = raw_lines[1]
                body_lines = raw_lines[2:]
                candidate_heading = f"{first} {title_line}".strip()
                normalized_heading_fp = _normalize_multiline_text(candidate_heading)
                normalized_remainder_fp = _normalize_multiline_text(" ".join(body_lines))
                if (
                    normalized_heading_fp
                    and normalized_remainder_fp
                    and 2 <= len(normalized_heading_fp.split()) <= 14
                    and _is_plausible_book_heading_candidate(normalized_heading_fp)
                    and _looks_like_visual_heading(normalized_heading_fp, 1)
                    and _looks_like_book_prose_lead(normalized_remainder_fp)
                    and _looks_like_book_prose_fragment(normalized_remainder_fp)
                ):
                    level = _book_heading_level(normalized_heading_fp, fallback=2) or 2
                    return normalized_heading_fp, normalized_remainder_fp, level

    normalized = _strip_leading_page_label(_normalize_pdf_signal_text(text))
    if not normalized or _LEADING_SECTION_NUMBER_PATTERN.match(normalized) is None:
        return None
    tokens = normalized.split()
    if len(tokens) < 8:
        return None
    for boundary in range(3, min(len(tokens) - 5, 12) + 1):
        heading_text = " ".join(tokens[:boundary]).strip()
        remainder = " ".join(tokens[boundary:]).strip()
        if not remainder:
            continue
        remainder_first = re.sub(r"[^A-Za-z'-]", "", tokens[boundary]).casefold()
        heading_last = re.sub(r"[^A-Za-z'-]", "", tokens[boundary - 1]).casefold()
        # Only a LOWERCASE continuation word signals a mid-phrase cut. A
        # capitalised lead ("For most people…", "By contrast…") is a
        # genuine sentence start even when the word also appears in the
        # continuation list.
        if remainder[:1].islower() and remainder_first in (
            _HEADING_CONTINUATION_START_WORDS - {"the"}
        ):
            continue
        if heading_last in _HEADING_CONTINUATION_START_WORDS or heading_last in {"a", "an", "the"}:
            continue
        # Don't cut inside a Title-cased proper-noun phrase. A sentence-case
        # section title ends with an ordinary lowercase word ("Sanitized
        # input", "…via formatting") or sentence punctuation ("…fair use?").
        # If the word right before the boundary is itself Title-cased
        # (mixed case, capital initial), the title most likely continues
        # (e.g. the boundary fell inside "Generative Pretrained
        # Transformers"), so reject. An ALL-CAPS acronym ("RLHF", "GPT")
        # is exempt — a real title can legitimately end with one.
        prev_raw = tokens[boundary - 1]
        prev_alpha = re.sub(r"[^A-Za-z]", "", prev_raw)
        if (
            prev_alpha
            and prev_alpha[0].isupper()
            and not prev_alpha.isupper()
            and not re.search(r"[.?!][\"'\)\]]*$", prev_raw)
        ):
            continue
        # An ALL-CAPS acronym at the START of the remainder, immediately
        # followed by a Title-cased word, belongs to the section title —
        # the body sentence really begins at that next word
        # (e.g. "…a naive | RLHF First, let's…" → title keeps "RLHF").
        rem_tokens = remainder.split()
        if len(rem_tokens) >= 2:
            rem_w0 = re.sub(r"[^A-Za-z]", "", rem_tokens[0])
            rem_w1 = re.sub(r"[^A-Za-z]", "", rem_tokens[1])
            if (
                len(rem_w0) >= 2
                and rem_w0.isupper()
                and rem_w1
                and rem_w1[0].isupper()
                and not rem_w1.isupper()
            ):
                continue
        if not _is_plausible_book_heading_candidate(heading_text):
            continue
        if not _looks_like_visual_heading(heading_text, 1):
            continue
        # The body must open a fresh sentence (uppercase, non-linking
        # lead). The loop runs boundaries low→high and returns the first
        # match, so the EARLIEST valid title wins — the split no longer
        # over-runs into the first body word.
        if not _remainder_starts_fresh_sentence(remainder):
            continue
        if not (
            _looks_like_book_prose_fragment(remainder)
            or _looks_like_sentence_prose_line(remainder)
        ):
            continue
        level = _book_heading_level(heading_text, fallback=2) or 2
        normalized_heading = _normalize_multiline_text(heading_text)
        normalized_remainder = _normalize_multiline_text(remainder)
        # Numbered headings include the section number as a token, so a
        # genuine short title ("6.2.1 Sanitized input") is only 3 tokens.
        if len(normalized_heading.split()) < 3:
            continue
        return normalized_heading, normalized_remainder, level
    return None


def _leading_all_caps_book_heading_and_remainder(text: str) -> tuple[str, str, int] | None:
    normalized = _strip_leading_page_label(_normalize_pdf_signal_text(text))
    if not normalized:
        return None
    tokens = normalized.split()
    if len(tokens) < 7:
        return None
    for boundary in range(min(len(tokens) - 4, 10), 2, -1):
        heading_text = " ".join(tokens[:boundary]).strip()
        remainder = " ".join(tokens[boundary:]).strip()
        alpha_tokens = [re.sub(r"[^A-Za-z]", "", token) for token in heading_text.split()]
        alpha_tokens = [token for token in alpha_tokens if token]
        if not alpha_tokens:
            continue
        if any(not token.isupper() for token in alpha_tokens):
            continue
        if not _is_plausible_book_heading_candidate(heading_text):
            continue
        if not _remainder_starts_fresh_sentence(remainder):
            continue
        if not (
            _looks_like_book_prose_fragment(remainder)
            or _looks_like_sentence_prose_line(remainder)
        ):
            continue
        normalized_heading = _normalize_multiline_text(heading_text)
        normalized_remainder = _normalize_multiline_text(remainder)
        if len(normalized_heading.split()) < 4:
            continue
        return normalized_heading, normalized_remainder, 4
    return None


# Tokens that, when they are the FIRST word of the proposed remainder
# body, indicate the "heading" was actually a mid-phrase cut — NOT a
# real section title. A real book heading is followed by a fresh
# sentence with its own subject ("Embedding layers" + "The first step
# ..."), never by:
#   - a bare verb / auxiliary that needs the heading as its subject
#     ("is poised...", "are constantly...", "have proven...")
#   - a coordinating conjunction ("or GenAI...", "and the next..." —
#     mid-coordination, the heading was sliced inside parenthetical
#     content)
#   - a preposition ("of LLMs...", "to change...", "with information..."
#     — the heading was sliced before the prepositional object)
_BODY_CUT_AT_VERB_LEAD = frozenset(
    {
        # auxiliaries / linking verbs
        "is", "are", "was", "were", "be", "been", "being", "am",
        "do", "does", "did",
        "has", "have", "had",
        "can", "could", "should", "would", "will", "may", "might", "must", "shall",
        "seems", "seem", "appears", "appear", "remains", "remain",
        # coordinating conjunctions
        "or", "nor",
        # prepositions that always link to a preceding noun phrase
        "of", "to", "for", "with", "without", "from", "by", "into",
        "as", "than", "via",
    }
)


def _leading_plain_book_heading_and_remainder(text: str) -> tuple[str, str, int] | None:
    normalized = _strip_leading_page_label(_normalize_pdf_signal_text(text))
    if not normalized:
        return None
    # Short blocks are almost always single list items or figure labels,
    # not "heading + body" combos. The numbered-step layout in figures
    # ("1 Map text to tokens (chapter 2).") triggered a false-positive
    # split that produced a heading "1 Map text" and a paragraph
    # "to tokens (chapter 2)." with identical bboxes. A 60-char floor
    # gates this rule on blocks that actually look like a paragraph.
    if len(normalized) < 60:
        return None
    tokens = normalized.split()
    if len(tokens) < 6:
        return None
    max_boundary = min(len(tokens) - 4, 6)
    for boundary in range(max_boundary, 0, -1):
        heading_text = " ".join(tokens[:boundary]).strip()
        remainder = " ".join(tokens[boundary:]).strip()
        if not heading_text or not remainder:
            continue
        if not _looks_like_visual_heading(heading_text, 1):
            continue
        if _looks_like_caption_text(heading_text) or _HEADING_PATTERN.match(heading_text):
            continue
        if _looks_like_code(heading_text, 1) or _looks_like_table(1, [heading_text]):
            continue
        alpha_tokens = [re.sub(r"[^A-Za-z-]", "", token) for token in heading_text.split()]
        alpha_tokens = [token for token in alpha_tokens if token]
        if not 1 <= len(alpha_tokens) <= _BOOK_INLINE_HEADING_MAX_WORDS:
            continue
        heading_last = alpha_tokens[-1].casefold()
        if heading_last in _PROSE_CONTINUATION_STOPWORDS or heading_last in _PROSE_CONTINUATION_START_WORDS:
            continue
        # NEW: reject when the proposed split is mid-phrase rather than
        # title→body. Real book headings produce a remainder that starts
        # a fresh sentence: the first character is an UPPERCASE LETTER
        # and the first word is neither a bare verb/auxiliary nor a
        # coordinating conjunction / preposition that always links back
        # to a preceding noun phrase.
        #
        # Example we used to incorrectly split:
        #   "Generative AI (GAI or GenAI) is poised to change ..."
        # The function tried multiple boundaries:
        #   boundary=5 → remainder "is poised..."           (verb lead)
        #   boundary=4 → heading ends with "or"             (already rejected)
        #   boundary=3 → remainder "or GenAI) is poised..."  (conjunction lead)
        #   boundary=2 → remainder "(GAI or GenAI) is..."    (paren/non-letter lead)
        # all four boundaries are mid-phrase cuts; we want NONE of them.
        if not remainder:
            continue
        first_char = remainder[0]
        if not (first_char.isalpha() and first_char.isupper()):
            continue
        remainder_tokens = remainder.split()
        if remainder_tokens:
            rem_first = re.sub(r"[^A-Za-z'-]", "", remainder_tokens[0]).casefold()
            if rem_first in _BODY_CUT_AT_VERB_LEAD:
                continue
        if not _looks_like_book_prose_fragment(remainder):
            continue
        if not (_looks_like_book_prose_lead(remainder) or _looks_like_sentence_prose_line(remainder)):
            continue
        if len(alpha_tokens) == 2 and alpha_tokens[0].casefold() == alpha_tokens[1].casefold():
            heading_text = alpha_tokens[0]
        return _normalize_multiline_text(heading_text), _normalize_multiline_text(remainder), 2
    return None


def _page_has_multi_column_signature(page: "PdfPage") -> bool:
    candidate_blocks = [
        block
        for block in page.blocks
        if len(_normalize_text(block.text)) >= 40 and (block.bbox[2] - block.bbox[0]) <= page.width * 0.7
    ]
    if len(candidate_blocks) < 4:
        return False

    column_candidate_blocks = [
        block
        for block in candidate_blocks
        if (block.bbox[2] - block.bbox[0]) <= page.width * _MULTI_COLUMN_BLOCK_WIDTH_RATIO
    ]
    left_blocks = [block for block in column_candidate_blocks if block.bbox[0] <= page.width * 0.22]
    right_blocks = [block for block in column_candidate_blocks if block.bbox[0] >= page.width * 0.45]
    if len(left_blocks) < 2:
        return False
    if len(right_blocks) < 2:
        right_blocks = [
            block
            for block in column_candidate_blocks
            if block.bbox[0] >= page.width * 0.45
            and (block.bbox[3] - block.bbox[1]) >= page.height * 0.24
        ]
        if not right_blocks:
            return False

    left_y = sorted(block.bbox[1] for block in left_blocks[:4])
    right_y = sorted(block.bbox[1] for block in right_blocks[:4])
    return bool(left_y and right_y and min(right_y) < max(left_y))


def _page_has_column_fragment_signature(page: "PdfPage") -> bool:
    dense_half_width_blocks = [
        block
        for block in page.blocks
        if (
            len(_normalize_text(block.text)) >= 600
            and block.span_count >= 700
            and page.width * 0.32 <= (block.bbox[2] - block.bbox[0]) <= page.width * 0.58
            and (block.bbox[0] >= page.width * 0.42 or block.bbox[2] <= page.width * 0.58)
        )
    ]
    if len(dense_half_width_blocks) >= 2:
        return True
    if len(dense_half_width_blocks) == 1 and len(page.blocks) <= 2:
        return True
    return False


def _page_has_abstract_signal(page: "PdfPage") -> bool:
    for block in page.blocks:
        normalized = _normalize_text(block.text)
        if not normalized:
            continue
        if normalized.casefold() == "abstract":
            return True
        if normalized.casefold().startswith("abstract "):
            return True
    return False


def _page_first_numbered_section_heading_top(page: "PdfPage") -> float | None:
    heading_tops: list[float] = []
    for block in page.blocks:
        normalized = _normalize_text(block.text)
        if not normalized:
            continue
        if _ACADEMIC_NUMBERED_SECTION_PATTERN.match(normalized):
            heading_tops.append(float(block.bbox[1]))
    return min(heading_tops) if heading_tops else None


def _page_has_asymmetric_academic_first_page_signal(page: "PdfPage", document_title: str | None) -> bool:
    if page.page_number != 1:
        return False
    if not (_page_has_centered_title_signal(page) or _page_has_title_overlap_signal(page, document_title)):
        return False
    if not _page_has_abstract_signal(page):
        return False
    intro_heading_top = _page_first_numbered_section_heading_top(page)
    if intro_heading_top is None:
        return False

    left_abstract_blocks = 0
    right_continuation_blocks = 0
    for block in page.blocks:
        normalized = _normalize_text(block.text)
        if len(normalized) < 40:
            continue
        if float(block.bbox[1]) >= intro_heading_top:
            continue
        if block.bbox[0] <= page.width * 0.22 and _looks_like_academic_prose_lead(normalized):
            left_abstract_blocks += 1
            continue
        if block.bbox[0] >= page.width * 0.42 and (
            _looks_like_academic_body_continuation(normalized) or _looks_like_academic_prose_lead(normalized)
        ):
            right_continuation_blocks += 1

    return left_abstract_blocks >= 1 and right_continuation_blocks >= 1


def _page_has_single_column_academic_first_page_signal(page: "PdfPage", document_title: str | None) -> bool:
    if page.page_number != 1:
        return False
    if not (_page_has_centered_title_signal(page) or _page_has_title_overlap_signal(page, document_title)):
        return False
    if not _page_has_abstract_signal(page):
        return False
    return _page_first_numbered_section_heading_top(page) is not None


def _page_extraction_plan(
    page: "PdfPage",
    *,
    profile: "PdfFileProfile",
    page_layout_assessment: _PageLayoutAssessment,
    page_context: _PageRecoveryContext,
) -> _PageExtractionPlan:
    reasons = list(page_layout_assessment.reasons)
    if page_context.page_family != "body":
        reasons.append(f"page_family_{page_context.page_family}")
    if page_context.family_source and page_context.family_source != "body":
        reasons.append(f"page_family_source_{page_context.family_source}")

    if profile.pdf_kind == "scanned_pdf" or "ocr_scanned_page" in page_layout_assessment.reasons:
        intent = "ocr_overlay"
        reasons.append("scanned_or_ocr_required")
    elif profile.pdf_kind == "mixed_pdf":
        intent = "hybrid_merge"
        reasons.append("mixed_pdf")
    elif (
        profile.recovery_lane == "academic_paper"
        and (
            "multi_column" in page_layout_assessment.reasons
            or "column_fragment" in page_layout_assessment.reasons
            or "academic_first_page_asymmetric" in page_layout_assessment.reasons
        )
    ):
        intent = "hybrid_merge"
        reasons.append("academic_paper_lane")
    elif "multi_column" in page_layout_assessment.reasons or "column_fragment" in page_layout_assessment.reasons:
        intent = "hybrid_merge"
        reasons.append("layout_fragmentation")
    else:
        intent = "native_text"
        reasons.append("text_preserving")

    deduped_reasons = tuple(dict.fromkeys(reason for reason in reasons if reason))
    return _PageExtractionPlan(intent=intent, reasons=deduped_reasons)


def _assess_page_layout(
    page: "PdfPage",
    *,
    profile: "PdfFileProfile",
    document_title: str | None,
) -> _PageLayoutAssessment:
    reasons: list[str] = []
    if profile.pdf_kind == "scanned_pdf":
        reasons.append("ocr_scanned_page")
    if _page_has_multi_column_signature(page):
        reasons.append("multi_column")
    if _page_has_column_fragment_signature(page):
        reasons.append("column_fragment")
    if (
        profile.recovery_lane == "academic_paper"
        and _page_has_asymmetric_academic_first_page_signal(page, document_title)
    ):
        reasons.append("academic_first_page_asymmetric")

    if "ocr_scanned_page" in reasons or "academic_first_page_asymmetric" in reasons:
        risk = "high"
    elif reasons:
        risk = "medium"
    else:
        risk = "low"
    return _PageLayoutAssessment(risk=risk, reasons=tuple(reasons))
