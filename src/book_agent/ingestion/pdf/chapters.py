"""Build chapters from recovered PDF blocks: outline, TOC (with page offsets), section families and chapter-intro pages."""


from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

from book_agent.domain.structure.models import (
    PROVENANCE_OCR,
    PROVENANCE_TEXT_LAYER,
    ParsedBlock,
    ParsedChapter,
    derive_translatability,
)
from book_agent.ingestion.pdf.classify import (
    _contains_chapter_intro_cue,
    _extract_book_main_chapter_number,
    _infer_appendix_intro_title,
    _infer_appendix_subheading_title,
    _infer_intro_page_title,
    _leading_academic_standalone_heading,
    _looks_like_book_primary_outline_title,
    _looks_like_chapter_intro_cue,
    _looks_like_frontmatter_signal,
    _looks_like_outlined_book_appendix_title,
    _looks_like_outlined_book_frontmatter_title,
    _looks_like_outlined_book_glossary_title,
    _looks_like_paper_title,
    _looks_like_title_page_metadata_signal,
    _normalize_outline_title,
    _parse_page_number,
    _section_family_display_title,
    _should_keep_book_top_level_outline_title,
    _should_start_outlined_book_top_level_chapter,
    _titles_overlap,
)
from book_agent.ingestion.pdf.models import (
    PdfFileProfile,
    PdfOutlineEntry,
    PdfPage,
    _ChapterStartCandidate,
    _RecoveredBlock,
)
from book_agent.ingestion.text import _HEADING_PATTERN, _normalize_text


def build_chapters(
    recovered_blocks: list[_RecoveredBlock],
    outline_entries: list[PdfOutlineEntry],
    profile: PdfFileProfile,
    file_path: str | Path,
    *,
    pages: list[PdfPage] | None = None,
) -> list[ParsedChapter]:
    # Page-level sanity lookup (PDF v2 M2.1). Pages default to empty when
    # callers haven't wired the new kwarg; in that case every block keeps
    # its default provenance="text_layer".
    sanity_by_page: dict[int, dict[str, Any]] = {
        page.page_number: dict(page.text_layer_sanity or {})
        for page in (pages or [])
    }
    chapter_starts = chapter_start_candidates(recovered_blocks, outline_entries, profile)
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
        if not has_substantive_content(current_blocks):
            current_blocks = []
            current_title = None
            current_section_family = None
            current_start_page = None
            return
        start_page = current_start_page or current_blocks[0].page_start
        title = current_title or fallback_chapter_title(current_blocks, current_chapter_index)
        chapter_id = f"pdf-chapter-{current_chapter_index:03d}"
        href = f"pdf://page/{start_page}"
        def _build_parsed_block(ordinal: int, block) -> ParsedBlock:
            block_metadata = {
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
                    f"pdf_{block.role}"
                    if block.role in {"header", "footer", "toc_entry"}
                    else "pdf_backmatter"
                    if str(block.metadata.get("pdf_page_family") or "body") == "backmatter"
                    else None
                ),
            }
            # Propagate per-page sanity verdict into ParsedBlock.provenance
            # (PDF v2 M2.1). When the text-layer sanity gate rejected the
            # block's source page, mark provenance advisory-only as
            # PROVENANCE_OCR — the actual router (M2.2) will read this
            # field to decide whether to re-extract via OCR/VLM.
            sanity = sanity_by_page.get(block.page_start, {}) if sanity_by_page else {}
            provenance_value = PROVENANCE_TEXT_LAYER
            confidence_breakdown: dict[str, Any] = {}
            if sanity.get("ok") is False:
                provenance_value = PROVENANCE_OCR
                confidence_breakdown["sanity_ok"] = False
                if sanity.get("reason"):
                    confidence_breakdown["sanity_reason"] = sanity["reason"]
            elif sanity.get("ok") is True:
                confidence_breakdown["sanity_ok"] = True
            return ParsedBlock(
                block_type=block.block_type.value,
                text=block.text,
                source_path=block.source_path,
                ordinal=ordinal,
                anchor=block.anchor,
                metadata=block_metadata,
                parse_confidence=block.parse_confidence,
                translatability=derive_translatability(
                    block.block_type.value, block_metadata
                ),
                provenance=provenance_value,
                confidence_breakdown=confidence_breakdown,
            )

        parsed_blocks = [
            _build_parsed_block(ordinal, block)
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
                    "pdf_section_family": current_section_family or chapter_section_family(current_blocks),
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
            if looks_like_frontmatter_chunk(current_blocks):
                current_title = "Front Matter"
                current_section_family = "frontmatter"
                flush_current()
            else:
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
            and looks_like_frontmatter_chunk(current_blocks)
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
            # Preserve the outline-derived chapter-number prefix when the
            # on-page heading is just the title body (e.g. outline says
            # "1 Big picture: What are LLMs?" but the heading on page is
            # "Big picture: What are LLMs?"). Losing the number prefix
            # later breaks _merge_outlined_book_auxiliary_chapters which
            # uses _extract_book_main_chapter_number(title) to detect
            # main chapters and would otherwise fold them into the
            # previous frontmatter group.
            outline_has_number = _extract_book_main_chapter_number(current_title) is not None
            heading_has_number = _extract_book_main_chapter_number(heading_title) is not None
            if not (outline_has_number and not heading_has_number):
                current_title = heading_title
        elif current_title is None and block.role == "heading":
            current_title = heading_title

        current_blocks.append(block)

    flush_current()
    if chapters and profile.recovery_lane == "outlined_book":
        chapters = merge_outlined_book_auxiliary_chapters(chapters)
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


def chapter_start_candidates(
    recovered_blocks: list[_RecoveredBlock],
    outline_entries: list[PdfOutlineEntry],
    profile: PdfFileProfile,
) -> list[_ChapterStartCandidate]:
    section_candidates = merge_chapter_start_candidates(
        section_family_candidates(recovered_blocks),
        section_family_page_candidates(recovered_blocks),
    )
    if profile.recovery_lane == "academic_paper":
        academic_heading_candidates = find_academic_heading_candidates(recovered_blocks)
        section_candidates = merge_chapter_start_candidates(section_candidates, academic_heading_candidates)
    outline_candidates = [
        _ChapterStartCandidate(page_number=entry.page_number, title=entry.title, source="outline")
        for entry in top_level_outline_entries(outline_entries)
    ]
    if profile.recovery_lane == "outlined_book":
        outline_candidates = [
            candidate
            for candidate in outline_candidates
            if _should_start_outlined_book_top_level_chapter(candidate.title)
        ]
    if outline_candidates:
        return merge_chapter_start_candidates(section_candidates, outline_candidates)

    toc_candidates = find_toc_candidates(recovered_blocks)
    if toc_candidates:
        return merge_chapter_start_candidates(section_candidates, toc_candidates)

    intro_candidates = chapter_intro_page_candidates(recovered_blocks)
    heading_candidates = [
        _ChapterStartCandidate(page_number=block.page_start, title=block.text, source="heading")
        for block in recovered_blocks
        if block.role == "heading" and _HEADING_PATTERN.match(block.text)
    ]
    return merge_chapter_start_candidates(
        section_candidates,
        [*intro_candidates, *heading_candidates],
    )


def find_academic_heading_candidates(
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


def chapter_intro_page_candidates(
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


def section_family_page_candidates(
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
                title=substantive_family_headings.get(page_number, section_family_display_title(page_family)),
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


def section_family_candidates(
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
            section_family_display_title(section_family)
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


def merge_chapter_start_candidates(
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


def toc_page_offsets(
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

    return dominant_nonnegative_offset(title_match_diffs), dominant_nonnegative_offset(footer_diffs)


def dominant_nonnegative_offset(values: list[int]) -> int | None:
    nonnegative = [value for value in values if value >= 0]
    if not nonnegative:
        return None
    counts = Counter(nonnegative)
    return max(counts.items(), key=lambda item: (item[1], -item[0]))[0]


def resolved_toc_page_number(
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


def top_level_outline_entries(outline_entries: list[PdfOutlineEntry]) -> list[PdfOutlineEntry]:
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


def find_toc_candidates(recovered_blocks: list[_RecoveredBlock]) -> list[_ChapterStartCandidate]:
    max_page_number = max((block.page_end for block in recovered_blocks), default=0)
    candidates: list[_ChapterStartCandidate] = []
    seen: set[tuple[int, str]] = set()
    headings = [
        block
        for block in recovered_blocks
        if block.role == "heading" and str(block.metadata.get("pdf_page_family") or "body") != "toc"
    ]
    title_match_offset, footer_offset = toc_page_offsets(recovered_blocks)
    for block in recovered_blocks:
        if block.role != "toc_entry":
            continue
        title = str(block.metadata.get("toc_title") or "").strip()
        printed_page_number = block.metadata.get("toc_page_number")
        if not title or not isinstance(printed_page_number, int):
            continue
        if not _HEADING_PATTERN.match(title):
            continue
        resolved_page_number, resolution_source, page_offset = resolved_toc_page_number(
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


def has_substantive_content(blocks: list[_RecoveredBlock]) -> bool:
    return any(block.role not in {"header", "footer", "toc_entry"} for block in blocks)


def looks_like_frontmatter_chunk(blocks: list[_RecoveredBlock]) -> bool:
    signal_count = 0
    heading_count = 0
    for block in blocks:
        if block.role not in {"body", "heading"}:
            continue
        if block.role == "heading":
            heading_count += 1
        if _looks_like_frontmatter_signal(block.text):
            signal_count += 1
        if _looks_like_title_page_metadata_signal(block.text):
            signal_count += 1
        if signal_count >= 2:
            return True
    if heading_count >= 2 and signal_count >= 1:
        return True
    return False


def chapter_section_family(blocks: list[_RecoveredBlock]) -> str:
    family_counts: Counter[str] = Counter(
        str(block.metadata.get("pdf_page_family") or "body")
        for block in blocks
        if block.role not in {"header", "footer"}
    )
    if looks_like_frontmatter_chunk(blocks):
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


def section_family_display_title(section_family: str) -> str:
    return _section_family_display_title(section_family)


def fallback_chapter_title(blocks: list[_RecoveredBlock], ordinal: int) -> str:
    for block in blocks:
        if block.role == "heading":
            return block.text
    return f"Chapter {ordinal}"


def merge_outlined_book_auxiliary_chapters(
    chapters: list[ParsedChapter],
) -> list[ParsedChapter]:
    merged: list[ParsedChapter] = []
    next_expected_main_chapter = 1
    main_sequence_started = False

    for chapter in chapters:
        title = _normalize_text(chapter.title or "")
        main_chapter_number = _extract_book_main_chapter_number(title)
        starts_appendix = _looks_like_outlined_book_appendix_title(title)
        starts_glossary = _looks_like_outlined_book_glossary_title(title)
        starts_frontmatter = _looks_like_outlined_book_frontmatter_title(title)

        start_new_group = False
        if main_chapter_number is not None:
            if not main_sequence_started:
                start_new_group = main_chapter_number == 1 or not merged
            elif main_chapter_number >= next_expected_main_chapter:
                start_new_group = True
        elif starts_appendix or starts_glossary:
            start_new_group = True
        elif not merged:
            start_new_group = True
        elif not main_sequence_started and starts_frontmatter:
            start_new_group = True

        if start_new_group or not merged:
            merged.append(chapter)
            if main_chapter_number is not None:
                main_sequence_started = True
                next_expected_main_chapter = main_chapter_number + 1
            continue

        merged[-1] = merge_parsed_chapters(merged[-1], chapter)

    return merged


def merge_parsed_chapters(
    primary: ParsedChapter,
    secondary: ParsedChapter,
) -> ParsedChapter:
    merged_blocks = list(primary.blocks)
    starting_ordinal = len(merged_blocks)
    merged_blocks.extend(
        replace(block, ordinal=starting_ordinal + index)
        for index, block in enumerate(secondary.blocks, start=1)
    )
    merged_metadata = {
        **primary.metadata,
        "source_page_end": secondary.metadata.get(
            "source_page_end",
            primary.metadata.get("source_page_end"),
        ),
        "pdf_collapsed_auxiliary_titles": [
            *list(primary.metadata.get("pdf_collapsed_auxiliary_titles") or []),
            *(
                [secondary.title]
                if secondary.title and secondary.title != primary.title
                else []
            ),
            *list(secondary.metadata.get("pdf_collapsed_auxiliary_titles") or []),
        ],
    }
    return replace(primary, blocks=merged_blocks, metadata=merged_metadata)
