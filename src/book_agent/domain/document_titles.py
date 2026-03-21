from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from book_agent.domain.enums import SourceType

_FRONTMATTER_TITLES = {
    "acknowledgment",
    "acknowledgments",
    "acknowledgements",
    "dedication",
    "foreword",
    "introduction",
    "preface",
    "prologue",
}
_BACKMATTER_TITLES = {
    "about the author",
    "about this book",
    "back matter",
    "colophon",
    "copyright",
    "index",
    "reader services",
}
_REFERENCE_TITLES = {
    "bibliography",
    "further reading",
    "notes",
    "references",
    "works cited",
}
_AUXILIARY_TITLES = _FRONTMATTER_TITLES.union(
    _BACKMATTER_TITLES,
    _REFERENCE_TITLES,
    {
        "at a glance",
        "conclusion",
        "conclusions",
        "glossary",
        "key takeaways",
        "practical applications & use cases",
        "visual summary",
    },
)
_STRUCTURAL_TITLE_PATTERN = re.compile(r"^(chapter|part|appendix)\b", re.IGNORECASE)
_SITE_NOISE_TOKEN_PATTERN = re.compile(
    r"\b(?:z-library(?:\.[a-z]{2,})?|1lib\.sk|z-lib\.sk|libgen|annas-archive|pdfcoffee|oceanofpdf)\b",
    re.IGNORECASE,
)
_GENERIC_FILE_STEMS = {
    "book",
    "document",
    "ebook",
    "export",
    "file",
    "sample",
    "scan",
}
_INVALID_FILENAME_CHARACTER_TRANSLATION = str.maketrans(
    {
        "/": "／",
        "\\": "＼",
        ":": "：",
        "*": "＊",
        "?": "？",
        '"': "＂",
        "<": "＜",
        ">": "＞",
        "|": "｜",
    }
)


def _normalize_title_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def compose_document_title(
    main_title: str | None,
    subtitle: str | None,
    *,
    separator: str = ": ",
) -> str | None:
    main = _normalize_title_text(main_title)
    sub = _normalize_title_text(subtitle)
    if not main and not sub:
        return None
    if not main:
        return sub
    if not sub:
        return main
    if main.casefold() == sub.casefold():
        return main
    return f"{main}{separator}{sub}"


def safe_title_for_filename(
    title: str | None,
    *,
    wrap_book_quotes: bool = False,
    fallback: str = "未命名书籍",
) -> str:
    normalized = _normalize_title_text(title) or fallback
    normalized = normalized.translate(_INVALID_FILENAME_CHARACTER_TRANSLATION).strip().strip(".")
    if wrap_book_quotes and normalized and not (normalized.startswith("《") and normalized.endswith("》")):
        normalized = f"《{normalized}》"
    return normalized or fallback


def _looks_like_metadata_filename(value: str) -> bool:
    candidate = _normalize_title_text(value).casefold()
    if not candidate:
        return False
    if "/" in candidate or "\\" in candidate:
        return True
    return candidate.endswith((".html", ".xhtml", ".htm", ".xml", ".opf", ".ncx", ".pdf", ".epub"))


def _looks_like_person_name_fragment(value: str) -> bool:
    normalized = _normalize_title_text(value)
    if not normalized:
        return False
    if any(char.isdigit() for char in normalized):
        return False
    if _SITE_NOISE_TOKEN_PATTERN.search(normalized):
        return False
    words = [word for word in re.split(r"\s+", normalized) if word]
    if not 1 <= len(words) <= 5:
        return False

    alpha_words = [re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ'`.-]", "", word).strip(".-") for word in words]
    alpha_words = [word for word in alpha_words if word]
    if not alpha_words:
        return False
    titleish_count = sum(
        1 for word in alpha_words if word[:1].isupper() and (len(word) == 1 or word[1:].islower())
    )
    return titleish_count >= max(1, len(alpha_words) - 1)


def cleaned_filename_book_title(source_path: str | Path | None) -> str | None:
    if not source_path:
        return None
    stem = Path(str(source_path)).stem
    candidate = _normalize_title_text(stem.replace("_", " "))
    if not candidate:
        return None

    stripped_site_noise = False
    while True:
        match = re.search(r"\(([^()]*)\)\s*$", candidate)
        if match is None:
            break
        group_text = _normalize_title_text(match.group(1))
        if not group_text:
            candidate = _normalize_title_text(candidate[: match.start()])
            continue
        if _SITE_NOISE_TOKEN_PATTERN.search(group_text):
            stripped_site_noise = True
            candidate = _normalize_title_text(candidate[: match.start()])
            continue
        if stripped_site_noise and _looks_like_person_name_fragment(group_text):
            candidate = _normalize_title_text(candidate[: match.start()])
        break

    candidate = candidate.strip(" -_.")
    lowered = candidate.casefold()
    if not candidate or lowered in _GENERIC_FILE_STEMS or _looks_like_metadata_filename(candidate):
        return None
    return candidate


def looks_like_auxiliary_document_title(value: str | None) -> bool:
    normalized = _normalize_title_text(value)
    if not normalized:
        return False
    lowered = normalized.casefold()
    if lowered in _AUXILIARY_TITLES:
        return True
    return bool(_STRUCTURAL_TITLE_PATTERN.match(normalized))


def document_source_title(document) -> str | None:
    return _normalize_title_text(
        getattr(document, "title_src", None) or getattr(document, "title", None)
    ) or None


def document_display_title(document) -> str | None:
    return _normalize_title_text(
        getattr(document, "title_tgt", None)
        or getattr(document, "title_src", None)
        or getattr(document, "title", None)
    ) or None


@dataclass(slots=True, frozen=True)
class ResolvedDocumentTitle:
    title: str | None
    title_src: str | None
    title_tgt: str | None
    resolution_source: str | None


def resolve_document_titles(
    *,
    source_type: SourceType,
    parsed_title: str | None,
    parsed_metadata: dict[str, object] | None,
    source_path: str | Path | None,
    src_lang: str | None,
    tgt_lang: str | None,
    pdf_recovery_lane: str | None = None,
) -> ResolvedDocumentTitle:
    metadata = parsed_metadata or {}
    explicit_src = _normalize_title_text(metadata.get("document_title_src")) or None
    explicit_tgt = _normalize_title_text(metadata.get("document_title_tgt")) or None
    parsed_resolution_source = _normalize_title_text(metadata.get("document_title_resolution_source")) or None
    normalized_parsed_title = _normalize_title_text(parsed_title) or None
    filename_candidate = cleaned_filename_book_title(source_path)

    title_src = explicit_src
    resolution_source = "parsed_metadata" if explicit_src else None
    if title_src is None:
        if source_type == SourceType.EPUB:
            title_src = normalized_parsed_title or filename_candidate
            resolution_source = "parsed_title" if normalized_parsed_title else "source_filename"
        elif pdf_recovery_lane == "academic_paper":
            title_src = normalized_parsed_title or filename_candidate
            resolution_source = (
                parsed_resolution_source
                if normalized_parsed_title and parsed_resolution_source
                else "parsed_title"
                if normalized_parsed_title
                else "source_filename"
            )
        else:
            if normalized_parsed_title and not looks_like_auxiliary_document_title(normalized_parsed_title):
                title_src = normalized_parsed_title
                resolution_source = parsed_resolution_source or "parsed_title"
            elif filename_candidate:
                title_src = filename_candidate
                resolution_source = "source_filename"
            else:
                title_src = normalized_parsed_title
                resolution_source = (
                    parsed_resolution_source
                    if normalized_parsed_title and parsed_resolution_source
                    else "parsed_title"
                    if normalized_parsed_title
                    else None
                )

    title_tgt = explicit_tgt
    if (
        title_tgt is None
        and title_src
        and src_lang
        and tgt_lang
        and _normalize_title_text(src_lang).casefold() == _normalize_title_text(tgt_lang).casefold()
    ):
        title_tgt = title_src

    return ResolvedDocumentTitle(
        title=title_src,
        title_src=title_src,
        title_tgt=title_tgt,
        resolution_source=resolution_source,
    )
