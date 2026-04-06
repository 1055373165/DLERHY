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
    bbox: tuple[float, float, float, float] | None = None  # (x0, y0, x1, y1) in PDF points


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

        # Image metadata + bbox
        image_meta = None
        bbox = None
        if block_type == "image":
            image_meta = {
                "image_type": span.get("image_type"),
                "image_ext": span.get("image_ext"),
                "image_alt": span.get("image_alt", ""),
                "width": span.get("image_width_px"),
                "height": span.get("image_height_px"),
            }
            # Parse bbox from source_bbox_json.regions[0].bbox
            sbj = span.get("source_bbox_json")
            if isinstance(sbj, dict):
                regions = sbj.get("regions", [])
                if regions and isinstance(regions[0], dict):
                    raw = regions[0].get("bbox")
                    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
                        bbox = (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))

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
            bbox=bbox,
        ))

    return result


# ---------------------------------------------------------------------------
# Image extraction from PDF
# ---------------------------------------------------------------------------

def _rect_overlap_area(r1: "fitz.Rect", r2: "fitz.Rect") -> float:
    """Compute intersection area between two fitz.Rect objects."""
    inter = r1 & r2
    if inter.is_empty:
        return 0.0
    return inter.width * inter.height


def _is_valid_bbox(bbox: tuple[float, float, float, float] | None) -> bool:
    """Check if bbox is valid (positive area, no negative coords)."""
    if not bbox:
        return False
    x0, y0, x1, y1 = bbox
    return x1 > x0 and y1 > y0 and x0 >= 0 and y0 >= 0


def extract_images_from_pdf(
    source_pdf: str,
    blocks: list[MdBlock],
    output_dir: Path,
) -> dict[str, str]:
    """Extract images from PDF for image blocks using bbox-based matching.

    For embedded images: matches block bbox to specific embedded image positions.
    For vector drawings: renders the page region as a high-DPI pixmap.
    Uses content-based MD5 dedup to avoid duplicate files.

    Returns block_id → relative path.
    """
    import hashlib

    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(source_pdf)
    image_paths: dict[str, str] = {}
    extracted = 0
    seen_hashes: dict[str, str] = {}  # md5 → relative path (content dedup)

    # Pre-compute per-page image xref → rect mapping (cached)
    _page_img_rects_cache: dict[int, list[tuple[int, "fitz.Rect"]]] = {}

    def _get_page_image_rects(page_idx: int) -> list[tuple[int, "fitz.Rect"]]:
        """Get (xref, rect) pairs for all embedded images on a page."""
        if page_idx in _page_img_rects_cache:
            return _page_img_rects_cache[page_idx]
        page = doc[page_idx]
        result = []
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                rects = page.get_image_rects(xref)
                for r in rects:
                    if not r.is_empty:
                        result.append((xref, r))
            except Exception:
                continue
        _page_img_rects_cache[page_idx] = result
        return result

    RENDER_DPI = 200  # High-DPI for vector/pixmap rendering
    VECTOR_PADDING_PT = 8   # Generous padding for vector drawings (borders, labels)
    EMBED_PADDING_PT = 3    # Smaller padding for embedded image fallback
    MIN_BBOX_AREA = 50      # Minimum useful bbox area in square points

    for block in blocks:
        if block.block_type != "image" or not block.image_meta:
            continue

        page_idx = block.page_number - 1
        if page_idx < 0 or page_idx >= len(doc):
            continue

        # Validate bbox
        if not _is_valid_bbox(block.bbox):
            logger.debug("Skipping image block %s: invalid bbox %s", block.block_id[:8], block.bbox)
            continue

        x0, y0, x1, y1 = block.bbox
        bbox_area = (x1 - x0) * (y1 - y0)
        if bbox_area < MIN_BBOX_AREA:
            logger.debug("Skipping tiny image block %s: area=%.1f", block.block_id[:8], bbox_area)
            continue

        block_rect = fitz.Rect(x0, y0, x1, y1)
        page = doc[page_idx]
        image_type = (block.image_meta or {}).get("image_type", "embedded_image")

        img_bytes = None
        ext = "png"

        # Strategy 1: For embedded images, match by bbox overlap with xref rects
        if image_type == "embedded_image":
            img_rects = _get_page_image_rects(page_idx)
            best_xref = None
            best_overlap = 0.0

            for xref, img_rect in img_rects:
                overlap = _rect_overlap_area(block_rect, img_rect)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_xref = xref

            if best_xref is not None:
                try:
                    base_image = doc.extract_image(best_xref)
                    if base_image and base_image.get("image"):
                        img_bytes = base_image["image"]
                        ext = base_image.get("ext", "png")
                except Exception:
                    pass

        # Strategy 2: Vector drawings OR embedded fallback — render page region as pixmap
        if img_bytes is None:
            padding = VECTOR_PADDING_PT if image_type == "vector_drawing" else EMBED_PADDING_PT
            mat = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
            padded = fitz.Rect(
                x0 - padding, y0 - padding,
                x1 + padding, y1 + padding,
            )
            padded = padded & page.rect  # clip to page bounds
            if padded.is_empty:
                continue
            try:
                pix = page.get_pixmap(matrix=mat, clip=padded, alpha=False)
                img_bytes = pix.tobytes("png")
                ext = "png"
            except Exception as e:
                logger.warning("Failed to render image block %s: %s", block.block_id[:8], e)
                continue

        if not img_bytes:
            continue

        # Content-based dedup via MD5
        md5 = hashlib.md5(img_bytes).hexdigest()
        if md5 in seen_hashes:
            image_paths[block.block_id] = seen_hashes[md5]
            continue

        filename = f"p{block.page_number:03d}-{block.block_id[:8]}.{ext}"
        img_path = images_dir / filename
        img_path.write_bytes(img_bytes)
        extracted += 1

        rel_path = f"images/{filename}"
        image_paths[block.block_id] = rel_path
        seen_hashes[md5] = rel_path

    doc.close()
    logger.info("Extracted %d unique images from PDF (deduped from %d blocks)",
                extracted, len([b for b in blocks if b.block_type == "image"]))
    return image_paths


def link_existing_assets(
    blocks: list[MdBlock],
    assets_dir: Path,
    output_dir: Path,
) -> dict[str, str]:
    """Link image blocks to existing extracted assets by page + ordinal matching.

    Asset filenames follow pattern: ch02-p019-blk0024.png
    Matches block ordinal to the blk number in the filename for per-block accuracy.
    Falls back to best-by-size on the same page if no ordinal match.

    Copies assets to output_dir/images/ for self-contained output.
    """
    if not assets_dir.exists():
        return {}

    images_out = output_dir / "images"
    images_out.mkdir(parents=True, exist_ok=True)

    # Build page → list of (ordinal, path) and page → [path] maps
    asset_by_page_ord: dict[tuple[int, int], Path] = {}
    asset_by_page: dict[int, list[Path]] = {}
    for f in assets_dir.iterdir():
        if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".svg"):
            continue
        m = re.match(r'ch\d+-p(\d+)-blk(\d+)', f.stem)
        if m:
            page = int(m.group(1))
            blk_ord = int(m.group(2))
            asset_by_page_ord[(page, blk_ord)] = f
            asset_by_page.setdefault(page, []).append(f)

    import shutil
    image_paths: dict[str, str] = {}
    used_assets: set[Path] = set()

    for block in blocks:
        if block.block_type != "image":
            continue
        page = block.page_number

        # Try exact ordinal match first
        matched = asset_by_page_ord.get((page, block.ordinal))

        # Fall back: find closest ordinal on the same page not yet used
        if not matched:
            candidates = [p for p in asset_by_page.get(page, []) if p not in used_assets]
            if len(candidates) == 1:
                matched = candidates[0]
            elif candidates:
                # Pick the one with closest blk number to block ordinal
                def _ord_key(f: Path) -> int:
                    m2 = re.match(r'ch\d+-p\d+-blk(\d+)', f.stem)
                    return abs(int(m2.group(1)) - block.ordinal) if m2 else 9999
                matched = min(candidates, key=_ord_key)

        if matched:
            used_assets.add(matched)
            dest = images_out / matched.name
            if not dest.exists():
                shutil.copy2(matched, dest)
            image_paths[block.block_id] = f"images/{matched.name}"

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
    # Fix malformed bold-italic markers: ***图 → *图  (triple star → single)
    text = re.sub(r'\*{3,}(图\s*\d)', r'*\1', text)
    # Clean bold-wrapped section numbers in headings: **2.2** → 2.2
    text = re.sub(r'\*\*(\d+(?:\.\d+)*)\*\*', r'\1', text)
    # Strip stray bold markers around punctuation
    text = re.sub(r'\*\*\s*\*\*', '', text)
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

    # Pre-pass 0: collect heading texts for dedup against chapter divider paragraphs
    heading_texts: set[str] = set()
    for b in blocks:
        if b.block_type == "heading":
            ht = _clean_text(b.target_text or b.source_text or "")
            # Strip number prefixes, chapter markers, bold markers, newlines
            for line in ht.split("\n"):
                line = line.strip()
                line = re.sub(r'^[\d.]+\s*', '', line)
                line = re.sub(r'^(第[一二三四五六七八九十\d]+章)\s*', '', line)
                line = re.sub(r'\*\*', '', line).strip()
                if line and len(line) > 3:
                    heading_texts.add(line)

    # Chapters without heading blocks in the DB — insert synthetic heading before X.1
    _missing_chapters: dict[str, str] = {
        "8.1": "8 大语言模型的实际应用",
        "9.1": "9 构建与使用大语言模型的伦理考量",
    }

    # Pre-pass 1: mark captions that are immediately after images
    for i in range(len(blocks) - 1):
        if blocks[i].block_type == "image" and blocks[i + 1].block_type == "caption":
            consumed_caption_ids.add(blocks[i + 1].block_id)

    # Pre-pass 2: merge consecutive paragraph blocks that are split mid-sentence.
    # When a translated paragraph ends without sentence-ending punctuation and the
    # next block is also a translated paragraph, merge them into one.
    _SENTENCE_ENDERS = set('。！？；…」』"）.!?;)')
    merged_into: set[int] = set()  # indices that were merged into a previous block
    for i in range(len(blocks) - 1):
        if i in merged_into:
            continue
        b = blocks[i]
        if b.block_type not in ("paragraph",) or not b.target_text:
            continue
        txt = _clean_text(b.target_text).rstrip()
        if not txt:
            continue
        # Check if text ends mid-sentence (no sentence-ending punctuation)
        if txt[-1] in _SENTENCE_ENDERS:
            continue
        # Look ahead: merge consecutive translated paragraph continuations
        j = i + 1
        while j < len(blocks):
            nb = blocks[j]
            if nb.block_type not in ("paragraph",):
                break
            # Skip blocks without translation (stray page numbers etc.)
            if not nb.target_text:
                j += 1
                continue
            ntxt = _clean_text(nb.target_text)
            if not ntxt or not ntxt.strip():
                j += 1
                continue
            # Skip stray fragments (page headers, short labels) — don't merge them
            if _is_stray_fragment(ntxt):
                merged_into.add(j)  # also suppress standalone rendering
                j += 1
                continue
            # Don't merge very short text that's likely a header/label, not continuation
            if len(ntxt.strip()) < 10:
                break
            # Merge: append to current block's target_text
            b.target_text = b.target_text.rstrip() + ntxt.strip()
            merged_into.add(j)
            # Check if merged text now ends with sentence punctuation
            merged_clean = _clean_text(b.target_text).rstrip()
            if merged_clean and merged_clean[-1] in _SENTENCE_ENDERS:
                break
            j += 1

    for i, block in enumerate(blocks):
        # Skip blocks that were merged into a previous block
        if i in merged_into:
            continue

        bt = block.block_type

        # Use translation if available; for untranslated content, SKIP paragraphs
        # (don't show raw English in a Chinese document). Only keep English for
        # structural/protected content types (code, equations, tables, images).
        text = ""
        if block.target_text:
            text = _clean_text(block.target_text)
        elif bt in ("code", "equation", "table", "image"):
            # These content types keep English source (code shouldn't be translated)
            text = _clean_text(block.source_text)
        elif bt == "heading":
            # Headings: keep English as fallback (structural markers)
            text = _clean_text(block.source_text)
        elif bt == "caption":
            # Captions: keep English as fallback (tied to images)
            text = _clean_text(block.source_text)
        else:
            # Untranslated paragraph/footnote/list_item: SKIP entirely
            # (even if protected_policy=protect — we don't show raw English)
            continue

        if not text and bt != "image":
            continue

        # Skip stray fragments (page numbers, short repeated labels)
        if bt == "paragraph" and _is_stray_fragment(text):
            continue

        # Convert PDF sidebar markers to Chinese callout labels
        _SIDEBAR_MAP = {
            "NOTE": "**注意**",
            "TIP": "**提示**",
            "WARNING": "**警告**",
            "DEFINITION": "**定义**",
            "IMPORTANT": "**重要**",
            "SIDEBAR": "",
        }
        text_stripped = text.strip()
        text_upper = text_stripped.upper()
        if bt in ("paragraph", "heading"):
            # Case 1: entire block is just a sidebar marker
            if text_upper in _SIDEBAR_MAP:
                label = _SIDEBAR_MAP[text_upper]
                if label:
                    lines.append(label)
                    lines.append("")
                prev_type = "paragraph"
                continue
            # Case 2: translation kept the English marker as prefix
            # e.g. "NOTE\n对人脑结构的..." → strip the marker, prepend Chinese label
            for marker, label in _SIDEBAR_MAP.items():
                if text_stripped.startswith(marker + "\n") or text_stripped.startswith(marker + " "):
                    text = text_stripped[len(marker):].strip()
                    if label:
                        lines.append(label)
                        lines.append("")
                    break

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

            # Synthesize missing chapter headings for chapters 8 and 9
            # These don't have heading blocks in the DB, so insert before X.1
            sec_prefix = re.match(r'^(\d+\.\d+)', heading_text)
            if sec_prefix:
                sec_key = sec_prefix.group(1)
                if sec_key in _missing_chapters:
                    ch_title = _missing_chapters.pop(sec_key)
                    lines.append("")
                    lines.append("")
                    lines.append(f"# {ch_title}")
                    lines.append("")
            # Clean bold markers around section numbers: **2.2** → 2.2
            heading_text = re.sub(r'\*\*(\d+(?:\.\d+)*)\*\*', r'\1', heading_text)
            heading_text = re.sub(r'^\*\*(.+)\*\*$', r'\1', heading_text)  # fully bolded heading
            # Clean bold chapter markers: **第六章** → 第六章
            heading_text = re.sub(r'\*\*(第[一二三四五六七八九十\d]+章)\*\*', r'\1', heading_text)
            heading_text = heading_text.strip()

            # --- Filter out non-heading content ---
            skip_as_heading = False

            # 1. Full paragraphs misclassified as headings (>100 chars)
            if len(heading_text) > 100:
                skip_as_heading = True

            # 2. Figure-internal numbered steps: "1 接收", "3 分词"
            #    But NOT chapter headings with colon: "2 分词器：大型语言模型如何理解世界"
            if re.match(r'^\d+\s+\S{1,8}$', heading_text) and not heading_text.rstrip().endswith(('：', ':')):
                skip_as_heading = True

            # 3. Decimal-prefix diagram labels: "0.7 计算损失 神经网络"
            #    Real sections start with 1-9: "2.2 语言模型", "3.1.1 Transformer"
            if re.match(r'^0\.\d+\s', heading_text):
                skip_as_heading = True

            # 4. Space-separated short terms (diagram labels): "提示 补全 评分"
            #    But NOT chapter headings: "2 分词器：" (single digit + meaningful title)
            words = heading_text.split()
            if len(words) >= 2 and all(len(w) <= 6 for w in words) and not re.match(r'^\d+\.\d+', heading_text):
                if len(heading_text) < 30:
                    # Don't skip if it looks like "N Title" (chapter heading)
                    if not (len(words) == 2 and words[0].isdigit() and len(words[1]) > 2):
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

            # If heading ends with colon or is truncated, merge with next block
            if i + 1 < len(blocks):
                next_b = blocks[i + 1]
                if next_b.block_type == "paragraph" and next_b.target_text:
                    next_txt = _clean_text(next_b.target_text).strip()
                    should_merge = False
                    # Case 1: heading ends with colon (title continuation)
                    if heading_text.rstrip().endswith(('：', ':')):
                        if next_txt and len(next_txt) < 60:
                            should_merge = True
                    if should_merge:
                        heading_text = heading_text.rstrip() + next_txt
                        merged_into.add(i + 1)

            # Fix truncated headings where PDF parser only captured first word
            # These have very short titles (1-2 Chinese chars after section number)
            # Supplement from the English source text
            _TRUNCATED_HEADING_MAP = {
                "5.4.3 修改": "5.4.3 在推理阶段修改模型预测",
                "6.1.1 提升": "6.1.1 提升代码生成能力",
                "6.2.2 辅助": "6.2.2 辅助大语言模型理解数字",
                "7.2.3 优化": "7.2.3 优化与改进",
            }
            if heading_text in _TRUNCATED_HEADING_MAP:
                heading_text = _TRUNCATED_HEADING_MAP[heading_text]

            # Known chapter titles without numbers — map to chapter number
            _CHAPTER_TITLE_MAP = {
                "宏观视角": "1",
                "大语言模型如何学习": "4",
                "如何约束大语言模型的行为": "5",
            }
            for title_key, ch_num in _CHAPTER_TITLE_MAP.items():
                if heading_text.startswith(title_key):
                    heading_text = f"{ch_num} {heading_text}"
                    break

            # Derive heading level from section number pattern instead of
            # relying on PDF heading_level (which is often wrong):
            #   "3" (chapter) → #
            #   "3.1" → ##
            #   "3.1.1" → ###
            #   "3.1.1.1" → ####
            #   No number (front matter, sidebar) → use heading_level from DB
            sec_m = re.match(r'^(\d+(?:\.\d+)*)', heading_text)
            if sec_m:
                sec = sec_m.group(1)
                dot_count = sec.count('.')
                if dot_count == 0:
                    # Single number = chapter heading → #
                    derived_level = 1
                elif dot_count == 1:
                    derived_level = 2
                elif dot_count == 2:
                    derived_level = 3
                else:
                    derived_level = 4
            elif re.match(r'^第[一二三四五六七八九十\d]+章', heading_text):
                # Chinese chapter marker → #
                derived_level = 1
            else:
                # No section number — use DB level, but cap sidebar/callout headings
                derived_level = block.heading_level if block.heading_level > 0 else 2

            # Extra spacing before headings for breathing room
            lines.append("")
            lines.append("")
            prefix = "#" * max(1, min(derived_level, 4)) + " "
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
                # It's actually prose — only show if we have a Chinese translation
                if block.target_text:
                    zh_text = _clean_text(block.target_text)
                    if zh_text:
                        lines.append(zh_text)
                        lines.append("")
                # Skip untranslated English prose in fake-code blocks
                prev_type = "paragraph"
            else:
                lang = _detect_code_language(source_text)
                # For real code: always use source text (code shouldn't be translated)
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
                # Prefer Chinese caption for alt text; fall back to generic "图片"
                # (don't use English alt text — this is a Chinese document)
                if caption_text and re.search(r'[\u4e00-\u9fff]', caption_text):
                    display_alt = caption_text
                else:
                    display_alt = "图片"
                # Clean alt text for markdown (no newlines, no bold markers)
                display_alt = display_alt.replace("\n", " ").replace("**", "").strip()
                display_alt = display_alt.strip("* ").strip()
                # Truncate long alt text for cleaner markdown
                if len(display_alt) > 80:
                    display_alt = display_alt[:77] + "..."
                lines.append(f"![{display_alt}]({img_path})")
            else:
                # No extracted image file — skip silently if no Chinese caption.
                # Only show placeholder if we have a meaningful Chinese caption.
                if caption_text and re.search(r'[\u4e00-\u9fff]', caption_text):
                    lines.append(f"> [图片]")
                else:
                    # Completely skip untranslated/missing images
                    prev_type = "image"
                    continue

            if caption_text:
                clean_caption = caption_text.replace("\n", " ").strip()
                # Strip all bold markers (**) — we'll wrap in single * for italic
                clean_caption = clean_caption.replace("**", "")
                clean_caption = clean_caption.strip("* ").strip()
                if clean_caption:
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
            # Strip all bold markers — we re-wrap in single * for italic
            caption = caption.replace("**", "")
            caption = caption.strip("* ").strip()
            if not caption:
                continue
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

            # Skip figure-internal labels: short text between images
            # (translated diagram annotations, axis labels, etc.)
            # A "figure zone" is a region of images + short paragraphs ending
            # with a caption. We detect it by checking:
            #   1. Previous rendered block was image (or we just skipped a label)
            #   2. This paragraph is short (< 40 chars)
            #   3. Looking ahead, we find an image or caption within the next
            #      few blocks (skipping other short paragraphs)
            if para_text and len(para_text) < 40 and prev_type in ("image", "figure_label"):
                # Check if next non-empty block is also image/caption
                next_bt = ""
                for ni in range(i + 1, min(i + 8, len(blocks))):
                    if ni in merged_into:
                        continue
                    nbt = blocks[ni].block_type
                    if nbt in ("image", "caption"):
                        next_bt = nbt
                        break
                    elif nbt == "paragraph":
                        nb_txt = _clean_text(blocks[ni].target_text or "")
                        if nb_txt and len(nb_txt) < 40:
                            continue  # also short, keep looking
                        break
                    else:
                        break  # non-paragraph, non-image → end of figure zone
                if next_bt in ("image", "caption"):
                    # This short text between images is a figure label — skip
                    # Keep prev_type as "figure_label" so consecutive labels
                    # are also caught (don't reset to "paragraph")
                    prev_type = "figure_label"
                    continue

            # Skip chapter divider paragraphs: "6 超越自然语言处理" that duplicate headings.
            # These are chapter divider pages in the PDF — short paragraphs starting
            # with a bare chapter number followed by the chapter title.
            if para_text and len(para_text) < 50:
                divider_m = re.match(r'^(\d+)\s+(.+)$', para_text)
                if divider_m:
                    divider_title = divider_m.group(2).strip()
                    # Check against heading texts OR source text pattern
                    # (source starts with "N Title" where N is chapter number)
                    src_text = (block.source_text or "").strip()
                    src_is_divider = bool(re.match(r'^\d+\s+[A-Z]', src_text)) and len(src_text) < 60
                    if divider_title in heading_texts or src_is_divider:
                        prev_type = "paragraph"
                        continue

            if para_text and not _is_stray_fragment(para_text):
                # Split multi-paragraph blocks at newlines → separate paragraphs
                # Each sub-paragraph gets its own blank line for breathing room
                sub_paragraphs = [p.strip() for p in para_text.split("\n") if p.strip()]
                for sp in sub_paragraphs:
                    if not _is_stray_fragment(sp):
                        lines.append(sp)
                        lines.append("")
            prev_type = "paragraph"

    if in_list:
        lines.append("")

    # Post-processing: normalize blank lines for readability
    # Allow up to 3 blank lines (around headings), collapse anything more
    output = "\n".join(lines)
    output = re.sub(r'\n{5,}', '\n\n\n\n', output)
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

    # Image handling — extract ALL images from PDF for accuracy
    image_paths: dict[str, str] = {}

    if not args.skip_images and source_pdf and Path(source_pdf).exists():
        image_blocks = [b for b in blocks if b.block_type == "image"]
        if image_blocks:
            image_paths = extract_images_from_pdf(source_pdf, image_blocks, output_dir)
            logger.info("Image paths: %d / %d image blocks",
                       len(image_paths), len(image_blocks))

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
