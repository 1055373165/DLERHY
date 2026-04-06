#!/usr/bin/env python3
"""Comprehensive image audit: compare ALL extracted images against PDF source.

For each image block, renders the PDF region as a reference and compares
with the currently extracted image. Generates an HTML report.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

DB_PATH = "artifacts/real-book-live/translate-agent-autopilot-00-how-large-language-models-work-edward-raff-drew-farris-stella-biderman-z-library-sk-1lib-sk-z-lib-sk/book-agent.db"
PDF_PATH = "/Volumes/XY_IMG/zlibrary/new20260325/How Large Language Models Work (Edward Raff, Drew Farris, Stella Biderman) (z-library.sk, 1lib.sk, z-lib.sk).pdf"
IMAGES_DIR = Path("artifacts/review/zh-markdown/images")
REPORT_DIR = Path("artifacts/review/zh-markdown/image-audit")


@dataclass
class ImageBlock:
    block_id: str
    ordinal: int
    page_number: int  # 1-based
    bbox: tuple[float, float, float, float] | None
    image_type: str  # embedded_image or vector_drawing
    image_ext: str | None
    width_px: int | None
    height_px: int | None
    caption: str


def load_image_blocks() -> list[ImageBlock]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT b.id, b.ordinal, b.source_span_json
        FROM blocks b
        JOIN chapters c ON c.id = b.chapter_id
        WHERE b.block_type = 'image' AND b.status = 'active'
        ORDER BY c.ordinal, b.ordinal
    """).fetchall()
    conn.close()

    blocks = []
    for r in rows:
        span = json.loads(r["source_span_json"]) if r["source_span_json"] else {}
        role = span.get("pdf_block_role", "")
        if role in ("header", "footer", "toc_entry"):
            continue

        bbox = None
        sbj = span.get("source_bbox_json")
        if isinstance(sbj, dict):
            regions = sbj.get("regions", [])
            if regions and isinstance(regions[0], dict):
                raw = regions[0].get("bbox")
                if isinstance(raw, (list, tuple)) and len(raw) >= 4:
                    bbox = (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))

        blocks.append(ImageBlock(
            block_id=r["id"],
            ordinal=r["ordinal"],
            page_number=span.get("source_page_start", 0) or 0,
            bbox=bbox,
            image_type=span.get("image_type", "unknown"),
            image_ext=span.get("image_ext"),
            width_px=span.get("image_width_px"),
            height_px=span.get("image_height_px"),
            caption=span.get("image_alt", "") or "",
        ))
    return blocks


def find_existing_image(block: ImageBlock) -> Path | None:
    """Find an existing extracted image for this block."""
    # Convention A: p{page}-{block_id[:8]}.{ext}
    for ext in ("png", "jpeg", "jpg"):
        p = IMAGES_DIR / f"p{block.page_number:03d}-{block.block_id[:8]}.{ext}"
        if p.exists():
            return p
    # Convention B: ch*-p{page}-blk{ordinal}.png
    for f in IMAGES_DIR.glob(f"ch*-p{block.page_number:03d}-blk{block.ordinal:04d}.*"):
        if f.exists():
            return f
    return None


def is_valid_bbox(bbox: tuple[float, float, float, float] | None, page_rect: fitz.Rect) -> tuple[bool, str]:
    """Validate bbox. Returns (valid, reason)."""
    if not bbox:
        return False, "no_bbox"
    x0, y0, x1, y1 = bbox
    if x1 <= x0 or y1 <= y0:
        return False, f"inverted_coords({x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f})"
    if x0 < 0 or y0 < 0:
        return False, f"negative_coords({x0:.0f},{y0:.0f})"
    area = (x1 - x0) * (y1 - y0)
    if area < 50:
        return False, f"tiny_area({area:.0f})"
    # Check if mostly within page bounds (allow 5pt slack)
    pr = page_rect
    if x0 > pr.x1 + 5 or y0 > pr.y1 + 5:
        return False, f"outside_page({x0:.0f},{y0:.0f} vs {pr.x1:.0f},{pr.y1:.0f})"
    return True, "ok"


def render_pdf_region(doc: fitz.Document, page_idx: int, bbox: tuple[float, float, float, float],
                      padding: float = 5.0, dpi: int = 200) -> bytes | None:
    """Render a PDF page region as PNG bytes."""
    if page_idx < 0 or page_idx >= len(doc):
        return None
    page = doc[page_idx]
    x0, y0, x1, y1 = bbox
    clip = fitz.Rect(x0 - padding, y0 - padding, x1 + padding, y1 + padding)
    clip = clip & page.rect
    if clip.is_empty:
        return None
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    try:
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        return pix.tobytes("png")
    except Exception as e:
        print(f"  ERROR rendering page {page_idx}: {e}")
        return None


def match_embedded_image(doc: fitz.Document, page_idx: int,
                         bbox: tuple[float, float, float, float]) -> tuple[int | None, float, str]:
    """Match bbox to a specific embedded image xref. Returns (xref, overlap_ratio, detail)."""
    if page_idx < 0 or page_idx >= len(doc):
        return None, 0.0, "invalid_page"
    page = doc[page_idx]
    block_rect = fitz.Rect(*bbox)
    block_area = block_rect.width * block_rect.height

    best_xref = None
    best_overlap = 0.0
    all_matches = []

    for img_info in page.get_images(full=True):
        xref = img_info[0]
        try:
            rects = page.get_image_rects(xref)
            for r in rects:
                if r.is_empty:
                    continue
                inter = block_rect & r
                if inter.is_empty:
                    continue
                overlap = inter.width * inter.height
                ratio = overlap / max(block_area, 1)
                all_matches.append((xref, ratio, r))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_xref = xref
        except Exception:
            continue

    if best_xref is None:
        return None, 0.0, f"no_overlap (page has {len(page.get_images())} images)"

    best_ratio = best_overlap / max(block_area, 1)
    detail = f"xref={best_xref}, overlap={best_ratio:.1%}, {len(all_matches)} candidates"
    return best_xref, best_ratio, detail


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ref_dir = REPORT_DIR / "reference"
    ref_dir.mkdir(exist_ok=True)

    blocks = load_image_blocks()
    print(f"Loaded {len(blocks)} image blocks")

    doc = fitz.open(PDF_PATH)
    print(f"Opened PDF: {len(doc)} pages")

    issues = []
    stats = {"total": 0, "ok": 0, "missing": 0, "bad_bbox": 0, "mismatch": 0, "tiny": 0}

    html_rows = []

    for block in blocks:
        stats["total"] += 1
        page_idx = block.page_number - 1
        page_rect = doc[page_idx].rect if 0 <= page_idx < len(doc) else fitz.Rect(0, 0, 612, 792)

        # Validate bbox
        valid, reason = is_valid_bbox(block.bbox, page_rect)

        # Find existing extracted image
        existing = find_existing_image(block)

        # Render reference from PDF
        ref_bytes = None
        if valid:
            ref_bytes = render_pdf_region(doc, page_idx, block.bbox, padding=5.0)

        # For embedded images, check xref matching
        xref_info = ""
        if block.image_type == "embedded_image" and valid:
            xref, overlap, detail = match_embedded_image(doc, page_idx, block.bbox)
            xref_info = detail

        # Determine status
        issue = None
        if not valid:
            issue = f"BAD_BBOX: {reason}"
            stats["bad_bbox"] += 1
        elif not existing:
            issue = "MISSING_FILE"
            stats["missing"] += 1
        elif block.bbox and (block.bbox[2] - block.bbox[0]) * (block.bbox[3] - block.bbox[1]) < 100:
            issue = "TINY_IMAGE"
            stats["tiny"] += 1
        else:
            stats["ok"] += 1

        # Save reference rendering
        ref_filename = None
        if ref_bytes:
            ref_filename = f"ref-p{block.page_number:03d}-{block.block_id[:8]}.png"
            (ref_dir / ref_filename).write_bytes(ref_bytes)

        # Build HTML row
        bbox_str = f"({block.bbox[0]:.0f},{block.bbox[1]:.0f},{block.bbox[2]:.0f},{block.bbox[3]:.0f})" if block.bbox else "None"
        area = (block.bbox[2]-block.bbox[0])*(block.bbox[3]-block.bbox[1]) if block.bbox and valid else 0

        row_class = "ok" if not issue else "error"
        existing_img_tag = ""
        if existing:
            rel = os.path.relpath(existing, REPORT_DIR)
            existing_img_tag = f'<img src="{rel}" style="max-width:300px;max-height:200px">'

        ref_img_tag = ""
        if ref_filename:
            ref_img_tag = f'<img src="reference/{ref_filename}" style="max-width:300px;max-height:200px">'

        html_rows.append(f"""
        <tr class="{row_class}">
            <td>{block.block_id[:8]}</td>
            <td>p{block.page_number}</td>
            <td>{block.image_type}</td>
            <td>{bbox_str}<br>area={area:.0f}</td>
            <td>{issue or "OK"}</td>
            <td>{ref_img_tag}</td>
            <td>{existing_img_tag}<br><small>{existing.name if existing else 'N/A'}</small></td>
            <td><small>{xref_info}</small></td>
        </tr>""")

        if issue:
            issues.append({
                "block_id": block.block_id[:8],
                "page": block.page_number,
                "type": block.image_type,
                "bbox": bbox_str,
                "area": area,
                "issue": issue,
                "existing": existing.name if existing else None,
            })

    doc.close()

    # Write HTML report
    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Image Audit Report</title>
<style>
body {{ font-family: sans-serif; margin: 20px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 6px; text-align: left; vertical-align: top; font-size: 12px; }}
th {{ background: #f0f0f0; }}
tr.error {{ background: #fff0f0; }}
tr.ok {{ background: #f0fff0; }}
img {{ border: 1px solid #ddd; }}
h2 {{ margin-top: 30px; }}
.stats {{ font-size: 14px; margin: 10px 0; }}
</style>
</head><body>
<h1>Image Extraction Audit Report</h1>
<div class="stats">
<p><strong>Total:</strong> {stats['total']} | <strong>OK:</strong> {stats['ok']} |
<strong>Missing:</strong> {stats['missing']} | <strong>Bad bbox:</strong> {stats['bad_bbox']} |
<strong>Tiny:</strong> {stats['tiny']}</p>
</div>

<h2>Issues ({len(issues)})</h2>
<table>
<tr><th>Block</th><th>Page</th><th>Type</th><th>BBox</th><th>Issue</th><th>PDF Reference</th><th>Extracted</th><th>Match Info</th></tr>
{''.join(r for r in html_rows if 'class="error"' in r)}
</table>

<h2>All Images ({stats['total']})</h2>
<table>
<tr><th>Block</th><th>Page</th><th>Type</th><th>BBox</th><th>Status</th><th>PDF Reference</th><th>Extracted</th><th>Match Info</th></tr>
{''.join(html_rows)}
</table>

</body></html>"""

    report_path = REPORT_DIR / "audit-report.html"
    report_path.write_text(html, encoding="utf-8")
    print(f"\nReport: {report_path}")
    print(f"\nStats: {stats}")
    print(f"\nIssues ({len(issues)}):")
    for iss in issues:
        print(f"  p{iss['page']:03d} {iss['block_id']} {iss['type']:16s} area={iss['area']:>8.0f}  {iss['issue']}  file={iss['existing']}")


if __name__ == "__main__":
    main()
