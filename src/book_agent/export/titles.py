"""Chapter title resolution and frontmatter / appendix detection for merged exports."""


from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Protocol

from book_agent.domain.enums import (
    BlockType,
    SourceType,
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
    return resolve_chapter_title(chapter_bundle.document, chapter_bundle.chapter, render_blocks)


class TitleBlock(Protocol):
    """What resolving a title needs of a block: what kind it is and how it reads."""

    block_type: Any
    target_text: str | None
    source_text: str | None


def resolve_chapter_title(
    document: Any,
    chapter: Any,
    blocks: Sequence[TitleBlock],
) -> str | None:
    """The chapter's title as a reader should see it, numbered titles in one form ("第 N 章 标题").

    The export and the interface must say the same thing, so both call this. `blocks` are the
    chapter's blocks in order, carrying the Chinese text where there is one.
    """
    title = _resolve_chapter_title(document, chapter, blocks)
    if document.source_type == SourceType.WEB:
        return title
    return normalize_numbered_title(title, source_title=getattr(chapter, "title_src", None))


def _resolve_chapter_title(
    document: Any,
    chapter: Any,
    blocks: Sequence[TitleBlock],
) -> str | None:
    fallback_title = next(
        (
            str(candidate).strip()
            for candidate in (
                chapter.title_tgt,
                chapter.title_src,
            )
            if str(candidate or "").strip()
        ),
        None,
    )
    render_blocks = blocks
    if _is_pdf_document(document):
        fallback_title = localize_chapter_label(localized_structural_title_fallback(fallback_title))
    if document.source_type == SourceType.WEB:
        # The page is the chapter: its title is the article's own first heading (a byline may
        # come first), not the <title> with the site name appended.
        heading = next(
            (
                block
                for block in render_blocks
                if block.block_type == BlockType.HEADING.value and _normalize_render_text(block.target_text or "")
            ),
            None,
        )
        if heading is not None:
            return str(heading.target_text).strip()
        return document.title_tgt or fallback_title
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
    part_name = _part_name_heading(render_blocks, first_content_block)
    if part_name:
        # "Part 1" with its name set as the next heading (a repair split the name out of the
        # part's introduction): one title, "第 1 部分 构建你的第一个 LLM 智能体".
        return f"{heading_target} {part_name}"
    if render_repair.looks_like_prose_title_text(
        heading_target,
        source_heading_text=first_content_block.source_text,
        fallback_title=fallback_title,
    ):
        return fallback_title
    return heading_target


_BARE_PART_SOURCE = re.compile(r"^\s*part\s+(?:\d{1,2}|[ivx]{1,4}|one|two|three|four|five|six|seven|eight|nine|ten)\s*$", re.IGNORECASE)


def _part_name_heading(blocks: Sequence[TitleBlock], first: TitleBlock) -> str | None:
    """The translated name heading right after a bare "Part N" heading, if there is one."""
    if not _BARE_PART_SOURCE.match(str(first.source_text or "")):
        return None
    following = [block for block in blocks if _normalize_render_text(block.target_text or block.source_text)]
    index = next((position for position, block in enumerate(following) if block is first), None)
    if index is None or index + 1 >= len(following):
        return None
    name = following[index + 1]
    if name.block_type != BlockType.HEADING.value:
        return None
    text = " ".join(str(name.target_text or "").split())
    return text if _CJK_RE.search(text) else None


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ZH_NUMERALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
# "第 5 章　使用 RAG…", "第3章：…", "第十章 …"; "第 1 部分 …", "第二部分：…".
_ZH_CHAPTER_LABEL = re.compile(r"^\s*第\s*(\d{1,3}|[一二三四五六七八九十]{1,3})\s*章\s*[:：.、·\-–—]?\s*")
_ZH_PART_LABEL = re.compile(r"^\s*第\s*(\d{1,2}|[一二三四五六七八九十]{1,3})\s*部分\s*[:：.、·\-–—]?\s*")
_EN_PART_LABEL = re.compile(r"^\s*part\s+(\d{1,2})\b\s*[:：.\-–—]?\s*", re.IGNORECASE)
# A translated Manning title keeps the bare number: "6 为你的智能体添加记忆", "8　用代码执行赋能智能体".
_BARE_TITLE_NUMBER = re.compile(r"^\s*(\d{1,3})(?:\s*[.:：、])?\s+(?=\S)")


def _label_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value in _ZH_NUMERALS:
        return _ZH_NUMERALS[value]
    if len(value) == 2 and value[0] == "十":
        return 10 + _ZH_NUMERALS.get(value[1], 0)
    if len(value) in {2, 3} and value[1] == "十":
        return _ZH_NUMERALS.get(value[0], 0) * 10 + (_ZH_NUMERALS.get(value[2], 0) if len(value) == 3 else 0)
    return None


def normalize_numbered_title(title_text: str | None, *, source_title: str | None = None) -> str | None:
    """A numbered chapter or part title in one form: "第 N 章 标题", "第 N 部分 标题" (normal spaces).

    The translations wrote the same kind of title four ways ("1 什么是 AI 智能体？", "第 3 章 使能行动",
    "第 5 章\u3000使用 RAG…", "第3章：…"). The number comes from the title's own label (Chinese,
    "Chapter N", "Part N") or, for a bare leading number, from the source title ("6 Adding memory to your
    agent"). A title whose rest is still English keeps its form (it is not translated yet); a title
    with no number is only tidied (one space for any run of whitespace).
    """
    if not title_text:
        return title_text
    text = " ".join(title_text.replace("\u3000", " ").split())
    source = " ".join((source_title or "").split())
    part = _ZH_PART_LABEL.match(text) or _EN_PART_LABEL.match(text)
    if part is not None:
        number = _label_number(part.group(1))
        rest = text[part.end():].strip()
        if number is None or (rest and not _CJK_RE.search(rest)):
            return text
        return f"第 {number} 部分 {rest}".strip()
    label = _ZH_CHAPTER_LABEL.match(text) or _LEADING_ENGLISH_CHAPTER_LABEL.match(text)
    number: int | None = None
    rest = text
    if label is not None:
        number = _label_number(label.group(1))
        rest = text[label.end():].strip()
    else:
        source_number = extract_main_chapter_number(source) if source and not looks_like_part_title(source) else None
        bare = _BARE_TITLE_NUMBER.match(text)
        if source_number is not None and bare is not None and int(bare.group(1)) == source_number:
            number, rest = source_number, text[bare.end():].strip()
        elif source_number is not None and bare is None and _CJK_RE.search(text):
            number = source_number
    if number is None or (rest and not _CJK_RE.search(rest)):
        return text
    return f"第 {number} 章 {rest}".strip()


def localize_chapter_label(title_text: str | None) -> str | None:
    """"CHAPTER 11：额外技巧" → "第 11 章 额外技巧"; titles without a Chinese part keep their English label."""
    return normalize_numbered_title(title_text)


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


# Manning-style outlines title a chapter "2 RAG systems and their design": a bare number, then the
# name. A section number ("1.2 Why…") has a dot after its first digits, so it never matches.
_BARE_CHAPTER_NUMBER_PATTERN = re.compile(r"^\s*(\d{1,3})\s+(?=[A-Z])")
_PART_TITLE_PATTERN = re.compile(r"^\s*part\s+(?:\d+|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.IGNORECASE)


def extract_main_chapter_number(title: str | None) -> int | None:
    if not title:
        return None
    stripped = title.strip()
    match = _MAIN_CHAPTER_TITLE_PATTERN.match(stripped)
    if not match:
        bare = _BARE_CHAPTER_NUMBER_PATTERN.match(stripped)
        return int(bare.group(1)) if bare else None
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


def looks_like_part_title(title: str | None) -> bool:
    return bool(title and _PART_TITLE_PATTERN.match(title.strip()))


def looks_like_appendix_title(title: str | None) -> bool:
    return bool(title and _APPENDIX_TITLE_PATTERN.match(title.strip()))


def looks_like_frontmatter_title(title: str | None) -> bool:
    """"Preface", and also a front-matter label with a subtitle: "INTRODUCTION: WHY RSI IS SO MAGICAL?"."""
    normalized = re.sub(r"\s+", " ", (title or "")).strip().casefold()
    if normalized in _FRONTMATTER_TITLES:
        return True
    label = re.split(r"\s*[:：\u2013\u2014]\s*|\s+-\s+", normalized, maxsplit=1)[0]
    return label != normalized and label in _FRONTMATTER_TITLES
