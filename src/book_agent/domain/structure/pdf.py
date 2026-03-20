from __future__ import annotations

import ctypes
import re
import sys
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import median
from typing import Any, Protocol

from book_agent.domain.enums import BlockType
from book_agent.domain.structure.artifact_grouping import looks_like_artifact_group_context_text
from book_agent.domain.structure.models import ParsedBlock, ParsedChapter, ParsedDocument

_TERMINAL_PUNCTUATION = (".", "!", "?", ":", ";", "\"", "'", "\u201d", "\u2019")
_HEADING_PATTERN = re.compile(r"^(chapter|part|appendix)\b", re.IGNORECASE)
_FIGURE_CAPTION_PATTERN = re.compile(
    r"^(?:figure|fig\.|image|diagram|chart)\s+"
    r"(?:\(?\d+(?:\.\d+)*[A-Za-z]?\)?|[A-Z])"
    r"(?:(?:[.:\-\u2013\u2014]\s+)\S+|\s+(?-i:[A-Z])[^\n]{2,})",
    re.IGNORECASE,
)
_TABLE_CAPTION_PATTERN = re.compile(
    r"^(?:table)\s+"
    r"(?:\(?\d+(?:\.\d+)*[A-Za-z]?\)?|[A-Z])"
    r"(?:(?:[.:\-\u2013\u2014]\s+)\S+|\s+(?-i:[A-Z])[^\n]{2,})",
    re.IGNORECASE,
)
_TABLE_HEADER_CUE_PATTERN = re.compile(
    r"\b(?:method|model|accuracy|team|individual|instances?|allocated|baseline(?:s)?|dataset|bleu|score(?:s)?|cost)\b",
    re.IGNORECASE,
)
_TABLE_BRACKETED_GROUP_PATTERN = re.compile(r"\[[^\]]+\]")
_TABLE_ID_EQUALS_PATTERN = re.compile(r"\bID\s*=\s*\d+\b", re.IGNORECASE)
_EQUATION_CAPTION_PATTERN = re.compile(
    r"^(?:eq(?:uation)?\.?)\s*(?:\(\s*\d+(?:\.\d+)*[A-Za-z]?\s*\)|\d+(?:\.\d+)*[A-Za-z]?)(?:[.:-]\s+)\S+",
    re.IGNORECASE,
)
_FOOTNOTE_PATTERN = re.compile(r"^(?:\d+|[*\u2020\u2021])(?:[.)]|\s)")
_PAGE_NUMBER_PATTERN = re.compile(r"^(?:page\s+)?(?:\d+|[ivxlcdm]+)$", re.IGNORECASE)
_CODE_IMPORT_LINE_PATTERN = re.compile(r"^(?:from\s+\S+\s+import\b.+|import\s+\S+.+)$", re.IGNORECASE)
_CODE_CONTROL_LINE_PATTERN = re.compile(
    r"^(?:"
    r"async\s+def\b.+:"
    r"|def\b.+:"
    r"|class\b.+:"
    r"|return\b.+"
    r"|yield\b.+"
    r"|raise\b.+"
    r"|pass\b.*"
    r"|break\b.*"
    r"|continue\b.*"
    r"|try:"
    r"|finally:"
    r"|except\b.*:"
    r"|if\b.+:"
    r"|elif\b.+:"
    r"|else:"
    r"|for\b.+\bin\b.+:"
    r"|while\b.+:"
    r"|with\b.+:"
    r")$",
    re.IGNORECASE,
)
_TABLE_SEPARATOR_PATTERN = re.compile(r"\S(?:\s{2,}|\t)\S")
_EQUATION_OPERATOR_PATTERN = re.compile(
    r"(?:=|≤|≥|≈|∝|×|÷|±|∑|∫|λ|α|β|γ|δ|θ|μ|σ|π|→|↔|\b(?:argmax|argmin|softmax|max|min)\b)",
    re.IGNORECASE,
)
_EQUATION_VARIABLE_PATTERN = re.compile(r"\b[A-Za-z](?:_[A-Za-z0-9]+)?\b")
_EQUATION_CODE_CUE_PATTERN = re.compile(
    r"\b(?:def|class|import|return|async|await|from|for|while|if|elif|else|print)\b",
    re.IGNORECASE,
)
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
_PROSE_CONTINUATION_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "their",
    "them",
    "these",
    "this",
    "those",
    "through",
    "to",
    "was",
    "we",
    "what",
    "when",
    "which",
    "while",
    "with",
    "you",
    "your",
}
_TITLE_OCTAL_ESCAPE_PATTERN = re.compile(r"\\\d{3}")
_TITLE_SINGLE_LETTER_SEQUENCE_PATTERN = re.compile(r"\b(?:[A-Za-z]\s+){2,}[A-Za-z]\b")
_APPENDIX_SHORT_LABEL_LEAD_PATTERN = re.compile(r"^[A-Z](?:[).:\-])?\s+[A-Z]")
_APPENDIX_SHORT_LABEL_PATTERN = re.compile(r"^([A-Z])(?:[).:\-])?\b")
_APPENDIX_SECTION_SUBHEADING_PATTERN = re.compile(r"^(?P<label>[A-Z]\.\d+(?:\.\d+)*)\s+(?P<title>.+)$")
_APPENDIX_SUBHEADING_PATTERN = re.compile(r"^(?P<label>[A-Z]\.\d+)\s+(?P<title>.+)$")
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
_ACADEMIC_BODY_STARTER_WORDS = {
    "additionally",
    "a",
    "an",
    "as",
    "at",
    "by",
    "for",
    "from",
    "given",
    "however",
    "in",
    "instead",
    "it",
    "its",
    "on",
    "over",
    "most",
    "multiple",
    "our",
    "since",
    "similarly",
    "that",
    "the",
    "their",
    "these",
    "they",
    "this",
    "those",
    "to",
    "we",
    "when",
    "while",
    "with",
}
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
    "model",
    "related work",
    "results",
    "training",
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


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_multiline_text(text: str) -> str:
    lines = [_normalize_text(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _bbox_to_json(bbox: tuple[float, float, float, float]) -> list[float]:
    return [round(value, 3) for value in bbox]


def _normalize_outline_title(text: str) -> str:
    title, _page_number = _split_toc_entry(text)
    return title.casefold()


def _normalize_outline_heading_text(text: str) -> str:
    return _normalize_intro_title_artifacts(_normalize_pdf_signal_text(_normalize_text(text)))


def _looks_like_book_primary_outline_title(text: str) -> bool:
    normalized = _normalize_outline_heading_text(text)
    if not normalized:
        return False
    return bool(_HEADING_PATTERN.match(normalized) or _APPENDIX_HEADING_PATTERN.match(normalized))


def _should_keep_book_top_level_outline_title(text: str) -> bool:
    normalized = _normalize_outline_heading_text(text)
    if not normalized:
        return False
    lowered = normalized.casefold()
    if _looks_like_book_primary_outline_title(normalized):
        return True
    return lowered in _BOOK_SPECIAL_OUTLINE_TITLES


def _roman_to_int(value: str) -> int | None:
    numerals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    text = value.strip().casefold()
    if not text or any(char not in numerals for char in text):
        return None
    total = 0
    previous = 0
    for char in reversed(text):
        current = numerals[char]
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total or None


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
        collapsed = re.sub(r"\b([A-Z])\s+([a-z]{2,})\b", r"\1\2", collapsed)
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
    if compact.endswith((".", "?", "!", ";")):
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

    return titleish_words >= max(2, len(alpha_words) - 3) and lowercase_words <= 1


def _is_name_like_token(token: str) -> bool:
    cleaned = re.sub(r"[^A-Za-z.'`-]", "", token).strip(".-")
    if not cleaned:
        return False
    return cleaned[:1].isupper() and (len(cleaned) == 1 or cleaned[1:].islower())


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
    match = _BROKEN_REFERENCES_HEADING_PATTERN.match(normalized)
    if match is None:
        return None
    remainder = _normalize_intro_title_artifacts(normalized[match.end() :]).strip(" -:;,.")
    if not remainder:
        return _section_family_display_title("references"), ""
    if remainder.startswith("[") or _looks_like_reference_entry(remainder):
        return _section_family_display_title("references"), remainder
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


def _consume_academic_heading_title(candidate_text: str) -> tuple[str, str] | None:
    tokens = candidate_text.split()
    if not tokens:
        return None
    title_tokens: list[str] = []
    content_tokens = 0
    for token in tokens:
        stripped = token.strip("()[]{}.,;:")
        lowered = stripped.casefold()
        if (
            title_tokens
            and lowered in _ACADEMIC_BODY_STARTER_WORDS
            and (
                content_tokens >= 2
                or (content_tokens == 1 and title_tokens[0].strip("()[]{}.,;:").casefold() in _ACADEMIC_SINGLE_TOKEN_HEADING_WORDS)
            )
        ):
            break
        if lowered in _ACADEMIC_HEADING_CONNECTOR_WORDS and title_tokens:
            title_tokens.append(token)
            continue
        if _looks_like_academic_heading_token(stripped):
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


def _looks_like_academic_prose_lead(text: str) -> bool:
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

    first_alpha = re.sub(r"[^A-Za-z]", "", first_token)
    if first_alpha and first_alpha.isupper() and len(first_alpha) >= 2:
        return False
    if any(char.isdigit() for char in first_token):
        return False

    sample = tokens[:8]
    long_lowercase = 0
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
        if alpha_only and alpha_only[:1].islower() and len(alpha_only) >= 3:
            long_lowercase += 1

    return uppercaseish + numericish <= 1 and long_lowercase >= 2


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
        cleaned = _clean_academic_heading_candidate(f"{label} {title_body}", remainder, "numbered")
        if cleaned is None:
            continue
        heading_text, cleaned_remainder = cleaned
        return start, heading_text, cleaned_remainder, {"heading_kind": "numbered", "section_level": label.count(".") + 1}
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
    normalized = _normalize_intro_title_artifacts(text)
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
        if index >= 2 and len(cleaned) == 1 and cleaned.isupper():
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
    normalized_texts = [
        _normalize_intro_title_artifacts(text)
        for text in block_texts
        if _normalize_intro_title_artifacts(text)
    ]
    if not normalized_texts:
        return None
    if len(normalized_texts) > 1 and _parse_page_number(normalized_texts[0]) is not None:
        normalized_texts = normalized_texts[1:]
    if not normalized_texts:
        return None

    if len(normalized_texts) == 1:
        title = _trim_intro_title_tail(normalized_texts[0])
    else:
        prefix_parts = normalized_texts[:-1]
        suffix = _trim_intro_title_tail(normalized_texts[-1])
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


def _looks_like_code(text: str, line_count: int) -> bool:
    normalized_lines = _expanded_code_candidate_lines(text)
    effective_line_count = max(line_count, len(normalized_lines))
    if effective_line_count < 2 or len(normalized_lines) < 2:
        return False

    keyword_lines = sum(
        1
        for line in normalized_lines
        if _CODE_IMPORT_LINE_PATTERN.match(line) or _CODE_CONTROL_LINE_PATTERN.match(line)
    )
    punctuation_lines = sum(
        1
        for line in normalized_lines
        if any(marker in line for marker in ("{", "}", "=>", "::", "->"))
    )
    assignment_lines = sum(
        1
        for line in normalized_lines
        if "=" in line and "==" not in line and not re.search(r"\b(?:Table|Figure)\b", line)
    )
    semicolon_lines = sum(1 for line in normalized_lines if line.rstrip().endswith(";"))
    import_like_lines = sum(
        1
        for line in normalized_lines
        if _CODE_IMPORT_LINE_PATTERN.match(line)
    )
    decorator_lines = sum(1 for line in normalized_lines if line.startswith("@"))
    comment_doc_lines = sum(
        1
        for line in normalized_lines
        if line.startswith("#") or '"""' in line or "'''" in line
    )
    embedded_code_lines = sum(1 for line in normalized_lines if _looks_like_embedded_code_line(line))
    prose_sentence_lines = sum(
        1
        for line in normalized_lines
        if len(line.split()) >= 6 and re.search(r"[.!?](?:[\"'\)\]\u201d\u2019])?$", line)
    )

    if (
        prose_sentence_lines >= 2
        and keyword_lines == 0
        and punctuation_lines == 0
        and assignment_lines == 0
        and decorator_lines == 0
        and import_like_lines == 0
        and embedded_code_lines < 2
    ):
        return False
    if import_like_lines >= 2:
        return True
    if keyword_lines >= 2:
        return True
    if decorator_lines >= 1 and (keyword_lines >= 1 or embedded_code_lines >= 2):
        return True
    if comment_doc_lines >= 1 and (
        keyword_lines >= 1 or assignment_lines >= 1 or import_like_lines >= 1 or embedded_code_lines >= 2
    ):
        return True
    if embedded_code_lines >= max(2, len(normalized_lines) // 2) and prose_sentence_lines <= 1:
        return True
    if keyword_lines >= 1 and (assignment_lines >= 1 or punctuation_lines >= 1 or import_like_lines >= 1):
        return True
    if punctuation_lines >= 1 and (assignment_lines >= 1 or semicolon_lines >= 2 or import_like_lines >= 1):
        return True
    if assignment_lines >= 2 and any(re.match(r"^[A-Za-z_][A-Za-z0-9_.,\s]*\s*=", line) for line in normalized_lines):
        return True
    return False


def _split_inline_code_prose_line(text: str) -> tuple[str, str] | None:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) < 24:
        return None
    match = re.match(r"^(?P<code>.*?[}\]\)])\s+(?P<prose>[A-Z][A-Za-z].+)$", normalized)
    if match is None:
        return None
    code = match.group("code").strip()
    prose = match.group("prose").strip()
    if not prose or not _looks_like_sentence_prose_line(prose):
        return None
    if not (
        _CODE_IMPORT_LINE_PATTERN.match(code)
        or _CODE_CONTROL_LINE_PATTERN.match(code)
        or _looks_like_embedded_code_line(code)
        or code.endswith(("}", "]", ")"))
        or "=" in code
    ):
        return None
    return code, prose


def _expanded_code_candidate_lines(text: str) -> list[str]:
    expanded: list[str] = []
    for line in text.splitlines():
        normalized = _normalize_text(line)
        if not normalized:
            continue
        split = _split_inline_code_prose_line(normalized)
        if split is None:
            expanded.append(normalized)
            continue
        expanded.extend(part for part in split if part)
    return expanded


def _looks_like_embedded_code_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 220:
        return False
    if _CODE_IMPORT_LINE_PATTERN.match(normalized) or _CODE_CONTROL_LINE_PATTERN.match(normalized):
        return True
    if normalized.startswith(("@", "#", ">>>")):
        return True
    if re.match(r"^(?:[\"'][^\"']+[\"']|\d+|[A-Za-z_][A-Za-z0-9_]*)\s*:\s*\S+", normalized):
        return True
    if any(marker in normalized for marker in ("{", "}", "=>", "::", "->")):
        return True
    if "=" in normalized and "==" not in normalized and not normalized.endswith((".", "?", "!")):
        return True
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=\s*\S+", normalized):
        return True
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\(", normalized):
        return True
    if re.match(r"^(?:print|await|yield|return)\(", normalized):
        return True
    return False


def _looks_like_sentence_prose_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if len(normalized.split()) < 6:
        return False
    if _looks_like_embedded_code_line(normalized):
        return False
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", normalized.casefold())
    if len(tokens) < 6:
        return False
    stopword_hits = sum(1 for token in tokens if token in _PROSE_CONTINUATION_STOPWORDS)
    if stopword_hits < max(2, len(tokens) // 6):
        return False
    return bool(re.search(r"[.!?](?:[\"'\)\]\u201d\u2019])?$", normalized) or normalized[:1].isupper())


def _looks_like_code_docstring_line(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if '"""' in normalized or "'''" in normalized:
        return True
    return bool(
        re.match(
            r"^(?:Args|Returns|Raises|Examples?|Parameters?|Yields|Attributes?)\b",
            normalized,
            re.IGNORECASE,
        )
    )


def _looks_like_code_docstring_text(text: str) -> bool:
    normalized_lines = _expanded_code_candidate_lines(text)
    if len(normalized_lines) < 3:
        return False
    quote_lines = sum(1 for line in normalized_lines if '"""' in line or "'''" in line)
    cue_lines = sum(1 for line in normalized_lines if _looks_like_code_docstring_line(line))
    typed_parameter_lines = sum(
        1
        for line in normalized_lines
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)\s*:\s+\S+", line)
    )
    if quote_lines >= 1 and cue_lines >= 2:
        return True
    if cue_lines >= 2 and typed_parameter_lines >= 1:
        return True
    prose_lines = sum(1 for line in normalized_lines if len(line.split()) >= 4)
    return quote_lines >= 2 and prose_lines >= 3


def _looks_like_table(line_count: int, lines: list[str]) -> bool:
    nonempty_lines = [line.rstrip("\n") for line in lines if _normalize_text(line)]
    if not nonempty_lines:
        return False
    if len(nonempty_lines) == 1:
        return _looks_like_flattened_table_text(nonempty_lines[0])
    if line_count < 2 or len(nonempty_lines) < 2:
        return False
    token_counts = [len(_normalize_text(line).split()) for line in nonempty_lines[:6]]
    if len(token_counts) < 2:
        return False
    separator_lines = sum(
        1
        for line in nonempty_lines[:6]
        if _TABLE_SEPARATOR_PATTERN.search(line) or "|" in line
    )
    numeric_lines = sum(
        1
        for line in nonempty_lines[:6]
        if len(re.findall(r"\b\d+(?:\.\d+)?\b", line)) >= 1
    )
    if separator_lines >= 2 and max(token_counts) - min(token_counts) <= 4:
        return True
    if separator_lines >= 1 and numeric_lines >= 2 and max(token_counts) - min(token_counts) <= 3:
        return True
    dense_numeric_lines = sum(
        1
        for line in nonempty_lines[:24]
        if (
            len(re.findall(r"\b\d+(?:\.\d+)?\b", line)) >= 2
            or "±" in line
            or _TABLE_BRACKETED_GROUP_PATTERN.search(line)
            or _TABLE_ID_EQUALS_PATTERN.search(line)
        )
    )
    if dense_numeric_lines >= 3 and _looks_like_flattened_table_text(" ".join(nonempty_lines)):
        return True
    return False


def _looks_like_flattened_table_text(text: str) -> bool:
    normalized = _normalize_text(text)
    if len(normalized) < 80 or len(normalized) > 800:
        return False
    if _looks_like_caption_text(normalized) or _HEADING_PATTERN.match(normalized):
        return False
    if normalized.endswith((".", "?", "!")) and "±" not in normalized:
        return False

    numeric_hits = len(re.findall(r"\b\d+(?:\.\d+)?\b", normalized))
    if numeric_hits < 6:
        return False

    plus_minus_hits = normalized.count("±")
    bracket_group_hits = len(_TABLE_BRACKETED_GROUP_PATTERN.findall(normalized))
    id_group_hits = len(_TABLE_ID_EQUALS_PATTERN.findall(normalized))
    percent_hits = normalized.count("%")
    header_cue = bool(_TABLE_HEADER_CUE_PATTERN.search(normalized))
    if not header_cue:
        return False

    if plus_minus_hits >= 2:
        return True
    if bracket_group_hits >= 2 and (percent_hits >= 1 or id_group_hits >= 1):
        return True
    if id_group_hits >= 2 and percent_hits >= 1:
        return True
    return False


def _looks_like_equation(
    text: str,
    line_count: int,
    bbox: tuple[float, float, float, float],
    page_width: float,
) -> bool:
    normalized_lines = [_normalize_text(line) for line in text.splitlines() if _normalize_text(line)]
    if not normalized_lines:
        return False
    if not 1 <= len(normalized_lines) <= 3:
        return False
    if line_count > 4:
        return False
    if _looks_like_table(len(normalized_lines), normalized_lines):
        return False
    normalized = " ".join(normalized_lines)
    if normalized_lines and normalized_lines[0].startswith("#") and "=" in normalized:
        return False
    if _looks_like_code(text, line_count):
        return False
    if len(normalized) < 6 or len(normalized) > 120:
        return False
    if _looks_like_caption_text(normalized) or _HEADING_PATTERN.match(normalized):
        return False
    if normalized.endswith((".", "?", "!", ":", ";")):
        return False
    if _EQUATION_CODE_CUE_PATTERN.search(normalized):
        return False
    if re.search(r"[A-Za-z_]\.[A-Za-z_]", normalized):
        return False

    operator_hits = len(_EQUATION_OPERATOR_PATTERN.findall(normalized))
    math_punctuation_hits = sum(normalized.count(marker) for marker in "=+-*/^_()[]{}")
    if operator_hits < 1 or math_punctuation_hits < 3:
        return False

    tokens = normalized.split()
    if len(tokens) > 18:
        return False
    long_alpha_words = [
        token
        for token in (re.sub(r"[^A-Za-z]", "", part) for part in tokens)
        if len(token) >= 4
    ]
    if len(long_alpha_words) > 5:
        return False

    variable_hits = len(_EQUATION_VARIABLE_PATTERN.findall(normalized))
    digit_hits = len(re.findall(r"\b\d+(?:\.\d+)?\b", normalized))
    if variable_hits < 3 and digit_hits < 1:
        return False

    block_center = (bbox[0] + bbox[2]) / 2.0
    centered = abs(block_center - (page_width / 2.0)) <= page_width * 0.18
    inset = bbox[0] >= page_width * 0.15 and bbox[2] <= page_width * 0.85
    if not (centered or inset):
        return False
    return True


def _caption_matches_artifact_role(text: str, artifact_role: str) -> bool:
    normalized = _normalize_text(text)
    role = (artifact_role or "").strip().casefold()
    if role == "image":
        return _looks_like_figure_caption(normalized)
    if role in {"table", "table_like"}:
        return _looks_like_table_caption(normalized)
    if role == "equation":
        return bool(_EQUATION_CAPTION_PATTERN.match(normalized))
    return False


def _looks_like_figure_caption(text: str) -> bool:
    return bool(_FIGURE_CAPTION_PATTERN.match(_normalize_text(text)))


def _looks_like_table_caption(text: str) -> bool:
    return bool(_TABLE_CAPTION_PATTERN.match(_normalize_text(text)))


def _looks_like_caption_text(text: str) -> bool:
    normalized = _normalize_text(text)
    return (
        _looks_like_figure_caption(normalized)
        or _looks_like_table_caption(normalized)
        or bool(_EQUATION_CAPTION_PATTERN.match(normalized))
    )


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


@dataclass(slots=True, frozen=True)
class _PageLayoutAssessment:
    risk: str = "low"
    reasons: tuple[str, ...] = ()


def _assess_page_layout(
    page: "PdfPage",
    *,
    profile: "PdfFileProfile",
    document_title: str | None,
) -> _PageLayoutAssessment:
    reasons: list[str] = []
    if _page_has_multi_column_signature(page):
        reasons.append("multi_column")
    if _page_has_column_fragment_signature(page):
        reasons.append("column_fragment")
    if (
        profile.recovery_lane == "academic_paper"
        and _page_has_asymmetric_academic_first_page_signal(page, document_title)
    ):
        reasons.append("academic_first_page_asymmetric")

    if "academic_first_page_asymmetric" in reasons:
        risk = "high"
    elif reasons:
        risk = "medium"
    else:
        risk = "low"
    return _PageLayoutAssessment(risk=risk, reasons=tuple(reasons))


@dataclass(slots=True, frozen=True)
class PdfFileProfile:
    pdf_kind: str
    page_count: int
    has_extractable_text: bool
    outline_present: bool
    layout_risk: str
    ocr_required: bool
    extractor_kind: str | None = None
    average_text_density: float = 0.0
    average_span_count: float = 0.0
    multi_column_page_count: int = 0
    fragment_page_count: int = 0
    suspicious_page_numbers: list[int] = field(default_factory=list)
    recovery_lane: str | None = None
    trailing_reference_page_count: int = 0
    academic_paper_candidate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pdf_kind": self.pdf_kind,
            "page_count": self.page_count,
            "has_extractable_text": self.has_extractable_text,
            "outline_present": self.outline_present,
            "layout_risk": self.layout_risk,
            "ocr_required": self.ocr_required,
            "extractor_kind": self.extractor_kind,
            "average_text_density": round(self.average_text_density, 3),
            "average_span_count": round(self.average_span_count, 3),
            "multi_column_page_count": self.multi_column_page_count,
            "fragment_page_count": self.fragment_page_count,
            "suspicious_page_numbers": self.suspicious_page_numbers,
            "recovery_lane": self.recovery_lane,
            "trailing_reference_page_count": self.trailing_reference_page_count,
            "academic_paper_candidate": self.academic_paper_candidate,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PdfFileProfile":
        return cls(
            pdf_kind=str(payload.get("pdf_kind", "text_pdf")),
            page_count=int(payload.get("page_count", 0)),
            has_extractable_text=bool(payload.get("has_extractable_text", False)),
            outline_present=bool(payload.get("outline_present", False)),
            layout_risk=str(payload.get("layout_risk", "low")),
            ocr_required=bool(payload.get("ocr_required", False)),
            extractor_kind=str(payload["extractor_kind"]) if payload.get("extractor_kind") else None,
            average_text_density=float(payload.get("average_text_density", 0.0) or 0.0),
            average_span_count=float(payload.get("average_span_count", 0.0) or 0.0),
            multi_column_page_count=int(payload.get("multi_column_page_count", 0) or 0),
            fragment_page_count=int(payload.get("fragment_page_count", 0) or 0),
            suspicious_page_numbers=[int(page) for page in payload.get("suspicious_page_numbers", [])],
            recovery_lane=str(payload["recovery_lane"]) if payload.get("recovery_lane") else None,
            trailing_reference_page_count=int(payload.get("trailing_reference_page_count", 0) or 0),
            academic_paper_candidate=bool(payload.get("academic_paper_candidate", False)),
        )


@dataclass(slots=True, frozen=True)
class PdfOutlineEntry:
    level: int
    title: str
    page_number: int


@dataclass(slots=True, frozen=True)
class PdfTextBlock:
    page_number: int
    block_number: int
    text: str
    bbox: tuple[float, float, float, float]
    line_texts: list[str]
    span_count: int
    line_count: int
    font_size_min: float
    font_size_max: float
    font_size_avg: float


@dataclass(slots=True, frozen=True)
class PdfImageBlock:
    page_number: int
    block_number: int
    bbox: tuple[float, float, float, float]
    width_px: int | None = None
    height_px: int | None = None
    image_ext: str | None = None
    image_type: str = "embedded_image"


@dataclass(slots=True, frozen=True)
class PdfPage:
    page_number: int
    width: float
    height: float
    blocks: list[PdfTextBlock]
    image_blocks: list[PdfImageBlock] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class PdfExtraction:
    title: str | None
    author: str | None
    metadata: dict[str, Any]
    pages: list[PdfPage]
    outline_entries: list[PdfOutlineEntry]


class PdfTextExtractor(Protocol):
    def extract(self, file_path: str | Path) -> PdfExtraction:
        ...


class PyMuPDFTextExtractor:
    def extract(self, file_path: str | Path) -> PdfExtraction:
        try:
            import fitz
        except ImportError as exc:  # pragma: no cover - exercised via runtime failure path.
            raise RuntimeError(
                "PDF support requires PyMuPDF. Run `uv sync` to install PDF dependencies."
            ) from exc

        document = fitz.open(str(file_path))
        try:
            metadata = {key: value for key, value in (document.metadata or {}).items() if value}
            outline_entries = self._extract_outline(document)
            pages: list[PdfPage] = []

            for page_index in range(document.page_count):
                page = document.load_page(page_index)
                text_dict = page.get_text("dict", sort=False)
                blocks: list[PdfTextBlock] = []
                image_blocks: list[PdfImageBlock] = []
                for block_number, block in enumerate(text_dict.get("blocks", []), start=1):
                    if block.get("type") == 1:
                        image_blocks.append(
                            PdfImageBlock(
                                page_number=page_index + 1,
                                block_number=block_number,
                                bbox=tuple(float(value) for value in block.get("bbox", (0, 0, 0, 0))),
                                width_px=(
                                    int(block["width"])
                                    if isinstance(block.get("width"), (int, float))
                                    else None
                                ),
                                height_px=(
                                    int(block["height"])
                                    if isinstance(block.get("height"), (int, float))
                                    else None
                                ),
                                image_ext=str(block["ext"]).strip().lower() if block.get("ext") else None,
                            )
                        )
                        continue
                    if block.get("type") != 0:
                        continue
                    lines: list[str] = []
                    span_count = 0
                    font_sizes: list[float] = []
                    for line in block.get("lines", []):
                        parts: list[str] = []
                        for span in line.get("spans", []):
                            text = span.get("text", "")
                            if not text:
                                continue
                            parts.append(text)
                            span_count += 1
                            font_sizes.append(float(span.get("size", 0.0) or 0.0))
                        normalized_line = _normalize_text("".join(parts))
                        if normalized_line:
                            lines.append(normalized_line)

                    text = _normalize_multiline_text("\n".join(lines))
                    if not text:
                        continue

                    blocks.append(
                        PdfTextBlock(
                            page_number=page_index + 1,
                            block_number=block_number,
                            text=text,
                            bbox=tuple(float(value) for value in block.get("bbox", (0, 0, 0, 0))),
                            line_texts=lines,
                            span_count=span_count,
                            line_count=len(lines),
                            font_size_min=min(font_sizes) if font_sizes else 0.0,
                            font_size_max=max(font_sizes) if font_sizes else 0.0,
                            font_size_avg=_safe_mean(font_sizes),
                        )
                    )

                pages.append(
                    PdfPage(
                        page_number=page_index + 1,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        blocks=blocks,
                        image_blocks=image_blocks,
                    )
                )

            return PdfExtraction(
                title=metadata.get("title") or None,
                author=metadata.get("author") or None,
                metadata={**metadata, "pdf_extractor": "pymupdf"},
                pages=pages,
                outline_entries=outline_entries,
            )
        finally:
            document.close()

    def _extract_outline(self, document: Any) -> list[PdfOutlineEntry]:
        outline_entries: list[PdfOutlineEntry] = []
        for item in document.get_toc() or []:
            if len(item) < 3:
                continue
            level, title, page_number = item[:3]
            if not title or not page_number:
                continue
            outline_entries.append(
                PdfOutlineEntry(
                    level=int(level),
                    title=_normalize_text(str(title)),
                    page_number=max(1, int(page_number)),
                )
            )
        return outline_entries


class BasicPdfTextExtractor:
    def extract(self, file_path: str | Path) -> PdfExtraction:
        raw_pdf = Path(file_path).read_bytes()
        objects = self._read_objects(raw_pdf)
        page_object_ids = self._ordered_page_object_ids(objects)
        metadata = self._read_metadata(raw_pdf, objects)
        pages = self._extract_pages(objects, page_object_ids)
        page_count_hint = (
            len(page_object_ids)
            or self._fallback_page_count_hint(objects)
            or self._coregraphics_page_count_hint(file_path)
        )
        outline_entries = self._extract_outline_entries(
            objects,
            {object_id: page_number for page_number, object_id in enumerate(page_object_ids, start=1)},
        )
        return PdfExtraction(
            title=metadata.get("title"),
            author=metadata.get("author"),
            metadata={
                **metadata,
                "pdf_extractor": "basic",
                **({"page_count_hint": page_count_hint} if page_count_hint else {}),
            },
            pages=pages,
            outline_entries=outline_entries,
        )

    def _read_objects(self, raw_pdf: bytes) -> dict[int, bytes]:
        objects: dict[int, bytes] = {}
        for match in re.finditer(rb"(?ms)(\d+)\s+\d+\s+obj\s*(.*?)\s*endobj", raw_pdf):
            objects[int(match.group(1))] = match.group(2)
        return objects

    def _read_metadata(self, raw_pdf: bytes, objects: dict[int, bytes]) -> dict[str, Any]:
        info_match = re.search(rb"/Info\s+(\d+)\s+\d+\s+R", raw_pdf)
        if not info_match:
            return {}
        info_body = objects.get(int(info_match.group(1)))
        if not info_body:
            return {}
        metadata = {}
        for metadata_field in ("Title", "Author"):
            value = self._extract_literal_string(info_body, f"/{metadata_field}".encode("ascii"))
            if value:
                metadata[metadata_field.casefold()] = value
        return metadata

    def _ordered_page_object_ids(self, objects: dict[int, bytes]) -> list[int]:
        return [
            object_id
            for object_id, body in sorted(
                (
                    (object_id, body)
                    for object_id, body in objects.items()
                    if b"/Type /Page" in body and b"/Type /Pages" not in body
                ),
                key=lambda item: item[0],
            )
        ]

    def _fallback_page_count_hint(self, objects: dict[int, bytes]) -> int:
        counts = [
            int(match.group(1))
            for body in objects.values()
            for match in re.finditer(rb"/Type\s*/Pages\b.*?/Count\s+(\d+)", body, re.S)
        ]
        return max(counts, default=0)

    def _coregraphics_page_count_hint(self, file_path: str | Path) -> int:
        if sys.platform != "darwin":
            return 0
        try:
            coregraphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")

            create_provider = coregraphics.CGDataProviderCreateWithFilename
            create_provider.argtypes = [ctypes.c_char_p]
            create_provider.restype = ctypes.c_void_p

            create_document = coregraphics.CGPDFDocumentCreateWithProvider
            create_document.argtypes = [ctypes.c_void_p]
            create_document.restype = ctypes.c_void_p

            get_page_count = coregraphics.CGPDFDocumentGetNumberOfPages
            get_page_count.argtypes = [ctypes.c_void_p]
            get_page_count.restype = ctypes.c_size_t

            release_document = coregraphics.CGPDFDocumentRelease
            release_document.argtypes = [ctypes.c_void_p]
            release_document.restype = None

            release_provider = coregraphics.CGDataProviderRelease
            release_provider.argtypes = [ctypes.c_void_p]
            release_provider.restype = None

            provider = create_provider(str(Path(file_path).resolve()).encode("utf-8"))
            if not provider:
                return 0
            document = create_document(provider)
            if not document:
                release_provider(provider)
                return 0
            try:
                return int(get_page_count(document))
            finally:
                release_document(document)
                release_provider(provider)
        except Exception:
            return 0

    def _extract_pages(self, objects: dict[int, bytes], page_object_ids: list[int]) -> list[PdfPage]:
        pages: list[PdfPage] = []
        enable_positioned_blocks = len(page_object_ids) <= 24
        for page_number, object_id in enumerate(page_object_ids, start=1):
            body = objects.get(object_id)
            if body is None:
                continue
            media_box = self._parse_media_box(body)
            width = media_box[2] - media_box[0]
            height = media_box[3] - media_box[1]
            content_streams = self._page_content_streams(body, objects)
            blocks: list[PdfTextBlock] = []
            block_number = 0
            for stream in content_streams:
                for block in self._parse_stream_blocks(
                    stream,
                    page_number,
                    height,
                    width,
                    enable_positioned_blocks=enable_positioned_blocks,
                ):
                    block_number += 1
                    blocks.append(
                        PdfTextBlock(
                            page_number=page_number,
                            block_number=block_number,
                            text=block["text"],
                            bbox=block["bbox"],
                            line_texts=[block["text"]],
                            span_count=max(1, len(block["text"])),
                            line_count=1,
                            font_size_min=block["font_size"],
                            font_size_max=block["font_size"],
                            font_size_avg=block["font_size"],
                        )
                    )
            pages.append(
                PdfPage(
                    page_number=page_number,
                    width=width,
                    height=height,
                    blocks=blocks,
                )
            )
        return pages

    def _extract_outline_entries(
        self,
        objects: dict[int, bytes],
        page_number_by_object: dict[int, int],
    ) -> list[PdfOutlineEntry]:
        outline_entries: list[PdfOutlineEntry] = []
        for object_id, body in sorted(objects.items()):
            title = self._extract_literal_string(body, b"/Title")
            if not title:
                continue
            page_object_id = self._extract_outline_page_object_id(body)
            if page_object_id is None:
                continue
            page_number = page_number_by_object.get(page_object_id)
            if page_number is None:
                continue
            outline_entries.append(
                PdfOutlineEntry(
                    level=self._outline_level(object_id, objects),
                    title=_normalize_text(title),
                    page_number=page_number,
                )
            )
        return outline_entries

    def _extract_outline_page_object_id(self, body: bytes) -> int | None:
        direct_match = re.search(rb"/Dest\s*\[\s*(\d+)\s+\d+\s+R", body, re.S)
        if direct_match:
            return int(direct_match.group(1))
        action_match = re.search(rb"/A\s*<<.*?/D\s*\[\s*(\d+)\s+\d+\s+R", body, re.S)
        if action_match:
            return int(action_match.group(1))
        return None

    def _outline_level(self, object_id: int, objects: dict[int, bytes]) -> int:
        level = 1
        seen = {object_id}
        current_object_id = object_id
        while True:
            body = objects.get(current_object_id)
            if body is None:
                return level
            parent_match = re.search(rb"/Parent\s+(\d+)\s+\d+\s+R", body)
            if not parent_match:
                return level
            parent_object_id = int(parent_match.group(1))
            if parent_object_id in seen:
                return level
            seen.add(parent_object_id)
            parent_body = objects.get(parent_object_id)
            if parent_body is None:
                return level
            if self._extract_literal_string(parent_body, b"/Title"):
                level += 1
            current_object_id = parent_object_id

    def _parse_media_box(self, body: bytes) -> tuple[float, float, float, float]:
        match = re.search(
            rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]",
            body,
        )
        if not match:
            return (0.0, 0.0, 595.0, 842.0)
        return tuple(float(value) for value in match.groups())  # type: ignore[return-value]

    def _page_content_streams(self, page_body: bytes, objects: dict[int, bytes]) -> list[bytes]:
        content_refs: list[int] = []
        array_match = re.search(rb"/Contents\s*\[(.*?)\]", page_body, re.S)
        if array_match:
            content_refs.extend(int(value) for value in re.findall(rb"(\d+)\s+\d+\s+R", array_match.group(1)))
        else:
            single_match = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", page_body)
            if single_match:
                content_refs.append(int(single_match.group(1)))

        streams: list[bytes] = []
        for ref in content_refs:
            body = objects.get(ref)
            if not body or b"stream" not in body:
                continue
            dictionary, stream = body.split(b"stream", 1)
            stream, _end_marker, _tail = stream.partition(b"endstream")
            stream = stream.lstrip(b"\r\n").rstrip(b"\r\n")
            if b"/FlateDecode" in dictionary:
                try:
                    stream = zlib.decompress(stream)
                except zlib.error:
                    continue
            streams.append(stream)
        return streams

    def _parse_stream_blocks(
        self,
        stream: bytes,
        page_number: int,
        page_height: float,
        page_width: float,
        *,
        enable_positioned_blocks: bool,
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for segment in re.findall(rb"BT(.*?)ET", stream, re.S):
            if enable_positioned_blocks and self._should_use_positioned_segment_parsing(segment, page_width):
                positioned_blocks = self._parse_positioned_segment_blocks(
                    segment,
                    page_number=page_number,
                    page_height=page_height,
                    page_width=page_width,
                )
                if positioned_blocks:
                    blocks.extend(positioned_blocks)
                    continue

            font_size = self._extract_font_size(segment)
            x, y = self._extract_position(segment)
            normalized_text = _normalize_text(self._extract_segment_text(segment))
            if not normalized_text:
                continue
            blocks.append(
                {
                    "page_number": page_number,
                    "text": normalized_text,
                    "font_size": font_size,
                    "bbox": self._estimated_bbox(
                        normalized_text,
                        font_size=font_size,
                        x=x,
                        y=y,
                        page_height=page_height,
                        page_width=page_width,
                        width_scale=0.5,
                    ),
                }
            )
        return blocks

    def _should_use_positioned_segment_parsing(self, segment: bytes, page_width: float) -> bool:
        tm_positions = [
            (float(x), float(y))
            for x, y in re.findall(
                rb"[-\d.]+\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s+([-\d.]+)\s+([-\d.]+)\s+Tm",
                segment,
            )
        ]
        if len(tm_positions) < 6:
            return False
        if len(re.findall(rb"\[(.*?)\]\s*TJ", segment, re.S)) < 6 and len(re.findall(rb"\((.*?)(?<!\\)\)\s*Tj", segment, re.S)) < 6:
            return False

        left_count = sum(1 for x, _y in tm_positions if x <= page_width * 0.32)
        right_count = sum(1 for x, _y in tm_positions if x >= page_width * 0.45)
        distinct_rows = len({round(y, 1) for _x, y in tm_positions})
        return left_count >= 3 and right_count >= 3 and distinct_rows >= 3

    def _extract_font_size(self, segment: bytes) -> float:
        match = re.findall(rb"/F\d+\s+([-\d.]+)\s+Tf", segment)
        if match:
            return float(match[-1])
        return 12.0

    def _extract_position(self, segment: bytes) -> tuple[float, float]:
        tm_matches = re.findall(rb"1\s+0\s+0\s+1\s+([-\d.]+)\s+([-\d.]+)\s+Tm", segment)
        if tm_matches:
            x, y = tm_matches[-1]
            return float(x), float(y)
        td_matches = re.findall(rb"([-\d.]+)\s+([-\d.]+)\s+Td", segment)
        if td_matches:
            x, y = td_matches[-1]
            return float(x), float(y)
        return 0.0, 0.0

    def _parse_positioned_segment_blocks(
        self,
        segment: bytes,
        *,
        page_number: int,
        page_height: float,
        page_width: float,
    ) -> list[dict[str, Any]]:
        token_pattern = re.compile(
            rb"/F\d+\s+[-\d.]+\s+Tf"
            rb"|(?:[-\d.]+\s+){5}[-\d.]+\s+Tm"
            rb"|[-\d.]+\s+[-\d.]+\s+Td"
            rb"|\[(?:.*?)\]\s*TJ"
            rb"|\((?:.*?)(?<!\\)\)\s*Tj",
            re.S,
        )
        operations = list(token_pattern.finditer(segment))
        if not operations:
            return []

        blocks: list[dict[str, Any]] = []
        current_font_size = self._extract_font_size(segment)
        current_parts: list[str] = []
        current_font_sizes: list[float] = []
        current_origin: tuple[float, float] | None = None
        current_cursor: tuple[float, float] | None = None

        def flush_current() -> None:
            nonlocal current_parts, current_font_sizes, current_origin
            normalized_text = _normalize_text(" ".join(part for part in current_parts if part))
            if not normalized_text or current_origin is None:
                current_parts = []
                current_font_sizes = []
                current_origin = None
                return
            font_size = _safe_mean(current_font_sizes) if current_font_sizes else current_font_size
            blocks.append(
                {
                    "page_number": page_number,
                    "text": normalized_text,
                    "font_size": font_size,
                    "bbox": self._estimated_bbox(
                        normalized_text,
                        font_size=font_size,
                        x=current_origin[0],
                        y=current_origin[1],
                        page_height=page_height,
                        page_width=page_width,
                        width_scale=0.32,
                    ),
                }
            )
            current_parts = []
            current_font_sizes = []
            current_origin = None

        for match in operations:
            token = match.group(0)
            if token.endswith(b" Tf"):
                font_size_match = re.search(rb"/F\d+\s+([-\d.]+)\s+Tf$", token)
                if font_size_match:
                    current_font_size = float(font_size_match.group(1))
                continue

            if token.endswith(b" Tm"):
                position = self._tm_position(token)
                if position is None:
                    continue
                if self._starts_new_positioned_block(
                    current_origin,
                    position,
                    current_parts=current_parts,
                    font_size=current_font_size,
                    page_width=page_width,
                ):
                    flush_current()
                current_cursor = position
                if current_origin is None:
                    current_origin = position
                continue

            if token.endswith(b" Td"):
                if current_cursor is None:
                    continue
                position = self._td_position(token, current_cursor)
                if position is None:
                    continue
                if self._starts_new_positioned_block(
                    current_origin,
                    position,
                    current_parts=current_parts,
                    font_size=current_font_size,
                    page_width=page_width,
                ):
                    flush_current()
                current_cursor = position
                if current_origin is None:
                    current_origin = position
                continue

            text = self._extract_text_token(token)
            if not text:
                continue
            if current_cursor is None:
                current_cursor = (0.0, 0.0)
            if current_origin is None:
                current_origin = current_cursor
            current_parts.append(text)
            current_font_sizes.append(current_font_size)

        flush_current()
        return blocks

    def _tm_position(self, token: bytes) -> tuple[float, float] | None:
        match = re.search(rb"([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm$", token)
        if not match:
            return None
        return float(match.group(5)), float(match.group(6))

    def _td_position(
        self,
        token: bytes,
        current_cursor: tuple[float, float],
    ) -> tuple[float, float] | None:
        match = re.search(rb"([-\d.]+)\s+([-\d.]+)\s+Td$", token)
        if not match:
            return None
        return current_cursor[0] + float(match.group(1)), current_cursor[1] + float(match.group(2))

    def _extract_text_token(self, token: bytes) -> str:
        stripped = token.rstrip()
        if stripped.endswith(b"Tj"):
            match = re.search(rb"\((.*?)(?<!\\)\)\s*Tj$", token, re.S)
            if not match:
                return ""
            return self._decode_pdf_string(match.group(1))
        if stripped.endswith(b"TJ"):
            parts = [
                self._decode_pdf_string(match)
                for match in re.findall(rb"\((.*?)(?<!\\)\)", token, re.S)
            ]
            return " ".join(part for part in parts if part)
        return ""

    def _starts_new_positioned_block(
        self,
        current_origin: tuple[float, float] | None,
        next_position: tuple[float, float],
        *,
        current_parts: list[str],
        font_size: float,
        page_width: float,
    ) -> bool:
        if current_origin is None or not current_parts:
            return False

        vertical_delta = abs(next_position[1] - current_origin[1])
        if vertical_delta >= max(6.0, font_size * 0.75):
            return True

        current_text = _normalize_text(" ".join(current_parts))
        horizontal_delta = abs(next_position[0] - current_origin[0])
        if (
            len(current_text) >= 60
            and horizontal_delta >= page_width * 0.3
            and (
                (current_origin[0] <= page_width * 0.32 and next_position[0] >= page_width * 0.45)
                or (current_origin[0] >= page_width * 0.45 and next_position[0] <= page_width * 0.32)
            )
        ):
            return True
        return False

    def _estimated_bbox(
        self,
        text: str,
        *,
        font_size: float,
        x: float,
        y: float,
        page_height: float,
        page_width: float,
        width_scale: float,
    ) -> tuple[float, float, float, float]:
        estimated_width = max(font_size * width_scale * len(text), font_size * 2)
        top = max(0.0, page_height - y - font_size)
        bottom = min(page_height, top + font_size * 1.2)
        return (x, top, min(x + estimated_width, page_width), bottom)

    def _extract_segment_text(self, segment: bytes) -> str:
        parts = [self._decode_pdf_string(match) for match in re.findall(rb"\((.*?)(?<!\\)\)\s*Tj", segment, re.S)]
        for array_match in re.findall(rb"\[(.*?)\]\s*TJ", segment, re.S):
            parts.extend(self._decode_pdf_string(match) for match in re.findall(rb"\((.*?)(?<!\\)\)", array_match, re.S))
        return " ".join(part for part in parts if part)

    def _extract_literal_string(self, body: bytes, key: bytes) -> str | None:
        match = re.search(key + rb"\s*\((.*?)(?<!\\)\)", body, re.S)
        if not match:
            return None
        return self._decode_pdf_string(match.group(1))

    def _decode_pdf_string(self, payload: bytes) -> str:
        decoded = payload.decode("latin1")
        decoded = decoded.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
        return decoded.strip()


class DefaultPdfTextExtractor:
    def __init__(self, extractors: list[PdfTextExtractor] | None = None):
        self.extractors = extractors or [PyMuPDFTextExtractor(), BasicPdfTextExtractor()]

    def extract(self, file_path: str | Path) -> PdfExtraction:
        last_error: Exception | None = None
        for extractor in self.extractors:
            try:
                return extractor.extract(file_path)
            except RuntimeError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise RuntimeError("No PDF extractor is configured")


class PdfFileProfiler:
    def __init__(self, extractor: PdfTextExtractor | None = None):
        self.extractor = extractor or DefaultPdfTextExtractor()

    def profile(self, file_path: str | Path) -> PdfFileProfile:
        extraction = self.extractor.extract(file_path)
        return self.profile_from_extraction(extraction)

    def profile_from_extraction(self, extraction: PdfExtraction) -> PdfFileProfile:
        page_count_hint = int(extraction.metadata.get("page_count_hint", 0) or 0)
        page_count = max(len(extraction.pages), page_count_hint)
        extractor_kind = str(extraction.metadata.get("pdf_extractor") or "").strip() or None
        if page_count == 0:
            return PdfFileProfile(
                pdf_kind="scanned_pdf",
                page_count=0,
                has_extractable_text=False,
                outline_present=bool(extraction.outline_entries),
                layout_risk="high",
                ocr_required=True,
                extractor_kind=extractor_kind,
            )

        text_density = [sum(len(_normalize_text(block.text)) for block in page.blocks) for page in extraction.pages]
        pages_with_text = sum(1 for density in text_density if density >= 32)
        has_extractable_text = pages_with_text > 0
        text_ratio = pages_with_text / page_count
        total_blocks = sum(len(page.blocks) for page in extraction.pages)
        total_spans = sum(block.span_count for page in extraction.pages for block in page.blocks)
        multi_column_pages: list[int] = []
        fragment_pages: list[int] = []
        suspicious_pages: list[int] = []
        for page in extraction.pages:
            has_multi_column = _page_has_multi_column_signature(page)
            has_fragment = _page_has_column_fragment_signature(page)
            if has_multi_column:
                multi_column_pages.append(page.page_number)
            if has_fragment:
                fragment_pages.append(page.page_number)
            if has_multi_column or has_fragment:
                suspicious_pages.append(page.page_number)

        if not has_extractable_text or text_ratio < 0.2:
            pdf_kind = "scanned_pdf"
            ocr_required = True
        elif text_ratio < 0.9:
            pdf_kind = "mixed_pdf"
            ocr_required = True
        else:
            pdf_kind = "text_pdf"
            ocr_required = False

        fragment_ratio = (len(fragment_pages) / page_count) if page_count else 0.0
        trailing_reference_page_count = _trailing_reference_page_count(extraction.pages)
        first_page = extraction.pages[0] if extraction.pages else None
        first_page_title_signal = bool(
            first_page is not None
            and (
                _page_has_centered_title_signal(first_page)
                or _page_has_title_overlap_signal(first_page, extraction.title)
            )
        )
        academic_paper_candidate = bool(
            pdf_kind == "text_pdf"
            and page_count <= 24
            and trailing_reference_page_count >= 1
            and (
                (
                    extractor_kind == "basic"
                    and len(suspicious_pages) >= 2
                    and first_page_title_signal
                )
                or (
                    extractor_kind == "pymupdf"
                    and (len(multi_column_pages) >= 2 or bool(extraction.outline_entries))
                    and (first_page_title_signal or bool(extraction.title))
                )
            )
        )
        basic_fragment_only_layout = bool(
            extractor_kind == "basic"
            and pdf_kind == "text_pdf"
            and not multi_column_pages
            and len(fragment_pages) >= 5
            and page_count >= 40
            and fragment_ratio <= 0.4
        )
        outlined_localized_multi_column_book = bool(
            pdf_kind == "text_pdf"
            and not academic_paper_candidate
            and extractor_kind == "pymupdf"
            and bool(extraction.outline_entries)
            and page_count >= 80
            and len(suspicious_pages) >= 2
            and not fragment_pages
            and (len(suspicious_pages) / page_count) <= 0.12
            and len(suspicious_pages) <= max(12, min(40, int(page_count * 0.15)))
        )

        if pdf_kind == "scanned_pdf":
            layout_risk = "high"
        elif pdf_kind == "mixed_pdf":
            layout_risk = "high" if suspicious_pages else "medium"
        elif academic_paper_candidate:
            layout_risk = "medium"
        elif outlined_localized_multi_column_book:
            layout_risk = "medium"
        elif basic_fragment_only_layout:
            layout_risk = "medium"
        elif len(suspicious_pages) >= 2:
            layout_risk = "high"
        elif suspicious_pages:
            layout_risk = "medium"
        else:
            layout_risk = "low"

        return PdfFileProfile(
            pdf_kind=pdf_kind,
            page_count=page_count,
            has_extractable_text=has_extractable_text,
            outline_present=bool(extraction.outline_entries),
            layout_risk=layout_risk,
            ocr_required=ocr_required,
            extractor_kind=extractor_kind,
            average_text_density=_safe_mean([float(value) for value in text_density]),
            average_span_count=(total_spans / total_blocks) if total_blocks else 0.0,
            multi_column_page_count=len(multi_column_pages),
            fragment_page_count=len(fragment_pages),
            suspicious_page_numbers=suspicious_pages,
            recovery_lane=(
                "academic_paper"
                if academic_paper_candidate
                else "outlined_book"
                if outlined_localized_multi_column_book
                else None
            ),
            trailing_reference_page_count=trailing_reference_page_count,
            academic_paper_candidate=academic_paper_candidate,
        )


@dataclass(slots=True)
class _RecoveredBlock:
    role: str
    block_type: BlockType
    text: str
    page_start: int
    page_end: int
    bbox_regions: list[dict[str, Any]]
    reading_order_index: int
    parse_confidence: float
    flags: list[str]
    font_size_avg: float
    source_path: str
    anchor: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class _TocEntryCandidate:
    title: str
    page_number: int | None


@dataclass(slots=True, frozen=True)
class _PageRecoveryContext:
    is_toc_page: bool
    page_family: str = "body"
    family_source: str = "body"
    family_heading: str | None = None
    content_family: str | None = None
    backmatter_cue: str | None = None
    backmatter_cue_source: str | None = None
    has_strong_heading: bool = False
    toc_entries_by_text: dict[str, _TocEntryCandidate] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class _ChapterStartCandidate:
    page_number: int
    title: str
    source: str
    section_family: str | None = None


class PdfStructureRecoveryService:
    def recover(
        self,
        file_path: str | Path,
        extraction: PdfExtraction,
        profile: PdfFileProfile,
    ) -> ParsedDocument:
        ordered_pages = sorted(extraction.pages, key=lambda page: page.page_number)
        repeated_edge_text = self._find_repeated_edge_text(ordered_pages)
        page_contexts = self._page_contexts(ordered_pages)
        page_layout_assessments = self._page_layout_assessments(ordered_pages, profile, extraction.title)
        recovered_blocks = self._recover_blocks(
            ordered_pages,
            repeated_edge_text,
            page_contexts,
            extraction.outline_entries,
            profile,
        )
        self._link_footnotes(recovered_blocks)
        recovered_blocks = self._recover_embedded_page_heading_blocks(recovered_blocks)
        recovered_blocks = self._recover_document_title_heading_blocks(recovered_blocks, extraction.title)
        recovered_blocks = self._recover_academic_section_blocks(recovered_blocks, profile)
        recovered_blocks = self._merge_adjacent_heading_continuations(recovered_blocks)
        recovered_blocks = self._repair_prose_artifact_continuations(recovered_blocks, ordered_pages)
        recovered_blocks = self._split_mixed_code_prose_blocks(recovered_blocks)
        recovered_blocks = self._promote_late_code_like_bodies(recovered_blocks)
        recovered_blocks = self._split_mixed_code_prose_blocks(recovered_blocks)
        recovered_blocks = self._promote_late_table_like_bodies(recovered_blocks)
        recovered_blocks = self._merge_adjacent_table_fragments(recovered_blocks)
        self._link_artifact_captions(recovered_blocks)
        self._link_artifact_group_contexts(
            recovered_blocks,
            academic_paper=profile.recovery_lane == "academic_paper",
        )
        chapters = self._build_chapters(recovered_blocks, extraction.outline_entries, profile, file_path)
        chapters = self._repair_academic_first_page_abstract_continuations(
            chapters,
            ordered_pages,
            page_layout_assessments,
            profile,
        )

        title = (
            extraction.title
            or self._infer_document_title_from_recovered_blocks(recovered_blocks)
            or (chapters[0].title if chapters else None)
        )
        if title and chapters and chapters[0].title and _titles_overlap(chapters[0].title, title):
            chapters[0] = replace(chapters[0], title=title)
        metadata = {
            **extraction.metadata,
            "pdf_profile": profile.to_dict(),
            "outline_entry_count": len(extraction.outline_entries),
            "pdf_page_evidence": self._page_evidence(
                ordered_pages,
                page_contexts,
                page_layout_assessments,
                recovered_blocks,
                extraction.outline_entries,
                profile,
            ),
        }
        return ParsedDocument(
            title=title,
            author=extraction.author,
            language="en",
            chapters=chapters,
            metadata=metadata,
        )

    def _infer_document_title_from_recovered_blocks(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> str | None:
        for block in recovered_blocks:
            if block.page_start != 1 or block.page_end != 1 or block.role != "heading":
                continue
            normalized = _normalize_paper_title_candidate(block.text)
            if _looks_like_paper_title(normalized):
                return normalized
        return None

    def _repair_academic_first_page_abstract_continuations(
        self,
        chapters: list[ParsedChapter],
        pages: list[PdfPage],
        page_layout_assessments: dict[int, _PageLayoutAssessment],
        profile: PdfFileProfile,
    ) -> list[ParsedChapter]:
        if profile.recovery_lane != "academic_paper" or len(chapters) < 2:
            return chapters
        title_page = chapters[0]
        first_body_chapter = chapters[1]
        title_page_start = int(title_page.metadata.get("source_page_start", 0) or 0)
        first_body_start = int(first_body_chapter.metadata.get("source_page_start", 0) or 0)
        if title_page_start != 1 or first_body_start != 1:
            return chapters
        if not self._chapter_contains_abstract(title_page):
            return chapters
        title_page_assessment = page_layout_assessments.get(title_page_start, _PageLayoutAssessment())
        page_by_number = {page.page_number: page for page in pages}
        intro_heading_top = self._parsed_block_top(first_body_chapter.blocks[0]) if first_body_chapter.blocks else None

        carry_blocks: list[ParsedBlock] = []
        remaining_blocks: list[ParsedBlock] = []
        for index, block in enumerate(first_body_chapter.blocks):
            if index == 0 or block.block_type != BlockType.PARAGRAPH.value:
                remaining_blocks.append(block)
                continue
            block_page_start = int(block.metadata.get("source_page_start", first_body_start) or first_body_start)
            normalized_text = _normalize_text(block.text)
            block_top = self._parsed_block_top(block)
            block_is_above_intro_heading = (
                block_top is not None
                and intro_heading_top is not None
                and block_top + 1.0 < intro_heading_top
            )
            page = page_by_number.get(block_page_start)
            if (
                block_page_start == first_body_start
                and normalized_text[:1].islower()
            ) or (
                block_page_start == first_body_start
                and "academic_first_page_asymmetric" in title_page_assessment.reasons
                and block_is_above_intro_heading
                and page is not None
                and self._looks_like_first_page_abstract_carry_block(block, page)
            ):
                carry_blocks.append(block)
                continue
            remaining_blocks.append(block)

        if not carry_blocks:
            return chapters

        repaired_title_blocks = self._reordinal_parsed_blocks([*title_page.blocks, *carry_blocks])
        repaired_body_blocks = self._reordinal_parsed_blocks(remaining_blocks)
        repaired_title_page = replace(
            title_page,
            blocks=repaired_title_blocks,
            metadata={
                **title_page.metadata,
                "source_page_end": max(
                    int(title_page.metadata.get("source_page_end", title_page_start) or title_page_start),
                    max(
                        int(block.metadata.get("source_page_end", first_body_start) or first_body_start)
                        for block in carry_blocks
                    ),
                ),
            },
        )
        repaired_first_body = replace(first_body_chapter, blocks=repaired_body_blocks)
        return [repaired_title_page, repaired_first_body, *chapters[2:]]

    def _page_layout_assessments(
        self,
        pages: list[PdfPage],
        profile: PdfFileProfile,
        document_title: str | None,
    ) -> dict[int, _PageLayoutAssessment]:
        return {
            page.page_number: _assess_page_layout(page, profile=profile, document_title=document_title)
            for page in pages
        }

    def _parsed_block_top(self, block: ParsedBlock) -> float | None:
        source_bbox_json = block.metadata.get("source_bbox_json")
        if not isinstance(source_bbox_json, dict):
            return None
        regions = source_bbox_json.get("regions")
        if not isinstance(regions, list):
            return None
        tops = [
            float(region["bbox"][1])
            for region in regions
            if isinstance(region, dict)
            and isinstance(region.get("bbox"), list)
            and len(region["bbox"]) >= 4
        ]
        return min(tops) if tops else None

    def _looks_like_first_page_abstract_carry_block(self, block: ParsedBlock, page: PdfPage) -> bool:
        normalized = _normalize_text(block.text)
        if not normalized:
            return False
        if not (_looks_like_academic_body_continuation(normalized) or _looks_like_academic_prose_lead(normalized)):
            return False
        source_bbox_json = block.metadata.get("source_bbox_json")
        if not isinstance(source_bbox_json, dict):
            return False
        regions = source_bbox_json.get("regions")
        if not isinstance(regions, list) or not regions:
            return False
        left_edges = [
            float(region["bbox"][0])
            for region in regions
            if isinstance(region, dict)
            and isinstance(region.get("bbox"), list)
            and len(region["bbox"]) >= 4
        ]
        if not left_edges:
            return False
        return min(left_edges) >= page.width * 0.3 or normalized[:1].islower()

    def _chapter_contains_abstract(self, chapter: ParsedChapter) -> bool:
        for block in chapter.blocks:
            normalized = _normalize_text(block.text)
            if block.block_type == BlockType.HEADING.value and normalized.casefold() == "abstract":
                return True
            if block.block_type == BlockType.PARAGRAPH.value and normalized.casefold().startswith("abstract "):
                return True
        return False

    def _reordinal_parsed_blocks(self, blocks: list[ParsedBlock]) -> list[ParsedBlock]:
        return [replace(block, ordinal=index) for index, block in enumerate(blocks, start=1)]

    def _find_repeated_edge_text(self, pages: list[PdfPage]) -> set[tuple[str, str]]:
        counts: Counter[tuple[str, str]] = Counter()
        for page in pages:
            for block in page.blocks:
                zone = self._page_zone(block.bbox, page.height)
                if zone not in {"top", "bottom"}:
                    continue
                signature = _header_footer_signature(block.text)
                if signature:
                    counts[(zone, signature)] += 1
        return {key for key, count in counts.items() if count >= 2}

    def _recover_blocks(
        self,
        pages: list[PdfPage],
        repeated_edge_text: set[tuple[str, str]],
        page_contexts: dict[int, _PageRecoveryContext],
        outline_entries: list[PdfOutlineEntry],
        profile: PdfFileProfile,
    ) -> list[_RecoveredBlock]:
        outline_by_page: dict[int, list[str]] = defaultdict(list)
        for entry in outline_entries:
            outline_by_page[entry.page_number].append(_normalize_outline_title(entry.title))

        recovered: list[_RecoveredBlock] = []
        reading_order_index = 0
        for page in pages:
            font_sizes = [block.font_size_avg for block in page.blocks if block.font_size_avg > 0]
            page_font_median = median(font_sizes) if font_sizes else 12.0
            ordered_blocks = self._ordered_page_blocks(page, profile)
            page_context = page_contexts.get(page.page_number, _PageRecoveryContext(is_toc_page=False))

            for raw_block in ordered_blocks:
                role = self._classify_role(
                    page=page,
                    raw_block=raw_block,
                    page_font_median=page_font_median,
                    repeated_edge_text=repeated_edge_text,
                    outline_titles=outline_by_page.get(page.page_number, []),
                    page_context=page_context,
                )
                reading_order_index += 1
                metadata, flags = self._metadata_for_block(role, raw_block.text, page_context)
                recovered_block = _RecoveredBlock(
                    role=role,
                    block_type=self._block_type_for_role(role),
                    text=self._normalized_block_text(
                        raw_block.text,
                        role=role,
                        page_number=page.page_number,
                        page_context=page_context,
                    ),
                    page_start=page.page_number,
                    page_end=page.page_number,
                    bbox_regions=[
                        {
                            "page_number": page.page_number,
                            "bbox": _bbox_to_json(raw_block.bbox),
                        }
                    ],
                    reading_order_index=reading_order_index,
                    parse_confidence=self._parse_confidence_for_role(role, profile.layout_risk),
                    flags=flags,
                    metadata=metadata,
                    font_size_avg=raw_block.font_size_avg,
                    source_path=f"pdf://page/{page.page_number}",
                    anchor=f"p{page.page_number}-b{reading_order_index}",
                )

                merge_index = self._merge_target_index(recovered, recovered_block, pages)
                if merge_index is not None:
                    recovered[merge_index] = self._merge_blocks(recovered[merge_index], recovered_block)
                else:
                    recovered.append(recovered_block)

            for raw_image in self._ordered_page_image_blocks(page):
                reading_order_index += 1
                metadata, flags = self._metadata_for_block("image", "[Image]", page_context)
                metadata.update(
                    {
                        "image_type": raw_image.image_type,
                        "image_ext": raw_image.image_ext,
                        "image_width_px": raw_image.width_px,
                        "image_height_px": raw_image.height_px,
                    }
                )
                recovered.append(
                    _RecoveredBlock(
                        role="image",
                        block_type=BlockType.IMAGE,
                        text="[Image]",
                        page_start=page.page_number,
                        page_end=page.page_number,
                        bbox_regions=[
                            {
                                "page_number": page.page_number,
                                "bbox": _bbox_to_json(raw_image.bbox),
                            }
                        ],
                        reading_order_index=reading_order_index,
                        parse_confidence=self._parse_confidence_for_role("image", profile.layout_risk),
                        flags=flags,
                        metadata=metadata,
                        font_size_avg=0.0,
                        source_path=f"pdf://page/{page.page_number}",
                        anchor=f"p{page.page_number}-img{reading_order_index}",
                    )
                )

        return self._merge_footnote_continuations(recovered, pages)

    def _ordered_page_image_blocks(self, page: PdfPage) -> list[PdfImageBlock]:
        return sorted(page.image_blocks, key=lambda block: (round(block.bbox[1], 2), round(block.bbox[0], 2)))

    def _ordered_page_blocks(
        self,
        page: PdfPage,
        profile: PdfFileProfile,
    ) -> list[PdfTextBlock]:
        ordered_blocks = sorted(page.blocks, key=lambda block: (round(block.bbox[1], 2), round(block.bbox[0], 2)))
        if profile.recovery_lane != "academic_paper":
            return ordered_blocks
        if not _page_has_multi_column_signature(page):
            return ordered_blocks
        column_major_blocks = self._academic_column_major_blocks(page, ordered_blocks)
        return column_major_blocks or ordered_blocks

    def _academic_column_major_blocks(
        self,
        page: PdfPage,
        ordered_blocks: list[PdfTextBlock],
    ) -> list[PdfTextBlock] | None:
        substantive_blocks = [
            block
            for block in ordered_blocks
            if len(_normalize_text(block.text)) >= 24 and self._page_zone(block.bbox, page.height) != "bottom"
        ]
        if len(substantive_blocks) < 6:
            return None

        narrow_blocks = [block for block in substantive_blocks if (block.bbox[2] - block.bbox[0]) <= page.width * 0.62]
        column_candidate_blocks = [
            block
            for block in narrow_blocks
            if (block.bbox[2] - block.bbox[0]) <= page.width * _MULTI_COLUMN_BLOCK_WIDTH_RATIO
        ]
        left_blocks = [block for block in column_candidate_blocks if block.bbox[0] <= page.width * 0.3]
        right_blocks = [block for block in column_candidate_blocks if block.bbox[0] >= page.width * 0.45]
        if len(left_blocks) < 2:
            return None
        if len(right_blocks) < 2:
            tall_right_blocks = [
                block
                for block in narrow_blocks
                if block.bbox[0] >= page.width * 0.45
                and (block.bbox[3] - block.bbox[1]) >= page.height * 0.24
            ]
            if not tall_right_blocks:
                return None
            right_blocks = tall_right_blocks

        left_min_y = min(block.bbox[1] for block in left_blocks)
        right_min_y = min(block.bbox[1] for block in right_blocks)
        left_max_y = max(block.bbox[1] for block in left_blocks)
        right_max_y = max(block.bbox[1] for block in right_blocks)
        min_column_y = min(left_min_y, right_min_y)
        max_column_y = max(left_max_y, right_max_y)

        group_by_id: dict[int, int] = {}
        for block in left_blocks:
            group_by_id[id(block)] = 1
        for block in right_blocks:
            group_by_id[id(block)] = 2

        for block in ordered_blocks:
            if id(block) in group_by_id:
                continue
            block_width = block.bbox[2] - block.bbox[0]
            if block_width <= page.width * 0.62:
                if block.bbox[0] <= page.width * 0.3:
                    group_by_id[id(block)] = 1
                    continue
                if block.bbox[0] >= page.width * 0.45:
                    group_by_id[id(block)] = 2
                    continue
                continue
            if block.bbox[1] <= min_column_y + 2:
                group_by_id[id(block)] = 0
                continue
            if block.bbox[1] >= max_column_y - 2:
                group_by_id[id(block)] = 3
                continue
            return None

        return sorted(
            ordered_blocks,
            key=lambda block: (
                group_by_id.get(id(block), 0 if block.bbox[1] <= min_column_y + 2 else 3),
                round(block.bbox[1], 2),
                round(block.bbox[0], 2),
            ),
        )

    def _page_evidence(
        self,
        pages: list[PdfPage],
        page_contexts: dict[int, _PageRecoveryContext],
        page_layout_assessments: dict[int, _PageLayoutAssessment],
        recovered_blocks: list[_RecoveredBlock],
        outline_entries: list[PdfOutlineEntry],
        profile: PdfFileProfile,
    ) -> dict[str, Any]:
        role_counts_by_page: dict[int, Counter[str]] = defaultdict(Counter)
        flags_by_page: dict[int, set[str]] = defaultdict(set)
        matched_footnotes_by_page: Counter[int] = Counter()
        orphan_footnotes_by_page: Counter[int] = Counter()
        relocated_footnotes_by_page: Counter[int] = Counter()
        max_footnote_segment_count_by_page: dict[int, int] = {}
        block_span_counts_by_page: Counter[int] = Counter()

        for block in recovered_blocks:
            page_numbers = sorted({int(region["page_number"]) for region in block.bbox_regions})
            for page_number in page_numbers:
                role_counts_by_page[page_number][block.role] += 1
                block_span_counts_by_page[page_number] += 1
                flags_by_page[page_number].update(str(flag) for flag in block.flags)
            if block.role == "footnote":
                if block.metadata.get("footnote_anchor_matched"):
                    matched_footnotes_by_page[block.page_start] += 1
                else:
                    orphan_footnotes_by_page[block.page_start] += 1
                segment_count = int(block.metadata.get("footnote_segment_count", 1) or 1)
                if segment_count > 1 or "footnote_multisegment_repaired" in block.flags:
                    for page_number in page_numbers:
                        relocated_footnotes_by_page[page_number] += 1
                        max_footnote_segment_count_by_page[page_number] = max(
                            max_footnote_segment_count_by_page.get(page_number, 0),
                            segment_count,
                        )

        outline_payload = [
            {
                "level": entry.level,
                "title": entry.title,
                "page_number": entry.page_number,
            }
            for entry in sorted(
                outline_entries,
                key=lambda item: (item.page_number, item.level, item.title.casefold()),
            )
        ]
        suspicious_pages = set(profile.suspicious_page_numbers)
        pages_payload: list[dict[str, Any]] = []
        for page in sorted(pages, key=lambda item: item.page_number):
            context = page_contexts.get(page.page_number, _PageRecoveryContext(is_toc_page=False))
            layout_assessment = page_layout_assessments.get(page.page_number, _PageLayoutAssessment())
            layout_signals = list(layout_assessment.reasons)
            toc_entries = [
                {
                    "title": entry.title,
                    "page_number": entry.page_number,
                }
                for entry in sorted(
                    context.toc_entries_by_text.values(),
                    key=lambda item: (
                        item.page_number if item.page_number is not None else 10**9,
                        item.title.casefold(),
                    ),
                )
            ]
            appendix_nested_subheadings: list[dict[str, Any]] = []
            if context.page_family == "appendix":
                seen_nested_titles: set[str] = set()
                for block in page.blocks[:6]:
                    candidate = _appendix_section_subheading_candidate(block.text)
                    if candidate is None or int(candidate["depth"]) < 2:
                        continue
                    full_title = str(candidate["full_title"])
                    if full_title in seen_nested_titles:
                        continue
                    seen_nested_titles.add(full_title)
                    appendix_nested_subheadings.append(candidate)
            pages_payload.append(
                {
                    "page_number": page.page_number,
                    "raw_block_count": len(page.blocks),
                    "raw_image_block_count": len(page.image_blocks),
                    "recovered_block_count": int(block_span_counts_by_page.get(page.page_number, 0)),
                    "page_family": context.page_family,
                    "page_family_source": context.family_source,
                    "page_family_heading": context.family_heading,
                    "content_family": context.content_family,
                    "backmatter_cue": context.backmatter_cue,
                    "backmatter_cue_source": context.backmatter_cue_source,
                    "is_toc_page": context.is_toc_page,
                    "has_strong_heading": context.has_strong_heading,
                    "layout_signals": layout_signals,
                    "page_layout_risk": layout_assessment.risk,
                    "page_layout_reasons": layout_signals,
                    "layout_suspect": page.page_number in suspicious_pages,
                    "role_counts": _sorted_counter(role_counts_by_page.get(page.page_number, Counter())),
                    "recovery_flags": sorted(flags_by_page.get(page.page_number, set())),
                    "toc_entries": toc_entries,
                    "appendix_nested_subheadings": appendix_nested_subheadings,
                    "matched_footnote_count": int(matched_footnotes_by_page.get(page.page_number, 0)),
                    "orphan_footnote_count": int(orphan_footnotes_by_page.get(page.page_number, 0)),
                    "relocated_footnote_count": int(relocated_footnotes_by_page.get(page.page_number, 0)),
                    "max_footnote_segment_count": int(max_footnote_segment_count_by_page.get(page.page_number, 0)),
                }
            )
        return {
            "schema_version": 1,
            "extractor_kind": profile.extractor_kind,
            "page_count": len(pages_payload),
            "pdf_pages": pages_payload,
            "pdf_outline_entries": outline_payload,
        }

    def _page_contexts(self, pages: list[PdfPage]) -> dict[int, _PageRecoveryContext]:
        contexts = {page.page_number: self._page_context(page) for page in pages}
        page_numbers = [page.page_number for page in sorted(pages, key=lambda item: item.page_number)]
        later_heading_after: dict[int, bool] = {}
        seen_future_heading = False
        for page_number in reversed(page_numbers):
            later_heading_after[page_number] = seen_future_heading
            context = contexts[page_number]
            if context.has_strong_heading and not context.is_toc_page:
                seen_future_heading = True

        previous_family = "body"
        for index, page in enumerate(sorted(pages, key=lambda item: item.page_number)):
            context = contexts[page.page_number]
            updated_context = context
            if context.page_family == "body":
                if context.content_family == "references":
                    updated_context = replace(
                        context,
                        page_family=context.content_family,
                        family_source="content_signature",
                    )
                elif context.content_family == "index" and self._should_promote_index_page_family(
                    page_numbers,
                    index,
                    contexts,
                    later_heading_after,
                ):
                    updated_context = replace(
                        context,
                        page_family=context.content_family,
                        family_source="content_signature",
                    )
                elif previous_family in {"index", "appendix"} and self._should_promote_backmatter_page_family(
                    page_numbers,
                    index,
                    previous_family,
                    contexts,
                    later_heading_after,
                ):
                    updated_context = replace(
                        context,
                        page_family="backmatter",
                        family_source=(
                            "backmatter_cue" if previous_family == "appendix" else "tail_body_after_special"
                        ),
                        family_heading=_section_family_display_title("backmatter"),
                    )
                elif previous_family == "backmatter" and not context.is_toc_page:
                    updated_context = replace(
                        context,
                        page_family="backmatter",
                        family_source="continuation",
                        family_heading=_section_family_display_title("backmatter"),
                    )
                elif previous_family == "appendix" and not context.has_strong_heading and not context.is_toc_page:
                    updated_context = replace(context, page_family="appendix", family_source="continuation")
                elif (
                    previous_family in {"references", "index"}
                    and context.content_family == previous_family
                    and not context.has_strong_heading
                ):
                    updated_context = replace(context, page_family=previous_family, family_source="continuation")
            contexts[page.page_number] = updated_context
            previous_family = updated_context.page_family if updated_context.page_family != "toc" else previous_family
        return contexts

    def _should_promote_backmatter_page_family(
        self,
        page_numbers: list[int],
        current_index: int,
        previous_family: str,
        contexts: dict[int, _PageRecoveryContext],
        later_heading_after: dict[int, bool],
    ) -> bool:
        page_number = page_numbers[current_index]
        remaining_page_count = len(page_numbers) - current_index
        if later_heading_after.get(page_number, False):
            return False
        if previous_family == "index":
            return remaining_page_count <= 8
        if previous_family != "appendix":
            return False
        context = contexts.get(page_number)
        return bool(context is not None and context.backmatter_cue and remaining_page_count <= 6)

    def _should_promote_index_page_family(
        self,
        page_numbers: list[int],
        current_index: int,
        contexts: dict[int, _PageRecoveryContext],
        later_heading_after: dict[int, bool],
    ) -> bool:
        page_number = page_numbers[current_index]
        previous_context = contexts.get(page_numbers[current_index - 1]) if current_index > 0 else None
        next_context = contexts.get(page_numbers[current_index + 1]) if current_index + 1 < len(page_numbers) else None
        neighbor_support = any(
            context is not None and (context.page_family == "index" or context.content_family == "index")
            for context in (previous_context, next_context)
        )
        if neighbor_support:
            return True
        remaining_page_count = len(page_numbers) - current_index - 1
        return remaining_page_count <= 1 and not later_heading_after.get(page_number, False)

    def _page_context(self, page: PdfPage) -> _PageRecoveryContext:
        heading_present = False
        toc_entries_by_text: dict[str, _TocEntryCandidate] = {}
        font_sizes = [block.font_size_avg for block in page.blocks if block.font_size_avg > 0]
        page_font_median = median(font_sizes) if font_sizes else 12.0
        ordered_blocks = sorted(page.blocks, key=lambda block: (round(block.bbox[1], 2), round(block.bbox[0], 2)))
        family_heading: str | None = None
        page_family = "body"
        family_source = "body"
        content_family: str | None = None
        backmatter_cue: str | None = None
        backmatter_cue_source: str | None = None
        has_strong_heading = False
        content_texts: list[str] = []
        for block in ordered_blocks:
            text = _normalize_text(block.text)
            if not text:
                continue
            zone = self._page_zone(block.bbox, page.height)
            if zone != "bottom" and len(text) <= 120 and (
                _HEADING_PATTERN.match(text) or block.font_size_avg >= page_font_median * 1.25
            ):
                has_strong_heading = True
            if _looks_like_toc_heading(text):
                heading_present = True
            if family_heading is None and len(text) <= 120 and block.font_size_avg >= page_font_median * 1.15:
                family = _page_family_for_heading(text, page.page_number)
                if family is not None:
                    family_heading = text
                    page_family = family
                    family_source = "heading"
            title, page_number = _split_toc_entry(text)
            if page_number is None:
                content_texts.append(text)
            else:
                toc_entries_by_text[text.casefold()] = _TocEntryCandidate(title=title, page_number=page_number)
        if page_family == "body" and family_heading is None:
            first_content_text = next(
                (
                    text
                    for text in content_texts
                    if text and not _is_page_number_text(text)
                ),
                None,
            )
            if first_content_text is not None:
                inline_heading = _inline_page_family_heading(first_content_text, page.page_number)
                if inline_heading is not None:
                    inline_family, inline_title = inline_heading
                    page_family = inline_family
                    family_source = "inline_heading"
                    family_heading = inline_title
        is_toc_page = (heading_present and bool(toc_entries_by_text)) or len(toc_entries_by_text) >= 3
        reference_like_count = sum(1 for text in content_texts if _looks_like_reference_entry(text))
        index_like_count = sum(1 for text in content_texts if _looks_like_index_entry(text))
        index_like_ratio = (index_like_count / len(content_texts)) if content_texts else 0.0
        citation_marker_count = sum(len(_REFERENCE_CITATION_PATTERN.findall(text)) for text in content_texts)
        year_count = sum(len(_REFERENCE_YEAR_PATTERN.findall(text)) for text in content_texts)
        if reference_like_count >= 2 or (
            reference_like_count >= 1 and citation_marker_count >= 2 and year_count >= 2
        ):
            content_family = "references"
        elif index_like_count >= 3 and index_like_ratio >= 0.6:
            content_family = "index"
        detected_backmatter_cue = _detect_backmatter_cue(content_texts)
        if detected_backmatter_cue is not None:
            backmatter_cue, backmatter_cue_source = detected_backmatter_cue
        if page_family == "body" and family_heading is None and content_texts:
            appendix_title = _infer_appendix_intro_title(content_texts[0])
            if appendix_title is not None or _contains_appendix_intro_cue(content_texts[0]):
                page_family = "appendix"
                family_source = "appendix_intro"
                family_heading = appendix_title
        if is_toc_page:
            page_family = "toc"
            family_source = "toc"
            if family_heading is None and heading_present:
                family_heading = next(
                    (_normalize_text(block.text) for block in ordered_blocks if _looks_like_toc_heading(block.text)),
                    family_heading,
                )
        return _PageRecoveryContext(
            is_toc_page=is_toc_page,
            page_family=page_family,
            family_source=family_source,
            family_heading=family_heading,
            content_family=content_family,
            backmatter_cue=backmatter_cue,
            backmatter_cue_source=backmatter_cue_source,
            has_strong_heading=has_strong_heading,
            toc_entries_by_text=toc_entries_by_text,
        )

    def _page_zone(self, bbox: tuple[float, float, float, float], page_height: float) -> str:
        top = bbox[1]
        bottom = bbox[3]
        if bottom <= page_height * 0.12:
            return "top"
        if top >= page_height * 0.88:
            return "bottom"
        return "middle"

    def _classify_role(
        self,
        *,
        page: PdfPage,
        raw_block: PdfTextBlock,
        page_font_median: float,
        repeated_edge_text: set[tuple[str, str]],
        outline_titles: list[str],
        page_context: _PageRecoveryContext,
    ) -> str:
        text = _normalize_text(raw_block.text)
        zone = self._page_zone(raw_block.bbox, page.height)
        signature = _header_footer_signature(text)
        if zone == "top" and ((zone, signature) in repeated_edge_text):
            return "header"
        if zone == "bottom" and (_is_page_number_text(text) or (zone, signature) in repeated_edge_text):
            return "footer"
        if zone == "top" and raw_block.font_size_avg and raw_block.font_size_avg < page_font_median * 0.95:
            if _FOOTNOTE_PATTERN.match(text):
                return "footnote"
        if page_context.is_toc_page and (
            _looks_like_toc_heading(text) or text.casefold() in page_context.toc_entries_by_text
        ):
            return "toc_entry"
        if zone == "bottom" and raw_block.font_size_avg and raw_block.font_size_avg < page_font_median * 0.95:
            if _FOOTNOTE_PATTERN.match(text):
                return "footnote"
        if _looks_like_caption_text(text):
            return "caption"
        if _normalize_outline_title(text) in outline_titles:
            return "heading"
        if _HEADING_PATTERN.match(text):
            return "heading"
        if (
            raw_block.font_size_max >= page_font_median * 1.25
            and len(text) <= 120
            and _looks_like_visual_heading(text, raw_block.line_count)
        ):
            if zone != "bottom":
                return "heading"
        if _looks_like_equation(text, raw_block.line_count, raw_block.bbox, page.width):
            return "equation"
        if _looks_like_code(text, raw_block.line_count):
            return "code_like"
        if _looks_like_table(raw_block.line_count, raw_block.line_texts):
            return "table_like"
        return "body"

    def _block_type_for_role(self, role: str) -> BlockType:
        if role == "heading":
            return BlockType.HEADING
        if role == "footnote":
            return BlockType.FOOTNOTE
        if role == "caption":
            return BlockType.CAPTION
        if role == "equation":
            return BlockType.EQUATION
        if role == "code_like":
            return BlockType.CODE
        if role == "table_like":
            return BlockType.TABLE
        if role == "image":
            return BlockType.IMAGE
        return BlockType.PARAGRAPH

    def _metadata_for_block(
        self,
        role: str,
        raw_text: str,
        page_context: _PageRecoveryContext,
    ) -> tuple[dict[str, Any], list[str]]:
        metadata: dict[str, Any] = {
            "pdf_page_family": page_context.page_family,
            "pdf_page_family_source": page_context.family_source,
        }
        flags: list[str] = []
        text = _normalize_text(raw_text)
        if page_context.family_heading:
            metadata["pdf_page_family_heading"] = page_context.family_heading
        if page_context.content_family:
            metadata["pdf_page_content_family"] = page_context.content_family
        if page_context.backmatter_cue:
            metadata["pdf_page_backmatter_cue"] = page_context.backmatter_cue
        if page_context.backmatter_cue_source:
            metadata["pdf_page_backmatter_cue_source"] = page_context.backmatter_cue_source
        if role == "footnote":
            metadata["footnote_segment_count"] = 1
            metadata["footnote_segment_roles"] = ["footnote"]
        if page_context.page_family not in {"body", "toc"}:
            flags.append(f"page_family_{page_context.page_family}")
        if page_context.family_source not in {"body", "heading", "toc"}:
            flags.append(f"page_family_source_{page_context.family_source}")
        if role != "toc_entry":
            return metadata, flags
        toc_entry = page_context.toc_entries_by_text.get(text.casefold())
        if toc_entry is not None:
            metadata["toc_title"] = toc_entry.title
            metadata["toc_page_number"] = toc_entry.page_number
            flags.append("toc_entry_detected")
            return metadata, flags
        metadata["toc_heading"] = True
        flags.append("toc_page_detected")
        return metadata, flags

    def _normalized_block_text(
        self,
        raw_text: str,
        *,
        role: str,
        page_number: int,
        page_context: _PageRecoveryContext,
    ) -> str:
        normalized_text = _normalize_multiline_text(raw_text)
        if (
            role == "heading"
            and page_number == 1
            and page_context.page_family == "body"
        ):
            cleaned_title = _normalize_paper_title_candidate(normalized_text)
            if _looks_like_paper_title(cleaned_title):
                return cleaned_title
        return normalized_text

    def _parse_confidence_for_role(self, role: str, layout_risk: str) -> float:
        base = {"low": 0.96, "medium": 0.82, "high": 0.65}.get(layout_risk, 0.8)
        if role in {"header", "footer", "toc_entry"}:
            return min(0.99, base + 0.02)
        if role == "image":
            return min(0.99, base + 0.01)
        if role in {"code_like", "table_like"}:
            return max(0.7, base - 0.05)
        return base

    def _should_merge(
        self,
        previous: _RecoveredBlock,
        current: _RecoveredBlock,
        pages: list[PdfPage],
    ) -> bool:
        if previous.role != "body" or current.role != "body":
            return False
        if previous.block_type != BlockType.PARAGRAPH or current.block_type != BlockType.PARAGRAPH:
            return False
        if current.page_start - previous.page_end > 1:
            return False
        if current.page_start == previous.page_end:
            prev_bottom = previous.bbox_regions[-1]["bbox"][3]
            curr_top = current.bbox_regions[0]["bbox"][1]
            gap = curr_top - prev_bottom
            if gap <= max(previous.font_size_avg * 1.8, 18.0):
                return previous.text.rstrip().endswith("-") or not previous.text.rstrip().endswith(
                    _TERMINAL_PUNCTUATION
                )
            return False

        page_lookup = {page.page_number: page for page in pages}
        prev_page = page_lookup.get(previous.page_end)
        curr_page = page_lookup.get(current.page_start)
        if prev_page is None or curr_page is None:
            return False
        prev_bottom = previous.bbox_regions[-1]["bbox"][3]
        curr_top = current.bbox_regions[0]["bbox"][1]
        if prev_bottom < prev_page.height * 0.72:
            return False
        if curr_top > curr_page.height * 0.28:
            return False
        if previous.text.rstrip().endswith("-") and current.text[:1].islower():
            return True
        if previous.text.rstrip().endswith(_TERMINAL_PUNCTUATION):
            return False
        return current.text[:1].islower()

    def _merge_target_index(
        self,
        recovered: list[_RecoveredBlock],
        current: _RecoveredBlock,
        pages: list[PdfPage],
    ) -> int | None:
        if not recovered:
            return None
        skipped_roles = {"header", "footer"}
        index = len(recovered) - 1
        while index >= 0 and recovered[index].role in skipped_roles:
            index -= 1
        if index < 0:
            return None
        if any(block.role not in skipped_roles for block in recovered[index + 1 :]):
            return None
        if self._should_merge(recovered[index], current, pages):
            return index
        return None

    def _merge_blocks(self, previous: _RecoveredBlock, current: _RecoveredBlock) -> _RecoveredBlock:
        flags = [*previous.flags]
        previous_text = previous.text.rstrip()
        current_text = current.text.lstrip()

        if previous_text.endswith("-") and current_text[:1].islower():
            merged_text = previous_text[:-1] + current_text
            flags.append("dehyphenated")
        else:
            separator = "" if previous_text.endswith(" ") else " "
            merged_text = previous_text + separator + current_text

        if current.page_start > previous.page_end:
            flags.append("cross_page_repaired")

        merged_flags = list(dict.fromkeys(flags + current.flags))
        return _RecoveredBlock(
            role=previous.role,
            block_type=previous.block_type,
            text=merged_text,
            page_start=previous.page_start,
            page_end=current.page_end,
            bbox_regions=[*previous.bbox_regions, *current.bbox_regions],
            reading_order_index=previous.reading_order_index,
            parse_confidence=round(min(previous.parse_confidence, current.parse_confidence), 3),
            flags=merged_flags,
            metadata={**previous.metadata, **current.metadata},
            font_size_avg=_safe_mean([previous.font_size_avg, current.font_size_avg]),
            source_path=previous.source_path,
            anchor=previous.anchor,
        )

    def _merge_adjacent_heading_continuations(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> list[_RecoveredBlock]:
        merged: list[_RecoveredBlock] = []
        for current in recovered_blocks:
            if merged and self._should_merge_heading_continuation(merged[-1], current):
                merged[-1] = self._merge_heading_blocks(merged[-1], current)
                continue
            merged.append(current)
        return merged

    def _should_merge_heading_continuation(
        self,
        previous: _RecoveredBlock,
        current: _RecoveredBlock,
    ) -> bool:
        if previous.role != "heading" or current.role != "heading":
            return False
        if previous.block_type != BlockType.HEADING or current.block_type != BlockType.HEADING:
            return False
        if previous.page_start != current.page_start or previous.page_end != current.page_end:
            return False
        if previous.page_start != previous.page_end:
            return False
        if previous.source_path != current.source_path:
            return False
        previous_family = str(previous.metadata.get("pdf_page_family") or "body")
        current_family = str(current.metadata.get("pdf_page_family") or "body")
        if previous_family != current_family or previous_family != "body":
            return False
        if current.reading_order_index - previous.reading_order_index != 1:
            return False

        current_text = _normalize_text(current.text)
        if not _looks_like_heading_continuation_fragment(current_text):
            return False

        previous_bbox = previous.bbox_regions[-1]["bbox"]
        current_bbox = current.bbox_regions[0]["bbox"]
        gap = float(current_bbox[1]) - float(previous_bbox[3])
        if gap > max(min(previous.font_size_avg, current.font_size_avg) * 1.8, 18.0):
            return False
        if abs(float(previous_bbox[0]) - float(current_bbox[0])) > 48.0:
            return False

        previous_size = max(previous.font_size_avg, 1.0)
        current_size = max(current.font_size_avg, 1.0)
        ratio = current_size / previous_size
        if ratio < 0.8 or ratio > 1.25:
            return False
        return True

    def _merge_heading_blocks(self, previous: _RecoveredBlock, current: _RecoveredBlock) -> _RecoveredBlock:
        previous_text = previous.text.rstrip()
        current_text = current.text.lstrip()
        separator = "" if previous_text.endswith("-") else " "
        merged_text = previous_text + separator + current_text
        merged_flags = list(dict.fromkeys([*previous.flags, *current.flags, "multiline_heading_merged"]))
        merged_metadata = {**previous.metadata, **current.metadata}
        return _RecoveredBlock(
            role="heading",
            block_type=BlockType.HEADING,
            text=merged_text,
            page_start=previous.page_start,
            page_end=current.page_end,
            bbox_regions=[*previous.bbox_regions, *current.bbox_regions],
            reading_order_index=previous.reading_order_index,
            parse_confidence=round(min(previous.parse_confidence, current.parse_confidence), 3),
            flags=merged_flags,
            metadata=merged_metadata,
            font_size_avg=_safe_mean([previous.font_size_avg, current.font_size_avg]),
            source_path=previous.source_path,
            anchor=previous.anchor,
        )

    def _repair_prose_artifact_continuations(
        self,
        recovered_blocks: list[_RecoveredBlock],
        pages: list[PdfPage],
    ) -> list[_RecoveredBlock]:
        repaired: list[_RecoveredBlock] = []
        for current in recovered_blocks:
            if repaired and self._should_repair_prose_artifact_continuation(repaired[-1], current, pages):
                promoted_current = replace(
                    current,
                    role="body",
                    block_type=BlockType.PARAGRAPH,
                    metadata={
                        **current.metadata,
                        "pdf_block_role": "body",
                        "pdf_prose_artifact_repaired_from_role": current.role,
                    },
                )
                merged_block = self._merge_blocks(repaired[-1], promoted_current)
                merged_block.flags = list(
                    dict.fromkeys([*merged_block.flags, "prose_artifact_continuation_repaired"])
                )
                repaired[-1] = merged_block
                continue
            repaired.append(current)
        return repaired

    def _split_mixed_code_prose_blocks(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> list[_RecoveredBlock]:
        repaired: list[_RecoveredBlock] = []
        for block in recovered_blocks:
            repaired.extend(self._split_mixed_code_prose_block(block))
        return repaired

    def _split_mixed_code_prose_block(
        self,
        block: _RecoveredBlock,
    ) -> list[_RecoveredBlock]:
        if block.role not in {"body", "code_like"} or block.block_type not in {BlockType.PARAGRAPH, BlockType.CODE}:
            return [block]
        raw_lines = _expanded_code_candidate_lines(block.text)
        if len(raw_lines) < 3:
            return [block]

        code_prefix_length = 0
        for line in raw_lines:
            if _looks_like_embedded_code_line(line) or _looks_like_code_docstring_line(line):
                code_prefix_length += 1
                continue
            break
        if code_prefix_length < 2 or code_prefix_length >= len(raw_lines):
            return [block]

        code_lines = raw_lines[:code_prefix_length]
        prose_lines = raw_lines[code_prefix_length:]
        if not _looks_like_code("\n".join(code_lines), len(code_lines)):
            return [block]
        if not prose_lines or not _looks_like_sentence_prose_line(prose_lines[0]):
            return [block]

        base_metadata = dict(block.metadata)
        code_metadata = {
            **base_metadata,
            "pdf_block_role": "code_like",
            "pdf_mixed_code_prose_split": "leading_code_prefix",
        }
        prose_metadata = {
            **base_metadata,
            "pdf_block_role": "body",
            "pdf_mixed_code_prose_split": "trailing_prose_suffix",
        }
        code_flags = list(dict.fromkeys([*block.flags, "mixed_code_prose_split", "leading_code_prefix"]))
        prose_flags = list(dict.fromkeys([*block.flags, "mixed_code_prose_split", "trailing_prose_suffix"]))
        code_block = replace(
            block,
            role="code_like",
            block_type=BlockType.CODE,
            text="\n".join(code_lines),
            metadata=code_metadata,
            flags=code_flags,
        )
        prose_block = replace(
            block,
            role="body",
            block_type=BlockType.PARAGRAPH,
            text="\n".join(prose_lines),
            metadata=prose_metadata,
            flags=prose_flags,
            reading_order_index=block.reading_order_index + 1,
        )
        return [code_block, prose_block]

    def _promote_late_code_like_bodies(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> list[_RecoveredBlock]:
        promoted: list[_RecoveredBlock] = []
        for index, block in enumerate(recovered_blocks):
            if not self._should_promote_late_code_like_body(recovered_blocks, index):
                promoted.append(block)
                continue
            promoted_block = replace(
                block,
                role="code_like",
                block_type=BlockType.CODE,
                metadata={
                    **block.metadata,
                    "pdf_block_role": "code_like",
                    "pdf_late_artifact_promotion": "code_like",
                },
            )
            promoted_block.flags = list(dict.fromkeys([*promoted_block.flags, "late_code_like_promoted"]))
            promoted.append(promoted_block)
        return promoted

    def _should_promote_late_code_like_body(
        self,
        recovered_blocks: list[_RecoveredBlock],
        index: int,
    ) -> bool:
        block = recovered_blocks[index]
        if block.role != "body" or block.block_type != BlockType.PARAGRAPH:
            return False
        if block.page_end < block.page_start or block.page_end - block.page_start > 1:
            return False
        if str(block.metadata.get("pdf_page_family") or "body") != "body":
            return False

        normalized_lines = _expanded_code_candidate_lines(block.text)
        if not normalized_lines:
            return False
        if _looks_like_code("\n".join(normalized_lines), max(2, len(normalized_lines))):
            return True
        if not _looks_like_code_docstring_text(block.text):
            return False

        for offset in range(1, 4):
            for candidate_index in (index - offset, index + offset):
                if candidate_index < 0 or candidate_index >= len(recovered_blocks):
                    continue
                candidate = recovered_blocks[candidate_index]
                if candidate.page_end < block.page_start - 1 or candidate.page_start > block.page_end + 1:
                    continue
                if candidate.role == "code_like" and candidate.block_type == BlockType.CODE:
                    return True
                candidate_lines = _expanded_code_candidate_lines(candidate.text)
                if candidate_lines and _looks_like_code("\n".join(candidate_lines), max(2, len(candidate_lines))):
                    return True
        return False

    def _promote_late_table_like_bodies(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> list[_RecoveredBlock]:
        promoted: list[_RecoveredBlock] = []
        for index, block in enumerate(recovered_blocks):
            if not self._should_promote_late_table_like_body(recovered_blocks, index):
                promoted.append(block)
                continue
            promoted_block = replace(
                block,
                role="table_like",
                block_type=BlockType.TABLE,
                metadata={
                    **block.metadata,
                    "pdf_block_role": "table_like",
                    "pdf_late_artifact_promotion": "table_like",
                },
            )
            promoted_block.flags = list(dict.fromkeys([*promoted_block.flags, "late_table_like_promoted"]))
            promoted.append(promoted_block)
        return promoted

    def _should_promote_late_table_like_body(
        self,
        recovered_blocks: list[_RecoveredBlock],
        index: int,
    ) -> bool:
        block = recovered_blocks[index]
        if block.role != "body" or block.block_type != BlockType.PARAGRAPH:
            return False
        if block.page_start != block.page_end:
            return False
        if str(block.metadata.get("pdf_page_family") or "body") != "body":
            return False
        lines = [line for line in block.text.splitlines() if _normalize_text(line)]
        if not _looks_like_table(len(lines), lines):
            return False

        page_number = block.page_start
        for offset in range(1, 4):
            for candidate_index in (index - offset, index + offset):
                if candidate_index < 0 or candidate_index >= len(recovered_blocks):
                    continue
                candidate = recovered_blocks[candidate_index]
                if candidate.page_start != page_number or candidate.page_end != page_number:
                    continue
                if candidate.role == "caption" and _caption_matches_artifact_role(candidate.text, "table"):
                    return True
                if candidate.role == "table_like" and candidate.block_type == BlockType.TABLE:
                    return True
        return False

    def _merge_adjacent_table_fragments(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> list[_RecoveredBlock]:
        merged: list[_RecoveredBlock] = []
        for current in recovered_blocks:
            if merged and self._should_merge_table_fragments(merged[-1], current):
                merged_block = self._merge_blocks(merged[-1], current)
                merged_block.flags = list(dict.fromkeys([*merged_block.flags, "table_fragments_merged"]))
                merged[-1] = merged_block
                continue
            merged.append(current)
        return merged

    def _should_merge_table_fragments(
        self,
        previous: _RecoveredBlock,
        current: _RecoveredBlock,
    ) -> bool:
        if previous.role != "table_like" or current.role != "table_like":
            return False
        if previous.block_type != BlockType.TABLE or current.block_type != BlockType.TABLE:
            return False
        if previous.page_start != current.page_start or previous.page_end != current.page_end:
            return False
        if previous.page_start != previous.page_end:
            return False
        if previous.source_path != current.source_path:
            return False
        if current.reading_order_index - previous.reading_order_index > 2:
            return False

        previous_bbox = previous.bbox_regions[-1]["bbox"]
        current_bbox = current.bbox_regions[0]["bbox"]
        gap = float(current_bbox[1]) - float(previous_bbox[3])
        if gap > 54.0:
            return False

        overlap_ratio = self._horizontal_overlap_ratio(previous_bbox, current_bbox)
        previous_center = (float(previous_bbox[0]) + float(previous_bbox[2])) / 2.0
        current_center = (float(current_bbox[0]) + float(current_bbox[2])) / 2.0
        max_width = max(float(previous_bbox[2]) - float(previous_bbox[0]), float(current_bbox[2]) - float(current_bbox[0]), 1.0)
        if overlap_ratio < 0.12 and abs(previous_center - current_center) > max_width * 0.85:
            return False
        return True

    def _should_repair_prose_artifact_continuation(
        self,
        previous: _RecoveredBlock,
        current: _RecoveredBlock,
        pages: list[PdfPage],
    ) -> bool:
        if previous.role != "body" or previous.block_type != BlockType.PARAGRAPH:
            return False
        if current.role not in {"code_like", "table_like"} or current.block_type not in {BlockType.CODE, BlockType.TABLE}:
            return False
        if current.page_start - previous.page_end > 1:
            return False
        if str(previous.metadata.get("pdf_page_family") or "body") != "body":
            return False
        if str(current.metadata.get("pdf_page_family") or "body") != "body":
            return False
        if previous.reading_order_index >= current.reading_order_index:
            return False
        if previous.text.rstrip().endswith(_TERMINAL_PUNCTUATION):
            return False
        if not _looks_like_prose_continuation_fragment(current.text):
            return False

        page_lookup = {page.page_number: page for page in pages}
        if current.page_start == previous.page_end:
            previous_bbox = previous.bbox_regions[-1]["bbox"]
            current_bbox = current.bbox_regions[0]["bbox"]
            gap = float(current_bbox[1]) - float(previous_bbox[3])
            x_delta = abs(float(previous_bbox[0]) - float(current_bbox[0]))
            if gap > max(min(previous.font_size_avg, current.font_size_avg) * 2.4, 30.0):
                return False
            return x_delta <= 72.0

        previous_page = page_lookup.get(previous.page_end)
        current_page = page_lookup.get(current.page_start)
        if previous_page is None or current_page is None:
            return False
        previous_bbox = previous.bbox_regions[-1]["bbox"]
        current_bbox = current.bbox_regions[0]["bbox"]
        prev_bottom = float(previous_bbox[3])
        curr_top = float(current_bbox[1])
        if prev_bottom < previous_page.height * 0.7:
            return False
        if curr_top > current_page.height * 0.32:
            return False
        return abs(float(previous_bbox[0]) - float(current_bbox[0])) <= 84.0

    def _merge_footnote_continuations(
        self,
        recovered_blocks: list[_RecoveredBlock],
        pages: list[PdfPage],
    ) -> list[_RecoveredBlock]:
        merged: list[_RecoveredBlock] = []
        for current in recovered_blocks:
            merge_index = self._footnote_continuation_target_index(merged, current, pages)
            if merge_index is not None:
                previous_block = merged[merge_index]
                merged_block = self._merge_blocks(previous_block, current)
                relocation_mode = self._footnote_paragraph_relocation_mode(previous_block, current, pages)
                merged_block.flags = list(
                    dict.fromkeys(
                        [
                            *merged_block.flags,
                            "footnote_continuation_repaired",
                            *(
                                ["footnote_multisegment_repaired"]
                                if relocation_mode is not None
                                else []
                            ),
                        ]
                    )
                )
                merged_block.metadata["footnote_segment_count"] = int(
                    previous_block.metadata.get("footnote_segment_count", 1)
                ) + int(current.metadata.get("footnote_segment_count", 1))
                previous_roles = [
                    str(role)
                    for role in list(previous_block.metadata.get("footnote_segment_roles") or [previous_block.role])
                    if isinstance(role, str)
                ]
                current_roles = [
                    str(role)
                    for role in list(current.metadata.get("footnote_segment_roles") or [current.role])
                    if isinstance(role, str)
                ]
                merged_block.metadata["footnote_segment_roles"] = [*previous_roles, *current_roles]
                if relocation_mode is not None:
                    previous_modes = [
                        str(mode)
                        for mode in list(previous_block.metadata.get("footnote_relocation_modes") or [])
                        if isinstance(mode, str)
                    ]
                    merged_block.metadata["footnote_relocation_modes"] = list(
                        dict.fromkeys([*previous_modes, relocation_mode])
                    )
                merged[merge_index] = merged_block
                continue
            merged.append(current)
        return merged

    def _recover_academic_section_blocks(
        self,
        recovered_blocks: list[_RecoveredBlock],
        profile: PdfFileProfile,
    ) -> list[_RecoveredBlock]:
        if profile.recovery_lane != "academic_paper":
            return recovered_blocks

        split_blocks: list[_RecoveredBlock] = []
        reading_order_index = 0
        for block in recovered_blocks:
            segments = self._split_academic_section_segments(block)
            for segment in segments:
                reading_order_index += 1
                segment.reading_order_index = reading_order_index
                split_blocks.append(segment)
        return split_blocks

    def _recover_embedded_page_heading_blocks(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> list[_RecoveredBlock]:
        first_substantive_anchor_by_page: dict[int, str] = {}
        has_heading_by_page: dict[int, bool] = defaultdict(bool)
        for block in recovered_blocks:
            if block.role == "heading":
                has_heading_by_page[block.page_start] = True
            if (
                block.role not in {"header", "footer", "toc_entry", "footnote"}
                and block.page_start not in first_substantive_anchor_by_page
            ):
                first_substantive_anchor_by_page[block.page_start] = block.anchor

        split_blocks: list[_RecoveredBlock] = []
        reading_order_index = 0
        for block in recovered_blocks:
            segments = self._split_embedded_page_heading_segments(
                block,
                is_first_substantive_page_block=(
                    first_substantive_anchor_by_page.get(block.page_start) == block.anchor
                ),
                page_has_heading=has_heading_by_page.get(block.page_start, False),
            )
            for segment in segments:
                reading_order_index += 1
                segment.reading_order_index = reading_order_index
                split_blocks.append(segment)
        return split_blocks

    def _recover_document_title_heading_blocks(
        self,
        recovered_blocks: list[_RecoveredBlock],
        document_title: str | None,
    ) -> list[_RecoveredBlock]:
        normalized_title = _normalize_intro_title_artifacts(_normalize_pdf_signal_text(document_title or ""))
        if len(normalized_title) < 12:
            return recovered_blocks

        first_page_blocks = [
            block
            for block in recovered_blocks
            if block.page_start == 1 and block.page_end == 1
        ]
        if any(
            block.role == "heading" and _titles_overlap(block.text, normalized_title)
            for block in first_page_blocks
        ):
            return recovered_blocks

        compact_title = re.sub(r"\s+", "", normalized_title).casefold()
        split_blocks: list[_RecoveredBlock] = []
        reading_order_index = 0
        title_recovered = False
        for block in recovered_blocks:
            segments = [replace(block)]
            if (
                not title_recovered
                and block.page_start == 1
                and block.page_end == 1
                and block.role == "body"
                and str(block.metadata.get("pdf_page_family") or "body") == "body"
            ):
                normalized_block = _normalize_intro_title_artifacts(_normalize_pdf_signal_text(block.text))
                compact_block = re.sub(r"\s+", "", normalized_block).casefold()
                if normalized_block and (
                    _titles_overlap(normalized_block, normalized_title)
                    or compact_block == compact_title
                    or (len(compact_block) >= 12 and (compact_block in compact_title or compact_title in compact_block))
                ):
                    heading_metadata = dict(block.metadata)
                    heading_metadata["pdf_heading_recovery_source"] = "document_title_overlap"
                    shared_flags = list(dict.fromkeys([*block.flags, "embedded_document_title_recovered"]))
                    segments = [
                        _RecoveredBlock(
                            role="heading",
                            block_type=BlockType.HEADING,
                            text=normalized_title,
                            page_start=block.page_start,
                            page_end=block.page_end,
                            bbox_regions=list(block.bbox_regions),
                            reading_order_index=block.reading_order_index,
                            parse_confidence=block.parse_confidence,
                            flags=shared_flags,
                            metadata=heading_metadata,
                            font_size_avg=block.font_size_avg,
                            source_path=block.source_path,
                            anchor=f"{block.anchor}-title",
                        )
                    ]
                    title_recovered = True
            for segment in segments:
                reading_order_index += 1
                segment.reading_order_index = reading_order_index
                split_blocks.append(segment)
        return split_blocks

    def _split_embedded_page_heading_segments(
        self,
        block: _RecoveredBlock,
        *,
        is_first_substantive_page_block: bool,
        page_has_heading: bool,
    ) -> list[_RecoveredBlock]:
        if (
            block.role != "body"
            or block.block_type != BlockType.PARAGRAPH
        ):
            return [replace(block)]

        heading_text: str | None = None
        remainder: str | None = None
        recovery_flag: str | None = None
        metadata = dict(block.metadata)
        page_family = str(metadata.get("pdf_page_family") or "body")
        if page_family == "references":
            reference_heading = _leading_reference_heading_and_remainder(block.text)
            if reference_heading is not None:
                heading_text, remainder = reference_heading
                recovery_flag = "embedded_references_heading_recovered"
        elif (
            block.page_start == 1
            and block.page_end == 1
            and page_family == "body"
            and is_first_substantive_page_block
            and not page_has_heading
        ):
            paper_title = _infer_first_page_paper_title_and_remainder(block.text)
            if paper_title is not None:
                heading_text, remainder = paper_title
                recovery_flag = "embedded_document_title_recovered"

        if heading_text is None or recovery_flag is None:
            return [replace(block)]

        shared_flags = list(dict.fromkeys([*block.flags, recovery_flag]))
        heading_metadata = dict(metadata)
        heading_metadata["pdf_heading_recovery_source"] = recovery_flag
        segments = [
            _RecoveredBlock(
                role="heading",
                block_type=BlockType.HEADING,
                text=_normalize_multiline_text(heading_text),
                page_start=block.page_start,
                page_end=block.page_end,
                bbox_regions=list(block.bbox_regions),
                reading_order_index=block.reading_order_index,
                parse_confidence=block.parse_confidence,
                flags=shared_flags,
                metadata=heading_metadata,
                font_size_avg=block.font_size_avg,
                source_path=block.source_path,
                anchor=f"{block.anchor}-s1",
            )
        ]
        normalized_remainder = _normalize_multiline_text(remainder or "")
        if normalized_remainder:
            body_metadata = dict(metadata)
            body_metadata["pdf_heading_recovery_source"] = recovery_flag
            segments.append(
                _RecoveredBlock(
                    role=block.role,
                    block_type=block.block_type,
                    text=normalized_remainder,
                    page_start=block.page_start,
                    page_end=block.page_end,
                    bbox_regions=list(block.bbox_regions),
                    reading_order_index=block.reading_order_index,
                    parse_confidence=block.parse_confidence,
                    flags=shared_flags,
                    metadata=body_metadata,
                    font_size_avg=block.font_size_avg,
                    source_path=block.source_path,
                    anchor=f"{block.anchor}-s2",
                )
            )
        return segments

    def _split_academic_section_segments(
        self,
        block: _RecoveredBlock,
    ) -> list[_RecoveredBlock]:
        if (
            block.role != "body"
            or block.block_type != BlockType.PARAGRAPH
            or str(block.metadata.get("pdf_page_family") or "body") != "body"
        ):
            return [replace(block)]

        remaining_text = _normalize_multiline_text(block.text)
        if len(remaining_text) < 24:
            return [replace(block, text=remaining_text)]

        segments: list[tuple[str, str, dict[str, Any]]] = []
        while remaining_text:
            candidate = _next_academic_inline_heading(remaining_text)
            if candidate is None:
                segments.append(("body", remaining_text, {}))
                break
            start_index, heading_text, remainder, heading_meta = candidate
            prefix = remaining_text[:start_index].strip()
            if prefix:
                segments.append(("body", prefix, {}))
            segments.append(("heading", heading_text, heading_meta))
            remaining_text = remainder
            if not remaining_text:
                break

        if not any(kind == "heading" for kind, _text, _meta in segments):
            return [replace(block, text=remaining_text or block.text)]

        split_blocks: list[_RecoveredBlock] = []
        segment_index = 0
        for kind, text, heading_meta in segments:
            normalized_text = _normalize_multiline_text(text)
            if not normalized_text:
                continue
            segment_index += 1
            flags = list(dict.fromkeys([*block.flags, "academic_section_split"]))
            metadata = dict(block.metadata)
            if kind == "heading":
                flags.append("academic_section_heading_recovered")
                metadata["pdf_academic_heading"] = True
                metadata["pdf_academic_heading_kind"] = heading_meta.get("heading_kind")
                metadata["pdf_academic_section_level"] = heading_meta.get("section_level")
                role = "heading"
                block_type = BlockType.HEADING
            else:
                role = block.role
                block_type = block.block_type
            split_blocks.append(
                _RecoveredBlock(
                    role=role,
                    block_type=block_type,
                    text=normalized_text,
                    page_start=block.page_start,
                    page_end=block.page_end,
                    bbox_regions=list(block.bbox_regions),
                    reading_order_index=block.reading_order_index,
                    parse_confidence=block.parse_confidence,
                    flags=flags,
                    metadata=metadata,
                    font_size_avg=block.font_size_avg,
                    source_path=block.source_path,
                    anchor=f"{block.anchor}-s{segment_index}",
                )
            )
        return split_blocks or [replace(block, text=remaining_text or block.text)]

    def _footnote_continuation_target_index(
        self,
        recovered: list[_RecoveredBlock],
        current: _RecoveredBlock,
        pages: list[PdfPage],
    ) -> int | None:
        if not recovered:
            return None
        skipped_roles = {"header", "footer"}
        index = len(recovered) - 1
        while index >= 0 and recovered[index].role in skipped_roles:
            index -= 1
        if index < 0:
            return None
        if any(block.role not in skipped_roles for block in recovered[index + 1 :]):
            return None
        if self._should_merge_footnote_continuation(recovered[index], current, pages):
            return index
        return None

    def _should_merge_footnote_continuation(
        self,
        previous: _RecoveredBlock,
        current: _RecoveredBlock,
        pages: list[PdfPage],
    ) -> bool:
        if previous.role != "footnote" or current.role not in {"body", "footnote"}:
            return False
        if current.page_start - previous.page_end > 1:
            return False

        previous_marker = str(previous.metadata.get("footnote_anchor_label") or _extract_footnote_marker(previous.text) or "")
        current_marker = _extract_footnote_marker(current.text)
        if current.role == "footnote" and previous_marker and current_marker and current_marker != previous_marker:
            return False
        if current.role == "body" and current_marker is not None:
            return False
        if current.font_size_avg > max(previous.font_size_avg * 1.15, previous.font_size_avg + 1.0):
            return False

        page_lookup = {page.page_number: page for page in pages}
        if current.page_start == previous.page_end:
            prev_bottom = previous.bbox_regions[-1]["bbox"][3]
            curr_top = current.bbox_regions[0]["bbox"][1]
            gap = curr_top - prev_bottom
            if gap > max(previous.font_size_avg * 1.8, 18.0):
                return False
        else:
            prev_page = page_lookup.get(previous.page_end)
            curr_page = page_lookup.get(current.page_start)
            if prev_page is None or curr_page is None:
                return False
            prev_bottom = previous.bbox_regions[-1]["bbox"][3]
            curr_top = current.bbox_regions[0]["bbox"][1]
            if prev_bottom < prev_page.height * 0.72:
                return False
            if curr_top > curr_page.height * 0.28:
                return False

        if self._footnote_paragraph_relocation_mode(previous, current, pages) is not None:
            return True

        first_char = current.text[:1]
        if first_char.islower() or first_char in {",", ";", ":", ".", ")", "]", "\"", "'", "\u201d", "\u2019"}:
            return True
        return not previous.text.rstrip().endswith(_TERMINAL_PUNCTUATION)

    def _footnote_paragraph_relocation_mode(
        self,
        previous: _RecoveredBlock,
        current: _RecoveredBlock,
        pages: list[PdfPage],
    ) -> str | None:
        if previous.role != "footnote" or current.role != "body":
            return None
        if _extract_footnote_marker(current.text) is not None:
            return None

        page_lookup = {page.page_number: page for page in pages}
        if current.page_start == previous.page_end:
            page = page_lookup.get(current.page_start)
            if page is None:
                return None
            current_top = current.bbox_regions[0]["bbox"][1]
            previous_bottom = previous.bbox_regions[-1]["bbox"][3]
            if previous_bottom >= page.height * 0.72 and current_top >= page.height * 0.72:
                return "same_page_body_segment"
            if (
                previous.page_end > previous.page_start
                and previous_bottom <= page.height * 0.18
                and current_top <= page.height * 0.26
            ):
                return "cross_page_body_segment"
            return None

        previous_page = page_lookup.get(previous.page_end)
        current_page = page_lookup.get(current.page_start)
        if previous_page is None or current_page is None:
            return None
        previous_bottom = previous.bbox_regions[-1]["bbox"][3]
        current_top = current.bbox_regions[0]["bbox"][1]
        if previous_bottom < previous_page.height * 0.72:
            return None
        if current_top > current_page.height * 0.26:
            return None
        return "cross_page_body_segment"

    def _link_footnotes(self, recovered_blocks: list[_RecoveredBlock]) -> None:
        for index, block in enumerate(recovered_blocks):
            if block.role != "footnote":
                continue
            marker = _extract_footnote_marker(block.text)
            if marker is not None:
                block.metadata["footnote_anchor_label"] = marker
            anchor_target = self._footnote_anchor_target(recovered_blocks, index, marker)
            if anchor_target is None:
                block.metadata["footnote_anchor_matched"] = False
                block.flags = list(dict.fromkeys([*block.flags, "footnote_orphaned"]))
                continue
            block.metadata.update(
                {
                    "footnote_anchor_matched": True,
                    "footnote_anchor_block_anchor": anchor_target.anchor,
                    "footnote_anchor_page": anchor_target.page_end,
                    "footnote_anchor_reading_order_index": anchor_target.reading_order_index,
                }
            )
            block.flags = list(dict.fromkeys([*block.flags, "footnote_anchor_linked"]))

    def _footnote_anchor_target(
        self,
        recovered_blocks: list[_RecoveredBlock],
        footnote_index: int,
        marker: str | None,
    ) -> _RecoveredBlock | None:
        if marker is None:
            return None
        footnote_block = recovered_blocks[footnote_index]
        for candidate in reversed(recovered_blocks[:footnote_index]):
            if candidate.role not in {"body", "heading", "caption"}:
                continue
            if candidate.page_end < footnote_block.page_start - 1:
                break
            if _body_contains_footnote_anchor(candidate.text, marker):
                return candidate
        return None

    def _link_artifact_captions(self, recovered_blocks: list[_RecoveredBlock]) -> None:
        claimed_caption_indexes: set[int] = set()
        for artifact_index, artifact_block in enumerate(recovered_blocks):
            if artifact_block.role not in {"image", "table_like", "equation"}:
                continue
            caption_index = self._artifact_caption_target(recovered_blocks, artifact_index, claimed_caption_indexes)
            if caption_index is None:
                continue
            caption_block = recovered_blocks[caption_index]
            claimed_caption_indexes.add(caption_index)
            caption_anchor = self._source_anchor(caption_block)
            artifact_anchor = self._source_anchor(artifact_block)
            artifact_role = self._normalized_artifact_caption_role(artifact_block)
            artifact_block.metadata.update(
                {
                    "linked_caption_text": caption_block.text,
                    "linked_caption_source_anchor": caption_anchor,
                    "linked_caption_page": caption_block.page_start,
                }
            )
            if artifact_role == "image" and not artifact_block.metadata.get("image_alt") and caption_block.text.strip():
                artifact_block.metadata["image_alt"] = caption_block.text
            artifact_block.flags = list(dict.fromkeys([*artifact_block.flags, "caption_linked"]))
            caption_block.metadata.update(
                {
                    "caption_for_source_anchor": artifact_anchor,
                    "caption_for_page": artifact_block.page_start,
                    "caption_for_role": artifact_role,
                }
            )
            caption_block.flags = list(
                dict.fromkeys([*caption_block.flags, f"{artifact_role}_caption_linked"])
            )

    def _link_artifact_group_contexts(
        self,
        recovered_blocks: list[_RecoveredBlock],
        *,
        academic_paper: bool,
    ) -> None:
        source_anchor_to_index = {
            self._source_anchor(block): index
            for index, block in enumerate(recovered_blocks)
            if block.anchor
        }
        claimed_context_indexes: set[int] = set()
        for artifact_index, artifact_block in enumerate(recovered_blocks):
            artifact_role = self._normalized_artifact_caption_role(artifact_block)
            if artifact_role not in {"image", "table", "equation"}:
                continue
            linked_caption_source_anchor = artifact_block.metadata.get("linked_caption_source_anchor")
            if not isinstance(linked_caption_source_anchor, str):
                continue
            caption_index = source_anchor_to_index.get(linked_caption_source_anchor)
            if caption_index is None:
                continue
            context_index = self._artifact_group_context_target(
                recovered_blocks,
                artifact_index,
                caption_index,
                claimed_context_indexes,
                artifact_role=artifact_role,
                academic_paper=academic_paper,
            )
            if context_index is None:
                continue
            context_block = recovered_blocks[context_index]
            claimed_context_indexes.add(context_index)
            context_anchor = self._source_anchor(context_block)
            artifact_anchor = self._source_anchor(artifact_block)
            artifact_block.metadata["artifact_group_context_source_anchors"] = [context_anchor]
            artifact_block.flags = list(
                dict.fromkeys([*artifact_block.flags, "artifact_group_context_linked"])
            )
            context_block.metadata.update(
                {
                    "artifact_group_source_anchor": artifact_anchor,
                    "artifact_group_role": artifact_role,
                }
            )
            context_block.flags = list(
                dict.fromkeys([*context_block.flags, f"{artifact_role}_group_context_linked"])
            )

    def _normalized_artifact_caption_role(self, block: _RecoveredBlock) -> str:
        if block.role == "table_like" or block.block_type == BlockType.TABLE:
            return "table"
        if block.role == "equation" or block.block_type == BlockType.EQUATION:
            return "equation"
        return block.role

    def _artifact_caption_target(
        self,
        recovered_blocks: list[_RecoveredBlock],
        artifact_index: int,
        claimed_caption_indexes: set[int],
    ) -> int | None:
        artifact_block = recovered_blocks[artifact_index]
        artifact_bbox = self._page_bbox(artifact_block, artifact_block.page_start)
        if artifact_bbox is None:
            return None

        below_candidates: list[tuple[float, float, int, int]] = []
        above_candidates: list[tuple[float, float, int, int]] = []
        for candidate_index, candidate in enumerate(recovered_blocks):
            if candidate_index == artifact_index or candidate_index in claimed_caption_indexes:
                continue
            if candidate.role != "caption":
                continue
            if candidate.page_start != artifact_block.page_start or candidate.page_end != artifact_block.page_end:
                continue
            if not _caption_matches_artifact_role(candidate.text, self._normalized_artifact_caption_role(artifact_block)):
                continue
            candidate_bbox = self._page_bbox(candidate, artifact_block.page_start)
            if candidate_bbox is None:
                continue
            overlap_ratio = self._horizontal_overlap_ratio(artifact_bbox, candidate_bbox)
            if overlap_ratio < 0.2:
                continue
            center_distance = abs(
                ((candidate_bbox[0] + candidate_bbox[2]) / 2.0)
                - ((artifact_bbox[0] + artifact_bbox[2]) / 2.0)
            )
            below_gap = candidate_bbox[1] - artifact_bbox[3]
            if -12.0 <= below_gap <= 120.0:
                below_candidates.append(
                    (
                        max(below_gap, 0.0),
                        center_distance,
                        abs(candidate.reading_order_index - artifact_block.reading_order_index),
                        candidate_index,
                    )
                )
                continue
            above_gap = artifact_bbox[1] - candidate_bbox[3]
            if -12.0 <= above_gap <= 80.0:
                above_candidates.append(
                    (
                        max(above_gap, 0.0),
                        center_distance,
                        abs(candidate.reading_order_index - artifact_block.reading_order_index),
                        candidate_index,
                    )
                )
        if below_candidates:
            return min(below_candidates)[3]
        if above_candidates:
            return min(above_candidates)[3]
        return None

    def _artifact_group_context_target(
        self,
        recovered_blocks: list[_RecoveredBlock],
        artifact_index: int,
        caption_index: int,
        claimed_context_indexes: set[int],
        *,
        artifact_role: str,
        academic_paper: bool,
    ) -> int | None:
        artifact_block = recovered_blocks[artifact_index]
        caption_block = recovered_blocks[caption_index]
        if artifact_block.page_start != caption_block.page_start:
            return None

        page_number = artifact_block.page_start
        artifact_bbox = self._page_bbox(artifact_block, page_number)
        caption_bbox = self._page_bbox(caption_block, page_number)
        if artifact_bbox is None and caption_bbox is None:
            return None
        cluster_bbox = self._union_bbox(artifact_bbox, caption_bbox)
        if cluster_bbox is None:
            return None

        cluster_bottom = cluster_bbox[3]
        cluster_center = (cluster_bbox[0] + cluster_bbox[2]) / 2.0
        cluster_width = max(cluster_bbox[2] - cluster_bbox[0], 1.0)
        cluster_reading_order = max(
            artifact_block.reading_order_index,
            caption_block.reading_order_index,
        )
        start_index = max(artifact_index, caption_index)

        for candidate_index in range(start_index + 1, len(recovered_blocks)):
            candidate = recovered_blocks[candidate_index]
            if candidate_index in claimed_context_indexes:
                continue
            if candidate.page_start != page_number or candidate.page_end != page_number:
                if candidate.page_start > page_number:
                    break
                continue
            if candidate.role in {"header", "footer", "toc_entry", "footnote"}:
                continue
            if candidate.role in {"caption", "image", "table_like", "equation", "heading"}:
                break
            if candidate.block_type not in {BlockType.PARAGRAPH, BlockType.QUOTE, BlockType.LIST_ITEM}:
                break
            candidate_bbox = self._page_bbox(candidate, page_number)
            if candidate_bbox is None:
                break
            gap = candidate_bbox[1] - cluster_bottom
            if candidate.reading_order_index <= cluster_reading_order:
                continue
            if candidate.reading_order_index - cluster_reading_order > 4:
                break
            if gap < -12.0:
                continue
            if gap > 96.0:
                break
            overlap_ratio = self._horizontal_overlap_ratio(cluster_bbox, candidate_bbox)
            center_distance = abs(
                ((candidate_bbox[0] + candidate_bbox[2]) / 2.0) - cluster_center
            )
            if overlap_ratio < 0.12 and center_distance > cluster_width * 0.9:
                break
            if not looks_like_artifact_group_context_text(
                candidate.text,
                artifact_role,
                academic_paper=academic_paper,
            ):
                break
            return candidate_index
        return None

    def _page_bbox(self, block: _RecoveredBlock, page_number: int) -> list[float] | None:
        for region in block.bbox_regions:
            if int(region["page_number"]) != page_number:
                continue
            bbox = region.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                try:
                    return [float(value) for value in bbox]
                except (TypeError, ValueError):
                    return None
        return None

    def _union_bbox(self, left: list[float] | None, right: list[float] | None) -> list[float] | None:
        if left is None:
            return right
        if right is None:
            return left
        return [
            min(left[0], right[0]),
            min(left[1], right[1]),
            max(left[2], right[2]),
            max(left[3], right[3]),
        ]

    def _horizontal_overlap_ratio(self, left: list[float], right: list[float]) -> float:
        overlap = min(left[2], right[2]) - max(left[0], right[0])
        if overlap <= 0:
            return 0.0
        left_width = max(left[2] - left[0], 1.0)
        right_width = max(right[2] - right[0], 1.0)
        return overlap / min(left_width, right_width)

    def _source_anchor(self, block: _RecoveredBlock) -> str:
        return f"{block.source_path}#{block.anchor}" if block.anchor else block.source_path

    def _build_chapters(
        self,
        recovered_blocks: list[_RecoveredBlock],
        outline_entries: list[PdfOutlineEntry],
        profile: PdfFileProfile,
        file_path: str | Path,
    ) -> list[ParsedChapter]:
        chapter_starts = self._chapter_start_candidates(recovered_blocks, outline_entries, profile)
        heading_titles_by_page: dict[int, list[str]] = defaultdict(list)
        for block in recovered_blocks:
            if block.role != "heading":
                continue
            heading_titles_by_page[block.page_start].append(_normalize_text(block.text))
        chapters: list[ParsedChapter] = []
        current_blocks: list[_RecoveredBlock] = []
        current_title: str | None = None
        current_section_family: str | None = None
        current_start_page: int | None = None
        current_chapter_index = 1
        next_chapter_start_index = 0
        has_started_from_candidate = False

        def flush_current() -> None:
            nonlocal current_blocks, current_title, current_section_family, current_start_page, current_chapter_index
            if not current_blocks:
                return
            if not self._has_substantive_content(current_blocks):
                current_blocks = []
                current_title = None
                current_section_family = None
                current_start_page = None
                return
            start_page = current_start_page or current_blocks[0].page_start
            title = current_title or self._fallback_chapter_title(current_blocks, current_chapter_index)
            chapter_id = f"pdf-chapter-{current_chapter_index:03d}"
            href = f"pdf://page/{start_page}"
            parsed_blocks = [
                ParsedBlock(
                    block_type=block.block_type.value,
                    text=block.text,
                    source_path=block.source_path,
                    ordinal=ordinal,
                    anchor=block.anchor,
                    metadata={
                        "source_page_start": block.page_start,
                        "source_page_end": block.page_end,
                        "source_bbox_json": {"regions": block.bbox_regions},
                        "reading_order_index": block.reading_order_index,
                        "pdf_block_role": block.role,
                        "recovery_flags": block.flags,
                        **block.metadata,
                        "translatable": (
                            block.role not in {"header", "footer", "toc_entry"}
                            and str(block.metadata.get("pdf_page_family") or "body") != "backmatter"
                        ),
                        "nontranslatable_reason": (
                            (
                                f"pdf_{block.role}"
                                if block.role in {"header", "footer", "toc_entry"}
                                else "pdf_backmatter"
                                if str(block.metadata.get("pdf_page_family") or "body") == "backmatter"
                                else None
                            )
                        ),
                    },
                    parse_confidence=block.parse_confidence,
                )
                for ordinal, block in enumerate(current_blocks, start=1)
            ]
            chapters.append(
                ParsedChapter(
                    chapter_id=chapter_id,
                    href=href,
                    title=title,
                    blocks=parsed_blocks,
                    metadata={
                        "source_path": str(file_path),
                        "source_page_start": start_page,
                        "source_page_end": current_blocks[-1].page_end,
                        "pdf_section_family": current_section_family or self._chapter_section_family(current_blocks),
                    },
                )
            )
            current_chapter_index += 1
            current_blocks = []
            current_title = None
            current_section_family = None
            current_start_page = None

        for block in recovered_blocks:
            should_start_new = False
            chapter_start: _ChapterStartCandidate | None = None
            chapter_start_title: str | None = None
            heading_title = (
                _infer_appendix_intro_title(block.text)
                if block.role == "heading" and str(block.metadata.get("pdf_page_family") or "body") == "appendix"
                else None
            ) or block.text

            while next_chapter_start_index < len(chapter_starts):
                entry = chapter_starts[next_chapter_start_index]
                if block.page_start < entry.page_number:
                    break
                if (
                    block.page_start == entry.page_number
                    and entry.source in {"outline", "toc", "academic_heading"}
                    and any(
                        _titles_overlap(candidate_title, entry.title)
                        for candidate_title in heading_titles_by_page.get(entry.page_number, [])
                    )
                    and (
                        block.role != "heading"
                        or not _titles_overlap(_normalize_text(heading_title), entry.title)
                    )
                ):
                    break
                chapter_start = entry
                chapter_start_title = entry.title
                should_start_new = True
                next_chapter_start_index += 1
                break

            if (
                should_start_new
                and current_blocks
                and not has_started_from_candidate
                and chapter_start is not None
                and chapter_start.source in {"outline", "toc"}
                and current_blocks[-1].page_end < chapter_start.page_number
            ):
                current_blocks = []
                current_title = None
                current_section_family = None
                current_start_page = None
            elif (
                should_start_new
                and current_blocks
                and not has_started_from_candidate
                and chapter_start is not None
                and chapter_start.source == "chapter_intro"
                and current_blocks[-1].page_end < chapter_start.page_number
                and self._looks_like_frontmatter_chunk(current_blocks)
            ):
                current_title = "Front Matter"
                current_section_family = "frontmatter"
                flush_current()
            elif should_start_new and current_blocks:
                flush_current()

            if not current_blocks:
                current_start_page = block.page_start
                current_title = chapter_start_title or (heading_title if block.role == "heading" else None)
                current_section_family = chapter_start.section_family if chapter_start is not None else None
            elif should_start_new and current_title is None:
                current_title = chapter_start_title or (heading_title if block.role == "heading" else current_title)
            if should_start_new and chapter_start is not None and chapter_start.section_family is not None:
                current_section_family = chapter_start.section_family

            if chapter_start is not None:
                has_started_from_candidate = True

            if current_title and block.role == "heading" and _titles_overlap(heading_title, current_title):
                current_title = heading_title
            elif current_title is None and block.role == "heading":
                current_title = heading_title

            current_blocks.append(block)

        flush_current()
        if chapters:
            return chapters

        return [
            ParsedChapter(
                chapter_id="pdf-chapter-001",
                href="pdf://page/1",
                title="Document",
                blocks=[],
                metadata={"source_path": str(file_path)},
            )
        ]

    def _chapter_start_candidates(
        self,
        recovered_blocks: list[_RecoveredBlock],
        outline_entries: list[PdfOutlineEntry],
        profile: PdfFileProfile,
    ) -> list[_ChapterStartCandidate]:
        section_candidates = self._merge_chapter_start_candidates(
            self._section_family_candidates(recovered_blocks),
            self._section_family_page_candidates(recovered_blocks),
        )
        if profile.recovery_lane == "academic_paper":
            academic_heading_candidates = self._academic_heading_candidates(recovered_blocks)
            section_candidates = self._merge_chapter_start_candidates(section_candidates, academic_heading_candidates)
        outline_candidates = [
            _ChapterStartCandidate(page_number=entry.page_number, title=entry.title, source="outline")
            for entry in self._top_level_outline_entries(outline_entries)
        ]
        if outline_candidates:
            return self._merge_chapter_start_candidates(section_candidates, outline_candidates)

        toc_candidates = self._toc_candidates(recovered_blocks)
        if toc_candidates:
            return self._merge_chapter_start_candidates(section_candidates, toc_candidates)

        intro_candidates = self._chapter_intro_page_candidates(recovered_blocks)
        heading_candidates = [
            _ChapterStartCandidate(page_number=block.page_start, title=block.text, source="heading")
            for block in recovered_blocks
            if block.role == "heading" and _HEADING_PATTERN.match(block.text)
        ]
        return self._merge_chapter_start_candidates(
            section_candidates,
            [*intro_candidates, *heading_candidates],
        )

    def _academic_heading_candidates(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> list[_ChapterStartCandidate]:
        candidates: list[_ChapterStartCandidate] = []
        seen: set[tuple[int, str]] = set()
        for block in recovered_blocks:
            if block.role != "heading":
                continue
            if str(block.metadata.get("pdf_page_family") or "body") != "body":
                continue
            title = _normalize_text(block.text)
            if not title or _looks_like_paper_title(title):
                continue
            section_level = int(block.metadata.get("pdf_academic_section_level", 0) or 0)
            is_academic_heading = bool(block.metadata.get("pdf_academic_heading"))
            if not is_academic_heading:
                lowered = title.casefold()
                if _leading_academic_standalone_heading(title) is not None:
                    is_academic_heading = True
                    section_level = max(section_level, 1)
                elif re.match(r"^\d+(?:\.\d+){0,2}\s+\S+", lowered):
                    is_academic_heading = True
                    section_level = max(section_level, title.split()[0].count(".") + 1)
            if not is_academic_heading or section_level not in {1}:
                continue
            key = (block.page_start, _normalize_outline_title(title))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                _ChapterStartCandidate(
                    page_number=block.page_start,
                    title=title,
                    source="academic_heading",
                    section_family="body",
                )
            )
        return candidates

    def _chapter_intro_page_candidates(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> list[_ChapterStartCandidate]:
        blocks_by_page: dict[int, list[_RecoveredBlock]] = defaultdict(list)
        for block in recovered_blocks:
            blocks_by_page[block.page_start].append(block)

        candidates: list[_ChapterStartCandidate] = []
        seen: set[tuple[int, str]] = set()
        for page_number in sorted(blocks_by_page):
            substantive_blocks = [
                block
                for block in sorted(blocks_by_page[page_number], key=lambda item: item.reading_order_index)
                if block.role not in {"header", "footer", "toc_entry", "footnote"}
            ]
            if not substantive_blocks:
                continue
            intro_index = next(
                (
                    index
                    for index, block in enumerate(substantive_blocks[:5])
                    if block.role == "body" and _contains_chapter_intro_cue(block.text)
                ),
                None,
            )
            if intro_index is None:
                continue
            intro_block = substantive_blocks[intro_index]
            title_source_blocks = (
                [intro_block.text]
                if intro_index == 0
                else [block.text for block in substantive_blocks[:intro_index]]
            )
            if intro_index == 0 and _looks_like_chapter_intro_cue(intro_block.text):
                continue
            title = _infer_intro_page_title(title_source_blocks)
            if title is None:
                continue
            key = (page_number, _normalize_outline_title(title))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                _ChapterStartCandidate(
                    page_number=page_number,
                    title=title,
                    source="chapter_intro",
                )
            )
        return candidates

    def _section_family_page_candidates(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> list[_ChapterStartCandidate]:
        blocks_by_page: dict[int, list[_RecoveredBlock]] = defaultdict(list)
        for block in recovered_blocks:
            blocks_by_page[block.page_start].append(block)

        page_numbers = sorted(blocks_by_page)
        substantive_page_numbers: list[int] = []
        substantive_families: dict[int, str] = {}
        substantive_family_headings: dict[int, str] = {}
        substantive_family_sources: dict[int, str] = {}
        substantive_content_families: dict[int, str] = {}
        substantive_block_counts: dict[int, int] = {}
        appendix_subheading_titles: dict[int, str] = {}
        page_has_heading: dict[int, bool] = {}
        candidates: list[_ChapterStartCandidate] = []
        previous_family = "body"
        appendix_subheading_sources = {"inline_heading", "appendix_intro"}
        last_appendix_heading: str | None = None
        for page_number in page_numbers:
            page_blocks = blocks_by_page[page_number]
            substantive_blocks = [
                block for block in page_blocks if block.role not in {"header", "footer", "toc_entry"}
            ]
            if not substantive_blocks:
                continue
            substantive_page_numbers.append(page_number)
            substantive_block_counts[page_number] = len(substantive_blocks)
            substantive_families[page_number] = str(substantive_blocks[0].metadata.get("pdf_page_family") or "body")
            family_heading = substantive_blocks[0].metadata.get("pdf_page_family_heading")
            if isinstance(family_heading, str) and family_heading.strip():
                substantive_family_headings[page_number] = family_heading.strip()
            family_source = substantive_blocks[0].metadata.get("pdf_page_family_source")
            if isinstance(family_source, str) and family_source.strip():
                substantive_family_sources[page_number] = family_source.strip()
            content_family = substantive_blocks[0].metadata.get("pdf_page_content_family")
            if isinstance(content_family, str) and content_family.strip():
                substantive_content_families[page_number] = content_family.strip()
            page_has_heading[page_number] = any(block.role == "heading" for block in substantive_blocks)
            if (
                substantive_families[page_number] == "appendix"
                and substantive_family_sources.get(page_number) == "continuation"
                and substantive_block_counts.get(page_number, 0) >= 4
            ):
                appendix_subheading = next(
                    (
                        title
                        for block in substantive_blocks[:6]
                        for title in [_infer_appendix_subheading_title(block.text)]
                        if title is not None
                    ),
                    None,
                )
                if appendix_subheading is not None:
                    appendix_subheading_titles[page_number] = appendix_subheading

        for index, page_number in enumerate(substantive_page_numbers):
            page_family = substantive_families[page_number]
            family_heading = substantive_family_headings.get(page_number)
            family_source = substantive_family_sources.get(page_number)
            if (
                page_family == previous_family == "appendix"
                and family_source in appendix_subheading_sources
                and family_heading is not None
                and (
                    last_appendix_heading is None
                    or not _titles_overlap(family_heading, last_appendix_heading)
                )
            ):
                candidates.append(
                    _ChapterStartCandidate(
                        page_number=page_number,
                        title=family_heading,
                        source="section_family_subheading",
                        section_family=page_family,
                    )
                )
                previous_family = page_family
                last_appendix_heading = family_heading
                continue
            appendix_subheading = appendix_subheading_titles.get(page_number)
            if (
                page_family == previous_family == "appendix"
                and family_source == "continuation"
                and appendix_subheading is not None
                and (
                    last_appendix_heading is None
                    or not _titles_overlap(appendix_subheading, last_appendix_heading)
                )
            ):
                candidates.append(
                    _ChapterStartCandidate(
                        page_number=page_number,
                        title=appendix_subheading,
                        source="section_family_subheading",
                        section_family=page_family,
                    )
                )
                previous_family = page_family
                last_appendix_heading = appendix_subheading
                continue
            if page_family not in {"frontmatter", "appendix", "references", "index", "backmatter"}:
                previous_family = page_family
                last_appendix_heading = None
                continue
            if previous_family == page_family:
                if page_family == "appendix" and family_source in appendix_subheading_sources and family_heading is not None:
                    last_appendix_heading = family_heading
                continue
            if page_has_heading[page_number] and page_family != "backmatter":
                previous_family = page_family
                last_appendix_heading = None
                continue
            next_page_family = (
                substantive_families.get(substantive_page_numbers[index + 1])
                if index + 1 < len(substantive_page_numbers)
                else None
            )
            remaining_page_numbers = substantive_page_numbers[index + 1 :]
            has_later_heading = any(page_has_heading.get(candidate_page, False) for candidate_page in remaining_page_numbers)
            if next_page_family != page_family and next_page_family is not None:
                allow_single_page_references = (
                    page_family == "references"
                    and substantive_content_families.get(page_number) == "references"
                )
                if not allow_single_page_references and (has_later_heading or len(remaining_page_numbers) > 2):
                    previous_family = page_family
                    last_appendix_heading = None
                    continue
            candidates.append(
                _ChapterStartCandidate(
                    page_number=page_number,
                    title=substantive_family_headings.get(page_number, self._section_family_display_title(page_family)),
                    source="section_family_page",
                    section_family=page_family,
                )
            )
            previous_family = page_family
            if page_family == "appendix" and family_source in appendix_subheading_sources and family_heading is not None:
                last_appendix_heading = family_heading
            else:
                last_appendix_heading = None
        return candidates

    def _section_family_candidates(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> list[_ChapterStartCandidate]:
        candidates: list[_ChapterStartCandidate] = []
        seen: set[tuple[int, str, str]] = set()
        for block in recovered_blocks:
            if block.role != "heading":
                continue
            section_family = str(block.metadata.get("pdf_page_family") or "body")
            if section_family not in {"frontmatter", "appendix", "references", "index", "backmatter"}:
                continue
            title = (
                _infer_appendix_intro_title(block.text)
                if section_family == "appendix"
                else None
            ) or (
                self._section_family_display_title(section_family)
                if section_family == "backmatter"
                else block.text
            )
            key = (block.page_start, section_family, _normalize_outline_title(title))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                _ChapterStartCandidate(
                    page_number=block.page_start,
                    title=title,
                    source="section_family",
                    section_family=section_family,
                )
            )
        return sorted(candidates, key=lambda candidate: candidate.page_number)

    def _merge_chapter_start_candidates(
        self,
        primary: list[_ChapterStartCandidate],
        secondary: list[_ChapterStartCandidate],
    ) -> list[_ChapterStartCandidate]:
        merged: list[_ChapterStartCandidate] = []
        seen: set[tuple[int, str]] = set()
        special_pages: set[int] = set()
        for candidate in sorted([*primary, *secondary], key=lambda item: (item.page_number, item.source != "section_family")):
            if candidate.page_number in special_pages and candidate.section_family is None:
                continue
            key = (candidate.page_number, _normalize_outline_title(candidate.title))
            if key in seen:
                continue
            seen.add(key)
            if candidate.section_family is not None:
                special_pages.add(candidate.page_number)
            merged.append(candidate)
        return merged

    def _toc_page_offsets(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> tuple[int | None, int | None]:
        title_match_diffs: list[int] = []
        footer_diffs: list[int] = []
        headings = [
            block
            for block in recovered_blocks
            if block.role == "heading" and str(block.metadata.get("pdf_page_family") or "body") != "toc"
        ]
        toc_end_page = max(
            (block.page_end for block in recovered_blocks if block.role == "toc_entry"),
            default=0,
        )

        for toc_block in recovered_blocks:
            if toc_block.role != "toc_entry":
                continue
            title = str(toc_block.metadata.get("toc_title") or "").strip()
            page_number = toc_block.metadata.get("toc_page_number")
            if not title or not isinstance(page_number, int):
                continue
            match_page = next(
                (
                    heading.page_start
                    for heading in headings
                    if heading.page_start > toc_block.page_end and _titles_overlap(heading.text, title)
                ),
                None,
            )
            if match_page is not None:
                title_match_diffs.append(match_page - page_number)

        for block in recovered_blocks:
            if block.role != "footer" or block.page_start <= toc_end_page:
                continue
            footer_page_label = _parse_page_number(block.text)
            if footer_page_label is None:
                continue
            footer_diffs.append(block.page_start - footer_page_label)

        return self._dominant_nonnegative_offset(title_match_diffs), self._dominant_nonnegative_offset(footer_diffs)

    def _dominant_nonnegative_offset(self, values: list[int]) -> int | None:
        nonnegative = [value for value in values if value >= 0]
        if not nonnegative:
            return None
        counts = Counter(nonnegative)
        return max(counts.items(), key=lambda item: (item[1], -item[0]))[0]

    def _resolved_toc_page_number(
        self,
        toc_block: _RecoveredBlock,
        title: str,
        printed_page_number: int,
        headings: list[_RecoveredBlock],
        title_match_offset: int | None,
        footer_offset: int | None,
        max_page_number: int,
    ) -> tuple[int | None, str, int | None]:
        matched_heading_page = next(
            (
                heading.page_start
                for heading in headings
                if heading.page_start > toc_block.page_end and _titles_overlap(heading.text, title)
            ),
            None,
        )
        if matched_heading_page is not None and matched_heading_page <= max_page_number:
            return matched_heading_page, "title_match", matched_heading_page - printed_page_number

        for offset, source in ((title_match_offset, "title_match_offset"), (footer_offset, "footer_offset")):
            if offset is None:
                continue
            resolved_page = printed_page_number + offset
            if toc_block.page_end < resolved_page <= max_page_number:
                return resolved_page, source, offset

        if toc_block.page_end < printed_page_number <= max_page_number:
            return printed_page_number, "printed", 0
        return None, "unresolved", None

    def _top_level_outline_entries(self, outline_entries: list[PdfOutlineEntry]) -> list[PdfOutlineEntry]:
        if not outline_entries:
            return []
        top_level = min(entry.level for entry in outline_entries)
        top_level_entries = [entry for entry in outline_entries if entry.level == top_level]
        primary_outline_entry_count = sum(
            1 for entry in top_level_entries if _looks_like_book_primary_outline_title(entry.title)
        )
        filter_auxiliary_book_outline_entries = primary_outline_entry_count >= 2
        deduped: list[PdfOutlineEntry] = []
        seen: set[tuple[int, str]] = set()
        for entry in sorted(outline_entries, key=lambda item: (item.page_number, item.level)):
            if entry.level != top_level:
                continue
            if (
                filter_auxiliary_book_outline_entries
                and not _should_keep_book_top_level_outline_title(entry.title)
            ):
                continue
            key = (entry.page_number, _normalize_outline_title(entry.title))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(entry)
        return deduped

    def _toc_candidates(self, recovered_blocks: list[_RecoveredBlock]) -> list[_ChapterStartCandidate]:
        max_page_number = max((block.page_end for block in recovered_blocks), default=0)
        candidates: list[_ChapterStartCandidate] = []
        seen: set[tuple[int, str]] = set()
        headings = [
            block
            for block in recovered_blocks
            if block.role == "heading" and str(block.metadata.get("pdf_page_family") or "body") != "toc"
        ]
        title_match_offset, footer_offset = self._toc_page_offsets(recovered_blocks)
        for block in recovered_blocks:
            if block.role != "toc_entry":
                continue
            title = str(block.metadata.get("toc_title") or "").strip()
            printed_page_number = block.metadata.get("toc_page_number")
            if not title or not isinstance(printed_page_number, int):
                continue
            if not _HEADING_PATTERN.match(title):
                continue
            resolved_page_number, resolution_source, page_offset = self._resolved_toc_page_number(
                block,
                title,
                printed_page_number,
                headings,
                title_match_offset,
                footer_offset,
                max_page_number,
            )
            block.metadata["toc_page_number_printed"] = printed_page_number
            block.metadata["toc_page_resolution_source"] = resolution_source
            block.metadata["toc_page_offset"] = page_offset
            block.metadata["toc_page_number_resolved"] = resolved_page_number
            if resolved_page_number is None:
                continue
            key = (resolved_page_number, _normalize_outline_title(title))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(_ChapterStartCandidate(page_number=resolved_page_number, title=title, source="toc"))
        return sorted(candidates, key=lambda candidate: candidate.page_number)

    def _has_substantive_content(self, blocks: list[_RecoveredBlock]) -> bool:
        return any(block.role not in {"header", "footer", "toc_entry"} for block in blocks)

    def _looks_like_frontmatter_chunk(self, blocks: list[_RecoveredBlock]) -> bool:
        signal_count = 0
        for block in blocks:
            if block.role not in {"body", "heading"}:
                continue
            if _looks_like_frontmatter_signal(block.text):
                signal_count += 1
            if signal_count >= 2:
                return True
        return False

    def _chapter_section_family(self, blocks: list[_RecoveredBlock]) -> str:
        family_counts: Counter[str] = Counter(
            str(block.metadata.get("pdf_page_family") or "body")
            for block in blocks
            if block.role not in {"header", "footer"}
        )
        if self._looks_like_frontmatter_chunk(blocks):
            return "frontmatter"
        body_count = int(family_counts.get("body", 0))
        dominant_special_family = max(
            ("frontmatter", "appendix", "references", "index", "backmatter"),
            key=lambda family: int(family_counts.get(family, 0)),
        )
        dominant_special_count = int(family_counts.get(dominant_special_family, 0))
        if dominant_special_count and dominant_special_count > body_count:
            return dominant_special_family
        return "body"

    def _section_family_display_title(self, section_family: str) -> str:
        return _section_family_display_title(section_family)

    def _fallback_chapter_title(self, blocks: list[_RecoveredBlock], ordinal: int) -> str:
        for block in blocks:
            if block.role == "heading":
                return block.text
        return f"Chapter {ordinal}"


class PDFParser:
    def __init__(
        self,
        extractor: PdfTextExtractor | None = None,
        profiler: PdfFileProfiler | None = None,
        recovery_service: PdfStructureRecoveryService | None = None,
    ):
        self.extractor = extractor or DefaultPdfTextExtractor()
        self.profiler = profiler or PdfFileProfiler(self.extractor)
        self.recovery_service = recovery_service or PdfStructureRecoveryService()

    def parse(
        self,
        file_path: str | Path,
        profile: PdfFileProfile | dict[str, Any] | None = None,
    ) -> ParsedDocument:
        extraction = self.extractor.extract(file_path)
        if isinstance(profile, PdfFileProfile):
            effective_profile = profile
        elif isinstance(profile, dict):
            effective_profile = PdfFileProfile.from_dict(profile)
        else:
            effective_profile = self.profiler.profile_from_extraction(extraction)
        return self.recovery_service.recover(file_path, extraction, effective_profile)
