#!/usr/bin/env python3
"""Production-grade PDF image extraction.

Strategy:
- Cluster DB image blocks into logical figures (by page + spatial proximity)
- For each figure, compute TRUE bounding box by analyzing:
  * DB block bboxes
  * Embedded image positions on the page
  * Vector drawing paths (arrows, connectors, shapes)
  * Caption text below the figure
- Simple single-raster figures → extract embedded image (pixel-perfect)
- Composite/vector figures → render full region at high DPI (captures everything)
- DB pages are 1-indexed; PyMuPDF is 0-indexed → offset by -1
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF


@dataclass
class ImageBlock:
    block_id: str
    ordinal: int
    ch_ord: int
    page: int        # corrected 0-indexed
    bbox: list[float]


@dataclass
class FigureCluster:
    """A logical figure composed of one or more image blocks."""
    blocks: list[ImageBlock] = field(default_factory=list)
    page: int = 0

    @property
    def db_bbox(self) -> tuple[float, float, float, float]:
        """Union of all block bboxes."""
        x0 = min(b.bbox[0] for b in self.blocks)
        y0 = min(b.bbox[1] for b in self.blocks)
        x1 = max(b.bbox[2] for b in self.blocks)
        y1 = max(b.bbox[3] for b in self.blocks)
        return (x0, y0, x1, y1)

    @property
    def primary_block(self) -> ImageBlock:
        """The block with the largest bbox area (main figure block)."""
        return max(self.blocks, key=lambda b: (b.bbox[2]-b.bbox[0])*(b.bbox[3]-b.bbox[1]))


def cluster_blocks_on_page(blocks: list[ImageBlock], overlap_threshold: float = 30) -> list[FigureCluster]:
    """Cluster blocks on the same page into logical figures by spatial proximity."""
    if not blocks:
        return []
    if len(blocks) == 1:
        return [FigureCluster(blocks=blocks, page=blocks[0].page)]

    # Sort by y-position
    sorted_blocks = sorted(blocks, key=lambda b: b.bbox[1])

    clusters: list[FigureCluster] = []
    current = FigureCluster(blocks=[sorted_blocks[0]], page=sorted_blocks[0].page)

    for blk in sorted_blocks[1:]:
        # Check if this block overlaps with the current cluster's bbox
        cx0, cy0, cx1, cy1 = current.db_bbox
        bx0, by0, bx1, by1 = blk.bbox

        # Vertical overlap or proximity
        v_overlap = min(cy1, by1) - max(cy0, by0)
        v_gap = max(0, by0 - cy1)

        # Horizontal overlap
        h_overlap = min(cx1, bx1) - max(cx0, bx0)

        if v_overlap > -overlap_threshold and (h_overlap > -overlap_threshold or v_gap < overlap_threshold):
            current.blocks.append(blk)
        else:
            clusters.append(current)
            current = FigureCluster(blocks=[blk], page=blk.page)

    clusters.append(current)
    return clusters


def compute_true_figure_bbox(
    page: fitz.Page,
    cluster: FigureCluster,
    page_embeds: list[dict],
    padding: float = 6,
) -> fitz.Rect:
    """Compute the true bounding box of a figure by analyzing all visual elements."""
    db_x0, db_y0, db_x1, db_y1 = cluster.db_bbox

    # Start with DB bbox
    rects = [fitz.Rect(db_x0, db_y0, db_x1, db_y1)]

    # Determine if figure region is a callout/sidebar box vs a diagram
    # Callout boxes: filled rectangle background + lots of text + no embedded images
    db_rect = fitz.Rect(db_x0, db_y0, db_x1, db_y1)
    text_in_region = page.get_text("text", clip=db_rect)
    has_nearby_embed = any(
        (e["bbox"][0] < db_x1 + 30 and e["bbox"][2] > db_x0 - 30 and
         e["bbox"][1] < db_y1 + 30 and e["bbox"][3] > db_y0 - 30)
        for e in page_embeds
    )
    is_text_heavy = len(text_in_region.strip()) > 400 and not has_nearby_embed

    # Collect nearby drawing paths
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []

    nearby_drawing_rects = []
    scan_margin = 40
    for d in drawings:
        r = d.get("rect")
        if r and not r.is_empty and r.width > 5 and r.height > 5:
            if (r.x0 < db_x1 + scan_margin and r.x1 > db_x0 - scan_margin and
                r.y0 < db_y1 + scan_margin and r.y1 > db_y0 - scan_margin):
                nearby_drawing_rects.append(r)

    # For text-heavy regions (callout boxes): use drawing paths as primary boundary
    # For diagrams: use DB bbox + expand with drawings and embedded images
    if is_text_heavy and nearby_drawing_rects:
        # Callout box: start from drawing rects instead of DB bbox
        rects = list(nearby_drawing_rects)
    else:
        margin = 40
        # Add embedded images that overlap with the DB bbox region
        for emb in page_embeds:
            eb = emb["bbox"]
            if (eb[0] < db_x1 + margin and eb[2] > db_x0 - margin and
                eb[1] < db_y1 + margin and eb[3] > db_y0 - margin):
                rects.append(fitz.Rect(eb))

        # Add drawing paths
        for r in nearby_drawing_rects:
            rects.append(r)

    # Compute union
    result = rects[0]
    for r in rects[1:]:
        result |= r

    # Only add caption for non-text-heavy figures (diagrams/charts)
    if not is_text_heavy:
        caption_bottom = result.y1 + 40
        try:
            text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
            for blk in text_dict.get("blocks", []):
                if blk.get("type") != 0:
                    continue
                bb = blk["bbox"]
                if (bb[1] >= result.y1 - 5 and bb[1] < caption_bottom and
                    bb[0] < result.x1 + 20 and bb[2] > result.x0 - 20):
                    text = ""
                    for line in blk.get("lines", []):
                        for span in line.get("spans", []):
                            text += span.get("text", "")
                    text = text.strip()
                    # Only include actual figure/table captions
                    if text.lower().startswith(("figure", "fig.", "fig ", "table", "listing")):
                        result |= fitz.Rect(bb)
        except Exception:
            pass

    # Apply padding and clamp to page bounds
    result.x0 = max(0, result.x0 - padding)
    result.y0 = max(0, result.y0 - padding)
    result.x1 = min(page.rect.width, result.x1 + padding)
    result.y1 = min(page.rect.height, result.y1 + padding)

    return result


def is_simple_raster_figure(
    cluster: FigureCluster,
    page: fitz.Page,
    page_embeds: list[dict],
) -> tuple[bool, int | None]:
    """Check if figure is a single embedded image with no significant vector content.
    Returns (is_simple, xref_or_none).
    """
    if len(cluster.blocks) > 2:
        return False, None

    db_x0, db_y0, db_x1, db_y1 = cluster.db_bbox

    # Find matching embedded images
    matching_xrefs = []
    for emb in page_embeds:
        eb = emb["bbox"]
        dx = abs((db_x0+db_x1)/2 - (eb[0]+eb[2])/2)
        dy = abs((db_y0+db_y1)/2 - (eb[1]+eb[3])/2)
        if (dx**2 + dy**2)**0.5 < 40:
            matching_xrefs.append(emb["xref"])

    if len(matching_xrefs) != 1:
        return False, None

    # Check for significant drawings in the area
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []

    significant_drawings = 0
    for d in drawings:
        r = d.get("rect")
        if r and not r.is_empty:
            if (r.x0 < db_x1 + 20 and r.x1 > db_x0 - 20 and
                r.y0 < db_y1 + 20 and r.y1 > db_y0 - 20):
                # Check if it's a significant drawing (not just a line)
                area = r.width * r.height
                if area > 100:
                    significant_drawings += 1

    if significant_drawings > 2:
        return False, None

    return True, matching_xrefs[0]


def extract_all_figures(
    pdf_path: str,
    db_path: str,
    output_dir: Path,
    render_dpi: int = 300,
) -> dict[str, str]:
    """Extract all figures from PDF. Returns block_id -> relative path mapping."""
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    conn = sqlite3.connect(db_path)

    # Load all image blocks
    rows = conn.execute('''
        SELECT b.id, b.ordinal, b.source_span_json, c.ordinal as ch_ord
        FROM blocks b
        JOIN chapters c ON c.id = b.chapter_id
        WHERE b.block_type = 'image'
        ORDER BY c.ordinal, b.ordinal
    ''').fetchall()

    # Parse into ImageBlock objects, grouped by page
    page_block_map: dict[int, list[ImageBlock]] = defaultdict(list)
    all_blocks: dict[str, ImageBlock] = {}

    for block_id, blk_ord, span_json, ch_ord in rows:
        span = json.loads(span_json) if span_json else {}
        regions = span.get("source_bbox_json", {}).get("regions", [])
        if not regions:
            continue
        real_page = regions[0]["page_number"] - 1
        if real_page < 0 or real_page >= len(doc):
            continue
        bbox = regions[0]["bbox"]
        ib = ImageBlock(block_id=block_id, ordinal=blk_ord, ch_ord=ch_ord,
                        page=real_page, bbox=bbox)
        page_block_map[real_page].append(ib)
        all_blocks[block_id] = ib

    # Build page -> embedded images map
    page_embeds: dict[int, list[dict]] = {}
    for page_idx in range(len(doc)):
        info = doc[page_idx].get_image_info(xrefs=True)
        if info:
            page_embeds[page_idx] = [
                {"xref": img["xref"], "bbox": img["bbox"],
                 "width": img["width"], "height": img["height"]}
                for img in info
            ]

    zoom = render_dpi / 72
    render_mat = fitz.Matrix(zoom, zoom)

    block_to_path: dict[str, str] = {}
    used_xrefs: set[int] = set()
    stats = {"simple_embed": 0, "composite_render": 0, "skipped": 0}

    # Process each page
    for page_idx in sorted(page_block_map.keys()):
        page = doc[page_idx]
        blocks = page_block_map[page_idx]
        embeds = page_embeds.get(page_idx, [])

        # Cluster blocks into logical figures
        clusters = cluster_blocks_on_page(blocks)

        for cluster in clusters:
            primary = cluster.primary_block
            fname_base = f"ch{primary.ch_ord:02d}-p{page_idx:03d}-blk{primary.ordinal:04d}"

            # Check if simple raster figure
            is_simple, xref = is_simple_raster_figure(cluster, page, embeds)

            if is_simple and xref and xref not in used_xrefs:
                # Extract embedded image directly (pixel-perfect)
                try:
                    pix = fitz.Pixmap(doc, xref)
                    if pix.n - pix.alpha > 3:  # CMYK → RGB
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    fname = f"{fname_base}.png"
                    out_path = output_dir / fname
                    pix.save(str(out_path))
                    used_xrefs.add(xref)

                    rel_path = f"assets/{fname}"
                    for blk in cluster.blocks:
                        block_to_path[blk.block_id] = rel_path
                    stats["simple_embed"] += 1
                    continue
                except Exception as e:
                    print(f"  WARN embed xref={xref}: {e}")
                    # Fall through to rendering

            # Composite or vector figure → compute true bbox and render
            true_rect = compute_true_figure_bbox(page, cluster, embeds)

            if true_rect.width < 2 or true_rect.height < 2:
                stats["skipped"] += len(cluster.blocks)
                continue

            try:
                pix = page.get_pixmap(matrix=render_mat, clip=true_rect)
                if pix.width < 2 or pix.height < 2:
                    stats["skipped"] += len(cluster.blocks)
                    continue

                fname = f"{fname_base}.png"
                out_path = output_dir / fname
                pix.save(str(out_path))

                rel_path = f"assets/{fname}"
                for blk in cluster.blocks:
                    block_to_path[blk.block_id] = rel_path
                stats["composite_render"] += 1
            except Exception as e:
                print(f"  WARN render p{page_idx} blk.{primary.ordinal}: {e}")
                stats["skipped"] += len(cluster.blocks)

    doc.close()
    conn.close()

    total_figs = stats["simple_embed"] + stats["composite_render"]
    print(f"Extracted {total_figs} figures ({len(block_to_path)} block refs) to {output_dir}")
    print(f"  Embedded (pixel-perfect): {stats['simple_embed']}")
    print(f"  Composite renders (full): {stats['composite_render']}")
    print(f"  Skipped:                  {stats['skipped']}")
    return block_to_path


def update_markdown(
    md_path: Path,
    db_path: str,
    block_to_path: dict[str, str],
):
    """Replace image placeholders in markdown with proper references."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute('''
        SELECT b.id, b.ordinal, c.ordinal as ch_ord
        FROM blocks b
        JOIN chapters c ON c.id = b.chapter_id
        WHERE b.block_type = 'image'
        ORDER BY c.ordinal, b.ordinal
    ''').fetchall()
    conn.close()

    # Ordered image path list (None for missing)
    ordered_paths: list[str | None] = []
    seen_paths: set[str] = set()
    for block_id, blk_ord, ch_ord in rows:
        path = block_to_path.get(block_id)
        if path and path in seen_paths:
            ordered_paths.append(None)  # Deduplicate: skip sub-blocks of same figure
        else:
            ordered_paths.append(path)
            if path:
                seen_paths.add(path)

    content = md_path.read_text(encoding="utf-8")
    lines = content.split("\n")
    new_lines = []
    img_idx = 0

    for line in lines:
        stripped = line.strip()
        is_placeholder = (
            stripped == "[Image]"
            or stripped == "*[未翻译]* [Image]"
            or (stripped.startswith("![图片](") and stripped.endswith(")"))
        )

        if is_placeholder:
            if img_idx < len(ordered_paths):
                path = ordered_paths[img_idx]
                if path:
                    new_lines.append(f"![图片]({path})")
                    new_lines.append("")
                else:
                    pass  # Skip duplicate sub-block placeholder
                img_idx += 1
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    md_path.write_text("\n".join(new_lines), encoding="utf-8")
    unique_figs = len(seen_paths)
    print(f"Updated {md_path.name}: {unique_figs} unique figures referenced")


def main():
    pdf_path = "/Volumes/XY_IMG/zlibrary/new20260325/How Large Language Models Work (Edward Raff, Drew Farris, Stella Biderman) (z-library.sk, 1lib.sk, z-lib.sk).pdf"
    db_path = "/Users/smy/project/book-agent/artifacts/real-book-live/translate-agent-autopilot-00-how-large-language-models-work-edward-raff-drew-farris-stella-biderman-z-library-sk-1lib-sk-z-lib-sk/book-agent.db"

    output_dir = Path("artifacts/review/bilingual-markdown-deliverables/assets")
    md_path = Path("artifacts/review/bilingual-markdown-deliverables/book-00-How-Large-Language-Models-Work.md")

    # Regenerate markdown first to reset placeholders
    print("Step 1: Extracting figures from PDF...")
    block_to_path = extract_all_figures(pdf_path, db_path, output_dir)

    print("\nStep 2: Updating markdown...")
    update_markdown(md_path, db_path, block_to_path)


if __name__ == "__main__":
    main()
