"""PDF-in-place text replacement engine.

Implements the "renovation, not reconstruction" approach: modifies the source PDF
directly via PyMuPDF's redact-and-insert API, replacing English text with Chinese
translations while preserving all non-text elements (images, vector graphics,
page layout, margins, figures).

Modules:
    A – PDF Text Run Extractor
    B – Block-to-Run Mapper
    C – PDF Redact & Insert Engine
    D – Font Size Fitter

Reference: docs/high-fidelity-document-translation-spec.md
"""

from __future__ import annotations

import logging
import re
import shutil
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_FONT_SIZE_ABSOLUTE = 4.0  # Hard floor: 4pt for footnotes/captions
_BODY_FONT_RATIO_FLOOR = 0.55  # 55% of original size for body text
_HEADING_FONT_RATIO_FLOOR = 0.55  # 55% for headings (relaxed for CJK)

# CJK vertical expansion ratios — CJK fonts have ~1.3-1.5x line height vs Latin ~1.2x
_HEADING_VEXPAND = 0.70  # 70% vertical expansion for headings (whitespace below)
_BODY_VEXPAND = 0.35  # 35% for body text
_SMALL_BLOCK_VEXPAND = 1.00  # 100% for small blocks (footnotes/captions need 2 CJK lines)
_SMALL_BLOCK_HEIGHT_THRESHOLD = 15.0  # blocks below this height get extra expansion
_FITTER_ITERATIONS = 12  # Binary-search iterations (~0.05pt precision)
_TEXT_SIMILARITY_THRESHOLD = 0.55  # Fuzzy match threshold (lowered for real-world)
_BBOX_OVERLAP_THRESHOLD = 0.20  # Minimum bbox overlap ratio

# Font pairing: PyMuPDF built-in CJK font names
_CJK_FONT_SERIF = "china-ss"  # Simplified Chinese Serif
_CJK_FONT_SANS = "china-s"  # Simplified Chinese Sans
_CJK_FONT_MONO = "china-s"  # No mono CJK built-in; use sans as fallback

# Keywords that suggest serif fonts
_SERIF_FONT_KEYWORDS = {"times", "palatino", "garamond", "georgia", "cambria", "book", "roman", "serif", "mincho"}
_MONO_FONT_KEYWORDS = {"mono", "courier", "consolas", "menlo", "fira code", "source code", "inconsolata"}

# Block types that should never be replaced
_PROTECTED_BLOCK_TYPES = {"code", "equation", "table", "image", "figure"}


# ---------------------------------------------------------------------------
# Module A: Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PdfTextRun:
    """A single text span extracted from the source PDF."""
    page_index: int  # 0-indexed
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    text: str
    font_name: str
    font_size: float
    font_flags: int  # bold=16, italic=2, serif=0, etc.
    color: int | tuple  # RGB color value
    block_index: int
    line_index: int
    span_index: int


@dataclass(slots=True)
class PdfParagraph:
    """Grouped text runs forming a logical paragraph on a page."""
    page_index: int
    bbox: tuple[float, float, float, float]
    text: str
    runs: list[PdfTextRun]
    dominant_font: str = ""
    dominant_size: float = 0.0
    dominant_flags: int = 0
    dominant_color: int | tuple = 0


@dataclass(slots=True)
class ReplacementPlan:
    """A single text replacement to execute on the PDF."""
    page_index: int
    bbox: tuple[float, float, float, float]
    source_text: str
    target_text: str
    replace_policy: str  # "replace" | "protect" | "skip"
    font_name: str  # source font name (for pairing lookup)
    font_size: float
    font_flags: int
    color: int | tuple
    block_id: str
    block_type: str
    confidence: float
    is_heading: bool = False


@dataclass(slots=True)
class FontReductionRecord:
    """Records where font size was reduced to fit text."""
    block_id: str
    page_index: int
    original_size: float
    used_size: float
    reduction_pct: float


@dataclass(slots=True)
class PdfInplaceExportResult:
    """Result of the PDF-in-place export operation."""
    output_path: str
    total_blocks: int
    replaced_blocks: int
    protected_blocks: int
    skipped_blocks: int  # unmapped
    warnings: list[str]
    coverage_pct: float
    pages_modified: int
    font_reductions: list[FontReductionRecord]
    elapsed_seconds: float


# ---------------------------------------------------------------------------
# Module A: PDF Text Run Extractor
# ---------------------------------------------------------------------------

def extract_text_runs(doc: fitz.Document) -> list[PdfTextRun]:
    """Extract all text runs from every page of the PDF.

    Returns runs ordered by (page_index, y_position, x_position).
    Uses span-level bbox for precision.
    """
    runs: list[PdfTextRun] = []
    for page in doc:
        try:
            text_dict = page.get_text("dict", sort=True)
        except Exception as exc:
            logger.warning("Failed to extract text from page %d: %s", page.number, exc)
            continue
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:  # skip image blocks
                continue
            block_idx = block.get("number", 0)
            for line_idx, line in enumerate(block.get("lines", [])):
                for span_idx, span in enumerate(line.get("spans", [])):
                    text = span.get("text", "").strip()
                    if not text:
                        continue
                    runs.append(PdfTextRun(
                        page_index=page.number,
                        bbox=tuple(span["bbox"]),
                        text=span["text"],  # preserve original whitespace
                        font_name=span.get("font", ""),
                        font_size=span.get("size", 12.0),
                        font_flags=span.get("flags", 0),
                        color=span.get("color", 0),
                        block_index=block_idx,
                        line_index=line_idx,
                        span_index=span_idx,
                    ))
    return runs


# ---------------------------------------------------------------------------
# Module A helpers: Group runs into paragraphs
# ---------------------------------------------------------------------------

def _runs_on_same_line(a: PdfTextRun, b: PdfTextRun, tolerance: float = 3.0) -> bool:
    """Check if two runs are on the same visual line (overlapping y-range)."""
    return abs(a.bbox[1] - b.bbox[1]) < tolerance and abs(a.bbox[3] - b.bbox[3]) < tolerance


def _runs_adjacent_vertically(a_bbox: tuple, b_bbox: tuple, line_height: float) -> bool:
    """Check if b_bbox is within ~1.5 line heights below a_bbox."""
    gap = b_bbox[1] - a_bbox[3]
    return -2.0 < gap < line_height * 1.8


def group_runs_into_paragraphs(runs: list[PdfTextRun]) -> dict[int, list[PdfParagraph]]:
    """Group consecutive text runs on the same page into logical paragraphs.

    Uses PyMuPDF block_index as the primary grouping key (runs in the same
    PDF block belong to the same paragraph), with fallback spatial merging
    for runs that span multiple blocks but are visually contiguous.
    """
    # Group by (page_index, block_index)
    page_block_runs: dict[tuple[int, int], list[PdfTextRun]] = {}
    for run in runs:
        key = (run.page_index, run.block_index)
        page_block_runs.setdefault(key, []).append(run)

    # Build paragraphs per page
    paragraphs_by_page: dict[int, list[PdfParagraph]] = {}
    for (page_idx, _block_idx), block_runs in sorted(page_block_runs.items()):
        if not block_runs:
            continue
        # Compute merged bbox and concatenated text
        x0 = min(r.bbox[0] for r in block_runs)
        y0 = min(r.bbox[1] for r in block_runs)
        x1 = max(r.bbox[2] for r in block_runs)
        y1 = max(r.bbox[3] for r in block_runs)

        # Build text: join lines, collapse spans on same line with space
        lines_map: dict[int, list[PdfTextRun]] = {}
        for r in block_runs:
            lines_map.setdefault(r.line_index, []).append(r)
        text_lines = []
        for _li, line_runs in sorted(lines_map.items()):
            line_text = " ".join(r.text for r in sorted(line_runs, key=lambda r: r.bbox[0]))
            text_lines.append(line_text)
        text = " ".join(text_lines)

        # Dominant font: most frequent by character count
        font_chars: dict[str, int] = {}
        size_chars: dict[float, int] = {}
        flag_chars: dict[int, int] = {}
        color_chars: dict[int | tuple, int] = {}
        for r in block_runs:
            n = len(r.text)
            font_chars[r.font_name] = font_chars.get(r.font_name, 0) + n
            size_chars[r.font_size] = size_chars.get(r.font_size, 0) + n
            flag_chars[r.font_flags] = flag_chars.get(r.font_flags, 0) + n
            color_chars[r.color] = color_chars.get(r.color, 0) + n

        para = PdfParagraph(
            page_index=page_idx,
            bbox=(x0, y0, x1, y1),
            text=text,
            runs=block_runs,
            dominant_font=max(font_chars, key=font_chars.get) if font_chars else "",
            dominant_size=max(size_chars, key=size_chars.get) if size_chars else 12.0,
            dominant_flags=max(flag_chars, key=flag_chars.get) if flag_chars else 0,
            dominant_color=max(color_chars, key=color_chars.get) if color_chars else 0,
        )
        paragraphs_by_page.setdefault(page_idx, []).append(para)

    return paragraphs_by_page


# ---------------------------------------------------------------------------
# Module B: Text normalization & matching utilities
# ---------------------------------------------------------------------------

def _normalize_text(text: str) -> str:
    """Normalize text for fuzzy comparison: NFKD, collapse whitespace, lowercase."""
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    # Expand common ligatures
    text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    return text


def _text_similarity(a: str, b: str) -> float:
    """Compute normalized text similarity using character-level Jaccard + containment.

    Fast approximation that avoids O(n*m) edit distance for long texts.
    Returns value in [0, 1].
    """
    if not a or not b:
        return 0.0
    na, nb = _normalize_text(a), _normalize_text(b)
    if na == nb:
        return 1.0
    # Check containment (one text is substring of the other)
    if na in nb or nb in na:
        return min(len(na), len(nb)) / max(len(na), len(nb))

    # Character n-gram Jaccard (trigrams)
    def trigrams(s: str) -> set[str]:
        if len(s) < 3:
            return {s}
        return {s[i:i + 3] for i in range(len(s) - 2)}

    ta, tb = trigrams(na), trigrams(nb)
    if not ta or not tb:
        return 0.0
    intersection = len(ta & tb)
    union = len(ta | tb)
    return intersection / union if union > 0 else 0.0


def _bbox_overlap_ratio(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Compute overlap ratio between two bounding boxes.

    Returns intersection_area / min(area_a, area_b).
    """
    ix0 = max(a[0], b[0])
    iy0 = max(a[1], b[1])
    ix1 = min(a[2], b[2])
    iy1 = min(a[3], b[3])
    if ix0 >= ix1 or iy0 >= iy1:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    area_a = max((a[2] - a[0]) * (a[3] - a[1]), 1e-6)
    area_b = max((b[2] - b[0]) * (b[3] - b[1]), 1e-6)
    return intersection / min(area_a, area_b)


# ---------------------------------------------------------------------------
# Module B: Block-to-Run Mapper
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _DbBlock:
    """Lightweight representation of a DB block for mapping."""
    block_id: str
    page_index: int  # 0-indexed (already converted from DB 1-indexed)
    bbox: tuple[float, float, float, float]
    source_text: str
    target_text: str
    block_type: str
    protected_policy: str  # "translate", "protect", "mixed"


def _extract_db_blocks(
    chapters: list[Any],
    render_blocks_by_chapter: dict[str, list[Any]],
) -> list[_DbBlock]:
    """Extract block data from chapter bundles + pre-resolved render blocks.

    Uses source_span_json for page/bbox info and MergedRenderBlock for target_text.
    """
    db_blocks: list[_DbBlock] = []
    for chapter_bundle in chapters:
        render_blocks = render_blocks_by_chapter.get(chapter_bundle.chapter.id, [])
        render_map = {rb.block_id: rb for rb in render_blocks}

        for block in chapter_bundle.blocks:
            source_meta = block.source_span_json or {}
            # Extract page number (1-indexed in DB → 0-indexed for PyMuPDF)
            page_num = None
            bbox_raw = None
            source_bbox_json = source_meta.get("source_bbox_json")
            if isinstance(source_bbox_json, dict):
                regions = source_bbox_json.get("regions")
                if isinstance(regions, list) and regions:
                    first_region = regions[0]
                    if isinstance(first_region, dict):
                        page_num = first_region.get("page_number")
                        bbox_raw = first_region.get("bbox")
            if page_num is None:
                page_num = source_meta.get("source_page_start")
            if page_num is None:
                continue  # no page info, skip

            page_index = page_num - 1  # Convert to 0-indexed
            if page_index < 0:
                continue

            if bbox_raw is None or not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
                continue

            bbox = (float(bbox_raw[0]), float(bbox_raw[1]), float(bbox_raw[2]), float(bbox_raw[3]))

            # Get target text from render block
            rb = render_map.get(block.id)
            target_text = ""
            if rb is not None and rb.target_text:
                target_text = rb.target_text

            db_blocks.append(_DbBlock(
                block_id=block.id,
                page_index=page_index,
                bbox=bbox,
                source_text=block.source_text or "",
                target_text=target_text,
                block_type=block.block_type.value if hasattr(block.block_type, "value") else str(block.block_type),
                protected_policy=block.protected_policy.value if hasattr(block.protected_policy, "value") else str(block.protected_policy),
            ))

    return db_blocks


def build_replacement_plans(
    paragraphs_by_page: dict[int, list[PdfParagraph]],
    db_blocks: list[_DbBlock],
) -> list[ReplacementPlan]:
    """Match DB blocks to PDF paragraphs and produce replacement plans.

    For each translatable block, finds the best matching paragraph on the same page
    using bbox overlap + text similarity scoring.
    """
    plans: list[ReplacementPlan] = []
    used_paragraph_keys: set[tuple[int, int]] = set()  # (page_idx, para_index)

    for block in db_blocks:
        # Determine replace policy
        if block.protected_policy == "protect":
            plans.append(ReplacementPlan(
                page_index=block.page_index,
                bbox=block.bbox,
                source_text=block.source_text[:100],
                target_text="",
                replace_policy="protect",
                font_name="",
                font_size=12.0,
                font_flags=0,
                color=0,
                block_id=block.block_id,
                block_type=block.block_type,
                confidence=1.0,
            ))
            continue

        if block.block_type in _PROTECTED_BLOCK_TYPES:
            plans.append(ReplacementPlan(
                page_index=block.page_index,
                bbox=block.bbox,
                source_text=block.source_text[:100],
                target_text="",
                replace_policy="protect",
                font_name="",
                font_size=12.0,
                font_flags=0,
                color=0,
                block_id=block.block_id,
                block_type=block.block_type,
                confidence=1.0,
            ))
            continue

        # Skip decorative elements: single-digit chapter numbers, section markers
        source_stripped = block.source_text.strip()
        if len(source_stripped) <= 3 and re.match(r"^\d{1,3}$", source_stripped):
            continue  # decorative chapter/section numbers

        if not block.target_text:
            # No translation available — skip (leave original)
            plans.append(ReplacementPlan(
                page_index=block.page_index,
                bbox=block.bbox,
                source_text=block.source_text[:100],
                target_text="",
                replace_policy="skip",
                font_name="",
                font_size=12.0,
                font_flags=0,
                color=0,
                block_id=block.block_id,
                block_type=block.block_type,
                confidence=0.0,
            ))
            continue

        # Find matching paragraph(s) on the same page
        page_paras = paragraphs_by_page.get(block.page_index, [])
        if not page_paras:
            plans.append(ReplacementPlan(
                page_index=block.page_index,
                bbox=block.bbox,
                source_text=block.source_text[:100],
                target_text=block.target_text,
                replace_policy="skip",
                font_name="",
                font_size=12.0,
                font_flags=0,
                color=0,
                block_id=block.block_id,
                block_type=block.block_type,
                confidence=0.0,
            ))
            continue

        best_score = -1.0
        best_para: PdfParagraph | None = None
        best_para_idx = -1

        for para_idx, para in enumerate(page_paras):
            # Skip already-used paragraphs
            para_key = (block.page_index, para_idx)
            if para_key in used_paragraph_keys:
                continue

            overlap = _bbox_overlap_ratio(para.bbox, block.bbox)
            if overlap < _BBOX_OVERLAP_THRESHOLD:
                continue

            text_sim = _text_similarity(para.text, block.source_text)
            # Combined score: weighted average (text more important than bbox)
            score = 0.7 * text_sim + 0.3 * overlap

            if score > best_score:
                best_score = score
                best_para = para
                best_para_idx = para_idx

        if best_para is not None and best_score >= _TEXT_SIMILARITY_THRESHOLD:
            used_paragraph_keys.add((block.page_index, best_para_idx))
            is_heading = block.block_type in {"heading"}
            # Use the LARGER of DB bbox and paragraph bbox for redaction/insertion
            # DB bbox is often more generous (full block area), while paragraph bbox
            # is tighter (just the text). Using the union ensures we clear enough
            # space and have room for CJK text which is typically wider.
            para_bbox = best_para.bbox
            db_bbox = block.bbox
            merged_bbox = (
                min(para_bbox[0], db_bbox[0]),
                min(para_bbox[1], db_bbox[1]),
                max(para_bbox[2], db_bbox[2]),
                max(para_bbox[3], db_bbox[3]),
            )
            plans.append(ReplacementPlan(
                page_index=block.page_index,
                bbox=merged_bbox,
                source_text=best_para.text[:200],
                target_text=block.target_text,
                replace_policy="replace",
                font_name=best_para.dominant_font,
                font_size=best_para.dominant_size,
                font_flags=best_para.dominant_flags,
                color=best_para.dominant_color,
                block_id=block.block_id,
                block_type=block.block_type,
                confidence=best_score,
                is_heading=is_heading,
            ))
        else:
            # Low confidence — fall back to DB bbox directly
            plans.append(ReplacementPlan(
                page_index=block.page_index,
                bbox=block.bbox,
                source_text=block.source_text[:200],
                target_text=block.target_text,
                replace_policy="replace",
                font_name="",
                font_size=10.0,
                font_flags=0,
                color=0,
                block_id=block.block_id,
                block_type=block.block_type,
                confidence=best_score if best_score > 0 else 0.1,
                is_heading=block.block_type in {"heading"},
            ))

    return plans


# ---------------------------------------------------------------------------
# Module D: Font Size Fitter
# ---------------------------------------------------------------------------

def _select_cjk_font(source_font_name: str, font_flags: int = 0) -> str:
    """Select the best CJK font name based on the source font characteristics.

    font_flags: PyMuPDF flags (bit 1=italic, bit 4=bold, bit 2=serif, bit 3=mono).
    """
    name_lower = source_font_name.lower()
    is_mono = bool(font_flags & 8) or any(kw in name_lower for kw in _MONO_FONT_KEYWORDS)
    is_sans = any(kw in name_lower for kw in {"sans", "gothic", "helvetica", "arial", "franklin", "gill", "futura", "avenir", "trebuchet", "segoe"})

    if is_mono:
        return _CJK_FONT_MONO
    if is_sans:
        return _CJK_FONT_SANS
    # Default serif (most book body text)
    return _CJK_FONT_SERIF


def _test_textbox_fit(
    text: str,
    width: float,
    height: float,
    fontname: str,
    fontsize: float,
    scratch_doc: fitz.Document | None = None,
    align: int = 0,
) -> bool:
    """Test if text fits in a rectangle at the given font size.

    Reuses scratch_doc if provided (much faster in loops).
    """
    try:
        if scratch_doc is None:
            scratch_doc = fitz.open()
        page = scratch_doc.new_page(width=width + 20, height=height + 20)
        rc = page.insert_textbox(
            fitz.Rect(0, 0, width, height),
            text,
            fontname=fontname,
            fontsize=fontsize,
            align=align,
        )
        # Delete the page to keep scratch doc small
        scratch_doc.delete_page(-1)
        return rc >= 0
    except Exception:
        try:
            scratch_doc.delete_page(-1)
        except Exception:
            pass
        return False


def compute_fitting_font_size(
    text: str,
    bbox: tuple[float, float, float, float],
    fontname: str,
    max_size: float,
    min_ratio: float = _BODY_FONT_RATIO_FLOOR,
    scratch_doc: fitz.Document | None = None,
    align: int = 0,
    height_expand_ratio: float = _BODY_VEXPAND,
) -> float:
    """Binary search for the largest font size that fits text in bbox.

    Uses fitz scratch page measurement for accurate CJK text layout.
    Returns the fitting font size (may be == max_size if text fits).
    Pass scratch_doc to reuse across multiple calls (significant speedup).
    align: fitz.TEXT_ALIGN_LEFT (0), TEXT_ALIGN_CENTER (1), TEXT_ALIGN_RIGHT (2), TEXT_ALIGN_JUSTIFY (3).
    height_expand_ratio: vertical expansion factor for CJK line height (default 0.35 = 35%).
    """
    min_size = max(max_size * min_ratio, _MIN_FONT_SIZE_ABSOLUTE)
    if min_size >= max_size:
        return min_size

    rect = fitz.Rect(bbox)
    if rect.width < 1 or rect.height < 1:
        return max_size

    # CJK fonts have larger line height (~1.3-1.5x) than Latin (~1.2x).
    # Expand measurement height to account for this difference.
    width = rect.width
    height = rect.height * (1.0 + height_expand_ratio)

    # Quick check: does it fit at max size?
    if _test_textbox_fit(text, width, height, fontname, max_size, scratch_doc, align):
        return max_size

    lo, hi = min_size, max_size
    best = min_size

    for _ in range(_FITTER_ITERATIONS):
        mid = (lo + hi) / 2
        if _test_textbox_fit(text, width, height, fontname, mid, scratch_doc, align):
            best = mid
            lo = mid
        else:
            hi = mid

    return best


# ---------------------------------------------------------------------------
# Module C: Background Color Detection
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _BgFill:
    """A filled rectangle on a page that may be a background."""
    rect: fitz.Rect
    fill: tuple[float, float, float]


def _detect_background_fills(page: fitz.Page) -> list[_BgFill]:
    """Find non-white filled rectangles on a page (callout boxes, sidebars).

    These indicate areas where redaction fill should match the background
    rather than using white.
    """
    fills: list[_BgFill] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return fills

    for d in drawings:
        fill = d.get("fill")
        if not fill or not isinstance(fill, (list, tuple)) or len(fill) < 3:
            continue
        # Skip white and near-white fills
        r, g, b = float(fill[0]), float(fill[1]), float(fill[2])
        if r > 0.98 and g > 0.98 and b > 0.98:
            continue
        # Skip very dark fills (likely borders/lines, not backgrounds)
        if r < 0.1 and g < 0.1 and b < 0.1:
            continue
        # Look for rectangle items (background shapes)
        for item in d.get("items", []):
            if item[0] == "re":  # rectangle
                rect = item[1]
                # Only consider reasonably sized rectangles (not tiny dots)
                if isinstance(rect, fitz.Rect) and rect.width > 20 and rect.height > 20:
                    fills.append(_BgFill(rect=rect, fill=(r, g, b)))
    return fills


def _find_background_color(
    text_rect: fitz.Rect,
    bg_fills: list[_BgFill],
) -> tuple[float, float, float]:
    """Find the background color behind a text rectangle.

    Returns the fill color of the largest background rectangle that
    contains or significantly overlaps the text rect.
    Falls back to white (1, 1, 1) if no background is detected.
    """
    best_area = 0.0
    best_fill = (1.0, 1.0, 1.0)

    for bg in bg_fills:
        # Check if the text rect is mostly inside this background rect
        intersection = bg.rect & text_rect  # fitz.Rect intersection
        if intersection.is_empty:
            continue
        overlap_area = intersection.width * intersection.height
        text_area = max(text_rect.width * text_rect.height, 1.0)
        # Text must be at least 50% inside the background
        if overlap_area / text_area > 0.5:
            bg_area = bg.rect.width * bg.rect.height
            if bg_area > best_area:
                best_area = bg_area
                best_fill = bg.fill

    return best_fill


def _translate_bookmarks(
    doc: fitz.Document,
    plans: list[ReplacementPlan],
    warnings: list[str],
) -> None:
    """Translate PDF bookmarks (TOC/outline) using heading replacement plans.

    Matches bookmark titles to heading plans by page number and text similarity,
    then replaces the bookmark text with the Chinese translation.
    """
    try:
        toc = doc.get_toc(simple=True)
    except Exception:
        return
    if not toc:
        return

    # Build a lookup: page_number → list of heading plans with translations
    heading_plans_by_page: dict[int, list[ReplacementPlan]] = {}
    for plan in plans:
        if plan.is_heading and plan.target_text:
            # TOC pages are 1-indexed in get_toc()
            toc_page = plan.page_index + 1
            heading_plans_by_page.setdefault(toc_page, []).append(plan)

    translated_count = 0
    new_toc = []
    for entry in toc:
        level, title, page = entry[0], entry[1], entry[2]
        # Try to find a matching heading plan on this page
        candidates = heading_plans_by_page.get(page, [])
        best_sim = 0.0
        best_target = None
        for hp in candidates:
            sim = _text_similarity(title, hp.source_text)
            if sim > best_sim:
                best_sim = sim
                best_target = hp.target_text

        if best_target and best_sim > 0.3:
            new_toc.append([level, best_target, page])
            translated_count += 1
        else:
            new_toc.append([level, title, page])

    if translated_count > 0:
        try:
            doc.set_toc(new_toc)
        except Exception as exc:
            warnings.append(f"Bookmark translation failed: {exc}")


# ---------------------------------------------------------------------------
# Module C: PDF Redact & Insert Engine
# ---------------------------------------------------------------------------

def execute_replacement(
    source_pdf_path: str | Path,
    output_path: str | Path,
    plans: list[ReplacementPlan],
    *,
    dry_run: bool = False,
) -> PdfInplaceExportResult:
    """Execute all replacement plans on the source PDF.

    CRITICAL: Never modifies the source PDF. Copies first, then modifies the copy.
    CRITICAL: Always uses images=fitz.PDF_REDACT_IMAGE_NONE to preserve images.
    """
    t0 = time.time()
    source_pdf_path = Path(source_pdf_path)
    output_path = Path(output_path)
    warnings: list[str] = []
    font_reductions: list[FontReductionRecord] = []

    if not source_pdf_path.exists():
        raise FileNotFoundError(f"Source PDF not found: {source_pdf_path}")

    # Never modify source — open source read-only, save to output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    replace_plans = [p for p in plans if p.replace_policy == "replace" and p.target_text]
    protected_plans = [p for p in plans if p.replace_policy == "protect"]
    skipped_plans = [p for p in plans if p.replace_policy == "skip"]

    total_translatable = len(replace_plans) + len(skipped_plans)

    if dry_run:
        coverage = len(replace_plans) / max(total_translatable, 1) * 100
        return PdfInplaceExportResult(
            output_path=str(output_path),
            total_blocks=len(plans),
            replaced_blocks=len(replace_plans),
            protected_blocks=len(protected_plans),
            skipped_blocks=len(skipped_plans),
            warnings=warnings,
            coverage_pct=coverage,
            pages_modified=len({p.page_index for p in replace_plans}),
            font_reductions=font_reductions,
            elapsed_seconds=time.time() - t0,
        )

    doc = fitz.open(str(source_pdf_path))

    # Validate page count
    total_pages = len(doc)
    valid_plans = []
    for plan in replace_plans:
        if plan.page_index < 0 or plan.page_index >= total_pages:
            warnings.append(f"Block {plan.block_id}: page {plan.page_index} out of range (0-{total_pages - 1})")
            continue
        valid_plans.append(plan)

    # Group by page
    plans_by_page: dict[int, list[ReplacementPlan]] = {}
    for plan in valid_plans:
        plans_by_page.setdefault(plan.page_index, []).append(plan)

    pages_modified = 0

    # Shared scratch document for font-size fitting (avoids creating thousands of temp docs)
    scratch_doc = fitz.open()

    # Page height lookup for clamping expanded rects
    page_heights: dict[int, float] = {}

    # Image count verification (per Hard Prohibition #2)
    for page_idx, page_plans in sorted(plans_by_page.items()):
        page = doc[page_idx]
        images_before = len(page.get_images(full=True))
        page_height = page.rect.height
        page_heights[page_idx] = page_height

        # Pre-compute background fills: detect colored rectangles behind text
        bg_fills = _detect_background_fills(page)

        # Pre-compute vertical expansion ratios per plan
        # CJK fonts have ~1.3-1.5x line height vs Latin ~1.2x, so we need
        # more vertical space than the original English bbox provides.
        def _vexpand_ratio(plan: ReplacementPlan) -> float:
            if plan.is_heading:
                return _HEADING_VEXPAND
            bbox_height = plan.bbox[3] - plan.bbox[1]
            if bbox_height < _SMALL_BLOCK_HEIGHT_THRESHOLD:
                return _SMALL_BLOCK_VEXPAND
            return _BODY_VEXPAND

        try:
            # Step 1: Add redaction annotations at ORIGINAL bbox only.
            # IMPORTANT: Do NOT expand redaction area — expansion is only for
            # insertion. Expanding redaction clears adjacent untranslated blocks'
            # text, creating visible damage (e.g., "rn how to" from "learn how to").
            for plan in page_plans:
                rect = fitz.Rect(plan.bbox)
                if rect.width < 1 or rect.height < 1:
                    warnings.append(f"Block {plan.block_id}: degenerate bbox on page {page_idx}")
                    continue
                fill_color = _find_background_color(rect, bg_fills)
                page.add_redact_annot(
                    quad=rect,
                    fill=fill_color,
                )

            # Step 2: Apply all redactions — CRITICAL: preserve images
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

            # Step 3: Insert translated text
            for plan in page_plans:
                rect = fitz.Rect(plan.bbox)
                if rect.width < 1 or rect.height < 1:
                    continue

                cjk_font = _select_cjk_font(plan.font_name, plan.font_flags)
                min_ratio = _HEADING_FONT_RATIO_FLOOR if plan.is_heading else _BODY_FONT_RATIO_FLOOR
                # Use JUSTIFY for body text (packs CJK tighter), LEFT for headings
                text_align = fitz.TEXT_ALIGN_LEFT if plan.is_heading else fitz.TEXT_ALIGN_JUSTIFY

                # Select vertical expansion based on block type
                vexpand = _vexpand_ratio(plan)

                target_size = compute_fitting_font_size(
                    text=plan.target_text,
                    bbox=plan.bbox,
                    fontname=cjk_font,
                    max_size=plan.font_size,
                    min_ratio=min_ratio,
                    scratch_doc=scratch_doc,
                    align=text_align,
                    height_expand_ratio=vexpand,
                )

                # Record font reduction
                if target_size < plan.font_size * 0.99:
                    reduction_pct = (plan.font_size - target_size) / plan.font_size * 100
                    font_reductions.append(FontReductionRecord(
                        block_id=plan.block_id,
                        page_index=page_idx,
                        original_size=plan.font_size,
                        used_size=target_size,
                        reduction_pct=reduction_pct,
                    ))
                    if target_size < _MIN_FONT_SIZE_ABSOLUTE + 1.0:
                        warnings.append(
                            f"Block {plan.block_id}: font reduced to {target_size:.1f}pt "
                            f"(near {_MIN_FONT_SIZE_ABSOLUTE}pt floor)"
                        )

                # Determine text color
                color = (0, 0, 0)  # default black
                if isinstance(plan.color, (list, tuple)) and len(plan.color) >= 3:
                    color = tuple(plan.color[:3])
                elif isinstance(plan.color, int):
                    # Convert int RGB to tuple
                    r = ((plan.color >> 16) & 0xFF) / 255.0
                    g = ((plan.color >> 8) & 0xFF) / 255.0
                    b = (plan.color & 0xFF) / 255.0
                    color = (r, g, b)

                # Expand insertion rect to match measurement expansion
                insert_rect = fitz.Rect(rect)
                expand_px = rect.height * vexpand
                insert_rect.y1 = min(insert_rect.y1 + expand_px, page_height)

                try:
                    rc = page.insert_textbox(
                        insert_rect,
                        plan.target_text,
                        fontname=cjk_font,
                        fontsize=target_size,
                        color=color,
                        align=text_align,
                    )
                    if rc < 0:
                        warnings.append(
                            f"Block {plan.block_id}: text overflow on page {page_idx} "
                            f"(size={target_size:.1f}pt)"
                        )
                except Exception as exc:
                    warnings.append(f"Block {plan.block_id}: insert failed on page {page_idx}: {exc}")

            pages_modified += 1

        except Exception as exc:
            warnings.append(f"Page {page_idx}: redaction failed: {exc}")
            continue

        # Verify images preserved
        images_after = len(page.get_images(full=True))
        if images_after < images_before:
            warnings.append(
                f"Page {page_idx}: IMAGE LOSS DETECTED! "
                f"Before={images_before}, After={images_after}. "
                f"This should never happen."
            )

    # Clean up scratch doc
    scratch_doc.close()

    # Translate PDF bookmarks/outline (TOC sidebar in PDF readers)
    _translate_bookmarks(doc, valid_plans, warnings)

    # Save with garbage collection and compression
    try:
        doc.save(str(output_path), garbage=4, deflate=True, incremental=False)
    except Exception as exc:
        # Fallback: save without optimization
        warnings.append(f"Optimized save failed ({exc}), trying basic save")
        doc.save(str(output_path))
    doc.close()

    coverage = len(valid_plans) / max(total_translatable, 1) * 100
    elapsed = time.time() - t0

    return PdfInplaceExportResult(
        output_path=str(output_path),
        total_blocks=len(plans),
        replaced_blocks=len(valid_plans),
        protected_blocks=len(protected_plans),
        skipped_blocks=len(skipped_plans),
        warnings=warnings,
        coverage_pct=coverage,
        pages_modified=pages_modified,
        font_reductions=font_reductions,
        elapsed_seconds=elapsed,
    )


# ---------------------------------------------------------------------------
# High-level orchestration
# ---------------------------------------------------------------------------

def export_pdf_inplace(
    source_pdf_path: str | Path,
    output_path: str | Path,
    chapter_bundles: list[Any],
    render_blocks_by_chapter: dict[str, list[Any]],
    *,
    dry_run: bool = False,
) -> PdfInplaceExportResult:
    """Top-level entry point for PDF-in-place export.

    Args:
        source_pdf_path: Path to the original English PDF.
        output_path: Where to write the Chinese PDF.
        chapter_bundles: List of ChapterExportBundle objects.
        render_blocks_by_chapter: Dict mapping chapter_id → list[MergedRenderBlock].
        dry_run: If True, compute plans but don't modify the PDF.

    Returns:
        PdfInplaceExportResult with coverage metrics and warnings.
    """
    logger.info("PDF-in-place export: extracting text runs from %s", source_pdf_path)
    doc = fitz.open(str(source_pdf_path))
    runs = extract_text_runs(doc)
    logger.info("Extracted %d text runs from %d pages", len(runs), len(doc))
    doc.close()

    logger.info("Grouping runs into paragraphs...")
    paragraphs_by_page = group_runs_into_paragraphs(runs)
    total_paras = sum(len(v) for v in paragraphs_by_page.values())
    logger.info("Formed %d paragraphs across %d pages", total_paras, len(paragraphs_by_page))

    logger.info("Extracting DB blocks with translations...")
    db_blocks = _extract_db_blocks(chapter_bundles, render_blocks_by_chapter)
    logger.info("Found %d DB blocks with page/bbox info", len(db_blocks))

    logger.info("Building replacement plans...")
    plans = build_replacement_plans(paragraphs_by_page, db_blocks)
    replace_count = sum(1 for p in plans if p.replace_policy == "replace")
    protect_count = sum(1 for p in plans if p.replace_policy == "protect")
    skip_count = sum(1 for p in plans if p.replace_policy == "skip")
    logger.info(
        "Replacement plans: %d replace, %d protect, %d skip",
        replace_count, protect_count, skip_count,
    )

    logger.info("Executing replacement on PDF...")
    result = execute_replacement(
        source_pdf_path=source_pdf_path,
        output_path=output_path,
        plans=plans,
        dry_run=dry_run,
    )

    logger.info(
        "PDF-in-place export complete: %.1f%% coverage, %d pages modified, %.1fs",
        result.coverage_pct,
        result.pages_modified,
        result.elapsed_seconds,
    )
    if result.warnings:
        for w in result.warnings[:20]:
            logger.warning("  %s", w)
        if len(result.warnings) > 20:
            logger.warning("  ... and %d more warnings", len(result.warnings) - 20)

    return result
