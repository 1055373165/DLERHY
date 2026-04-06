#!/usr/bin/env python3
"""Generate side-by-side visual comparison: PDF source page vs extracted images.

Creates an HTML page showing each PDF page alongside the extracted images for
blocks on that page, making it easy to verify extraction accuracy.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import fitz

DB_PATH = "artifacts/real-book-live/translate-agent-autopilot-00-how-large-language-models-work-edward-raff-drew-farris-stella-biderman-z-library-sk-1lib-sk-z-lib-sk/book-agent.db"
PDF_PATH = "/Volumes/XY_IMG/zlibrary/new20260325/How Large Language Models Work (Edward Raff, Drew Farris, Stella Biderman) (z-library.sk, 1lib.sk, z-lib.sk).pdf"
IMAGES_DIR = Path("artifacts/review/zh-markdown/images")
OUTPUT_DIR = Path("artifacts/review/zh-markdown/visual-comparison")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pages_dir = OUTPUT_DIR / "pages"
    pages_dir.mkdir(exist_ok=True)

    # Load image blocks grouped by page
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

    # Group by page
    page_blocks: dict[int, list[dict]] = {}
    for r in rows:
        span = json.loads(r["source_span_json"]) if r["source_span_json"] else {}
        page_num = span.get("source_page_start", 0) or 0
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
                    bbox = [float(raw[i]) for i in range(4)]

        block_id = r["id"][:8]
        image_type = span.get("image_type", "unknown")
        caption = span.get("image_alt", "") or ""

        page_blocks.setdefault(page_num, []).append({
            "block_id": block_id,
            "ordinal": r["ordinal"],
            "image_type": image_type,
            "bbox": bbox,
            "caption": caption[:80],
        })

    doc = fitz.open(PDF_PATH)

    # Render each page that has image blocks
    html_sections = []
    for page_num in sorted(page_blocks.keys()):
        page_idx = page_num - 1
        if page_idx < 0 or page_idx >= len(doc):
            continue

        blocks = page_blocks[page_num]

        # Render full page at 150 DPI
        page = doc[page_idx]
        mat = fitz.Matrix(150 / 72, 150 / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        page_filename = f"page-{page_num:03d}.png"
        pix.save(str(pages_dir / page_filename))

        # Draw bbox rectangles on a copy
        annotated_filename = f"page-{page_num:03d}-annotated.png"
        # Re-render with annotations
        for block in blocks:
            if block["bbox"]:
                b = block["bbox"]
                if b[2] > b[0] and b[3] > b[1]:
                    rect = fitz.Rect(b[0], b[1], b[2], b[3])
                    page.draw_rect(rect, color=(1, 0, 0), width=1.5)  # red outline
        pix2 = page.get_pixmap(matrix=mat, alpha=False)
        pix2.save(str(pages_dir / annotated_filename))

        # Find extracted images
        extracted_imgs = []
        for block in blocks:
            bid = block["block_id"]
            found = None
            for ext in ("png", "jpeg", "jpg"):
                p = IMAGES_DIR / f"p{page_num:03d}-{bid}.{ext}"
                if p.exists():
                    found = p
                    break
            extracted_imgs.append({
                **block,
                "file": found,
            })

        # Build HTML section
        img_cells = []
        for ei in extracted_imgs:
            if ei["file"]:
                rel = os.path.relpath(ei["file"], OUTPUT_DIR)
                sz = os.path.getsize(ei["file"]) // 1024
                img_cells.append(f'''
                <div class="img-card">
                    <img src="{rel}" style="max-width:250px;max-height:200px">
                    <p>{ei["block_id"]} ({ei["image_type"][:6]})<br>{sz}KB</p>
                </div>''')
            else:
                img_cells.append(f'''
                <div class="img-card missing">
                    <p>{ei["block_id"]}<br>{ei["image_type"][:6]}<br>NOT EXTRACTED</p>
                </div>''')

        html_sections.append(f'''
        <div class="page-section">
            <h3>Page {page_num} ({len(blocks)} image blocks)</h3>
            <div class="comparison">
                <div class="pdf-page">
                    <img src="pages/{annotated_filename}" style="max-height:500px">
                    <p>PDF Source (red = bbox)</p>
                </div>
                <div class="extracted-images">
                    {''.join(img_cells)}
                </div>
            </div>
        </div>''')

    doc.close()

    # Write HTML
    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Visual Comparison: PDF vs Extracted Images</title>
<style>
body {{ font-family: sans-serif; margin: 20px; background: #f5f5f5; }}
h1 {{ margin-bottom: 5px; }}
.page-section {{ background: white; padding: 15px; margin: 15px 0; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.comparison {{ display: flex; gap: 20px; align-items: flex-start; }}
.pdf-page {{ flex: 0 0 auto; }}
.pdf-page img {{ border: 1px solid #ccc; }}
.extracted-images {{ display: flex; flex-wrap: wrap; gap: 10px; }}
.img-card {{ border: 1px solid #ddd; padding: 8px; border-radius: 4px; text-align: center; background: #fafafa; }}
.img-card img {{ border: 1px solid #eee; display: block; margin: 0 auto 5px; }}
.img-card p {{ margin: 4px 0; font-size: 11px; color: #666; }}
.img-card.missing {{ background: #fff0f0; border-color: #fcc; }}
.img-card.missing p {{ color: #c00; }}
h3 {{ margin: 0 0 10px; color: #333; }}
</style>
</head><body>
<h1>PDF vs Extracted Images — Visual Comparison</h1>
<p>{len(html_sections)} pages with image blocks | {sum(len(page_blocks[p]) for p in page_blocks)} total blocks</p>
{''.join(html_sections)}
</body></html>"""

    out_path = OUTPUT_DIR / "comparison.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Report: {out_path}")
    print(f"Pages rendered: {len(html_sections)}")


if __name__ == "__main__":
    main()
