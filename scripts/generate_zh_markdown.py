#!/usr/bin/env python3
"""Generate high-fidelity Chinese Markdown from translated book data.

Produces a production-quality pure Chinese Markdown that faithfully mirrors
the original document's structure: heading hierarchy, paragraphs, code blocks,
lists, figures, captions, and footnotes.

Usage:
    python scripts/generate_zh_markdown.py [--book-index 0] [--output PATH]
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF — for image extraction

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

STATE_JSON = Path("artifacts/review/translate-agent-rollout-state-current.json")

# pdf_block_role values to skip (page headers/footers, not content)
SKIP_ROLES = {"header", "footer", "toc_entry"}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MdBlock:
    """A single block ready for Markdown rendering."""
    ordinal: int
    block_type: str  # heading, paragraph, code, list_item, caption, footnote, image, table, equation
    source_text: str
    target_text: str  # Chinese translation (empty if not translated)
    heading_level: int  # 1-4 for headings, 0 otherwise
    page_number: int  # source page (1-indexed)
    pdf_block_role: str
    protected_policy: str  # translate, protect, mixed
    image_meta: dict | None  # for image blocks: image_type, image_ext, etc.
    block_id: str
    list_marker: str  # detected list marker: "1.", "2.", "-", "•", etc.


# ---------------------------------------------------------------------------
# DB extraction
# ---------------------------------------------------------------------------

def load_blocks(conn: sqlite3.Connection) -> list[MdBlock]:
    """Load all active blocks with translations, ordered by reading order."""
    # Get blocks
    blocks = conn.execute("""
        SELECT b.id, b.ordinal, b.block_type, b.source_text,
               b.source_span_json, b.protected_policy, b.chapter_id
        FROM blocks b
        JOIN chapters c ON c.id = b.chapter_id
        WHERE b.status = 'active'
        ORDER BY c.ordinal, b.ordinal
    """).fetchall()

    # Get translations (block_id → list of zh text)
    segments = conn.execute("""
        SELECT
            b.id as block_id,
            ts.text_zh,
            ts.id as ts_id,
            ts.ordinal as ts_ordinal
        FROM alignment_edges ae
        JOIN sentences s ON s.id = ae.sentence_id
        JOIN blocks b ON b.id = s.block_id
        JOIN target_segments ts ON ts.id = ae.target_segment_id
        JOIN translation_runs tr ON tr.id = ts.translation_run_id
        WHERE ts.final_status != 'superseded'
          AND tr.status = 'succeeded'
        ORDER BY b.id, ts.ordinal
    """).fetchall()

    # Deduplicate
    block_translations: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for seg in segments:
        key = (seg["block_id"], seg["ts_id"])
        if key in seen:
            continue
        seen.add(key)
        bid = seg["block_id"]
        zh = seg["text_zh"]
        if zh:
            block_translations.setdefault(bid, []).append(zh)

    result: list[MdBlock] = []
    for b in blocks:
        span = {}
        if b["source_span_json"]:
            try:
                span = json.loads(b["source_span_json"]) if isinstance(b["source_span_json"], str) else b["source_span_json"]
            except (json.JSONDecodeError, TypeError):
                pass

        role = span.get("pdf_block_role", "")
        if role in SKIP_ROLES:
            continue

        source_text = b["source_text"] or ""
        block_type = b["block_type"] or "paragraph"
        heading_level = span.get("heading_level", 0) or 0
        page_num = span.get("source_page_start", 0) or 0

        # Get translation
        trans_parts = block_translations.get(b["id"], [])
        target_text = "\n".join(trans_parts) if trans_parts else ""

        # Image metadata
        image_meta = None
        if block_type == "image":
            image_meta = {
                "image_type": span.get("image_type"),
                "image_ext": span.get("image_ext"),
                "image_alt": span.get("image_alt", ""),
                "width": span.get("image_width_px"),
                "height": span.get("image_height_px"),
            }

        # Detect list marker
        list_marker = ""
        if block_type == "list_item":
            m = re.match(r'^(\d+[\.\)]\s*|[-•◦▪]\s*)', source_text)
            if m:
                marker = m.group(1).strip()
                if re.match(r'^\d+[\.\)]$', marker):
                    list_marker = marker
                else:
                    list_marker = "-"
            else:
                list_marker = "-"

        result.append(MdBlock(
            ordinal=b["ordinal"],
            block_type=block_type,
            source_text=source_text,
            target_text=target_text,
            heading_level=heading_level,
            page_number=page_num,
            pdf_block_role=role,
            protected_policy=b["protected_policy"] or "translate",
            image_meta=image_meta,
            block_id=b["id"],
            list_marker=list_marker,
        ))

    return result


# ---------------------------------------------------------------------------
# Image extraction from PDF
# ---------------------------------------------------------------------------

def extract_images_from_pdf(
    source_pdf: str,
    blocks: list[MdBlock],
    output_dir: Path,
) -> dict[str, str]:
    """Extract images from PDF for image blocks. Returns block_id → relative path."""
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(source_pdf)
    image_paths: dict[str, str] = {}
    extracted = 0

    for block in blocks:
        if block.block_type != "image" or not block.image_meta:
            continue

        page_idx = block.page_number - 1
        if page_idx < 0 or page_idx >= len(doc):
            continue

        page = doc[page_idx]
        page_images = page.get_images(full=True)

        if not page_images:
            continue

        # Use the first/largest image on the page near this block
        # For simplicity, extract all images on the page and pick the best match
        best_img = None
        best_size = 0
        for img_info in page_images:
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
                if base_image and len(base_image.get("image", b"")) > best_size:
                    best_img = base_image
                    best_size = len(base_image["image"])
            except Exception:
                continue

        if best_img:
            ext = best_img.get("ext", "png")
            filename = f"p{block.page_number:03d}-{block.block_id[:8]}.{ext}"
            img_path = images_dir / filename
            if not img_path.exists():
                img_path.write_bytes(best_img["image"])
                extracted += 1
            image_paths[block.block_id] = f"images/{filename}"

    doc.close()
    logger.info("Extracted %d images from PDF", extracted)
    return image_paths


def link_existing_assets(
    blocks: list[MdBlock],
    assets_dir: Path,
    output_dir: Path,
) -> dict[str, str]:
    """Link image blocks to existing extracted assets by page number matching.

    Copies assets to output_dir/images/ for self-contained output.
    """
    if not assets_dir.exists():
        return {}

    images_out = output_dir / "images"
    images_out.mkdir(parents=True, exist_ok=True)

    # Build page → asset file map
    asset_files: dict[int, list[Path]] = {}
    for f in assets_dir.iterdir():
        if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".svg"):
            continue
        m = re.match(r'ch\d+-p(\d+)-blk\d+', f.stem)
        if m:
            page = int(m.group(1))
            asset_files.setdefault(page, []).append(f)

    import shutil
    image_paths: dict[str, str] = {}
    for block in blocks:
        if block.block_type != "image":
            continue
        page = block.page_number
        candidates = asset_files.get(page, [])
        if candidates:
            best = max(candidates, key=lambda f: f.stat().st_size)
            dest = images_out / best.name
            if not dest.exists():
                shutil.copy2(best, dest)
            image_paths[block.block_id] = f"images/{best.name}"

    return image_paths


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    """Clean text for Markdown output: normalize whitespace, strip page artifacts."""
    if not text:
        return ""
    # Remove page numbers embedded in text (e.g., "ABOUT THIS BOOK\nxv")
    text = re.sub(r'\n[xivXIV]+$', '', text.strip())
    text = re.sub(r'\n\d{1,3}$', '', text.strip())
    # Remove standalone page numbers at start
    text = re.sub(r'^[xivXIV]+\n', '', text.strip())
    text = re.sub(r'^\d{1,3}\n', '', text.strip())
    # Collapse multiple newlines within a block
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Collapse multiple spaces
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


def _is_stray_fragment(text: str) -> bool:
    """Detect stray page numbers, roman numerals, and other non-content fragments."""
    t = text.strip()
    if not t:
        return True
    # Pure page numbers: "1", "xv", "xviii", "132"
    if re.match(r'^[xivXIV]+$', t) or re.match(r'^\d{1,3}$', t):
        return True
    # Very short text that's just a label: "前言", "致谢", "图像", "蛋白质"
    if len(t) <= 6 and re.match(r'^[\u4e00-\u9fff]{1,6}$', t):
        return True
    # Single English word or very short fragments
    if len(t) <= 3 and re.match(r'^[a-zA-Z]+$', t):
        return True
    return False


def _is_real_code(text: str) -> bool:
    """Heuristic: determine if a 'code' block actually contains code vs misclassified prose.

    Many PDF parsers misclassify body text as code_like based on font appearance.
    Real code has: indentation, symbols, short lines. Prose has: long sentences, few symbols.
    """
    lines = text.strip().split('\n')
    if not lines:
        return False
    # If the text is very long (>500 chars) with few code-like characters, it's probably prose
    total_chars = len(text)
    code_chars = sum(1 for c in text if c in '{}[]()=<>;&|#@$%^*~/\\')
    # Prose indicators: sentence endings, long average line length
    sentence_endings = sum(1 for c in text if c in '.!?')
    avg_line_len = total_chars / max(len(lines), 1)

    # Real code: high ratio of code chars OR short lines OR has indentation
    has_indentation = any(line.startswith('    ') or line.startswith('\t') for line in lines if line.strip())
    code_ratio = code_chars / max(total_chars, 1)

    if has_indentation and code_ratio > 0.02:
        return True
    if code_ratio > 0.05:
        return True
    # Short lines with code syntax (not just plain text wrapped at short widths)
    if avg_line_len < 60 and len(lines) > 2 and (code_ratio > 0.01 or has_indentation):
        return True
    # Prose: long lines, many sentence endings, low code chars
    if avg_line_len > 80 and sentence_endings > 3 and code_ratio < 0.02:
        return False
    # Prose disguised as code: reads like natural language with newlines
    if total_chars > 100 and code_ratio < 0.02 and sentence_endings >= 1:
        return False
    # List-like "code" blocks (product names, short items with newlines)
    if total_chars < 300 and all(len(l.strip()) < 30 for l in lines) and code_ratio < 0.01:
        return False
    # Default: trust the parser for short blocks
    return total_chars < 200


def _heading_prefix(level: int) -> str:
    """Map heading_level to Markdown prefix."""
    if level <= 0:
        return "## "
    level = min(level, 5)
    return "#" * level + " "


def _detect_code_language(text: str) -> str:
    """Heuristic: detect programming language from code block content."""
    if re.search(r'\bdef\s+\w+\(|import\s+\w+|print\(', text):
        return "python"
    if re.search(r'\bfunction\s+\w+|const\s+\w+|console\.log', text):
        return "javascript"
    if re.search(r'\bpublic\s+class|System\.out\.println|void\s+main', text):
        return "java"
    if re.search(r'<\w+>.*</\w+>|<\w+\s*/>', text):
        return "html"
    if re.search(r'SELECT\s+|FROM\s+|WHERE\s+', text, re.IGNORECASE):
        return "sql"
    return ""


def render_markdown(
    blocks: list[MdBlock],
    image_paths: dict[str, str],
    book_title: str,
    output_dir: Path,
) -> str:
    """Render blocks into a clean Chinese Markdown document."""
    lines: list[str] = []

    # Title
    lines.append(f"# {book_title}")
    lines.append("")

    prev_type = ""
    in_list = False
    # Track rendered headings and images to deduplicate
    rendered_headings: set[str] = set()  # normalized heading text
    rendered_heading_nums: set[str] = set()  # section numbers like "1.1", "2.3"
    rendered_image_paths: set[str] = set()  # already-rendered image file paths
    # Track which caption blocks are consumed by images (looked ahead)
    consumed_caption_ids: set[str] = set()
    # Track when we've passed the "参考文献" / "索引" section (back matter)
    passed_references = False

    # Pre-pass: mark captions that are immediately after images
    for i in range(len(blocks) - 1):
        if blocks[i].block_type == "image" and blocks[i + 1].block_type == "caption":
            consumed_caption_ids.add(blocks[i + 1].block_id)

    for i, block in enumerate(blocks):
        bt = block.block_type

        # Use translation if available, otherwise fall back to source for protected content
        text = ""
        if block.target_text:
            text = _clean_text(block.target_text)
        elif block.protected_policy in ("protect", "mixed") or bt in ("code", "equation", "table", "image"):
            text = _clean_text(block.source_text)
        elif bt == "heading":
            text = _clean_text(block.source_text)
        else:
            text = _clean_text(block.source_text)

        if not text and bt != "image":
            continue

        # Skip stray fragments (page numbers, short repeated labels)
        if bt == "paragraph" and _is_stray_fragment(text):
            continue
        # Skip footnote-role blocks that are just page references
        if bt == "footnote":
            fn_clean = text.replace("\n", " ").strip()
            # Footnotes like "1.2 What you will learn 5" are running headers
            if re.match(r'^[\d\.\s]+\w', fn_clean) and len(fn_clean) < 60:
                continue

        # ---- Heading ----
        if bt == "heading":
            if in_list:
                lines.append("")
                in_list = False
            heading_text = text.replace("\n", " ").strip()

            # --- Filter out non-heading content ---
            skip_as_heading = False

            # 1. Full paragraphs misclassified as headings (>100 chars)
            if len(heading_text) > 100:
                skip_as_heading = True

            # 2. Figure-internal numbered steps: "1 接收", "3 分词"
            if re.match(r'^\d+\s+\S{1,8}$', heading_text):
                skip_as_heading = True

            # 3. Decimal-prefix diagram labels: "0.7 计算损失 神经网络"
            #    Real sections start with 1-9: "2.2 语言模型", "3.1.1 Transformer"
            if re.match(r'^0\.\d+\s', heading_text):
                skip_as_heading = True

            # 4. Space-separated short terms (diagram labels): "提示 补全 评分"
            words = heading_text.split()
            if len(words) >= 2 and all(len(w) <= 6 for w in words) and not re.match(r'^\d+\.\d+', heading_text):
                if len(heading_text) < 30:
                    skip_as_heading = True

            # 5. Multi-concept figure labels with mixed CJK/Latin terms
            #    "大语言模型（LLMs）准确执行任务的能力 CNN XGBoost"
            if not re.match(r'^\d+\.\d+', heading_text):
                # Count parenthesized acronyms — figure labels often have 2+
                acronym_count = len(re.findall(r'[（(][A-Za-z]+[）)]', heading_text))
                if acronym_count >= 2 and len(heading_text) > 20:
                    skip_as_heading = True
                # Also catch long text with multiple spaces but no section number
                if len(heading_text) > 30 and heading_text.count(' ') > 3:
                    skip_as_heading = True

            # 6. Numbered list items misclassified as headings: "5. 这算是一个伦理问题吗？"
            #    Real sections use X.Y format (e.g., "5.1"), not "5."
            if re.match(r'^\d+\.\s+[^\d]', heading_text) and not re.match(r'^\d+\.\d+', heading_text):
                skip_as_heading = True

            # 7. Single short word (≤3 chars) without section number, surrounded by code/equation
            if len(heading_text) <= 3 and not re.search(r'\d', heading_text):
                if prev_type in ("code", "equation"):
                    skip_as_heading = True  # "模型" between equations

            # 8. Code listing titles: "代码清单 X.Y ..."
            if re.match(r'^代码清单\s', heading_text):
                skip_as_heading = True

            # 9. Chapter markers in reference sections: "第X章"
            if re.match(r'^第[一二三四五六七八九十\d]+章$', heading_text):
                skip_as_heading = True

            # 10. Back cover / colophon content (after references)
            if passed_references and block.heading_level <= 2:
                skip_as_heading = True

            if skip_as_heading:
                # Render as regular text, not heading
                if heading_text:
                    lines.append(heading_text)
                    lines.append("")
                prev_type = "paragraph"
                continue

            # Deduplicate: skip if same heading text or same section number
            heading_key = re.sub(r'[\s\d\.\-]+', '', heading_text.lower())
            sec_match = re.match(r'^(\d+(?:\.\d+)+)', heading_text)
            sec_num = sec_match.group(1) if sec_match else ""
            if heading_key in rendered_headings and len(heading_key) > 2:
                continue
            if sec_num and sec_num in rendered_heading_nums:
                continue
            rendered_headings.add(heading_key)
            if sec_num:
                rendered_heading_nums.add(sec_num)

            lines.append("")
            prefix = _heading_prefix(block.heading_level)
            lines.append(f"{prefix}{heading_text}")
            lines.append("")
            prev_type = "heading"

            # Mark when we pass references/index section
            if heading_text in ("参考文献", "索引") or re.match(r'^参考', heading_text):
                passed_references = True

        # ---- Code ----
        elif bt == "code":
            if in_list:
                lines.append("")
                in_list = False
            # Check if this is actually prose misclassified as code
            source_text = _clean_text(block.source_text)
            if not _is_real_code(source_text):
                # Treat as paragraph
                if text:
                    lines.append(text)
                    lines.append("")
                prev_type = "paragraph"
            else:
                lang = _detect_code_language(source_text)
                # For code: always use source text (code shouldn't be translated)
                lines.append(f"```{lang}")
                lines.append(source_text)
                lines.append("```")
                lines.append("")
                prev_type = "code"

        # ---- Image ----
        elif bt == "image":
            if in_list:
                lines.append("")
                in_list = False

            img_path = image_paths.get(block.block_id, "")

            # Deduplicate: skip if this exact image file was already rendered
            if img_path and img_path in rendered_image_paths:
                continue

            # Look ahead for caption
            caption_text = ""
            if i + 1 < len(blocks) and blocks[i + 1].block_type == "caption":
                next_block = blocks[i + 1]
                if next_block.target_text:
                    caption_text = _clean_text(next_block.target_text)
                else:
                    caption_text = _clean_text(next_block.source_text)

            alt = ""
            if block.image_meta and block.image_meta.get("image_alt"):
                alt = block.image_meta["image_alt"].replace("\n", " ").strip()

            if img_path:
                rendered_image_paths.add(img_path)

            if img_path:
                display_alt = alt or caption_text or "图片"
                # Clean alt text for markdown (no newlines)
                display_alt = display_alt.replace("\n", " ").strip()
                lines.append(f"![{display_alt}]({img_path})")
            else:
                display_alt = alt or caption_text or "（见原书）"
                lines.append(f"[图片: {display_alt}]")

            if caption_text:
                clean_caption = caption_text.replace("\n", " ").strip()
                lines.append(f"*{clean_caption}*")

            lines.append("")
            prev_type = "image"

        # ---- Caption ----
        elif bt == "caption":
            # Skip if already consumed by a preceding image block
            if block.block_id in consumed_caption_ids:
                prev_type = "caption"
                continue
            # Standalone caption (not after an image)
            caption = text.replace("\n", " ").strip()
            lines.append(f"*{caption}*")
            lines.append("")
            prev_type = "caption"

        # ---- List item ----
        elif bt == "list_item":
            if not in_list and prev_type != "list_item":
                lines.append("")
            marker = block.list_marker or "-"
            item_text = text
            m = re.match(r'^(\d+[\.\)]\s*|[-•◦▪]\s*)', item_text)
            if m:
                item_text = item_text[m.end():]
            item_text = item_text.replace("\n", " ").strip()
            lines.append(f"{marker} {item_text}")
            in_list = True
            prev_type = "list_item"

        # ---- Table ----
        elif bt == "table":
            if in_list:
                lines.append("")
                in_list = False
            lines.append("```")
            lines.append(text)
            lines.append("```")
            lines.append("")
            prev_type = "table"

        # ---- Equation ----
        elif bt == "equation":
            if in_list:
                lines.append("")
                in_list = False
            lines.append(f"$$")
            lines.append(text)
            lines.append(f"$$")
            lines.append("")
            prev_type = "equation"

        # ---- Footnote ----
        elif bt == "footnote":
            fn_text = text.replace("\n", " ").strip()
            lines.append(f"> {fn_text}")
            lines.append("")
            prev_type = "footnote"

        # ---- Paragraph (default) ----
        else:
            if in_list:
                lines.append("")
                in_list = False
            para_text = text.strip()
            if para_text and not _is_stray_fragment(para_text):
                lines.append(para_text)
                lines.append("")
            prev_type = "paragraph"

    if in_list:
        lines.append("")

    # Post-processing: remove excessive blank lines
    output = "\n".join(lines)
    output = re.sub(r'\n{4,}', '\n\n\n', output)
    return output


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate high-fidelity Chinese Markdown")
    parser.add_argument("--book-index", type=int, default=0, help="Book index in state JSON")
    parser.add_argument("--output", type=str, default=None, help="Output directory")
    parser.add_argument("--skip-images", action="store_true", help="Skip image extraction from PDF")
    args = parser.parse_args()

    # Load state
    state = json.loads(STATE_JSON.read_text())
    books = state if isinstance(state, list) else state.get("books", [])
    if args.book_index >= len(books):
        logger.error("Book index %d out of range", args.book_index)
        sys.exit(1)

    book = books[args.book_index]
    live_state = book.get("live_state", {})
    root = live_state.get("root", "")
    db_path = str(Path(root) / "book-agent.db") if root else None
    source_pdf = live_state.get("source_path") or book.get("path")

    if not db_path or not Path(db_path).exists():
        logger.error("DB not found: %s", db_path)
        sys.exit(1)

    # Output directory
    output_dir = Path(args.output) if args.output else Path("artifacts/review/zh-markdown")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Connect to DB
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get book title
    doc_row = conn.execute("SELECT title, title_src FROM documents LIMIT 1").fetchone()
    book_title = doc_row["title_src"] or doc_row["title"] or "Unknown"
    logger.info("Book: %s", book_title)

    # Load blocks
    blocks = load_blocks(conn)
    conn.close()

    total = len(blocks)
    translated = sum(1 for b in blocks if b.target_text)
    logger.info("Loaded %d blocks (%d translated, %.1f%%)", total, translated, translated / max(total, 1) * 100)

    # Block type summary
    from collections import Counter
    type_counts = Counter(b.block_type for b in blocks)
    for bt, cnt in type_counts.most_common():
        logger.info("  %s: %d", bt, cnt)

    # Image handling
    image_paths: dict[str, str] = {}

    # Try linking to existing assets first
    existing_assets = Path("artifacts/review/bilingual-markdown-deliverables/assets")
    if existing_assets.exists():
        image_paths = link_existing_assets(blocks, existing_assets, output_dir)
        logger.info("Linked %d images from existing assets", len(image_paths))

    # Extract missing images from PDF
    if not args.skip_images and source_pdf and Path(source_pdf).exists():
        image_blocks_without_path = [b for b in blocks if b.block_type == "image" and b.block_id not in image_paths]
        if image_blocks_without_path:
            pdf_paths = extract_images_from_pdf(source_pdf, image_blocks_without_path, output_dir)
            image_paths.update(pdf_paths)
            logger.info("Total image paths: %d / %d image blocks",
                       len(image_paths), sum(1 for b in blocks if b.block_type == "image"))

    # Render Markdown
    md_content = render_markdown(blocks, image_paths, book_title, output_dir)

    # Write output
    safe_name = re.sub(r'[^\w\s-]', '', book_title)[:60].strip().replace(' ', '-')
    md_path = output_dir / f"{safe_name}-zh.md"
    md_path.write_text(md_content, encoding="utf-8")

    # Stats
    total_chars = len(md_content)
    zh_chars = sum(1 for c in md_content if '\u4e00' <= c <= '\u9fff')
    print(f"\n{'=' * 60}")
    print(f"Chinese Markdown Export")
    print(f"{'=' * 60}")
    print(f"Output:           {md_path}")
    print(f"Size:             {total_chars // 1024}KB")
    print(f"Chinese chars:    {zh_chars}")
    print(f"Total blocks:     {total}")
    print(f"Translated:       {translated} ({translated / max(total, 1) * 100:.1f}%)")
    print(f"Images:           {len(image_paths)} linked")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
