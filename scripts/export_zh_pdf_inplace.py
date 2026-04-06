#!/usr/bin/env python3
"""Export a translated PDF using the in-place text replacement engine.

Usage:
    python scripts/export_zh_pdf_inplace.py [--book-index 0] [--dry-run] [--output PATH]

Reads translations from the book's SQLite DB and replaces English text
in the source PDF with Chinese, preserving all layout, images, and figures.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from book_agent.services.pdf_inplace import (
    _DbBlock,
    build_replacement_plans,
    execute_replacement,
    export_pdf_inplace,
    extract_text_runs,
    group_runs_into_paragraphs,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

STATE_JSON = Path("artifacts/review/translate-agent-rollout-state-current.json")

# Block types that should be protected from replacement
PROTECTED_BLOCK_TYPES = {"code", "equation", "table", "image", "figure"}


def load_state() -> list[dict]:
    """Load the rollout state JSON to find book databases."""
    if not STATE_JSON.exists():
        logger.error("State file not found: %s", STATE_JSON)
        sys.exit(1)
    with open(STATE_JSON) as f:
        return json.load(f)


def extract_blocks_with_translations(db_path: str) -> list[_DbBlock]:
    """Extract blocks with their translations directly from SQLite.

    Uses the same query pattern as generate_bilingual_markdown.py but
    also extracts source_span_json for page/bbox information.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get all active chapters (skip frontmatter-like ones)
    chapters = conn.execute("""
        SELECT c.id, c.ordinal, c.title_src
        FROM chapters c
        JOIN documents d ON d.id = c.document_id
        ORDER BY c.ordinal
    """).fetchall()

    all_blocks: list[_DbBlock] = []

    for chapter in chapters:
        chapter_id = chapter["id"]
        chapter_title = chapter["title_src"] or ""

        # Get blocks with bbox info
        blocks = conn.execute("""
            SELECT b.id, b.ordinal, b.block_type, b.source_text,
                   b.source_span_json, b.protected_policy
            FROM blocks b
            WHERE b.chapter_id = ?
              AND b.status = 'active'
            ORDER BY b.ordinal
        """, (chapter_id,)).fetchall()

        # Get translations via alignment_edges
        segments = conn.execute("""
            SELECT
                b.id as block_id,
                b.ordinal as block_ordinal,
                ts.text_zh,
                ts.id as ts_id
            FROM alignment_edges ae
            JOIN sentences s ON s.id = ae.sentence_id
            JOIN blocks b ON b.id = s.block_id
            JOIN target_segments ts ON ts.id = ae.target_segment_id
            JOIN translation_runs tr ON tr.id = ts.translation_run_id
            WHERE s.chapter_id = ?
              AND ts.final_status != 'superseded'
              AND tr.status = 'succeeded'
            ORDER BY b.ordinal, ts.ordinal
        """, (chapter_id,)).fetchall()

        # Build block_id → translations map (deduped)
        block_translations: dict[str, list[str]] = {}
        seen_keys: set[tuple[str, str]] = set()
        for seg in segments:
            block_id = seg["block_id"]
            ts_id = seg["ts_id"]
            key = (block_id, ts_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if block_id not in block_translations:
                block_translations[block_id] = []
            text_zh = seg["text_zh"]
            if text_zh:
                block_translations[block_id].append(text_zh)

        # Build _DbBlock objects
        for block in blocks:
            block_id = block["id"]
            source_text = block["source_text"] or ""
            block_type = block["block_type"] or "paragraph"
            protected_policy = block["protected_policy"] or "translate"

            # Parse source_span_json for page/bbox
            span_json_str = block["source_span_json"]
            if not span_json_str:
                continue
            try:
                span_json = json.loads(span_json_str) if isinstance(span_json_str, str) else span_json_str
            except (json.JSONDecodeError, TypeError):
                continue

            # Extract page number
            page_num = None
            bbox_raw = None
            source_bbox_json = span_json.get("source_bbox_json")
            if isinstance(source_bbox_json, dict):
                regions = source_bbox_json.get("regions")
                if isinstance(regions, list) and regions:
                    first_region = regions[0]
                    if isinstance(first_region, dict):
                        page_num = first_region.get("page_number")
                        bbox_raw = first_region.get("bbox")
            if page_num is None:
                page_num = span_json.get("source_page_start")
            if page_num is None:
                continue

            page_index = page_num - 1  # DB is 1-indexed, PyMuPDF is 0-indexed
            if page_index < 0:
                continue

            if bbox_raw is None or not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                continue

            bbox = (float(bbox_raw[0]), float(bbox_raw[1]), float(bbox_raw[2]), float(bbox_raw[3]))

            # Get translation
            trans_parts = block_translations.get(block_id, [])
            target_text = "\n".join(trans_parts) if trans_parts else ""

            # Skip header/footer/toc_entry blocks
            pdf_role = span_json.get("pdf_block_role", "")
            if pdf_role in ("header", "footer", "toc_entry"):
                continue

            all_blocks.append(_DbBlock(
                block_id=block_id,
                page_index=page_index,
                bbox=bbox,
                source_text=source_text,
                target_text=target_text,
                block_type=block_type,
                protected_policy=protected_policy,
            ))

    conn.close()
    logger.info("Loaded %d blocks from DB (%d with translations)",
                len(all_blocks),
                sum(1 for b in all_blocks if b.target_text))
    return all_blocks


def main():
    parser = argparse.ArgumentParser(description="Export translated PDF in-place")
    parser.add_argument("--book-index", type=int, default=0, help="Book index in state JSON")
    parser.add_argument("--dry-run", action="store_true", help="Compute plans but don't modify PDF")
    parser.add_argument("--output", type=str, default=None, help="Output PDF path")
    parser.add_argument("--source-pdf", type=str, default=None, help="Override source PDF path")
    args = parser.parse_args()

    state = load_state()
    # State JSON is a dict with a "books" key containing a list
    books = state if isinstance(state, list) else state.get("books", [])
    if args.book_index >= len(books):
        logger.error("Book index %d out of range (have %d books)", args.book_index, len(books))
        sys.exit(1)

    book = books[args.book_index]
    live_state = book.get("live_state", {})
    root = live_state.get("root", "")
    db_path = str(Path(root) / "book-agent.db") if root else None
    source_pdf = args.source_pdf or live_state.get("source_path") or book.get("path")

    if not db_path or not Path(db_path).exists():
        logger.error("DB not found: %s", db_path)
        sys.exit(1)
    if not source_pdf or not Path(source_pdf).exists():
        logger.error("Source PDF not found: %s", source_pdf)
        sys.exit(1)

    logger.info("Book: %s", book.get("title", "Unknown"))
    logger.info("Source PDF: %s", source_pdf)
    logger.info("DB: %s", db_path)

    # Extract blocks with translations from DB
    db_blocks = extract_blocks_with_translations(db_path)
    if not db_blocks:
        logger.error("No blocks found in DB")
        sys.exit(1)

    # Extract text runs from PDF
    import fitz
    logger.info("Extracting text runs from source PDF...")
    doc = fitz.open(source_pdf)
    runs = extract_text_runs(doc)
    logger.info("Extracted %d text runs from %d pages", len(runs), len(doc))
    doc.close()

    # Group into paragraphs
    paragraphs_by_page = group_runs_into_paragraphs(runs)
    total_paras = sum(len(v) for v in paragraphs_by_page.values())
    logger.info("Formed %d paragraphs", total_paras)

    # Build replacement plans
    plans = build_replacement_plans(paragraphs_by_page, db_blocks)
    replace_count = sum(1 for p in plans if p.replace_policy == "replace")
    protect_count = sum(1 for p in plans if p.replace_policy == "protect")
    skip_count = sum(1 for p in plans if p.replace_policy == "skip")
    logger.info("Plans: %d replace, %d protect, %d skip", replace_count, protect_count, skip_count)

    # Determine output path
    output_path = args.output
    if not output_path:
        source_stem = Path(source_pdf).stem
        output_dir = Path("artifacts/review/zh-pdf-inplace")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"{source_stem}-zh.pdf")

    logger.info("Output: %s", output_path)

    # Execute replacement
    result = execute_replacement(
        source_pdf_path=source_pdf,
        output_path=output_path,
        plans=plans,
        dry_run=args.dry_run,
    )

    # Report
    print(f"\n{'=' * 60}")
    print(f"PDF In-Place Export Result")
    print(f"{'=' * 60}")
    print(f"Output:           {result.output_path}")
    print(f"Total blocks:     {result.total_blocks}")
    print(f"Replaced:         {result.replaced_blocks}")
    print(f"Protected:        {result.protected_blocks}")
    print(f"Skipped:          {result.skipped_blocks}")
    print(f"Coverage:         {result.coverage_pct:.1f}%")
    print(f"Pages modified:   {result.pages_modified}")
    print(f"Time:             {result.elapsed_seconds:.1f}s")

    if result.font_reductions:
        reductions = result.font_reductions
        avg_reduction = sum(r.reduction_pct for r in reductions) / len(reductions)
        max_reduction = max(r.reduction_pct for r in reductions)
        print(f"\nFont reductions:  {len(reductions)} blocks")
        print(f"  Average:        {avg_reduction:.1f}%")
        print(f"  Maximum:        {max_reduction:.1f}%")

    if result.warnings:
        print(f"\nWarnings ({len(result.warnings)}):")
        for w in result.warnings[:30]:
            print(f"  - {w}")
        if len(result.warnings) > 30:
            print(f"  ... and {len(result.warnings) - 30} more")

    print(f"{'=' * 60}")

    if args.dry_run:
        print("\n[DRY RUN] No PDF was modified.")
    else:
        print(f"\nOutput saved to: {result.output_path}")
        print(f"File size: {Path(result.output_path).stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
