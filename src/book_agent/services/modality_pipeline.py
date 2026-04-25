"""Modality pipeline orchestrator (PDF v2 M3 wire-up / TATR-c).

Single entry point that runs the four M3 modality enhancers in a
deterministic order over a `ParsedDocument`. Each enhancer can be
toggled independently so production rollouts can shadow-deploy one
modality at a time, observe telemetry, then switch defaults — same
gradual-rollout discipline as M2.3.

Order rationale:

  1. **References** — once classified, the entire section is locked
     to `translate_none`. Doing this first means later enhancers
     don't waste cycles on bibliography blocks.
  2. **Equations** — sets render_mode and translatability before any
     downstream block-walking that might mis-classify operators as
     prose.
  3. **Tables** — heuristic per-block + optional TATR post-pass. TATR
     overrides heuristic results when both produce markdown (TATR is
     higher fidelity when available).
  4. **Images** — final pass that enforces image/figure translate_none
     and re-enables captions, shielding against any earlier flip.

`ModalityPipelineSummary` aggregates per-modality counts; consumers
fan it out to telemetry / review UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace as _replace
from typing import TYPE_CHECKING

from book_agent.domain.structure.models import (
    ParsedBlock,
    ParsedChapter,
    ParsedDocument,
)
from book_agent.services.equation_extractor import (
    EquationLatexAdapter,
    enhance_block_for_equation,
)
from book_agent.services.image_modality import (
    ImageEnhancementSummary,
    enhance_document_image_modality,
)
from book_agent.services.references_extractor import (
    ReferencesProtectionResult,
    protect_references_section,
)
from book_agent.services.table_extractor import (
    TableExtractorAdapter,
    enhance_block_for_table,
)

if TYPE_CHECKING:
    from book_agent.services.tatr_extractor import (
        PageImageTableExtractor,
    )


@dataclass(slots=True, frozen=True)
class ModalityPipelineOptions:
    """Feature flags + adapter overrides for the pipeline.

    All flags default OFF so calling `enhance_parsed_document` with no
    options is a no-op — production must opt in explicitly. This keeps
    behaviour reversible at the call site.
    """

    enable_references: bool = False
    enable_equations: bool = False
    enable_tables: bool = False
    enable_images: bool = False

    # Adapter overrides — fall back to the modules' defaults when None.
    table_extractor: TableExtractorAdapter | None = None
    equation_adapter: EquationLatexAdapter | None = None
    page_image_table_extractor: "PageImageTableExtractor | None" = None


@dataclass(slots=True)
class ModalityPipelineSummary:
    """Aggregated counts after a pipeline run."""

    references: ReferencesProtectionResult | None = None
    equations_enhanced: int = 0
    table_blocks_enhanced: int = 0
    table_markdowns_recovered: int = 0
    tatr_tables_recovered: int = 0
    images: ImageEnhancementSummary | None = None
    skipped_due_to_disabled: list[str] = field(default_factory=list)


def enhance_parsed_document(
    document: ParsedDocument,
    *,
    options: ModalityPipelineOptions | None = None,
) -> tuple[ParsedDocument, ModalityPipelineSummary]:
    """Run the M3 modality enhancers over `document` per `options`.

    Returns a NEW document (never mutates the input) and a summary.
    Each modality is independently togglable; a disabled modality is
    recorded in `summary.skipped_due_to_disabled` so observability can
    distinguish "ran and produced nothing" from "wasn't asked to run".
    """
    opts = options or ModalityPipelineOptions()
    summary = ModalityPipelineSummary()
    current = document

    # 1. References protection — coarsest first.
    if opts.enable_references:
        current, ref_result = protect_references_section(current)
        summary.references = ref_result
    else:
        summary.skipped_due_to_disabled.append("references")

    # 2. Equations.
    if opts.enable_equations:
        current, eq_count = _apply_per_block(
            current,
            transform=lambda b: enhance_block_for_equation(
                b, adapter=opts.equation_adapter
            ),
            block_type_filter={"equation"},
        )
        summary.equations_enhanced = eq_count
    else:
        summary.skipped_due_to_disabled.append("equations")

    # 3. Tables — heuristic per-block, then TATR post-pass when adapter
    # is provided. TATR runs page-by-page so we route at the doc level.
    if opts.enable_tables:
        # 3a. Heuristic.
        current, table_block_count, recovered_md_count = _apply_table_blocks(
            current, extractor=opts.table_extractor
        )
        summary.table_blocks_enhanced = table_block_count
        summary.table_markdowns_recovered = recovered_md_count

        # 3b. TATR override — when adapter provided AND a table block
        # got NO heuristic markdown, ask TATR to try at the page level.
        if opts.page_image_table_extractor is not None:
            current, tatr_count = _apply_tatr_post_pass(
                current, extractor=opts.page_image_table_extractor
            )
            summary.tatr_tables_recovered = tatr_count
    else:
        summary.skipped_due_to_disabled.append("tables")

    # 4. Images / figures — final invariant pass.
    if opts.enable_images:
        current, img_summary = enhance_document_image_modality(current)
        summary.images = img_summary
    else:
        summary.skipped_due_to_disabled.append("images")

    return current, summary


# --- Internals ---


def _apply_per_block(
    document: ParsedDocument,
    *,
    transform,
    block_type_filter: set[str] | frozenset[str] | None = None,
) -> tuple[ParsedDocument, int]:
    changed_count = 0
    new_chapters: list[ParsedChapter] = []
    for chapter in document.chapters:
        new_blocks: list[ParsedBlock] = []
        for block in chapter.blocks:
            if block_type_filter and block.block_type not in block_type_filter:
                new_blocks.append(block)
                continue
            transformed = transform(block)
            if transformed is not block:
                changed_count += 1
            new_blocks.append(transformed)
        new_chapters.append(_replace(chapter, blocks=new_blocks))
    rewritten = ParsedDocument(
        title=document.title,
        author=document.author,
        language=document.language,
        chapters=new_chapters,
        metadata=dict(document.metadata),
    )
    return rewritten, changed_count


def _apply_table_blocks(
    document: ParsedDocument,
    *,
    extractor: TableExtractorAdapter | None,
) -> tuple[ParsedDocument, int, int]:
    """Apply the heuristic table enhancer to every `block_type=table`
    block. Returns (rewritten_doc, block_count_seen, markdown_recovered).
    """
    block_count = 0
    md_count = 0
    new_chapters: list[ParsedChapter] = []
    for chapter in document.chapters:
        new_blocks: list[ParsedBlock] = []
        for block in chapter.blocks:
            if block.block_type != "table":
                new_blocks.append(block)
                continue
            block_count += 1
            new_block, structure = enhance_block_for_table(block, extractor=extractor)
            if structure is not None:
                md_count += 1
            new_blocks.append(new_block)
        new_chapters.append(_replace(chapter, blocks=new_blocks))
    rewritten = ParsedDocument(
        title=document.title,
        author=document.author,
        language=document.language,
        chapters=new_chapters,
        metadata=dict(document.metadata),
    )
    return rewritten, block_count, md_count


def _apply_tatr_post_pass(
    document: ParsedDocument,
    *,
    extractor: "PageImageTableExtractor",
) -> tuple[ParsedDocument, int]:
    """Run TATR on every table block that lacks heuristic markdown.

    For each unresolved table block we build a `PageTableExtractionRequest`
    using the block's bbox + page text blocks recovered from sibling
    blocks on the same page. Returns the rewritten document and the
    count of blocks that received TATR-recovered markdown.
    """
    from book_agent.services.tatr_extractor import (
        PageTableExtractionRequest,
    )

    # Build a per-page index of (bbox, text) once for cell-text mapping.
    page_text_blocks_by_page: dict[int, list[tuple]] = {}
    pdf_path: str | None = None
    page_dims_by_page: dict[int, tuple[float, float]] = {}
    for chapter in document.chapters:
        for block in chapter.blocks:
            page = int(block.metadata.get("source_page_start") or 0)
            if page <= 0:
                continue
            pdf_path = pdf_path or _document_pdf_path(document)
            bboxes = (block.metadata.get("source_bbox_json", {}) or {}).get(
                "regions"
            ) or []
            if bboxes and isinstance(bboxes[0], dict):
                bbox = bboxes[0].get("bbox")
                if bbox and len(bbox) >= 4 and block.text:
                    bbox_tuple = (
                        float(bbox[0]),
                        float(bbox[1]),
                        float(bbox[2]),
                        float(bbox[3]),
                    )
                    page_text_blocks_by_page.setdefault(page, []).append(
                        (bbox_tuple, block.text)
                    )

    if pdf_path is None:
        return document, 0

    recovered = 0
    new_chapters: list[ParsedChapter] = []
    for chapter in document.chapters:
        new_blocks: list[ParsedBlock] = []
        for block in chapter.blocks:
            if block.block_type != "table":
                new_blocks.append(block)
                continue
            # Skip if heuristic already produced markdown.
            if block.metadata.get("table_markdown"):
                new_blocks.append(block)
                continue
            page = int(block.metadata.get("source_page_start") or 0)
            if page <= 0:
                new_blocks.append(block)
                continue
            bbox_regions = (
                block.metadata.get("source_bbox_json", {}) or {}
            ).get("regions") or []
            region_bbox = None
            if bbox_regions and isinstance(bbox_regions[0], dict):
                region = bbox_regions[0].get("bbox")
                if region and len(region) >= 4:
                    region_bbox = (
                        float(region[0]),
                        float(region[1]),
                        float(region[2]),
                        float(region[3]),
                    )
            page_dims = page_dims_by_page.get(page) or _guess_page_dims(
                page_text_blocks_by_page.get(page, [])
            )
            request = PageTableExtractionRequest(
                pdf_path=pdf_path,
                page_number=page,
                page_dimensions=page_dims,
                region_bbox=region_bbox,
                page_text_blocks=tuple(page_text_blocks_by_page.get(page, [])),
            )
            tatr_results = extractor.extract(request) or []
            if not tatr_results:
                new_blocks.append(block)
                continue
            best = max(tatr_results, key=lambda t: t.confidence)
            new_metadata = dict(block.metadata)
            new_metadata["table_markdown"] = best.markdown
            new_metadata["table_confidence"] = best.confidence
            new_metadata["table_column_count"] = best.column_count
            new_metadata["table_recovered_via"] = "tatr"
            new_blocks.append(_replace(block, metadata=new_metadata))
            recovered += 1
        new_chapters.append(_replace(chapter, blocks=new_blocks))
    rewritten = ParsedDocument(
        title=document.title,
        author=document.author,
        language=document.language,
        chapters=new_chapters,
        metadata=dict(document.metadata),
    )
    return rewritten, recovered


def _document_pdf_path(document: ParsedDocument) -> str | None:
    metadata = document.metadata or {}
    candidate = metadata.get("source_path") or metadata.get("file_path")
    if isinstance(candidate, str) and candidate:
        return candidate
    return None


def _guess_page_dims(
    page_text_blocks: list[tuple],
) -> tuple[float, float]:
    """Coarse page dimension estimate from observed bbox extrema.

    Used only when the document metadata didn't carry explicit page
    dimensions. TATR uses these to reason about region coords; off-by-
    a-few-percent is fine as long as TATR's geometry stays internally
    consistent.
    """
    if not page_text_blocks:
        return (612.0, 792.0)  # US Letter default
    max_x = max(bbox[2] for bbox, _ in page_text_blocks)
    max_y = max(bbox[3] for bbox, _ in page_text_blocks)
    return (max(max_x, 612.0), max(max_y, 792.0))
