"""Chapter title resolution and frontmatter / appendix detection for merged exports."""


from __future__ import annotations

import re

from book_agent.domain.enums import (
    BlockType,
)
from book_agent.export import render_repair
from book_agent.export.common import (
    _APPENDIX_TITLE_PATTERN,
    _LEADING_ENGLISH_CHAPTER_LABEL,
    _FRONTMATTER_TITLE_TRANSLATIONS,
    _FRONTMATTER_TITLES,
    _MAIN_CHAPTER_TITLE_PATTERN,
    _is_pdf_document,
    _normalize_render_text,
)
from book_agent.export.models import (
    MergedRenderBlock,
)
from book_agent.infra.repositories.export import ChapterExportBundle


def should_skip_merged_frontmatter_chapter(
    chapter_bundle: ChapterExportBundle,
    render_blocks: list[MergedRenderBlock],
    title_text: str | None,
) -> bool:
    source_title = source_title_for_chapter(chapter_bundle, title_text)
    if source_title and not looks_like_frontmatter_title(source_title):
        return False
    if any(sentence.translatable for sentence in chapter_bundle.sentences):
        return False
    if not render_blocks:
        return True
    return all(block.render_mode == "image_anchor_with_translated_caption" for block in render_blocks)


def resolved_chapter_title_text(
    chapter_bundle: ChapterExportBundle,
    render_blocks: list[MergedRenderBlock],
) -> str | None:
    fallback_title = next(
        (
            str(candidate).strip()
            for candidate in (
                chapter_bundle.chapter.title_tgt,
                chapter_bundle.chapter.title_src,
            )
            if str(candidate or "").strip()
        ),
        None,
    )
    if _is_pdf_document(chapter_bundle.document):
        fallback_title = localize_chapter_label(localized_structural_title_fallback(fallback_title))
    first_content_block = next(
        (
            block
            for block in render_blocks
            if _normalize_render_text(block.target_text or block.source_text)
        ),
        None,
    )
    if first_content_block is None:
        return fallback_title
    if first_content_block.block_type != BlockType.HEADING.value:
        return fallback_title

    heading_target = localize_chapter_label(str(first_content_block.target_text or "").strip()) or ""
    if not heading_target:
        return fallback_title
    if render_repair.looks_like_prose_title_text(
        heading_target,
        source_heading_text=first_content_block.source_text,
        fallback_title=fallback_title,
    ):
        return fallback_title
    return heading_target


def localize_chapter_label(title_text: str | None) -> str | None:
    """"CHAPTER 11：额外技巧" → "第11章：额外技巧"; titles without a Chinese part keep their English label."""
    if not title_text:
        return title_text
    match = _LEADING_ENGLISH_CHAPTER_LABEL.match(title_text)
    if not match:
        return title_text
    rest = title_text[match.end():].strip()
    if rest and not re.search(r"[\u4e00-\u9fff]", rest):
        return title_text
    return f"第{int(match.group(1))}章：{rest}" if rest else f"第{int(match.group(1))}章"


def localized_structural_title_fallback(title_text: str | None) -> str | None:
    normalized = _normalize_render_text(title_text)
    if not normalized:
        return None
    return _FRONTMATTER_TITLE_TRANSLATIONS.get(normalized.casefold(), normalized)


def source_title_for_chapter(
    chapter_bundle: ChapterExportBundle,
    title_text: str | None,
) -> str:
    return str(chapter_bundle.chapter.title_src or title_text or "").strip()


def extract_main_chapter_number(title: str | None) -> int | None:
    if not title:
        return None
    stripped = title.strip()
    match = _MAIN_CHAPTER_TITLE_PATTERN.match(stripped)
    if not match:
        return None
    remainder = stripped[match.end():]
    normalized_remainder = remainder.lstrip(" .:_-/\\)\u2013\u2014")
    # "Chapter 3 of this book explains…" is prose, not a title. A title continues with a
    # capital letter, a digit ("CHAPTER 9: 20 POPULAR STRATEGIES") or a quote.
    if normalized_remainder and not (
        normalized_remainder[:1].isupper() or normalized_remainder[:1].isdigit() or normalized_remainder[:1] in "\"'\u201c\u2018"
    ):
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def looks_like_appendix_title(title: str | None) -> bool:
    return bool(title and _APPENDIX_TITLE_PATTERN.match(title.strip()))


def looks_like_frontmatter_title(title: str | None) -> bool:
    """"Preface", and also a front-matter label with a subtitle: "INTRODUCTION: WHY RSI IS SO MAGICAL?"."""
    normalized = re.sub(r"\s+", " ", (title or "")).strip().casefold()
    if normalized in _FRONTMATTER_TITLES:
        return True
    label = re.split(r"\s*[:：\u2013\u2014]\s*|\s+-\s+", normalized, maxsplit=1)[0]
    return label != normalized and label in _FRONTMATTER_TITLES
