from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from book_agent.ingestion.pdf.ocr_reextraction import (
        OcrReextractionAdapter,
    )


from book_agent.domain.document_titles import (
    cleaned_filename_book_title,
    looks_like_auxiliary_document_title,
)
from book_agent.domain.enums import BlockType
from book_agent.domain.structure.artifact_grouping import looks_like_artifact_group_context_text
from book_agent.domain.structure.figure_clustering import (
    FigureClusterConfig,
    cluster_figure_regions,
)
from book_agent.domain.structure.models import (
    PROVENANCE_OCR,
    ParsedBlock,
    ParsedChapter,
    ParsedDocument,
)
from book_agent.ingestion.pdf import chapters as pdf_chapters
from book_agent.ingestion.pdf.classify import (
    _CHAPTER_PREFIX_PATTERN,
    _FOOTNOTE_PATTERN,
    _LEADING_SECTION_NUMBER_PATTERN,
    _MULTI_COLUMN_BLOCK_WIDTH_RATIO,
    _OUTLINED_BOOK_FRONTMATTER_TITLES,
    _REFERENCE_CITATION_PATTERN,
    _REFERENCE_YEAR_PATTERN,
    _REFERENCES_HEADING_TITLES,
    _TOC_HEADING_TITLES,
    _appendix_section_subheading_candidate,
    _assess_page_layout,
    _body_contains_footnote_anchor,
    _book_heading_level,
    _contains_appendix_intro_cue,
    _detect_backmatter_cue,
    _embedded_academic_abstract_segments,
    _extract_footnote_marker,
    _header_footer_signature,
    _infer_appendix_intro_title,
    _infer_first_page_paper_title_and_remainder,
    _inline_page_family_heading,
    _is_book_running_header_text,
    _is_page_number_text,
    _leading_all_caps_book_heading_and_remainder,
    _leading_numbered_book_heading_and_remainder,
    _leading_plain_book_heading_and_remainder,
    _leading_reference_heading_and_remainder,
    _looks_like_academic_body_continuation,
    _looks_like_book_prose_fragment,
    _looks_like_contextual_image_legend_text,
    _looks_like_dense_toc_block,
    _looks_like_heading_continuation_fragment,
    _looks_like_index_entry,
    _looks_like_inline_book_heading_text,
    _looks_like_paper_title,
    _looks_like_prose_continuation_fragment,
    _looks_like_reference_entry,
    _looks_like_toc_heading,
    _looks_like_visual_heading,
    _next_academic_inline_heading,
    _normalize_intro_title_artifacts,
    _normalize_outline_title,
    _normalize_paper_title_candidate,
    _normalize_pdf_signal_text,
    _page_extraction_plan,
    _page_family_for_heading,
    _page_has_multi_column_signature,
    _section_family_display_title,
    _split_toc_entry,
    _titles_overlap,
)
from book_agent.ingestion.pdf.extract import (
    DefaultPdfTextExtractor,
    PdfFileProfiler,
    PdfTextExtractor,
)
from book_agent.ingestion.pdf.models import (
    PdfExtraction,
    PdfFileProfile,
    PdfImageBlock,
    PdfOutlineEntry,
    PdfPage,
    PdfTextBlock,
    _PageLayoutAssessment,
    _PageRecoveryContext,
    _RecoveredBlock,
    _TocEntryCandidate,
)
from book_agent.ingestion.text import (
    _CODE_CONTROL_LINE_PATTERN,
    _HEADING_PATTERN,
    _TERMINAL_PUNCTUATION,
    _bbox_to_json,
    _caption_matches_artifact_role,
    _dense_toc_line_count,
    _expanded_code_candidate_lines,
    _has_monospace_font,
    _has_prose_body_font,
    _has_unterminated_quoted_string,
    _has_unterminated_triple_quoted_string,
    _looks_like_academic_prose_lead,
    _looks_like_caption_text,
    _looks_like_code,
    _looks_like_code_comment_text,
    _looks_like_code_continuation_line,
    _looks_like_code_docstring_line,
    _looks_like_code_docstring_text,
    _looks_like_embedded_code_line,
    _looks_like_equation,
    _looks_like_figure_caption,
    _looks_like_list_item,
    _looks_like_numeric_table_fragment,
    _looks_like_prose_line_group,
    _looks_like_shell_command_continuation_line,
    _looks_like_shell_command_line,
    _looks_like_splitworthy_single_line_code_fragment,
    _looks_like_table,
    _normalize_multiline_text,
    _normalize_text,
    _safe_mean,
    _sorted_counter,
)

_OUTLINED_BOOK_TOP_LEVEL_SPECIAL_TITLES = _OUTLINED_BOOK_FRONTMATTER_TITLES.union({"glossary"})
_APPENDIX_SUBHEADING_PATTERN = re.compile(r"^(?P<label>[A-Z]\.\d+)\s+(?P<title>.+)$")
# Manning-style numbered listings. Matches "Listing 4.1 ...", "Listing
# 10.12 ...". Other publishers' "Example 1", "Code Snippet 2A", "示例 1"
# do not match — keeps the scope-locked behaviour from polluting books
# with different conventions.
_LISTING_TITLE_RE = re.compile(r"^Listing\s+\d+(?:\.\d+)+\b", re.IGNORECASE)

# Trailing function-call expression typical at the end of a listing
# annotation that was concatenated with the final code line by the PDF
# extractor. Matches print(calculate_pi(1000000)), foo.bar(1,2,3), etc.
# Supports a single level of nested call.
_LISTING_ANNOTATION_CODE_TAIL_RE = re.compile(
    r"(?<![A-Za-z_])"
    r"((?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*"
    r"\((?:[^()]*"                              # plain args
    r"|[A-Za-z_][A-Za-z0-9_]*\([^)]*\))*"       # or nested call(args)
    r"\))"
)

# Code-line starters typical of programming-language source. If a
# paragraph block within a Listing scope starts with one of these, it
# is almost certainly a code line that was misclassified as paragraph
# — keep it (don't suppress) but re-tag as code_like.
_LISTING_CODE_LINE_STARTERS_RE = re.compile(
    r"^(?:IMPORT|MODULE|PROCEDURE|VAR|BEGIN|END|FOR|IF|ELSE|RETURN|TYPE|CONST|FROM|"
    r"import|from|def|class|if|else|elif|for|while|return|try|except|with|"
    r"int|float|double|void|public|private|static|new)\b",
)

# Short prose-style annotation patterns (callout phrases the PDF puts
# next to code with an arrow). Must look like English prose — starts
# with capital and contains a verb or modal. The "** isn't an
# operator." pattern (starting with code symbols) is also accepted.
_LISTING_ANNOTATION_PROSE_RE = re.compile(
    r"^(?:"
    r"\*\*\s+\w"                                    # "** isn't an operator."
    r"|[A-Z][A-Za-z_]*\s+(?:can|will|is|are|isn'?t|"
    r"aren'?t|has|have|does|do|may|might|should|must|"
    r"takes?|holds?|points?|matches?|sets?|returns?|sounds?)\s"
    r"|[A-Z][a-z]+\s+(?:the|a|an|to|from|with|by|of|in|on|for|via|"
    r"that|this|these|those)\s+\w"
    r"|(?:Tests?|Note|See|Returns?|Computes?|Calculates?|Uses?|"
    r"Forces?|Drops?|Adds?|Removes?|Sets?|Gets?|Missing|Optional|"
    r"Required|Required:|Optional:)\s+\w"
    r")",
)


class PdfStructureRecoveryService:
    def __init__(
        self,
        *,
        ocr_reextraction_adapter: "OcrReextractionAdapter | None" = None,
        figure_cluster_config: "FigureClusterConfig | None" = None,
    ) -> None:
        # M2.3a: adapter for re-extracting blocks whose source page failed
        # the text-layer sanity gate. Default None = no re-extraction
        # (current behavior preserved for all in-tree callers).
        self._ocr_reextraction_adapter = ocr_reextraction_adapter
        # Figure clustering config — None means defaults. Wired from
        # Settings in build_default_recovery_service.
        self._figure_cluster_config = figure_cluster_config

    def recover(
        self,
        file_path: str | Path,
        extraction: PdfExtraction,
        profile: PdfFileProfile,
    ) -> ParsedDocument:
        ordered_pages = sorted(extraction.pages, key=lambda page: page.page_number)
        context = RecoveryContext(
            file_path=file_path,
            extraction=extraction,
            profile=profile,
            ordered_pages=ordered_pages,
            page_contexts=self._page_contexts(ordered_pages),
            page_layout_assessments=self._page_layout_assessments(ordered_pages, profile, extraction.title),
        )
        page_contexts = context.page_contexts
        page_layout_assessments = context.page_layout_assessments
        recovered_blocks = self._recover_blocks(
            ordered_pages,
            self._find_repeated_edge_text(ordered_pages),
            page_contexts,
            extraction.outline_entries,
            profile,
        )
        for block_pass in _BLOCK_RECOVERY_PASSES:
            recovered_blocks = block_pass.run(self, recovered_blocks, context)
        chapters = pdf_chapters.build_chapters(
            recovered_blocks,
            extraction.outline_entries,
            profile,
            file_path,
            pages=ordered_pages,
        )
        chapters = self._repair_academic_first_page_abstract_continuations(
            chapters,
            ordered_pages,
            page_layout_assessments,
            profile,
        )

        # PDF v2 M2.3a: re-extract blocks on sanity-failed pages through
        # the configured OCR adapter. Default adapter is None → no-op,
        # preserving backward compatibility for callers that haven't
        # opted in.
        if self._ocr_reextraction_adapter is not None:
            chapters = self._apply_ocr_reextraction(
                chapters,
                pdf_path=str(file_path),
            )

        inferred_title = self._infer_document_title_from_recovered_blocks(recovered_blocks)
        filename_title = cleaned_filename_book_title(file_path)
        if profile.recovery_lane == "academic_paper":
            title = extraction.title or inferred_title or (chapters[0].title if chapters else None) or filename_title
        else:
            title = extraction.title or inferred_title
            if title and looks_like_auxiliary_document_title(title):
                title = None
            title = title or filename_title
        if title and chapters and chapters[0].title and _titles_overlap(chapters[0].title, title):
            chapters[0] = replace(chapters[0], title=title)
        metadata = {
            **extraction.metadata,
            "pdf_profile": profile.to_dict(),
            "outline_entry_count": len(extraction.outline_entries),
            "document_title_resolution_source": (
                "extraction_metadata"
                if title and title == extraction.title
                else "recovered_heading"
                if title and title == inferred_title
                else "source_filename"
                if title and title == filename_title
                else None
            ),
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

    def _apply_ocr_reextraction(
        self,
        chapters: list[ParsedChapter],
        *,
        pdf_path: str,
    ) -> list[ParsedChapter]:
        """Route blocks with sanity_ok=False through the OCR adapter (M2.3a).

        For each such block the adapter may return a replacement text
        keyed by the block's anchor; when present, we rebuild the block
        with the new text, flip `confidence_breakdown.sanity_ok` to True
        (the new provenance is authoritative), and keep
        `provenance=PROVENANCE_OCR`. Blocks the adapter omits are left
        unchanged — silence is "no confident replacement available."
        """
        from dataclasses import replace as _replace

        from book_agent.ingestion.pdf.ocr_reextraction import (
            OcrReextractionRequest,
        )

        requests: list[OcrReextractionRequest] = []
        for chapter in chapters:
            for block in chapter.blocks:
                if block.confidence_breakdown.get("sanity_ok") is not False:
                    continue
                if not block.anchor:
                    continue
                page_number = int(
                    block.metadata.get("source_page_start") or 0
                )
                if page_number <= 0:
                    continue
                bbox_regions = (
                    block.metadata.get("source_bbox_json", {}) or {}
                ).get("regions") or []
                bbox_value = (
                    bbox_regions[0].get("bbox")
                    if bbox_regions and isinstance(bbox_regions[0], dict)
                    else None
                )
                if not bbox_value or len(bbox_value) < 4:
                    bbox_tuple: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
                else:
                    bbox_tuple = (
                        float(bbox_value[0]),
                        float(bbox_value[1]),
                        float(bbox_value[2]),
                        float(bbox_value[3]),
                    )
                requests.append(
                    OcrReextractionRequest(
                        block_anchor=block.anchor,
                        page_number=page_number,
                        bbox=bbox_tuple,
                        current_text=block.text,
                        failure_reason=str(
                            block.confidence_breakdown.get("sanity_reason") or "unknown"
                        ),
                    )
                )

        if not requests:
            return chapters

        adapter = self._ocr_reextraction_adapter
        assert adapter is not None  # guarded by caller
        replacements = adapter.reextract_blocks(pdf_path, requests) or {}
        if not replacements:
            return chapters

        new_chapters: list[ParsedChapter] = []
        for chapter in chapters:
            rebuilt_blocks: list[ParsedBlock] = []
            changed = False
            for block in chapter.blocks:
                if block.anchor and block.anchor in replacements:
                    new_text = replacements[block.anchor]
                    if new_text and new_text != block.text:
                        new_confidence = dict(block.confidence_breakdown)
                        new_confidence["sanity_ok"] = True
                        new_confidence["reextracted_via"] = "ocr_adapter"
                        rebuilt_blocks.append(
                            _replace(
                                block,
                                text=new_text,
                                provenance=PROVENANCE_OCR,
                                confidence_breakdown=new_confidence,
                            )
                        )
                        changed = True
                        continue
                rebuilt_blocks.append(block)
            if changed:
                new_chapters.append(_replace(chapter, blocks=rebuilt_blocks))
            else:
                new_chapters.append(chapter)
        return new_chapters

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
            page_context = page_contexts.get(page.page_number, _PageRecoveryContext(is_toc_page=False))
            for entry_kind, raw_entry in self._ordered_page_content_entries(page, profile):
                reading_order_index += 1
                if entry_kind == "text":
                    raw_block = raw_entry
                    role = self._classify_role(
                        page=page,
                        raw_block=raw_block,
                        page_font_median=page_font_median,
                        repeated_edge_text=repeated_edge_text,
                        outline_titles=outline_by_page.get(page.page_number, []),
                        page_context=page_context,
                    )
                    metadata, flags = self._metadata_for_block(role, raw_block.text, page_context)
                    # Font metadata enrichment
                    metadata["pdf_font_names"] = sorted(raw_block.font_names) if raw_block.font_names else []
                    if raw_block.font_names and _has_monospace_font(raw_block.font_names):
                        metadata["has_monospace_font"] = True
                        if role == "code_like":
                            metadata["detected_by"] = "monospace_font"
                    # For code blocks with preserved whitespace, use raw_text
                    # from clip extraction instead of the normalized version.
                    if role == "code_like" and raw_block.raw_text:
                        block_text = raw_block.raw_text
                        metadata["raw_text"] = raw_block.raw_text
                    else:
                        block_text = self._normalized_block_text(
                            raw_block.text,
                            role=role,
                            page_number=page.page_number,
                            page_context=page_context,
                        )
                    recovered_block = _RecoveredBlock(
                        role=role,
                        block_type=self._block_type_for_role(role),
                        text=block_text,
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
                    continue

                raw_image = raw_entry
                metadata, flags = self._metadata_for_block("image", "[Image]", page_context)
                metadata.update(
                    {
                        "image_type": raw_image.image_type,
                        "image_ext": raw_image.image_ext,
                        "image_width_px": raw_image.width_px,
                        "image_height_px": raw_image.height_px,
                    }
                )
                if raw_image.materialized_path:
                    metadata["image_path"] = raw_image.materialized_path
                    metadata["image_xref"] = raw_image.xref
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

    def _ordered_page_content_entries(
        self,
        page: PdfPage,
        profile: PdfFileProfile,
    ) -> list[tuple[str, PdfTextBlock | PdfImageBlock]]:
        ordered_text_blocks = self._ordered_page_blocks(page, profile)
        ordered_image_blocks = self._ordered_page_image_blocks(page)
        if profile.recovery_lane == "academic_paper":
            return [
                *[("text", block) for block in ordered_text_blocks],
                *[("image", block) for block in ordered_image_blocks],
            ]

        combined_entries: list[tuple[str, PdfTextBlock | PdfImageBlock]] = [
            *[("text", block) for block in ordered_text_blocks],
            *[("image", block) for block in ordered_image_blocks],
        ]
        combined_entries.sort(
            key=lambda item: (
                round(item[1].bbox[1], 2),
                round(item[1].bbox[0], 2),
                0 if item[0] == "image" else 1,
                getattr(item[1], "block_number", 0),
            )
        )
        return combined_entries

    def _ordered_page_image_blocks(self, page: PdfPage) -> list[PdfImageBlock]:
        return sorted(page.image_blocks, key=lambda block: (round(block.bbox[1], 2), round(block.bbox[0], 2)))

    def _ordered_page_blocks(
        self,
        page: PdfPage,
        profile: PdfFileProfile,
    ) -> list[PdfTextBlock]:
        ordered_blocks = sorted(page.blocks, key=lambda block: (round(block.bbox[1], 2), round(block.bbox[0], 2)))
        # PDF v2 M1.3: apply column-major reordering on ANY lane when the
        # multi-column signature is present. The helper already bails out
        # (returns None) when the page can't be cleanly grouped, so the
        # top-down fallback remains authoritative for ambiguous layouts.
        if not _page_has_multi_column_signature(page):
            return ordered_blocks
        column_major_blocks = self._column_major_blocks(page, ordered_blocks)
        return column_major_blocks or ordered_blocks

    def _column_major_blocks(
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
            extraction_plan = _page_extraction_plan(
                page,
                profile=profile,
                page_layout_assessment=layout_assessment,
                page_context=context,
            )
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
                    "extraction_intent": extraction_plan.intent,
                    "extraction_intent_scope": extraction_plan.scope,
                    "extraction_intent_reasons": list(extraction_plan.reasons),
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
                if context.content_family == "references" and not context.has_strong_heading:
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
        dense_toc_line_total = 0
        dense_toc_block_count = 0
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
            dense_toc_line_total += _dense_toc_line_count(block.text)
            if _looks_like_dense_toc_block(block.text, block.line_count):
                dense_toc_block_count += 1
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
        is_toc_page = (
            (heading_present and bool(toc_entries_by_text))
            or len(toc_entries_by_text) >= 3
            or dense_toc_line_total >= 3
            or (heading_present and dense_toc_block_count >= 1)
            or dense_toc_block_count >= 2
        )
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
        if page_context.is_toc_page and _looks_like_toc_heading(text):
            return "toc_entry"
        if page_context.is_toc_page and text.casefold() in page_context.toc_entries_by_text:
            return "toc_entry"
        if zone == "bottom" and raw_block.font_size_avg and raw_block.font_size_avg < page_font_median * 0.95:
            if _FOOTNOTE_PATTERN.match(text):
                return "footnote"
        if _looks_like_caption_text(text):
            return "caption"
        if _normalize_outline_title(text) in outline_titles:
            return "heading"
        if _HEADING_PATTERN.match(text) and len(text) <= 150:
            return "heading"
        if (
            raw_block.font_size_max >= page_font_median * 1.25
            and len(text) <= 120
            and _looks_like_visual_heading(text, raw_block.line_count)
        ):
            if zone != "bottom":
                return "heading"
        if _looks_like_table(raw_block.line_count, raw_block.line_texts) or _looks_like_numeric_table_fragment(raw_block.line_texts):
            return "table_like"
        if _looks_like_equation(text, raw_block.line_count, raw_block.bbox, page.width):
            return "equation"
        if page_context.is_toc_page and _looks_like_dense_toc_block(raw_block.text, raw_block.line_count):
            return "list_item"
        if _looks_like_list_item(text, raw_block.line_count):
            return "list_item"
        if _looks_like_code(text, raw_block.line_count):
            return "code_like"
        # Font-based code detection: a block that uses ONLY monospace
        # font(s) is almost certainly real code. But many book pages
        # use a monospace font for inline identifiers ("…obtaining
        # text input as a `string` data type…") while the surrounding
        # prose stays in a regular body font. If a prose body font
        # (Baskerville / Garamond / Times / Helvetica / Franklin) is
        # present alongside the monospace one, this is body text with
        # inline-code highlights — NOT a code block. Without this
        # guard, list items like "1 Receiving the text to process—
        # This means obtaining text input as a string data type…" got
        # mis-tagged as code and dropped from the translation pipeline.
        if (
            raw_block.font_names
            and _has_monospace_font(raw_block.font_names)
            and raw_block.line_count >= 2
            and not _has_prose_body_font(raw_block.font_names)
        ):
            return "code_like"
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
        if role == "list_item":
            return BlockType.LIST_ITEM
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
        # Format metadata enrichment
        if role == "code_like":
            metadata["detected_by"] = "text_heuristic"
        if role == "list_item":
            metadata["detected_by"] = "bullet_pattern"
        if role == "heading":
            heading_level = _book_heading_level(text)
            if heading_level is None:
                if text.casefold() in _REFERENCES_HEADING_TITLES or page_context.page_family == "references":
                    heading_level = 2
                elif page_context.page_family == "body":
                    heading_level = 1 if _looks_like_paper_title(text) else 2
            if heading_level is not None:
                metadata["heading_level"] = heading_level

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
        if previous.role not in {"body", "list_item"} or current.role != "body":
            return False
        if previous.block_type not in {BlockType.PARAGRAPH, BlockType.LIST_ITEM} or current.block_type != BlockType.PARAGRAPH:
            return False
        if current.page_start - previous.page_end > 1:
            return False
        page_lookup = {page.page_number: page for page in pages}
        if current.page_start == previous.page_end:
            if self._should_keep_inline_book_heading_separate(previous, current):
                return False
            if self._looks_like_contextual_image_legend_block(previous, page_lookup):
                return False
            prev_bottom = previous.bbox_regions[-1]["bbox"][3]
            curr_top = current.bbox_regions[0]["bbox"][1]
            gap = curr_top - prev_bottom
            if gap <= max(previous.font_size_avg * 1.8, 18.0):
                return previous.text.rstrip().endswith("-") or not previous.text.rstrip().endswith(
                    _TERMINAL_PUNCTUATION
                )
            return False
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

    def _should_keep_inline_book_heading_separate(
        self,
        previous: _RecoveredBlock,
        current: _RecoveredBlock,
    ) -> bool:
        if previous.page_start != current.page_start:
            return False
        if previous.page_start != previous.page_end:
            return False
        if str(previous.metadata.get("pdf_page_family") or "body") != "body":
            return False
        if str(current.metadata.get("pdf_page_family") or "body") != "body":
            return False
        if not _looks_like_inline_book_heading_text(previous.text):
            return False
        if not _looks_like_book_prose_fragment(current.text):
            return False
        if previous.font_size_avg < max(current.font_size_avg * 1.08, current.font_size_avg + 0.6):
            return False
        previous_bbox = previous.bbox_regions[-1]["bbox"]
        current_bbox = current.bbox_regions[0]["bbox"]
        gap = float(current_bbox[1]) - float(previous_bbox[3])
        if gap > max(previous.font_size_avg * 2.6, 32.0):
            return False
        return abs(float(previous_bbox[0]) - float(current_bbox[0])) <= 72.0

    def _looks_like_contextual_image_legend_block(
        self,
        block: _RecoveredBlock,
        page_lookup: dict[int, PdfPage],
    ) -> bool:
        if block.role != "body" or block.block_type != BlockType.PARAGRAPH:
            return False
        if block.page_start != block.page_end:
            return False
        if str(block.metadata.get("pdf_page_family") or "body") != "body":
            return False
        if not _looks_like_contextual_image_legend_text(block.text):
            return False
        page = page_lookup.get(block.page_start)
        if page is None:
            return False
        block_bbox = self._page_bbox(block, block.page_start)
        if block_bbox is None:
            return False
        block_width = max(block_bbox[2] - block_bbox[0], 1.0)
        block_center = (block_bbox[0] + block_bbox[2]) / 2.0
        if block_width > page.width * 0.58:
            return False
        if abs(block_center - (page.width / 2.0)) > page.width * 0.2:
            return False

        for image in page.image_blocks:
            image_bbox = [float(value) for value in image.bbox]
            if self._horizontal_overlap_ratio(block_bbox, image_bbox) < 0.25:
                continue
            below_gap = block_bbox[1] - image_bbox[3]
            above_gap = image_bbox[1] - block_bbox[3]
            if -8.0 <= below_gap <= 48.0 or -8.0 <= above_gap <= 32.0:
                return True
        return False

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
        code_merge = previous.block_type == BlockType.CODE and current.block_type == BlockType.CODE
        if code_merge:
            previous_text = previous.text.rstrip("\n")
            current_text = current.text.lstrip("\n")
        else:
            previous_text = previous.text.rstrip()
            current_text = current.text.lstrip()

        if not code_merge and previous_text.endswith("-") and current_text[:1].islower():
            merged_text = previous_text[:-1] + current_text
            flags.append("dehyphenated")
        else:
            separator = "\n" if code_merge else ("" if previous_text.endswith(" ") else " ")
            merged_text = previous_text + separator + current_text

        if current.page_start > previous.page_end:
            flags.append("cross_page_repaired")

        merged_flags = list(dict.fromkeys(flags + current.flags))
        merged_metadata = {**previous.metadata, **current.metadata}
        # When merging code blocks that both carry raw_text, concatenate
        # preserving the original whitespace from both regions.
        if code_merge:
            prev_raw = previous.metadata.get("raw_text")
            curr_raw = current.metadata.get("raw_text")
            if prev_raw or curr_raw:
                merged_metadata["raw_text"] = (
                    (prev_raw or previous.text).rstrip("\n")
                    + "\n"
                    + (curr_raw or current.text).lstrip("\n")
                )
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
            metadata=merged_metadata,
            font_size_avg=_safe_mean([previous.font_size_avg, current.font_size_avg]),
            source_path=previous.source_path,
            anchor=previous.anchor,
        )

    def _merge_adjacent_heading_continuations(
        self,
        recovered_blocks: list[_RecoveredBlock],
        pages: list[PdfPage] | None = None,
    ) -> list[_RecoveredBlock]:
        merged: list[_RecoveredBlock] = []
        for current in recovered_blocks:
            if merged and self._should_merge_heading_continuation(merged[-1], current, pages):
                merged[-1] = self._merge_heading_blocks(merged[-1], current)
                continue
            merged.append(current)
        return merged

    def _should_merge_heading_continuation(
        self,
        previous: _RecoveredBlock,
        current: _RecoveredBlock,
        pages: list[PdfPage] | None = None,
    ) -> bool:
        if previous.role != "heading" or current.role != "heading":
            return False
        if previous.block_type != BlockType.HEADING or current.block_type != BlockType.HEADING:
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

        # Font size ratio check (applies to both same-page and cross-page)
        previous_size = max(previous.font_size_avg, 1.0)
        current_size = max(current.font_size_avg, 1.0)
        ratio = current_size / previous_size
        if ratio < 0.8 or ratio > 1.25:
            return False

        # --- Same-page merge ---
        same_page = (
            previous.page_start == current.page_start
            and previous.page_end == current.page_end
            and previous.page_start == previous.page_end
        )
        if same_page:
            previous_bbox = previous.bbox_regions[-1]["bbox"]
            current_bbox = current.bbox_regions[0]["bbox"]
            gap = float(current_bbox[1]) - float(previous_bbox[3])
            if gap > max(min(previous.font_size_avg, current.font_size_avg) * 1.8, 18.0):
                return False
            if abs(float(previous_bbox[0]) - float(current_bbox[0])) > 48.0:
                return False
            return True

        # --- Cross-page heading merge ---
        if not pages:
            return False
        if current.page_start != previous.page_end + 1:
            return False

        page_lookup = {page.page_number: page for page in pages}
        prev_page = page_lookup.get(previous.page_end)
        curr_page = page_lookup.get(current.page_start)
        if prev_page is None or curr_page is None:
            return False

        previous_bbox = self._page_bbox(previous, previous.page_end)
        current_bbox = self._page_bbox(current, current.page_start)
        if previous_bbox is None or current_bbox is None:
            return False

        prev_bottom = float(previous_bbox[3])
        curr_top = float(current_bbox[1])
        if prev_bottom < prev_page.height * 0.72:
            return False
        if curr_top > curr_page.height * 0.28:
            return False

        # Mark as cross-page heading merge
        previous.flags = list(dict.fromkeys([*previous.flags, "cross_page_heading_merged"]))
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

    def _merge_same_anchor_code_continuations(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> list[_RecoveredBlock]:
        merged: list[_RecoveredBlock] = []
        for current in recovered_blocks:
            if merged and self._should_merge_same_anchor_code_continuation(merged[-1], current):
                merged_block = self._merge_blocks(merged[-1], current)
                merged_block.flags = list(
                    dict.fromkeys([*merged_block.flags, "same_anchor_code_continuation_merged"])
                )
                merged[-1] = merged_block
                continue
            merged.append(current)
        return merged

    def _merge_cross_page_code_continuations(
        self,
        recovered_blocks: list[_RecoveredBlock],
        pages: list[PdfPage],
    ) -> list[_RecoveredBlock]:
        merged: list[_RecoveredBlock] = []
        for current in recovered_blocks:
            target_index = self._cross_page_code_merge_target_index(merged, current, pages)
            if target_index is not None:
                merged_block = self._merge_blocks(merged[target_index], current)
                merged_block.flags = list(
                    dict.fromkeys([*merged_block.flags, "cross_page_code_continuation_merged"])
                )
                merged[target_index] = merged_block
                continue
            merged.append(current)
        return merged

    def _cross_page_code_merge_target_index(
        self,
        recovered: list[_RecoveredBlock],
        current: _RecoveredBlock,
        pages: list[PdfPage],
    ) -> int | None:
        if not recovered:
            return None
        index = len(recovered) - 1
        while index >= 0 and self._is_ignorable_code_separator(recovered[index]):
            index -= 1
        if index < 0:
            return None
        if any(not self._is_ignorable_code_separator(block) for block in recovered[index + 1 :]):
            return None
        if self._should_merge_cross_page_code_continuation(recovered[index], current, pages):
            return index
        return None

    def _is_ignorable_code_separator(self, block: _RecoveredBlock) -> bool:
        if block.role in {"header", "footer"}:
            return True
        return block.block_type == BlockType.PARAGRAPH and _is_page_number_text(block.text)

    def _should_merge_cross_page_code_continuation(
        self,
        previous: _RecoveredBlock,
        current: _RecoveredBlock,
        pages: list[PdfPage],
    ) -> bool:
        if previous.role != "code_like" or current.role != "code_like":
            return False
        if previous.block_type != BlockType.CODE or current.block_type != BlockType.CODE:
            return False
        if previous.source_path != current.source_path:
            return False
        if current.page_start != previous.page_end + 1:
            return False
        if str(previous.metadata.get("pdf_page_family") or "body") != "body":
            return False
        if str(current.metadata.get("pdf_page_family") or "body") != "body":
            return False

        page_lookup = {page.page_number: page for page in pages}
        previous_page = page_lookup.get(previous.page_end)
        current_page = page_lookup.get(current.page_start)
        if previous_page is None or current_page is None:
            return False

        previous_bbox = self._page_bbox(previous, previous.page_end)
        current_bbox = self._page_bbox(current, current.page_start)
        if previous_bbox is None or current_bbox is None:
            return False
        prev_bottom = float(previous_bbox[3])
        curr_top = float(current_bbox[1])
        if prev_bottom < previous_page.height * 0.68:
            return False
        if curr_top > current_page.height * 0.34:
            return False
        if (
            self._horizontal_overlap_ratio(previous_bbox, current_bbox) < 0.25
            and abs(float(previous_bbox[0]) - float(current_bbox[0])) > 96.0
        ):
            return False

        previous_lines = _expanded_code_candidate_lines(previous.text)
        current_lines = _expanded_code_candidate_lines(current.text)
        if not previous_lines or not current_lines:
            return False
        if _looks_like_code_continuation_line(current_lines[0], previous_lines):
            return True

        previous_last_line = previous_lines[-1].strip()
        if previous_last_line.endswith(("(", "[", "{", ",", "\\", "+", "|", "=")):
            return True
        if _CODE_CONTROL_LINE_PATTERN.match(previous_last_line):
            return True
        if _looks_like_shell_command_line(previous_last_line) and _looks_like_shell_command_continuation_line(current_lines[0]):
            return True
        if previous_last_line.startswith("# ---") and current_lines[0].strip() == "---":
            return True

        previous_joined = "\n".join(previous_lines)
        if (
            _has_unterminated_triple_quoted_string(previous_joined)
            or _has_unterminated_quoted_string(previous_joined, '"')
            or _has_unterminated_quoted_string(previous_joined, "'")
        ):
            return True

        # Fallback: if both blocks are code_like and spatially adjacent,
        # merge even without syntactic continuation signals
        previous_line_count = len(previous_lines)
        current_line_count = len(current_lines)
        if previous_line_count >= 2 and current_line_count >= 2:
            # Both are substantial code blocks — merge if they share similar
            # indentation or formatting characteristics
            prev_indent_chars = sum(1 for line in previous_lines if line.startswith((' ', '\t')))
            curr_indent_chars = sum(1 for line in current_lines if line.startswith((' ', '\t')))
            # At least one block has indented lines (looks like structured code)
            if prev_indent_chars >= 1 or curr_indent_chars >= 1:
                return True
            # Or both blocks have code-like lines (assignments, punctuation, keywords)
            prev_code_signals = sum(1 for line in previous_lines if _looks_like_embedded_code_line(line.strip()))
            curr_code_signals = sum(1 for line in current_lines if _looks_like_embedded_code_line(line.strip()))
            if prev_code_signals >= previous_line_count * 0.5 and curr_code_signals >= current_line_count * 0.5:
                return True

        return False

    def _lock_listing_scope(
        self, recovered_blocks: list[_RecoveredBlock]
    ) -> list[_RecoveredBlock]:
        """Clean up Manning-style ``Listing N.M`` code-snippet regions.

        This book uses styled ``Listing 4.1   ChatGPT calculating pi in
        Python`` title bars over code samples. The PDF text extractor
        often interleaves the listing's side-annotations (e.g. "Tests
        the function; the more terms, the more accurate the
        approximation") with code lines, sometimes concatenating an
        annotation with the trailing code line into a single paragraph
        block:

            "Tests the function; the more terms,\\n
             the more accurate the approximation print(calculate_pi(...))"

        The end result: the actual print() call gets stripped from the
        code block AND glued onto the annotation prose, breaking both
        the listing render and the body translation.

        Strategy: scope-locked by ``^Listing\\s+\\d+(?:\\.\\d+)+\\b``
        pattern (Manning convention — does NOT match generic "Example",
        "Code Snippet" or non-numbered listings used by other
        publishers). Within the scope we

        1. detect paragraph blocks whose *tail* matches a code-shaped
           function-call suffix (``identifier(...)``) and split that
           tail back into a synthetic code block;
        2. flag the remaining annotation prefix with
           ``pdf_listing_annotation_suppressed`` so the exporter can
           skip rendering it (the annotation is decorative — it points
           into the code with an arrow in the PDF, and its translation
           would only confuse the reader).
        """
        if not recovered_blocks:
            return recovered_blocks

        # Pre-pass: when a paragraph block within a Listing scope vicinity
        # contains BOTH a code-call expression AND a subsequent capital-
        # letter sentence (annotation + code + body glued by upstream
        # cross-page merge), split it into [annotation_or_code, body].
        recovered_blocks = self._split_listing_artifact_continuations(recovered_blocks)

        result: list[_RecoveredBlock] = []
        i = 0
        n = len(recovered_blocks)
        while i < n:
            block = recovered_blocks[i]
            text = (block.text or "").strip()
            if not _LISTING_TITLE_RE.match(text):
                result.append(block)
                i += 1
                continue

            # Walk forward up to 16 blocks to find scope end. Scope ends
            # on: heading, image, figure, caption, table_like, next
            # listing header, or first long prose paragraph (≥250 chars
            # with ≥2 sentence breaks) that doesn't fit code-shape.
            result.append(block)  # title block stays as-is
            scope_blocks: list[_RecoveredBlock] = []
            j = i + 1
            scope_end = min(i + 16, n)
            while j < scope_end:
                nxt = recovered_blocks[j]
                nxt_text = (nxt.text or "").strip()
                if nxt.role in {"heading", "image", "figure", "caption", "table_like", "header", "footer"}:
                    break
                if _LISTING_TITLE_RE.match(nxt_text):
                    break
                # Long prose paragraph = scope end.
                if (
                    nxt.role == "body"
                    and nxt.block_type == BlockType.PARAGRAPH
                    and len(nxt_text) >= 250
                    and len(re.findall(r"[.!?]\s+[A-Z]", nxt_text)) >= 2
                ):
                    break
                scope_blocks.append(nxt)
                j += 1

            # Reclassify each scope block.
            for sb in scope_blocks:
                result.extend(
                    self._reclassify_listing_scope_block(
                        self._strip_listing_code_trailing_annotation(sb)
                    )
                )
            i = j
        return result

    def _strip_listing_code_trailing_annotation(
        self, block: _RecoveredBlock
    ) -> _RecoveredBlock:
        """Remove trailing annotation-fragment lines from listing code.

        The PDF extractor sometimes merges the first words of a side
        annotation onto the bottom of a code block (e.g. ``return pi\\n
        Tests the``). We detect a trailing line of the form
        ``[Capital] [lowercase-verb]`` with no code punctuation, and
        strip it.
        """
        if block.role != "code_like" or block.block_type != BlockType.CODE:
            return block
        text = block.text or ""
        if "\n" not in text:
            return block
        lines = text.split("\n")
        last = lines[-1].strip()
        if not last:
            return block
        # Trailing fragment looks like prose: short (≤ 4 words), starts
        # with capital + lowercase, no code punctuation, no operators.
        if (
            re.match(r"^[A-Z][a-z]+(?:\s+[a-z]+){0,3}$", last)
            and not re.search(r"[(){}\[\];=:.<>+]", last)
        ):
            stripped = "\n".join(lines[:-1]).rstrip()
            if not stripped:
                return block
            return replace(
                block,
                text=stripped,
                flags=list(dict.fromkeys([*block.flags, "listing_code_trailing_annotation_stripped"])),
            )
        return block

    def _split_listing_artifact_continuations(
        self, blocks: list[_RecoveredBlock]
    ) -> list[_RecoveredBlock]:
        """Split paragraphs that glue annotation+code+body together.

        Triggers when a paragraph follows a code block AND a Listing
        title was seen earlier on the same page. The text typically
        looks like:

            "Tests the function; the more terms, the more accurate the
             approximation print(calculate_pi(1000000)) Now let us
             force ChatGPT to do some not terribly challenging
             extrapolation. ..."

        We use ``)\\s+[A-Z][a-z]+\\s+[a-z]`` (close paren + Capital +
        lowercase prose start) as the boundary between the code call
        and the body sentence.
        """
        out: list[_RecoveredBlock] = []
        seen_listing_on_page: int | None = None
        for block in blocks:
            text = (block.text or "")
            if _LISTING_TITLE_RE.match(text.strip()):
                seen_listing_on_page = block.page_start
            elif block.page_start != seen_listing_on_page:
                seen_listing_on_page = None

            should_split = (
                seen_listing_on_page is not None
                and block.role == "body"
                and block.block_type == BlockType.PARAGRAPH
                and len(text) >= 200
            )
            if not should_split:
                out.append(block)
                continue

            # Search for "close paren + capital sentence start" boundary.
            # Anchor: a function-call expression (.../identifier(...))
            # immediately followed by " Now/We/This/These/That/The …"
            boundary = re.search(
                r"\)\s+(?=(?:Now|We|This|These|That|The|It|If|For|While|After|Before|"
                r"Once|However|Additionally|Furthermore|Therefore|Thus|Hence|Similarly)\b)",
                text,
            )
            if boundary is None:
                out.append(block)
                continue

            split_pos = boundary.start() + 1  # include the ')' in first half
            first_half = text[:split_pos].rstrip()
            second_half = text[split_pos:].lstrip()
            if not first_half or not second_half:
                out.append(block)
                continue

            out.append(
                replace(
                    block,
                    text=first_half,
                    flags=list(dict.fromkeys([*block.flags, "listing_artifact_split"])),
                )
            )
            body_meta = dict(block.metadata)
            out.append(
                replace(
                    block,
                    text=second_half,
                    metadata=body_meta,
                    anchor=f"{block.anchor}-bodysplit",
                    flags=list(dict.fromkeys([*block.flags, "listing_body_continuation"])),
                )
            )
        return out

    def _reclassify_listing_scope_block(
        self, block: _RecoveredBlock
    ) -> list[_RecoveredBlock]:
        """Within a Listing scope, fix paragraph misclassifications.

        Four cases (conservative — leave block alone if none match):

        1. Block text starts with a programming-language keyword
           (``IMPORT``, ``MODULE``, ``def``, ``class``, …) → reclassify
           as code_like (was misclassified as paragraph by PDF column-
           extraction).
        2. Block text starts with a short callout phrase (``Tests the
           function``, ``Returns the …``) AND has a code-call tail
           glued on the end → split into [annotation, code, body_rest].
        3. Block text starts with a short callout phrase AND is < 160
           chars total → flag as listing side annotation (suppressed).
        4. Otherwise leave unchanged.
        """
        if block.role != "body" or block.block_type != BlockType.PARAGRAPH:
            return [block]
        stripped = (block.text or "").strip()
        if not stripped:
            return [block]

        # Case 1: code line misclassified as paragraph.
        if _LISTING_CODE_LINE_STARTERS_RE.match(stripped):
            code_meta = dict(block.metadata)
            code_meta["pdf_block_role"] = "code_like"
            code_meta["translatable"] = False
            code_meta["nontranslatable_reason"] = "code"
            return [
                replace(
                    block,
                    role="code_like",
                    block_type=BlockType.CODE,
                    metadata=code_meta,
                    flags=list(dict.fromkeys([*block.flags, "listing_paragraph_to_code"])),
                )
            ]

        # Case 2 / 3: annotation prose (must start with callout phrase).
        is_annotation_lead = bool(_LISTING_ANNOTATION_PROSE_RE.match(stripped))
        if not is_annotation_lead:
            return [block]

        code_match = _LISTING_ANNOTATION_CODE_TAIL_RE.search(stripped)
        if code_match is not None:
            # Case 2: split annotation + code (+ trailing body if any).
            ann_text = stripped[: code_match.start()].rstrip(" ,;:")
            code_text = code_match.group(0).strip()
            body_text = stripped[code_match.end():].strip()
            outputs: list[_RecoveredBlock] = []
            if ann_text:
                ann_meta = dict(block.metadata)
                ann_meta["pdf_listing_annotation_suppressed"] = True
                ann_meta["translatable"] = False
                ann_meta["nontranslatable_reason"] = "listing_side_annotation"
                outputs.append(
                    replace(
                        block,
                        text=ann_text,
                        metadata=ann_meta,
                        flags=list(dict.fromkeys([*block.flags, "listing_annotation_suppressed"])),
                    )
                )
            code_meta = dict(block.metadata)
            code_meta["pdf_block_role"] = "code_like"
            code_meta["translatable"] = False
            code_meta["nontranslatable_reason"] = "code"
            outputs.append(
                replace(
                    block,
                    role="code_like",
                    block_type=BlockType.CODE,
                    text=code_text,
                    metadata=code_meta,
                    anchor=f"{block.anchor}-code",
                    flags=list(dict.fromkeys([*block.flags, "listing_annotation_split"])),
                )
            )
            if body_text:
                body_meta = dict(block.metadata)
                outputs.append(
                    replace(
                        block,
                        text=body_text,
                        metadata=body_meta,
                        anchor=f"{block.anchor}-body",
                    )
                )
            return outputs

        # Case 3: short annotation without code tail.
        if len(stripped) <= 160:
            ann_meta = dict(block.metadata)
            ann_meta["pdf_listing_annotation_suppressed"] = True
            ann_meta["translatable"] = False
            ann_meta["nontranslatable_reason"] = "listing_side_annotation"
            return [
                replace(
                    block,
                    metadata=ann_meta,
                    flags=list(dict.fromkeys([*block.flags, "listing_annotation_suppressed"])),
                )
            ]

        return [block]

    def _merge_cross_page_prose_continuations(
        self,
        recovered_blocks: list[_RecoveredBlock],
        pages: list[PdfPage],
    ) -> list[_RecoveredBlock]:
        """Merge body paragraph blocks split across a page boundary.

        Books frequently break a sentence at the page bottom and continue
        it on the next page after a running header. The parser emits two
        ``paragraph`` blocks (one per page) with header/footer/page-number
        artifacts in between. Without merging, the translator sees the
        halves independently → two Chinese paragraphs with a mid-sentence
        seam. We detect the continuation pattern (previous ends without a
        sentence terminator, current starts lowercase or with a hyphen
        continuation) and glue them so a single packet covers the full
        sentence.
        """
        merged: list[_RecoveredBlock] = []
        for current in recovered_blocks:
            target_index = self._cross_page_prose_merge_target_index(merged, current, pages)
            if target_index is not None:
                merged_block = self._merge_blocks(merged[target_index], current)
                merged_block.flags = list(
                    dict.fromkeys(
                        [*merged_block.flags, "cross_page_prose_continuation_merged"]
                    )
                )
                merged[target_index] = merged_block
                continue
            merged.append(current)
        return merged

    def _cross_page_prose_merge_target_index(
        self,
        recovered: list[_RecoveredBlock],
        current: _RecoveredBlock,
        pages: list[PdfPage],
    ) -> int | None:
        if not recovered:
            return None
        index = len(recovered) - 1
        while index >= 0 and self._is_ignorable_prose_separator(recovered[index]):
            index -= 1
        if index < 0:
            return None
        if any(not self._is_ignorable_prose_separator(block) for block in recovered[index + 1 :]):
            return None
        if self._should_merge_cross_page_prose_continuation(recovered[index], current, pages):
            return index
        return None

    def _is_ignorable_prose_separator(self, block: _RecoveredBlock) -> bool:
        if block.role in {"header", "footer", "footnote"}:
            return True
        text = block.text or ""
        if block.block_type == BlockType.PARAGRAPH and _is_page_number_text(text):
            return True
        # Running-header text that the parser captured as a paragraph
        # block. These look like "22\nCHAPTER 2\n<chapter title>" or
        # "2.2 Language models see only tokens\n23" and must not break a
        # cross-page sentence continuation chain.
        if block.block_type == BlockType.PARAGRAPH and _is_book_running_header_text(text):
            return True
        return False

    def _should_merge_cross_page_prose_continuation(
        self,
        previous: _RecoveredBlock,
        current: _RecoveredBlock,
        pages: list[PdfPage],
    ) -> bool:
        if previous.role != "body" or current.role != "body":
            return False
        if previous.block_type != BlockType.PARAGRAPH or current.block_type != BlockType.PARAGRAPH:
            return False
        if current.page_start <= previous.page_end:
            return False
        if current.page_start > previous.page_end + 2:
            return False
        if str(previous.metadata.get("pdf_page_family") or "body") != "body":
            return False
        if str(current.metadata.get("pdf_page_family") or "body") != "body":
            return False
        # Current must NOT itself be a separator (page number, running
        # header) — otherwise we would graft the page header onto the
        # previous body block.
        if self._is_ignorable_prose_separator(current):
            return False

        prev_text = (previous.text or "").rstrip()
        curr_text = (current.text or "").lstrip()
        if not prev_text or not curr_text:
            return False
        # Strip trailing whitespace and quote-pairs so the terminator
        # check looks at meaningful characters.
        prev_tail = prev_text.rstrip("\"'“”‘’)] }\t\r\n ")
        if not prev_tail:
            return False
        last_char = prev_tail[-1]
        # Sentence-final punctuation rules out a continuation.
        if last_char in {".", "!", "?", ":", ";"}:
            return False
        # Mid-word hyphen break or trailing connector (comma, dash, ampersand,
        # open-paren leftover, alphabetic char) signals continuation.
        if last_char not in {",", "-", "–", "—", "&"} and not last_char.isalpha():
            return False

        first_char = curr_text[0]
        # Hyphenated break — accept any reasonable continuation lead.
        if last_char == "-":
            return first_char.isalnum()
        # Standard continuation: current begins with lowercase letter.
        # A digit or capital-letter lead almost always signals a new
        # paragraph (section number, sentence, list item, etc.).
        return first_char.islower()

    def _should_merge_same_anchor_code_continuation(
        self,
        previous: _RecoveredBlock,
        current: _RecoveredBlock,
    ) -> bool:
        if not previous.anchor or previous.anchor != current.anchor:
            return False
        if previous.source_path != current.source_path:
            return False
        if previous.role != "code_like" or current.role != "code_like":
            return False
        if previous.block_type != BlockType.CODE or current.block_type != BlockType.CODE:
            return False
        if current.page_start < previous.page_start:
            return False
        if current.reading_order_index - previous.reading_order_index > 1:
            return False
        return True

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
        if len(raw_lines) < 2:
            return [block]

        code_prefix_length = 0
        unterminated_string = False
        for line in raw_lines:
            if (
                _looks_like_embedded_code_line(line)
                or _looks_like_code_docstring_line(line)
                or _looks_like_code_continuation_line(line, raw_lines[:code_prefix_length])
                or unterminated_string
            ):
                code_prefix_length += 1
                joined_prefix = "\n".join(raw_lines[:code_prefix_length])
                unterminated_string = (
                    _has_unterminated_triple_quoted_string(joined_prefix)
                    or _has_unterminated_quoted_string(joined_prefix, '"')
                    or _has_unterminated_quoted_string(joined_prefix, "'")
                )
                continue
            break
        if code_prefix_length >= len(raw_lines):
            return [block]

        code_lines = raw_lines[:code_prefix_length]
        prose_lines = raw_lines[code_prefix_length:]
        single_line_code_ok = (
            code_prefix_length == 1
            and len(code_lines) == 1
            and bool(prose_lines)
            and _looks_like_splitworthy_single_line_code_fragment(code_lines[0])
        )
        if code_prefix_length < 2 and not single_line_code_ok:
            return [block]
        if not _looks_like_code("\n".join(code_lines), len(code_lines)) and not single_line_code_ok:
            return [block]
        if not prose_lines or not _looks_like_prose_line_group(prose_lines):
            return [block]

        base_metadata = dict(block.metadata)
        split_source_anchor_base = f"{block.source_path}#{block.anchor}"
        code_metadata = {
            **base_metadata,
            "pdf_block_role": "code_like",
            "pdf_mixed_code_prose_split": "leading_code_prefix",
        }
        prose_metadata = {
            **base_metadata,
            "pdf_block_role": "body",
            "pdf_mixed_code_prose_split": "trailing_prose_suffix",
            "pdf_split_source_anchor_base": split_source_anchor_base,
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
            anchor=f"{block.anchor}-trailing-prose",
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
        if "embedded_abstract_heading_recovered" in block.flags:
            return False
        if block.page_end < block.page_start or block.page_end - block.page_start > 1:
            return False
        if str(block.metadata.get("pdf_page_family") or "body") != "body":
            return False

        normalized_lines = _expanded_code_candidate_lines(block.text)
        if not normalized_lines:
            return False
        if _looks_like_table(len(normalized_lines), normalized_lines) or _looks_like_numeric_table_fragment(normalized_lines):
            return False
        if _looks_like_code("\n".join(normalized_lines), max(2, len(normalized_lines))):
            return True
        if not (_looks_like_code_docstring_text(block.text) or _looks_like_code_comment_text(block.text)):
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
        if not (_looks_like_table(len(lines), lines) or _looks_like_numeric_table_fragment(lines)):
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
        pages: list[PdfPage] | None = None,
    ) -> list[_RecoveredBlock]:
        merged: list[_RecoveredBlock] = []
        for current in recovered_blocks:
            if merged and self._should_merge_table_fragments(merged[-1], current, pages):
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
        pages: list[PdfPage] | None = None,
    ) -> bool:
        if previous.role != "table_like" or current.role != "table_like":
            return False
        if previous.block_type != BlockType.TABLE or current.block_type != BlockType.TABLE:
            return False
        if previous.source_path != current.source_path:
            return False

        # --- Same-page merge (original logic) ---
        if previous.page_end == current.page_start:
            if previous.page_start != current.page_start or previous.page_end != current.page_end:
                return False
            if previous.page_start != previous.page_end:
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

        # --- Cross-page table merge ---
        if not pages:
            return False
        if current.page_start != previous.page_end + 1:
            return False

        page_lookup = {page.page_number: page for page in pages}
        prev_page = page_lookup.get(previous.page_end)
        curr_page = page_lookup.get(current.page_start)
        if prev_page is None or curr_page is None:
            return False

        previous_bbox = self._page_bbox(previous, previous.page_end)
        current_bbox = self._page_bbox(current, current.page_start)
        if previous_bbox is None or current_bbox is None:
            return False

        prev_bottom = float(previous_bbox[3])
        curr_top = float(current_bbox[1])
        if prev_bottom < prev_page.height * 0.70:
            return False
        if curr_top > curr_page.height * 0.30:
            return False

        overlap_ratio = self._horizontal_overlap_ratio(previous_bbox, current_bbox)
        left_edge_dist = abs(float(previous_bbox[0]) - float(current_bbox[0]))
        if overlap_ratio < 0.12 and left_edge_dist > 96.0:
            return False

        # Validate column count similarity via separators
        def _count_columns(text: str) -> int:
            lines = [l for l in text.splitlines() if l.strip()]
            if not lines:
                return 0
            counts: list[int] = []
            for line in lines:
                pipe_count = line.count("|")
                if pipe_count >= 1:
                    counts.append(pipe_count)
                else:
                    import re
                    multi_space = len(re.findall(r"  +", line))
                    counts.append(multi_space)
            return max(counts) if counts else 0

        prev_cols = _count_columns(previous.text)
        curr_cols = _count_columns(current.text)
        if abs(prev_cols - curr_cols) > 2:
            return False

        # Mark as cross-page merge
        previous.flags = list(dict.fromkeys([*previous.flags, "cross_page_table_fragments_merged"]))
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
        *,
        academic_lane: bool = False,
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
                academic_lane=academic_lane,
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

    def _populate_missing_heading_levels(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> list[_RecoveredBlock]:
        first_substantive_anchor_by_page: dict[int, str] = {}
        for block in recovered_blocks:
            if (
                block.role not in {"header", "footer", "toc_entry", "footnote"}
                and block.page_start not in first_substantive_anchor_by_page
            ):
                first_substantive_anchor_by_page[block.page_start] = block.anchor

        normalized_blocks: list[_RecoveredBlock] = []
        last_body_heading_level: int | None = None
        last_body_heading_page: int | None = None
        for block in recovered_blocks:
            if block.role != "heading":
                normalized_blocks.append(block)
                continue
            metadata = dict(block.metadata)
            existing_heading_level = metadata.get("heading_level")
            normalized_text = _normalize_text(block.text)
            page_family = str(metadata.get("pdf_page_family") or "body")
            is_first_substantive_on_page = first_substantive_anchor_by_page.get(block.page_start) == block.anchor
            if isinstance(existing_heading_level, int):
                if (
                    existing_heading_level == 1
                    and block.page_start > 1
                    and page_family == "body"
                    and not (
                        is_first_substantive_on_page
                        and len(normalized_text.split()) >= 4
                        and _looks_like_visual_heading(normalized_text, 1)
                    )
                    and not _CHAPTER_PREFIX_PATTERN.match(normalized_text)
                    and not _HEADING_PATTERN.match(normalized_text)
                    and _LEADING_SECTION_NUMBER_PATTERN.match(normalized_text) is None
                    and metadata.get("pdf_heading_recovery_source") != "embedded_document_title_recovered"
                ):
                    metadata["heading_level"] = 2
                elif normalized_text.casefold() in _TOC_HEADING_TITLES:
                    metadata["heading_level"] = 1
                elif (
                    existing_heading_level == 2
                    and is_first_substantive_on_page
                    and page_family == "body"
                    and normalized_text.casefold() in {"introduction", "overview", "conclusion"}
                ):
                    metadata["heading_level"] = 1
                elif (
                    existing_heading_level == 2
                    and metadata.get("pdf_heading_recovery_source") == "embedded_book_plain_heading_recovered"
                    and len({token.casefold() for token in re.findall(r"[A-Za-z][A-Za-z'-]*", normalized_text)}) == 1
                    and last_body_heading_level == 2
                    and last_body_heading_page is not None
                    and block.page_start - last_body_heading_page <= 1
                    and not is_first_substantive_on_page
                ):
                    metadata["heading_level"] = 3
                normalized_block = replace(block, metadata=metadata)
                normalized_blocks.append(normalized_block)
                last_body_heading_level = int(metadata.get("heading_level") or existing_heading_level)
                last_body_heading_page = block.page_start
                continue
            heading_level = _book_heading_level(block.text)
            if heading_level is None:
                if normalized_text.casefold() in _REFERENCES_HEADING_TITLES or page_family == "references":
                    heading_level = 2
                elif normalized_text.casefold() in _TOC_HEADING_TITLES:
                    heading_level = 1
                elif (
                    block.page_start == 1
                    and is_first_substantive_on_page
                    and _looks_like_paper_title(normalized_text)
                ):
                    heading_level = 1
                elif (
                    page_family == "body"
                    and is_first_substantive_on_page
                    and normalized_text.casefold() in {"introduction", "overview", "conclusion"}
                ):
                    heading_level = 1
                else:
                    heading_level = 2

            if heading_level is None:
                normalized_blocks.append(block)
                continue

            metadata["heading_level"] = heading_level
            normalized_block = replace(block, metadata=metadata)
            normalized_blocks.append(normalized_block)
            last_body_heading_level = heading_level
            last_body_heading_page = block.page_start
        return normalized_blocks

    def _promote_inline_book_heading_blocks(
        self,
        recovered_blocks: list[_RecoveredBlock],
    ) -> list[_RecoveredBlock]:
        promoted: list[_RecoveredBlock] = []
        for index, block in enumerate(recovered_blocks):
            if not self._should_promote_inline_book_heading_block(recovered_blocks, index):
                promoted.append(block)
                continue
            metadata = dict(block.metadata)
            metadata["pdf_heading_recovery_source"] = "inline_book_heading"
            heading_level = _book_heading_level(block.text, fallback=2)
            if heading_level is not None:
                metadata["heading_level"] = heading_level
            promoted_block = replace(
                block,
                role="heading",
                block_type=BlockType.HEADING,
                metadata=metadata,
            )
            promoted_block.flags = list(dict.fromkeys([*block.flags, "inline_book_heading_promoted"]))
            promoted.append(promoted_block)
        return promoted

    def _should_promote_inline_book_heading_block(
        self,
        recovered_blocks: list[_RecoveredBlock],
        index: int,
    ) -> bool:
        block = recovered_blocks[index]
        if block.role != "body" or block.block_type != BlockType.PARAGRAPH:
            return False
        if str(block.metadata.get("pdf_page_family") or "body") != "body":
            return False
        if _normalize_text(block.text).casefold() in _TOC_HEADING_TITLES:
            if block.page_start != block.page_end or not block.bbox_regions:
                return False
            top = float(block.bbox_regions[0]["bbox"][1])
            return top <= 220.0
        next_body = self._next_book_body_candidate(recovered_blocks, index)
        if next_body is not None:
            return self._should_keep_inline_book_heading_separate(block, next_body)
        if not _looks_like_inline_book_heading_text(block.text):
            return False
        if block.page_start != block.page_end:
            return False
        if not block.bbox_regions:
            return False
        top = float(block.bbox_regions[0]["bbox"][1])
        return top <= 140.0

    def _next_book_body_candidate(
        self,
        recovered_blocks: list[_RecoveredBlock],
        index: int,
    ) -> _RecoveredBlock | None:
        block = recovered_blocks[index]
        for candidate in recovered_blocks[index + 1 : index + 5]:
            if candidate.page_start - block.page_end > 1:
                break
            if candidate.role in {"header", "footer", "toc_entry", "image", "caption", "footnote"}:
                continue
            if candidate.role == "body" and candidate.block_type == BlockType.PARAGRAPH:
                return candidate
            break
        return None

    def _promote_contextual_image_legend_blocks(
        self,
        recovered_blocks: list[_RecoveredBlock],
        pages: list[PdfPage],
    ) -> list[_RecoveredBlock]:
        page_lookup = {page.page_number: page for page in pages}
        promoted: list[_RecoveredBlock] = []
        for block in recovered_blocks:
            if not self._looks_like_contextual_image_legend_block(block, page_lookup):
                promoted.append(block)
                continue
            metadata = dict(block.metadata)
            metadata["pdf_contextual_image_legend"] = True
            promoted_block = replace(
                block,
                role="caption",
                block_type=BlockType.CAPTION,
                metadata=metadata,
            )
            promoted_block.flags = list(dict.fromkeys([*block.flags, "contextual_image_legend_promoted"]))
            promoted.append(promoted_block)
        return promoted

    def _split_embedded_page_heading_segments(
        self,
        block: _RecoveredBlock,
        *,
        is_first_substantive_page_block: bool,
        page_has_heading: bool,
        academic_lane: bool = False,
    ) -> list[_RecoveredBlock]:
        if (
            block.role not in {"body", "code_like"}
            or block.block_type not in {BlockType.PARAGRAPH, BlockType.CODE}
        ):
            return [replace(block)]

        if (
            block.page_start == 1
            and block.page_end == 1
            and block.role == "body"
            and str(block.metadata.get("pdf_page_family") or "body") == "body"
        ):
            abstract_segments = _embedded_academic_abstract_segments(block.text)
            if abstract_segments is not None:
                prefix, heading_text, remainder = abstract_segments
                shared_flags = list(dict.fromkeys([*block.flags, "embedded_abstract_heading_recovered"]))
                prefix_metadata = dict(block.metadata)
                prefix_metadata["pdf_heading_recovery_source"] = "embedded_abstract_heading_recovered"
                heading_metadata = dict(block.metadata)
                heading_metadata["pdf_heading_recovery_source"] = "embedded_abstract_heading_recovered"
                heading_metadata["heading_level"] = 2
                body_metadata = dict(block.metadata)
                body_metadata["pdf_heading_recovery_source"] = "embedded_abstract_heading_recovered"
                segments: list[_RecoveredBlock] = []
                segment_index = 0
                normalized_prefix = _normalize_multiline_text(prefix)
                prefix_title = (
                    _infer_first_page_paper_title_and_remainder(normalized_prefix)
                    if normalized_prefix and is_first_substantive_page_block and not page_has_heading
                    else None
                )
                if prefix_title is not None:
                    # The embedded-abstract split runs before first-page title
                    # recovery, so recover the paper title from the prefix here.
                    title_text, normalized_prefix = prefix_title
                    title_metadata = dict(block.metadata)
                    title_metadata["pdf_heading_recovery_source"] = "embedded_document_title_recovered"
                    title_metadata["heading_level"] = 1
                    segment_index += 1
                    segments.append(
                        _RecoveredBlock(
                            role="heading",
                            block_type=BlockType.HEADING,
                            text=_normalize_multiline_text(title_text),
                            page_start=block.page_start,
                            page_end=block.page_end,
                            bbox_regions=list(block.bbox_regions),
                            reading_order_index=block.reading_order_index,
                            parse_confidence=block.parse_confidence,
                            flags=list(dict.fromkeys([*shared_flags, "embedded_document_title_recovered"])),
                            metadata=title_metadata,
                            font_size_avg=block.font_size_avg,
                            source_path=block.source_path,
                            anchor=f"{block.anchor}-s{segment_index}",
                        )
                    )
                    normalized_prefix = _normalize_multiline_text(normalized_prefix)
                if normalized_prefix:
                    segment_index += 1
                    segments.append(
                        _RecoveredBlock(
                            role="body",
                            block_type=BlockType.PARAGRAPH,
                            text=normalized_prefix,
                            page_start=block.page_start,
                            page_end=block.page_end,
                            bbox_regions=list(block.bbox_regions),
                            reading_order_index=block.reading_order_index,
                            parse_confidence=block.parse_confidence,
                            flags=shared_flags,
                            metadata=prefix_metadata,
                            font_size_avg=block.font_size_avg,
                            source_path=block.source_path,
                            anchor=f"{block.anchor}-s{segment_index}",
                        )
                    )
                segment_index += 1
                segments.append(
                    _RecoveredBlock(
                        role="heading",
                        block_type=BlockType.HEADING,
                        text=heading_text,
                        page_start=block.page_start,
                        page_end=block.page_end,
                        bbox_regions=list(block.bbox_regions),
                        reading_order_index=block.reading_order_index,
                        parse_confidence=block.parse_confidence,
                        flags=shared_flags,
                        metadata=heading_metadata,
                        font_size_avg=block.font_size_avg,
                        source_path=block.source_path,
                        anchor=f"{block.anchor}-s{segment_index}",
                    )
                )
                normalized_remainder = _normalize_multiline_text(remainder)
                if normalized_remainder:
                    segment_index += 1
                    segments.append(
                        _RecoveredBlock(
                            role="body",
                            block_type=BlockType.PARAGRAPH,
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
                            anchor=f"{block.anchor}-s{segment_index}",
                        )
                    )
                if segments:
                    return segments

        heading_text: str | None = None
        remainder: str | None = None
        recovery_flag: str | None = None
        recovered_heading_level: int | None = None
        metadata = dict(block.metadata)
        page_family = str(metadata.get("pdf_page_family") or "body")
        reference_heading = _leading_reference_heading_and_remainder(block.text)
        if reference_heading is not None:
            heading_text, remainder = reference_heading
            recovery_flag = "embedded_references_heading_recovered"
            recovered_heading_level = 2
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
                recovered_heading_level = 1
        elif (
            page_family == "body"
            and block.page_end - block.page_start <= 1
            and block.role in {"body", "code_like"}
        ):
            # Academic-inline-heading detection ("Training" → standalone
            # heading) is appropriate for academic-paper PDFs but
            # produces destructive false positives in book prose. The
            # callout title "Training LLMs is expensive Training an LLM
            # is not realistically possible..." was being split as
            # heading="Training" + body="LLMs is expensive Training an
            # LLM..." (first word stolen, title destroyed). Gate by the
            # current recovery lane so book-lane parses keep the block
            # intact and let the book-style detectors run instead.
            academic_heading = (
                _next_academic_inline_heading(_normalize_multiline_text(block.text))
                if academic_lane
                else None
            )
            if academic_heading is not None and academic_heading[0] == 0:
                _start_index, heading_text, remainder, heading_meta = academic_heading
                recovered_heading_level = int(heading_meta.get("section_level") or 0) or None
                recovery_flag = "academic_section_heading_recovered"
            else:
                numbered_book_heading = _leading_numbered_book_heading_and_remainder(block.text)
                if numbered_book_heading is not None:
                    heading_text, remainder, recovered_heading_level = numbered_book_heading
                    recovery_flag = "embedded_book_heading_recovered"
                else:
                    inline_caps_heading = _leading_all_caps_book_heading_and_remainder(block.text)
                    if inline_caps_heading is not None:
                        heading_text, remainder, recovered_heading_level = inline_caps_heading
                        recovery_flag = "embedded_book_subheading_recovered"
                    else:
                        plain_heading = _leading_plain_book_heading_and_remainder(block.text)
                        if plain_heading is not None:
                            heading_text, remainder, recovered_heading_level = plain_heading
                            recovery_flag = "embedded_book_plain_heading_recovered"

        if heading_text is None or recovery_flag is None:
            return [replace(block)]

        shared_flags = list(dict.fromkeys([*block.flags, recovery_flag]))
        heading_metadata = dict(metadata)
        heading_metadata["pdf_heading_recovery_source"] = recovery_flag
        if recovered_heading_level is not None:
            heading_metadata["heading_level"] = recovered_heading_level
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
                    role="body",
                    block_type=BlockType.PARAGRAPH,
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

    def _apply_figure_clustering(
        self, recovered_blocks: list[_RecoveredBlock]
    ) -> list[_RecoveredBlock]:
        """Cluster spatially-adjacent image / vector / label blocks into FIGURE blocks.

        Replaces image/vector_drawing anchors that share a figure region
        with one ``BlockType.FIGURE`` block whose bbox is the union of
        all clustered components. Inline diagram labels are absorbed
        (no separate translation). Caption-pattern text is kept as a
        separate translatable block but tagged with ``figure_anchor`` so
        the export layer can render it next to the figure.

        Returns a new list — the input is not mutated.
        """
        if not recovered_blocks:
            return recovered_blocks
        report = cluster_figure_regions(
            recovered_blocks, config=self._figure_cluster_config
        )
        if not report.clusters:
            return recovered_blocks

        replaced_indices: set[int] = set()
        figure_replacement_at: dict[int, _RecoveredBlock] = {}
        caption_anchor_for_index: dict[int, str] = {}

        for cluster in report.clusters:
            anchor_indices = sorted(cluster.anchor_indices)
            if not anchor_indices:
                continue
            # Skip clusters that don't actually merge or absorb anything.
            # A single anchor with no inline labels has no clustering work
            # to do; the existing _link_artifact_captions pass handles
            # caption→image linking on its own. Emitting FIGURE blocks for
            # plain images would needlessly change the type label and
            # break tests that expect BlockType.IMAGE for single-figure
            # pages.
            is_trivial = (
                len(anchor_indices) == 1 and not cluster.inline_label_indices
            )
            if is_trivial:
                continue
            primary_index = anchor_indices[0]
            primary = recovered_blocks[primary_index]

            absorbed_anchors: list[str] = []
            for label_idx in cluster.inline_label_indices:
                absorbed_anchors.append(recovered_blocks[label_idx].anchor)

            metadata = dict(primary.metadata)
            metadata["image_type"] = cluster.image_type or metadata.get(
                "image_type", "vector_drawing"
            )
            caption_block_anchor = (
                recovered_blocks[cluster.caption_index].anchor
                if cluster.caption_index is not None
                else None
            )
            metadata["figure_cluster"] = {
                "anchor_block_anchors": [recovered_blocks[i].anchor for i in anchor_indices],
                "absorbed_label_anchors": absorbed_anchors,
                "caption_block_anchor": caption_block_anchor,
            }
            # Mirror the bidirectional caption metadata that
            # ``_link_artifact_captions`` would produce for an IMAGE block,
            # so downstream consumers (export, refresh, render) treat
            # clustered FIGURE blocks identically to non-clustered IMAGE
            # blocks. Without this, clustered figures lose their captions
            # in the rendered output.
            if cluster.caption_index is not None:
                caption_block_for_meta = recovered_blocks[cluster.caption_index]
                metadata["linked_caption_text"] = caption_block_for_meta.text
                metadata["linked_caption_source_anchor"] = self._source_anchor(caption_block_for_meta)
                metadata["linked_caption_page"] = caption_block_for_meta.page_start
                if not metadata.get("image_alt") and caption_block_for_meta.text.strip():
                    metadata["image_alt"] = caption_block_for_meta.text
            # Replace the per-image source_bbox_json with the cluster's
            # union bbox so bootstrap.py emits a single DocumentImage row
            # covering the whole figure region (rendered later by the
            # export pipeline).
            metadata["source_bbox_json"] = {
                "regions": [
                    {
                        "page_number": cluster.page_number,
                        "bbox": _bbox_to_json(cluster.bbox),
                    }
                ]
            }

            replacement = _RecoveredBlock(
                role="figure",
                block_type=BlockType.FIGURE,
                text="[Figure]",
                page_start=cluster.page_number,
                page_end=cluster.page_number,
                bbox_regions=[
                    {
                        "page_number": cluster.page_number,
                        "bbox": _bbox_to_json(cluster.bbox),
                    }
                ],
                reading_order_index=primary.reading_order_index,
                parse_confidence=primary.parse_confidence,
                flags=list(primary.flags),
                font_size_avg=0.0,
                source_path=primary.source_path,
                anchor=f"p{cluster.page_number}-fig{primary.reading_order_index}",
                metadata=metadata,
            )
            figure_replacement_at[primary_index] = replacement
            for idx in anchor_indices:
                replaced_indices.add(idx)
            for idx in cluster.inline_label_indices:
                replaced_indices.add(idx)
            if cluster.caption_index is not None:
                caption_anchor_for_index[cluster.caption_index] = replacement.anchor

        if not replaced_indices and not caption_anchor_for_index:
            return recovered_blocks

        new_blocks: list[_RecoveredBlock] = []
        for index, block in enumerate(recovered_blocks):
            if index in figure_replacement_at:
                new_blocks.append(figure_replacement_at[index])
                continue
            if index in replaced_indices:
                continue
            if index in caption_anchor_for_index:
                tagged_metadata = dict(block.metadata)
                figure_anchor_value = caption_anchor_for_index[index]
                tagged_metadata["figure_anchor"] = figure_anchor_value
                # Back-pointer mirroring what ``_link_artifact_captions``
                # writes onto a CAPTION block. The replacement FIGURE that
                # owns this caption was constructed with primary.source_path
                # and anchor=f"p{page}-fig{...}", so build the same
                # ``source_path#anchor`` form here.
                tagged_metadata["caption_for_source_anchor"] = (
                    f"{block.source_path}#{figure_anchor_value}"
                    if figure_anchor_value
                    else None
                )
                tagged_metadata["caption_for_page"] = block.page_start
                tagged_metadata["caption_for_role"] = "image"
                tagged_flags = list(dict.fromkeys([*block.flags, "image_caption_linked", "caption_linked"]))
                new_blocks.append(
                    _RecoveredBlock(
                        role=block.role,
                        block_type=block.block_type,
                        text=block.text,
                        page_start=block.page_start,
                        page_end=block.page_end,
                        bbox_regions=list(block.bbox_regions),
                        reading_order_index=block.reading_order_index,
                        parse_confidence=block.parse_confidence,
                        flags=tagged_flags,
                        font_size_avg=block.font_size_avg,
                        source_path=block.source_path,
                        anchor=block.anchor,
                        metadata=tagged_metadata,
                    )
                )
                continue
            new_blocks.append(block)
        return new_blocks

    def _recover_text_only_figures(
        self, recovered_blocks: list[_RecoveredBlock]
    ) -> list[_RecoveredBlock]:
        """Synthesize FIGURE blocks for captions whose figure has no image anchor.

        Some figures (word-relation diagrams, conceptual flow charts) are
        rendered in the source PDF as a constellation of short text and
        code blocks with no embedded image and no vector_drawing anchor.
        ``_apply_figure_clustering`` cannot detect them because there is
        no anchor to start from. The result is an orphan caption ("Figure
        3.3 ...") and a flock of mis-typed body blocks (heading "Capital
        Debt -1xcapital", paragraph "Stock Rare", etc.) that get fed into
        the translator as prose.

        Heuristic: for each unlinked CAPTION block matching the
        ``Figure N.M`` pattern, look at non-prose blocks above it on the
        same page. If at least three short blocks cluster within ~200pt
        above the caption AND there is a clear vertical gap separating
        them from the surrounding prose, treat them as the figure body
        and emit a synthetic FIGURE block whose bbox is their union.
        """
        if not recovered_blocks:
            return recovered_blocks

        # Index blocks by page for fast lookup.
        by_page: dict[int, list[tuple[int, _RecoveredBlock]]] = defaultdict(list)
        for index, block in enumerate(recovered_blocks):
            if block.page_start == block.page_end:
                by_page[block.page_start].append((index, block))

        # First pass: pair each orphan "Figure N.M" caption with an unlinked
        # FIGURE/IMAGE on the same page above it, even when the spatial gap
        # exceeds the limits in ``_link_artifact_captions``. Real-world books
        # sometimes wedge a body paragraph between the figure and its caption
        # (e.g. the transformer book's Figure 3.1, where intro prose pushes
        # the caption past the 120pt below-gap limit). Without this pass the
        # text-only synthesizer below mints a duplicate FIGURE for the labels
        # near the caption and the actual image stays orphan.
        for caption_index, caption_block in enumerate(recovered_blocks):
            if caption_block.role != "caption":
                continue
            if (caption_block.metadata or {}).get("caption_for_source_anchor"):
                continue
            if not _looks_like_figure_caption(caption_block.text):
                continue
            caption_bbox = self._page_bbox(caption_block, caption_block.page_start)
            if caption_bbox is None:
                continue
            page_blocks = by_page.get(caption_block.page_start, [])
            existing_unlinked: list[tuple[int, _RecoveredBlock, tuple[float, float, float, float]]] = []
            for idx, blk in page_blocks:
                if idx == caption_index:
                    continue
                if blk.role not in {"image", "figure"}:
                    continue
                if (blk.metadata or {}).get("linked_caption_source_anchor"):
                    continue
                blk_bbox = self._page_bbox(blk, caption_block.page_start)
                if blk_bbox is None:
                    continue
                # Caption must sit BELOW the figure (typical book layout).
                if blk_bbox[3] > caption_bbox[1] + 4.0:
                    continue
                existing_unlinked.append((idx, blk, blk_bbox))
            if existing_unlinked:
                # Pick the figure whose bottom is closest to the caption top —
                # the natural reading-order parent. With multiple unlinked
                # figures on a page (e.g. main + sub-figure), the smaller one
                # right above the caption usually owns the caption.
                target_idx, target_blk, _ = min(
                    existing_unlinked,
                    key=lambda c: abs(caption_bbox[1] - c[2][3]),
                )
                caption_anchor = self._source_anchor(caption_block)
                artifact_anchor = self._source_anchor(target_blk)
                target_blk.metadata.update(
                    {
                        "linked_caption_text": caption_block.text,
                        "linked_caption_source_anchor": caption_anchor,
                        "linked_caption_page": caption_block.page_start,
                    }
                )
                if not target_blk.metadata.get("image_alt") and caption_block.text.strip():
                    target_blk.metadata["image_alt"] = caption_block.text
                target_blk.flags = list(
                    dict.fromkeys([*target_blk.flags, "caption_linked"])
                )
                caption_block.metadata.update(
                    {
                        "caption_for_source_anchor": artifact_anchor,
                        "caption_for_page": target_blk.page_start,
                        "caption_for_role": "image",
                    }
                )
                caption_block.flags = list(
                    dict.fromkeys(
                        [*caption_block.flags, "image_caption_linked", "caption_linked"]
                    )
                )

        # Process captions in reading order so multi-caption pages
        # partition labels deterministically (top caption claims first).
        # ``already_claimed`` tracks block indices absorbed by a previous
        # synthesis so a second caption on the same page can pick up the
        # labels that sit above the first caption but below the second.
        already_claimed: set[int] = set()
        synth: list[tuple[int, list[int], list[float], bool]] = []  # (caption_index, absorbed_indices, union_bbox, orphan_figure)
        for caption_index, caption_block in enumerate(recovered_blocks):
            if caption_block.role != "caption":
                continue
            if (caption_block.metadata or {}).get("caption_for_source_anchor"):
                continue
            if not _looks_like_figure_caption(caption_block.text):
                continue
            page_blocks = by_page.get(caption_block.page_start, [])
            if not page_blocks:
                continue
            caption_bbox = self._page_bbox(caption_block, caption_block.page_start)
            if caption_bbox is None:
                continue
            caption_top = caption_bbox[1]
            # Orphan figure caption: the parser found "Figure N.M …" but no
            # corresponding image / figure / vector_drawing anchor exists on
            # the same page. This happens when the figure is rendered as
            # pure vector graphics (arrows, text labels, code samples) with
            # no embedded raster. In that case the figure body is just a
            # constellation of short text labels — relax the candidate
            # threshold and search window so the synthesizer can still
            # cluster them.
            page_has_artifact_anchor = any(
                blk.role in {"image", "figure"}
                for _idx, blk in page_blocks
            )
            orphan_figure = not page_has_artifact_anchor
            zone_top = 0.0 if orphan_figure else caption_top - 240.0

            # Candidate filter: small text-like blocks above the caption.
            candidates: list[tuple[int, _RecoveredBlock, list[float]]] = []
            for idx, block in page_blocks:
                if idx == caption_index or idx in already_claimed:
                    continue
                if block.role in {"image", "table_like", "equation", "figure", "header", "footer", "footnote"}:
                    continue
                # Only protect HEADING blocks that came from a RIGOROUS
                # recovery source (numbered section, all-caps subheading,
                # academic / abstract / references / document-title). The
                # "soft" heading detectors (inline_book_heading,
                # embedded_book_plain_heading_recovered) regularly promote
                # noun-phrase figure-internal labels — those must stay
                # absorbable so the figure cluster swallows them.
                if block.role == "heading" or block.block_type == BlockType.HEADING:
                    heading_recovery_source = str(
                        (block.metadata or {}).get("pdf_heading_recovery_source") or ""
                    )
                    if heading_recovery_source in {
                        "embedded_book_heading_recovered",
                        "embedded_book_subheading_recovered",
                        "embedded_abstract_heading_recovered",
                        "embedded_document_title_recovered",
                        "embedded_references_heading_recovered",
                        "academic_section_heading_recovered",
                    }:
                        continue
                bbox = self._page_bbox(block, caption_block.page_start)
                if bbox is None:
                    continue
                if not (zone_top <= bbox[1] < caption_top and bbox[3] <= caption_top + 1.0):
                    continue
                text_len = len((block.text or "").strip())
                if text_len == 0 or text_len > 240:
                    continue
                candidates.append((idx, block, bbox))

            candidate_threshold = 2 if orphan_figure else 3
            if len(candidates) < candidate_threshold:
                continue

            # Reject if any candidate is wide-format prose — i.e. spans
            # most of the page width with substantial text. Real figure
            # labels are narrow.
            page_widths = {(b[2] - b[0]) for _, _, b in candidates}
            max_width = max(page_widths) if page_widths else 0.0
            if max_width > 380.0:
                continue

            # Verify the candidates are spatially clustered: the
            # vertical span should be < 240pt and there should be no
            # large prose paragraph between them (we approximate this by
            # requiring the topmost candidate's top to sit at least 12pt
            # below the previous non-candidate prose block on this page).
            sorted_candidates = sorted(candidates, key=lambda c: c[2][1])
            top_y = sorted_candidates[0][2][1]
            bot_y = max(c[2][3] for c in sorted_candidates)
            # Orphan figures (vector-graphics-only) often span more of the
            # page since labels are scattered across the diagram.
            vertical_span_limit = 480.0 if orphan_figure else 240.0
            if bot_y - top_y > vertical_span_limit:
                continue
            # Block immediately above the cluster must NOT be a long
            # paragraph that looks like body prose continuation.
            preceding = [
                (i, b, bb) for i, b in page_blocks
                if (bb := self._page_bbox(b, caption_block.page_start)) is not None
                and bb[3] <= top_y + 0.5 and i not in {c[0] for c in candidates}
            ]
            preceding.sort(key=lambda c: c[2][3])  # by bottom
            if preceding:
                last = preceding[-1]
                gap = top_y - last[2][3]
                # 6pt is enough of a gap to declare a visual break in
                # most book layouts; tighter than that and the candidate
                # is probably a continuation of the preceding paragraph.
                if gap < 6.0 and len((last[1].text or "").strip()) > 80:
                    continue

            # Union bbox.
            ux0 = min(c[2][0] for c in sorted_candidates)
            uy0 = top_y
            ux1 = max(c[2][2] for c in sorted_candidates)
            uy1 = bot_y
            absorbed_idxs = [c[0] for c in sorted_candidates]
            already_claimed.update(absorbed_idxs)
            synth.append((caption_index, absorbed_idxs, [ux0, uy0, ux1, uy1], orphan_figure))

        if not synth:
            return recovered_blocks

        replaced_indices: set[int] = set()
        synth_block_at: dict[int, _RecoveredBlock] = {}
        caption_anchor_for_index: dict[int, str] = {}
        for caption_index, absorbed, union_bbox, was_orphan in synth:
            primary_index = absorbed[0]
            primary = recovered_blocks[primary_index]
            caption_block = recovered_blocks[caption_index]
            page_number = caption_block.page_start
            anchor = f"p{page_number}-tfig{primary.reading_order_index}"
            metadata = dict(primary.metadata)
            metadata["image_type"] = "text_only_figure"
            metadata["figure_cluster"] = {
                "anchor_block_anchors": [recovered_blocks[i].anchor for i in absorbed],
                "absorbed_label_anchors": [recovered_blocks[i].anchor for i in absorbed],
                "caption_block_anchor": caption_block.anchor,
            }
            metadata["linked_caption_text"] = caption_block.text
            metadata["linked_caption_source_anchor"] = self._source_anchor(caption_block)
            metadata["linked_caption_page"] = caption_block.page_start
            if caption_block.text.strip():
                metadata["image_alt"] = caption_block.text
            # Same as for clustered FIGUREs: emit one DocumentImage row
            # via the union bbox so the export can render the figure
            # region from the source PDF.
            metadata["source_bbox_json"] = {
                "regions": [{"page_number": page_number, "bbox": list(union_bbox)}]
            }
            metadata["source_page_start"] = page_number

            replacement = _RecoveredBlock(
                role="figure",
                block_type=BlockType.FIGURE,
                text="[Figure]",
                page_start=page_number,
                page_end=page_number,
                bbox_regions=[{"page_number": page_number, "bbox": list(union_bbox)}],
                reading_order_index=primary.reading_order_index,
                parse_confidence=primary.parse_confidence,
                flags=list(dict.fromkeys([
                    *primary.flags,
                    "text_only_figure_synthesized",
                    *(["orphan_figure_synthesized"] if was_orphan else []),
                ])),
                font_size_avg=0.0,
                source_path=primary.source_path,
                anchor=anchor,
                metadata=metadata,
            )
            synth_block_at[primary_index] = replacement
            for idx in absorbed:
                replaced_indices.add(idx)
            caption_anchor_for_index[caption_index] = anchor

        new_blocks: list[_RecoveredBlock] = []
        for index, block in enumerate(recovered_blocks):
            if index in synth_block_at:
                new_blocks.append(synth_block_at[index])
                continue
            if index in replaced_indices:
                continue
            if index in caption_anchor_for_index:
                tagged_metadata = dict(block.metadata)
                figure_anchor_value = caption_anchor_for_index[index]
                tagged_metadata["figure_anchor"] = figure_anchor_value
                tagged_metadata["caption_for_source_anchor"] = (
                    f"{block.source_path}#{figure_anchor_value}"
                )
                tagged_metadata["caption_for_page"] = block.page_start
                tagged_metadata["caption_for_role"] = "image"
                tagged_flags = list(dict.fromkeys([*block.flags, "image_caption_linked", "caption_linked"]))
                new_blocks.append(
                    _RecoveredBlock(
                        role=block.role,
                        block_type=block.block_type,
                        text=block.text,
                        page_start=block.page_start,
                        page_end=block.page_end,
                        bbox_regions=list(block.bbox_regions),
                        reading_order_index=block.reading_order_index,
                        parse_confidence=block.parse_confidence,
                        flags=tagged_flags,
                        font_size_avg=block.font_size_avg,
                        source_path=block.source_path,
                        anchor=block.anchor,
                        metadata=tagged_metadata,
                    )
                )
                continue
            new_blocks.append(block)
        return new_blocks

    def _link_artifact_captions(self, recovered_blocks: list[_RecoveredBlock]) -> None:
        # Captions already claimed by figure-clustering must not be
        # re-claimed by the spatial heuristic — otherwise we double-link
        # adjacent images on the same page to the same caption.
        claimed_caption_indexes: set[int] = {
            index
            for index, block in enumerate(recovered_blocks)
            if block.role == "caption"
            and (block.metadata or {}).get("caption_for_source_anchor")
        }
        for artifact_index, artifact_block in enumerate(recovered_blocks):
            if artifact_block.role not in {"image", "table_like", "equation", "figure"}:
                continue
            # Already linked by clustering — leave clustering's choice alone.
            if (artifact_block.metadata or {}).get("linked_caption_source_anchor"):
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
        next_page_candidates: list[tuple[float, float, int, int]] = []
        for candidate_index, candidate in enumerate(recovered_blocks):
            if candidate_index == artifact_index or candidate_index in claimed_caption_indexes:
                continue
            if candidate.role != "caption":
                continue
            artifact_role = self._normalized_artifact_caption_role(artifact_block)
            if not self._caption_candidate_matches_artifact_role(candidate, artifact_role):
                continue
            same_page = (
                candidate.page_start == artifact_block.page_start
                and candidate.page_end == artifact_block.page_end
            )
            next_page = (
                candidate.page_start == artifact_block.page_end + 1
                and candidate.page_start == candidate.page_end
            )
            if same_page:
                candidate_bbox = self._page_bbox(candidate, artifact_block.page_start)
            elif next_page:
                candidate_bbox = self._page_bbox(candidate, candidate.page_start)
            else:
                continue
            if candidate_bbox is None:
                continue
            overlap_ratio = self._horizontal_overlap_ratio(artifact_bbox, candidate_bbox)
            if overlap_ratio < 0.2:
                continue
            center_distance = abs(
                ((candidate_bbox[0] + candidate_bbox[2]) / 2.0)
                - ((artifact_bbox[0] + artifact_bbox[2]) / 2.0)
            )
            ordinal_distance = abs(candidate.reading_order_index - artifact_block.reading_order_index)
            if same_page:
                below_gap = candidate_bbox[1] - artifact_bbox[3]
                below_lower_bound = -48.0 if artifact_role == "image" else -12.0
                if below_lower_bound <= below_gap <= 120.0:
                    below_candidates.append(
                        (max(below_gap, 0.0), center_distance, ordinal_distance, candidate_index)
                    )
                    continue
                above_gap = artifact_bbox[1] - candidate_bbox[3]
                if -12.0 <= above_gap <= 80.0:
                    above_candidates.append(
                        (max(above_gap, 0.0), center_distance, ordinal_distance, candidate_index)
                    )
                    continue
            elif next_page:
                if (
                    candidate_bbox[1] <= 220.0
                    and artifact_bbox[3] >= artifact_bbox[1] + 120.0
                ):
                    next_page_candidates.append(
                        (candidate_bbox[1], center_distance, ordinal_distance, candidate_index)
                    )
        if below_candidates:
            return min(below_candidates)[3]
        if above_candidates:
            return min(above_candidates)[3]
        if next_page_candidates:
            return min(next_page_candidates)[3]
        return None

    def _caption_candidate_matches_artifact_role(
        self,
        candidate: _RecoveredBlock,
        artifact_role: str,
    ) -> bool:
        if _caption_matches_artifact_role(candidate.text, artifact_role):
            return True
        return artifact_role == "image" and bool(candidate.metadata.get("pdf_contextual_image_legend"))

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
        *,
        pages: list[PdfPage] | None = None,
    ) -> list[ParsedChapter]:
        # Page-level sanity lookup (PDF v2 M2.1). Pages default to empty when
        # callers haven't wired the new kwarg; in that case every block keeps
        # its default provenance="text_layer".
        return pdf_chapters.build_chapters(
            recovered_blocks,
            outline_entries,
            profile,
            file_path,
            pages=pages,
        )

    def _looks_like_frontmatter_chunk(self, blocks: list[_RecoveredBlock]) -> bool:
        return pdf_chapters.looks_like_frontmatter_chunk(blocks)




def build_default_recovery_service(
    *,
    settings: Any | None = None,
    allow_ocr_reextraction: bool = True,
) -> PdfStructureRecoveryService:
    """Construct a PdfStructureRecoveryService configured from ``Settings``.

    When ``settings.pdf_sanity_ocr_reextraction`` is on (and allowed), we
    attach a `SuryaOcrReextractionAdapter`; otherwise we return a plain
    service. The OCR parser disallows it: its pages already come from OCR.
    Callers that want explicit control can instantiate
    `PdfStructureRecoveryService(ocr_reextraction_adapter=...)` directly.
    """
    if settings is None:
        settings = _default_settings()
    figure_config = _resolve_default_figure_cluster_config(settings)
    if not allow_ocr_reextraction or not getattr(settings, "pdf_sanity_ocr_reextraction", False):
        return PdfStructureRecoveryService(figure_cluster_config=figure_config)
    # Lazy import to avoid pulling Surya-runtime deps into every code path
    # that merely constructs a parser.
    from book_agent.ingestion.pdf.surya_reextraction import (
        SuryaOcrReextractionAdapter,
    )

    return PdfStructureRecoveryService(
        ocr_reextraction_adapter=SuryaOcrReextractionAdapter(),
        figure_cluster_config=figure_config,
    )


def _default_settings() -> Any | None:
    """The application settings, or None when they cannot be loaded."""
    try:
        from book_agent.core.config import get_settings

        return get_settings()
    except Exception:
        return None


def _resolve_default_figure_cluster_config(settings: Any | None) -> FigureClusterConfig | None:
    """Figure-cluster knobs from ``settings``; None (algorithm defaults) without settings."""
    if settings is None:
        return None
    from book_agent.domain.structure.figure_clustering import figure_cluster_config_from_settings

    return figure_cluster_config_from_settings(settings)


class PDFParser:
    def __init__(
        self,
        extractor: PdfTextExtractor | None = None,
        profiler: PdfFileProfiler | None = None,
        recovery_service: PdfStructureRecoveryService | None = None,
        *,
        image_output_dir: str | Path | None = None,
    ):
        self.extractor = extractor or DefaultPdfTextExtractor(image_output_dir=image_output_dir)
        self.profiler = profiler or PdfFileProfiler(self.extractor)
        self.recovery_service = recovery_service or build_default_recovery_service()

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


@dataclass(frozen=True, slots=True)
class RecoveryContext:
    """Per-document inputs shared by the block recovery passes (read-only)."""

    file_path: str | Path
    extraction: PdfExtraction
    profile: PdfFileProfile
    ordered_pages: list[PdfPage]
    page_contexts: dict[int, _PageRecoveryContext]
    page_layout_assessments: dict[int, _PageLayoutAssessment]

    @property
    def academic_lane(self) -> bool:
        return self.profile.recovery_lane == "academic_paper"


BlockPassFn = Callable[["PdfStructureRecoveryService", list[_RecoveredBlock], RecoveryContext], list[_RecoveredBlock]]


@dataclass(frozen=True, slots=True)
class BlockRecoveryPass:
    name: str
    run: BlockPassFn


def _in_place(method_name: str, *context_args: str, **context_kwargs: str) -> BlockPassFn:
    """Adapt a service method that links blocks in place (returns None)."""

    def run(service, blocks, context):
        getattr(service, method_name)(
            blocks,
            *(getattr(context, arg) for arg in context_args),
            **{key: getattr(context, attr) for key, attr in context_kwargs.items()},
        )
        return blocks

    return run


def _returning(method_name: str, *context_args: str, **context_kwargs: str) -> BlockPassFn:
    def run(service, blocks, context):
        return getattr(service, method_name)(
            blocks,
            *(getattr(context, arg) for arg in context_args),
            **{key: getattr(context, attr) for key, attr in context_kwargs.items()},
        )

    return run


def _document_title_pass(service, blocks, context):
    return service._recover_document_title_heading_blocks(blocks, context.extraction.title)


# Ordered block-shaping passes run by PdfStructureRecoveryService.recover after
# the per-page block recovery and before chapters are built.
_BLOCK_RECOVERY_PASSES: tuple[BlockRecoveryPass, ...] = (
    BlockRecoveryPass("link_footnotes", _in_place("_link_footnotes")),
    BlockRecoveryPass("promote_inline_book_headings", _returning("_promote_inline_book_heading_blocks")),
    BlockRecoveryPass("promote_contextual_image_legends", _returning("_promote_contextual_image_legend_blocks", "ordered_pages")),
    BlockRecoveryPass(
        "recover_embedded_page_headings",
        _returning("_recover_embedded_page_heading_blocks", academic_lane="academic_lane"),
    ),
    BlockRecoveryPass("recover_document_title_headings", _document_title_pass),
    BlockRecoveryPass("recover_academic_sections", _returning("_recover_academic_section_blocks", "profile")),
    BlockRecoveryPass("populate_missing_heading_levels", _returning("_populate_missing_heading_levels")),
    BlockRecoveryPass("merge_heading_continuations", _returning("_merge_adjacent_heading_continuations", "ordered_pages")),
    BlockRecoveryPass("repair_prose_artifact_continuations", _returning("_repair_prose_artifact_continuations", "ordered_pages")),
    BlockRecoveryPass("merge_same_anchor_code", _returning("_merge_same_anchor_code_continuations")),
    BlockRecoveryPass("merge_cross_page_code", _returning("_merge_cross_page_code_continuations", "ordered_pages")),
    BlockRecoveryPass("merge_cross_page_prose", _returning("_merge_cross_page_prose_continuations", "ordered_pages")),
    BlockRecoveryPass("split_mixed_code_prose", _returning("_split_mixed_code_prose_blocks")),
    BlockRecoveryPass("promote_late_code_bodies", _returning("_promote_late_code_like_bodies")),
    BlockRecoveryPass("split_mixed_code_prose_again", _returning("_split_mixed_code_prose_blocks")),
    BlockRecoveryPass("promote_late_table_bodies", _returning("_promote_late_table_like_bodies")),
    BlockRecoveryPass("merge_table_fragments", _returning("_merge_adjacent_table_fragments", "ordered_pages")),
    BlockRecoveryPass("lock_listing_scope", _returning("_lock_listing_scope")),
    BlockRecoveryPass("figure_clustering", _returning("_apply_figure_clustering")),
    BlockRecoveryPass("recover_text_only_figures", _returning("_recover_text_only_figures")),
    BlockRecoveryPass("link_artifact_captions", _in_place("_link_artifact_captions")),
    BlockRecoveryPass("link_artifact_group_contexts", _in_place("_link_artifact_group_contexts", academic_paper="academic_lane")),
)
