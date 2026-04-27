"""Export a chapter range to a Chinese-only HTML document.

Usage (env vars):
- CHAPTER_LABEL    : human-readable chapter label, e.g. "第 2 章"
- CHAPTER_TITLE    : Chinese title to display, e.g. "分词器: LLM 如何看见输入"
- ORDINAL_LO       : block ordinal lower bound (inclusive)
- ORDINAL_HI       : block ordinal upper bound (inclusive)
- OUTPUT_PATH      : where to write the HTML file
- DOCUMENT_ID      : document UUID
- CHAPTER_ID       : DB chapter UUID containing the lumped book body
- SOURCE_LABEL     : original document title for the citation header

The recovery service lumped the entire book body into a single DB
chapter for this PDF, so the in-product export gate isn't usable. We
read translated target_segments straight from the DB in block order
and render a self-contained HTML page.
"""
from __future__ import annotations

import base64
import html
import io
import logging
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import select

from book_agent.core.config import get_settings
from book_agent.domain.enums import TargetSegmentStatus
from book_agent.domain.models import Block, Chapter, Document, Sentence
from book_agent.domain.models.document import DocumentImage
from book_agent.domain.models.translation import (
    AlignmentEdge,
    TargetSegment,
    TranslationRun,
)
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope

logger = logging.getLogger("export-html")


DOCUMENT_ID = os.environ.get("DOCUMENT_ID", "d71027f0-6537-58d1-8e47-42ef2834fca4")
CHAPTER_ID = os.environ.get("CHAPTER_ID", "de30483c-ec5f-5d3d-a728-69de943db663")
ORDINAL_LO = int(os.environ.get("ORDINAL_LO", "130"))
ORDINAL_HI = int(os.environ.get("ORDINAL_HI", "247"))
CHAPTER_LABEL = os.environ.get("CHAPTER_LABEL", "第 2 章")
CHAPTER_TITLE = os.environ.get("CHAPTER_TITLE", "分词器")
SOURCE_LABEL = os.environ.get("SOURCE_LABEL", "How Large Language Models Work")
PDF_PATH = Path(
    os.environ.get(
        "PDF_PATH",
        "/Users/smy/project/book-agent/artifacts/uploads/8676b71d9dc34225b86939a5ecec92fc/llm-book.pdf",
    )
)
# Adaptive DPI: pick a render zoom so the longer side of the rendered
# bitmap doesn't exceed IMAGE_TARGET_MAX_PIXELS. Smaller figures get
# higher DPI (more detail), larger figures get lower DPI (smaller files).
# The clamp [IMAGE_MIN_DPI, IMAGE_MAX_DPI] guards both ends:
#   - min keeps text legible inside small icons
#   - max keeps full-page figures from blowing up to 30+ MB PNGs
# Page width in HTML is 760px; we render at ~2x for retina sharpness.
IMAGE_TARGET_MAX_PIXELS = int(os.environ.get("IMAGE_TARGET_MAX_PIXELS", "1600"))
IMAGE_MIN_DPI = int(os.environ.get("IMAGE_MIN_DPI", "180"))
IMAGE_MAX_DPI = int(os.environ.get("IMAGE_MAX_DPI", "360"))
# Encode threshold: re-encode as JPEG-90 if PNG exceeds this size.
# Diagrams stay PNG (lossless line art); photos drop to JPEG (4× smaller).
IMAGE_PNG_TO_JPEG_BYTE_THRESHOLD = int(
    os.environ.get("IMAGE_PNG_TO_JPEG_BYTE_THRESHOLD", "200000")  # 200 KiB
)
IMAGE_JPEG_QUALITY = int(os.environ.get("IMAGE_JPEG_QUALITY", "88"))
# Page-area fraction above which we treat a stored "image" block as
# misclassified full-page content (chapter cover pages, drop caps, etc.)
# and skip rendering. Empirically ~50% catches all the false positives
# in this corpus without dropping real figures.
IMAGE_MAX_PAGE_AREA_FRACTION = float(os.environ.get("IMAGE_MAX_PAGE_AREA_FRACTION", "0.5"))


def _adaptive_dpi(bbox: tuple[float, float, float, float]) -> int:
    """Pick a render DPI so output ≤ IMAGE_TARGET_MAX_PIXELS on the long side.

    bbox is in PDF points (1pt = 1/72 inch). The shorter the bbox, the
    higher the DPI we can afford. Clamped to [IMAGE_MIN_DPI, IMAGE_MAX_DPI]
    so small icons don't overshoot and full-page figures don't blow up
    file size.
    """
    width_pt = max(0.0, bbox[2] - bbox[0])
    height_pt = max(0.0, bbox[3] - bbox[1])
    longer_pt = max(width_pt, height_pt)
    if longer_pt <= 0:
        return IMAGE_MAX_DPI
    target_dpi = IMAGE_TARGET_MAX_PIXELS * 72.0 / longer_pt
    return int(max(IMAGE_MIN_DPI, min(IMAGE_MAX_DPI, target_dpi)))
OUTPUT_PATH = Path(
    os.environ.get(
        "OUTPUT_PATH",
        str(ROOT / ".test-tmp" / "ch2-export" / "chapter2-zh.html"),
    )
)


_PDF_DOC = None  # lazily-opened fitz.Document; reused across blocks


def _open_pdf():
    global _PDF_DOC
    if _PDF_DOC is None:
        try:
            import fitz  # PyMuPDF  # noqa: F401 — used below
        except ImportError as exc:
            raise SystemExit("PyMuPDF (fitz) is required for image rendering.") from exc
        if not PDF_PATH.is_file():
            logger.warning("source PDF not found at %s; images will be omitted", PDF_PATH)
            return None
        import fitz
        _PDF_DOC = fitz.open(str(PDF_PATH))
    return _PDF_DOC


def _resolve_embedded_image_bbox(page, fallback_bbox):
    """Return PyMuPDF's pixel-perfect bbox for the largest raster on this
    page, intersected with the parser's bbox. This eliminates the prose
    bleed seen on figures whose stored bbox ran a few points high/wide.
    """
    try:
        import fitz
    except ImportError:
        return fallback_bbox
    try:
        candidates = page.get_images(full=True)
    except Exception:
        return fallback_bbox
    fb_rect = fitz.Rect(*fallback_bbox)
    best = None
    best_area = 0.0
    for img_info in candidates:
        try:
            ib = page.get_image_bbox(img_info)
        except Exception:
            continue
        if not ib or ib.is_empty:
            continue
        # Only accept the image if it overlaps the parser's bbox enough that
        # we're confident it's the same logical figure.
        intersection = ib & fb_rect
        if intersection.is_empty:
            continue
        overlap_area = intersection.get_area()
        if overlap_area < 0.5 * fb_rect.get_area():
            continue
        if overlap_area > best_area:
            best = ib
            best_area = overlap_area
    return tuple(best) if best else fallback_bbox


def _expand_figure_bbox_to_drawings(
    page,
    fallback_bbox,
    *,
    page_rect,
    x_slack: float = 30.0,
    y_slack: float = 25.0,
):
    """Compute the figure's true bbox by anchoring on vector drawings.

    Strategy: drawings are the spine of a figure; figure-internal text
    (annotations, axis labels, side callouts) sits physically close to
    them. For each text block in a slack-expanded search window, measure
    its minimum gap to ANY drawing rect; include it iff both axes are
    within the proximity thresholds.

    Thresholds were calibrated against pages 58–59:
      - Fig 3.5 top annotation has 7.5pt vertical gap to drawings → keep
      - Fig 3.6 description "Different ways..." has 35.6pt gap above
        the small order-indicator boxes → keep
      - Fig 3.6 right-side speech bubbles have 42pt horizontal gap from
        the order-indicator boxes (drawings only cluster on the left) → keep
      - Fig 3.7 NOTE callout sits 30pt above the matrix drawings → drop
        (excluded by the search-window outer bound)
      - Official figure captions ("Figure N.M ...") at 16-32pt vertical
        gap → drop via caption-pattern guard (rendered separately as
        <figcaption>; including them as part of the image would duplicate)

    A 40pt vertical / 50pt horizontal gate keeps figure-internal text
    while the caption-pattern guard prevents the official caption from
    leaking in. Falls back to the parser bbox if no drawings are found.
    """
    try:
        import fitz
    except ImportError:
        return fallback_bbox
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    try:
        text_blocks = page.get_text("blocks")
    except Exception:
        text_blocks = []

    fb = fitz.Rect(*fallback_bbox)
    # Asymmetric slack defaults (text_only_figure): horizontal expansion
    # catches diagram extensions past the label-cluster (Fig 3.7's matrix
    # columns sit ~30pt right of the rightmost absorbed label), while a
    # tight vertical slack keeps us from dragging in body paragraphs
    # sandwiched between adjacent figures (page 61: Fig 3.8 + Fig 3.9
    # have drawings spanning both). Vector-drawing parser bboxes are
    # often missing whole sides of the figure (Fig 3.8 misses the left
    # query box; Fig 3.11 misses the top step labels and right-side
    # text), so callers can override with larger slack.
    search = fitz.Rect(
        max(page_rect.x0, fb.x0 - x_slack),
        max(page_rect.y0, fb.y0 - y_slack),
        min(page_rect.x1, fb.x1 + x_slack),
        min(page_rect.y1, fb.y1 + y_slack),
    )

    # Step 1: drawings anchor the figure. Keep only drawings that lie
    # entirely inside the search window so vectors from a neighbouring
    # figure on the same page can't pollute the anchor.
    drawing_rects: list = []
    for drawing in drawings:
        rect = drawing.get("rect") if isinstance(drawing, dict) else None
        if rect is None or rect.is_empty:
            continue
        if not (rect.x0 >= search.x0 and rect.y0 >= search.y0
                and rect.x1 <= search.x1 and rect.y1 <= search.y1):
            continue
        drawing_rects.append(rect)

    if not drawing_rects:
        return fallback_bbox

    anchor_x0 = min(r.x0 for r in drawing_rects)
    anchor_y0 = min(r.y0 for r in drawing_rects)
    anchor_x1 = max(r.x1 for r in drawing_rects)
    anchor_y1 = max(r.y1 for r in drawing_rects)

    # Step 2: per-text-block proximity gate.
    Y_PROX = 40.0   # pt — covers Fig 3.6's "Different ways..." description (35.6pt above)
    X_PROX = 50.0   # pt — covers Fig 3.6's right speech bubbles (42pt right of drawings)

    def _block_gap(tx0, ty0, tx1, ty1):
        best_x = best_y = float("inf")
        for d in drawing_rects:
            dx = max(0.0, max(d.x0 - tx1, tx0 - d.x1))
            dy = max(0.0, max(d.y0 - ty1, ty0 - d.y1))
            if dx < best_x:
                best_x = dx
            if dy < best_y:
                best_y = dy
        return best_x, best_y

    content_x0, content_y0 = anchor_x0, anchor_y0
    content_x1, content_y1 = anchor_x1, anchor_y1
    for tb in text_blocks:
        if len(tb) < 5:
            continue
        x0, y0, x1, y1, content = tb[0], tb[1], tb[2], tb[3], tb[4]
        text = (content or "").strip()
        if not text:
            continue
        # Hard outer bound — never reach into far-away body paragraphs
        # even if a stray drawing happens to be near. Use intersection
        # rather than strict containment so a block straddling the search
        # edge can still contribute its near-side coordinates.
        if x1 < search.x0 or x0 > search.x1 or y1 < search.y0 or y0 > search.y1:
            continue
        # Inside-figure check using drawings as a secondary anchor: a
        # text block whose vertical midpoint sits well OUTSIDE both the
        # parser y range AND the drawings y range belongs to body
        # content. The OR-of-mins/maxes keeps figure-internal text alive
        # when EITHER the parser bbox or the drawings extent vouches
        # for it. This handles two cases:
        #   - Fig 3.9 (parser bbox correct, drawings tight): body
        #     paragraph above must be REJECTED → both bounds reject it.
        #   - Fig 3.11 (parser bbox missing top, drawings extend up):
        #     top step "2. A probability ..." has mid_y=138 above
        #     parser.y0=191.7 but inside drawings.y0=154.7 ± 25 → KEPT.
        mid_y = (y0 + y1) * 0.5
        y_low = min(fb.y0, anchor_y0) - 25.0
        y_high = max(fb.y1, anchor_y1) + 25.0
        if mid_y < y_low or mid_y > y_high:
            continue
        # Drop the official "Figure N.M ..." caption — it's rendered
        # separately as <figcaption>, so including it in the image would
        # duplicate the caption.
        if _is_real_caption_text(text):
            continue
        # Drop body callouts (NOTE / TIP / WARNING ...). Page 59's
        # Fig 3.7 has a NOTE block sandwiched between Fig 3.6 and
        # Fig 3.7 that the parser absorbed; visually it's body content,
        # not figure-internal annotation.
        if _is_body_callout_text(text):
            continue
        # Drop running page headers/footers.
        if _looks_like_page_header(text, y0):
            continue
        gap_x, gap_y = _block_gap(x0, y0, x1, y1)
        if gap_x > X_PROX or gap_y > Y_PROX:
            continue
        content_x0 = min(content_x0, x0)
        content_y0 = min(content_y0, y0)
        content_x1 = max(content_x1, x1)
        content_y1 = max(content_y1, y1)

    pad = 4.0
    refined = fitz.Rect(
        max(page_rect.x0, content_x0 - pad),
        max(page_rect.y0, content_y0 - pad),
        min(page_rect.x1, content_x1 + pad),
        min(page_rect.y1, content_y1 + pad),
    )
    if refined.is_empty:
        return fallback_bbox
    return tuple(refined)


def _resolve_vector_drawing_bbox(page, fallback_bbox):
    """Tighten the parser's union-bbox to actual drawn shapes within it.

    The DocIR parser unions every stroke's bbox into one rectangle. That
    rectangle frequently overshoots vertically when the figure sits next
    to body prose — the union gobbles a few text lines above/below.

    Strategy: ask PyMuPDF for every drawing on the page, keep only those
    whose rect lies (mostly) inside the parser bbox, then re-union those.
    Text inside the resulting rect is preserved because pixmap renders
    everything in the clip — labels printed on top of shapes still appear.
    """
    try:
        import fitz
    except ImportError:
        return fallback_bbox
    try:
        drawings = page.get_drawings()
    except Exception:
        return fallback_bbox
    if not drawings:
        return fallback_bbox
    fb_rect = fitz.Rect(*fallback_bbox)
    union = fitz.Rect()  # empty rect; |= grows it
    for drawing in drawings:
        rect = drawing.get("rect") if isinstance(drawing, dict) else None
        if rect is None or rect.is_empty:
            continue
        if fb_rect.contains(rect):
            union |= rect
            continue
        intersection = rect & fb_rect
        if intersection.is_empty:
            continue
        # Mostly-inside drawings (e.g. a stroke that nicks the parser bbox
        # edge) get clipped to the parser bbox before joining the union.
        if intersection.get_area() >= 0.5 * rect.get_area():
            union |= intersection
    if union.is_empty:
        return fallback_bbox
    # Pad by a small margin so anti-aliased edges aren't shaved off.
    pad = 1.0
    union.x0 = max(union.x0 - pad, fb_rect.x0)
    union.y0 = max(union.y0 - pad, fb_rect.y0)
    union.x1 = min(union.x1 + pad, fb_rect.x1)
    union.y1 = min(union.y1 + pad, fb_rect.y1)
    return tuple(union)


def _render_image_data_uri(
    *,
    page_number: int,
    bbox: list[float] | tuple[float, ...],
    image_type: str | None,
) -> tuple[str | None, str | None]:
    """Render the requested page region as a base64 PNG (or JPEG) data URI.

    Returns (data_uri, skip_reason). page_number is 1-indexed (DocIR
    convention); we subtract 1 for PyMuPDF. bbox is (x0, y0, x1, y1)
    in PDF point coordinates.

    DPI is chosen adaptively so output pixel dimensions stay within
    IMAGE_TARGET_MAX_PIXELS on the longer side. Output is PNG by default;
    photo-like content (PNG > IMAGE_PNG_TO_JPEG_BYTE_THRESHOLD) is
    re-encoded as JPEG-90 for ~4x size reduction with no visible loss.
    """
    pdf = _open_pdf()
    if pdf is None:
        return None, "pdf-unavailable"
    try:
        import fitz
    except ImportError:
        return None, "no-pymupdf"
    pdf_page_index = max(0, int(page_number) - 1)
    if pdf_page_index >= pdf.page_count:
        return None, "page-out-of-range"
    try:
        page = pdf[pdf_page_index]
        page_area = max(1.0, page.rect.get_area())
        clip = fitz.Rect(*bbox)
        if clip.is_empty:
            return None, "empty-bbox"
        # Drop misclassified full-page "images" early so we don't render
        # 16MB PNGs of body prose.
        if clip.get_area() / page_area > IMAGE_MAX_PAGE_AREA_FRACTION:
            return None, f"oversized-bbox (>{IMAGE_MAX_PAGE_AREA_FRACTION:.0%} of page)"
        kind = (image_type or "").lower()
        if kind == "embedded_image":
            clip = fitz.Rect(*_resolve_embedded_image_bbox(page, tuple(bbox)))
        elif "vector" in kind:
            # The parser's vector-drawing bbox is built from the
            # drawing-stroke union, but the union sometimes misses
            # whole sides of the figure: Fig 3.8 omits the left query
            # box entirely (parser x0=171, drawings extend to x=103);
            # Fig 3.11 omits the top step labels and right-side
            # annotation text (parser y range is 191-353, but actual
            # extent is 128-340 with text reaching x=398). Larger slack
            # lets the drawings-anchored expander recover them while
            # the per-block-gap + midpoint guards keep body text out.
            clip = fitz.Rect(
                *_expand_figure_bbox_to_drawings(
                    page, tuple(bbox), page_rect=page.rect,
                    x_slack=80.0, y_slack=50.0,
                )
            )
        elif kind == "text_only_figure":
            # Synthesizer's bbox unions absorbed labels only; expand to
            # cover any drawings + short labels in the same y-band so
            # we don't crop out vector matrices/arrows on the right side
            # (Figure 3.7's three matrix columns motivate this).
            clip = fitz.Rect(
                *_expand_figure_bbox_to_drawings(page, tuple(bbox), page_rect=page.rect)
            )
        # Clamp to the page so the renderer doesn't error on near-edge bboxes.
        clip = clip & page.rect
        if clip.is_empty:
            return None, "clamped-empty"
        clip_tuple = (clip.x0, clip.y0, clip.x1, clip.y1)
        dpi = _adaptive_dpi(clip_tuple)
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
        png_bytes = pix.tobytes("png")
        if len(png_bytes) > IMAGE_PNG_TO_JPEG_BYTE_THRESHOLD:
            jpeg_bytes = pix.tobytes("jpg", jpg_quality=IMAGE_JPEG_QUALITY)
            if len(jpeg_bytes) < len(png_bytes):
                encoded = base64.b64encode(jpeg_bytes).decode("ascii")
                return f"data:image/jpeg;base64,{encoded}", None
    except Exception as exc:  # pragma: no cover - defensive against PDF quirks
        logger.warning(
            "failed to render image region page=%s bbox=%s err=%s",
            page_number,
            bbox,
            exc,
        )
        return None, f"render-error: {exc}"
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}", None


# Standard content-area envelope for this book (US-letter with ~1in margins
# and a paragraph-indent on the left). Synthesized text-only figures whose
# union-of-labels bbox sits well inside this envelope get expanded to it so
# the rendered image captures the full drawing — not just the labels.
_CONTENT_BOX_X_MIN = 56.0
_CONTENT_BOX_X_MAX = 540.0
# A synthesized figure narrower than this fraction of the content width is
# treated as label-only and widened. Figures legitimately narrower than half
# the content width (e.g. icons, sidebar marks) won't carry the
# ``text_only_figure`` flag, so this widening only fires on the bug case.
_SYNTHESIZED_FIGURE_WIDTH_FLOOR_RATIO = 0.7


def _block_image_data(session, block) -> tuple[str | None, str | None, str | None]:
    """Render this block's image as (data_uri, alt_text, skip_reason)."""
    image_row = session.execute(
        select(DocumentImage)
        .where(DocumentImage.block_id == block.id)
        .order_by(DocumentImage.created_at.asc())
        .limit(1)
    ).scalar_one_or_none()
    if image_row is None:
        return None, None, "no-document-image-row"
    bbox_payload = image_row.bbox_json or {}
    regions = bbox_payload.get("regions") if isinstance(bbox_payload, dict) else None
    if not isinstance(regions, list) or not regions:
        return None, image_row.alt_text, "no-bbox-regions"
    region = regions[0]
    if not isinstance(region, dict):
        return None, image_row.alt_text, "bad-region-shape"
    bbox = region.get("bbox")
    page_number = region.get("page_number") or image_row.page_number
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None, image_row.alt_text, "bad-bbox-shape"
    if not isinstance(page_number, int):
        return None, image_row.alt_text, "bad-page-number"
    bbox_list = list(bbox)
    image_type = image_row.image_type or ""
    if image_type == "text_only_figure":
        bbox_list = _widen_text_only_figure_bbox(bbox_list)
    data_uri, skip_reason = _render_image_data_uri(
        page_number=page_number,
        bbox=bbox_list,
        image_type=image_type,
    )
    return data_uri, image_row.alt_text, skip_reason


def _widen_text_only_figure_bbox(bbox: list[float]) -> list[float]:
    """Expand a label-union bbox to the page content envelope.

    The synthesizer builds a ``text_only_figure``'s bbox from the union of
    its absorbed text labels. When the underlying drawing is wider than the
    label cluster (the labels sit on the left side of the figure, or only
    one row of labels was extracted) the rendered crop misses the boxes,
    arrows, and right-side text — Figure 3.3 (Stock/Capital/Rare/Debt) and
    Figure 3.5 (Man/Woman/King/Queen vector traversal) both surfaced this
    way. If the union is narrower than ~70% of the page content width, snap
    it to the standard content envelope so the full drawing renders.
    """
    if len(bbox) != 4:
        return bbox
    x0, y0, x1, y1 = bbox
    content_width = _CONTENT_BOX_X_MAX - _CONTENT_BOX_X_MIN
    if content_width <= 0:
        return bbox
    if (x1 - x0) >= _SYNTHESIZED_FIGURE_WIDTH_FLOOR_RATIO * content_width:
        return bbox
    return [
        min(x0, _CONTENT_BOX_X_MIN),
        y0,
        max(x1, _CONTENT_BOX_X_MAX),
        y1,
    ]


def _bbox_is_inside_any(
    inner: list[float] | tuple[float, float, float, float],
    outers: list[tuple[float, float, float, float]],
    pad: float = 2.0,
) -> bool:
    """True if ``inner`` sits (within ``pad`` slack) inside any ``outer``."""
    if not isinstance(inner, (list, tuple)) or len(inner) != 4:
        return False
    ix0, iy0, ix1, iy1 = inner
    for ox0, oy0, ox1, oy1 in outers:
        if (
            ix0 >= ox0 - pad
            and iy0 >= oy0 - pad
            and ix1 <= ox1 + pad
            and iy1 <= oy1 + pad
        ):
            return True
    return False


def _block_lies_inside_figure(
    block,
    figure_bboxes_by_page: dict[int, list[tuple[float, float, float, float]]],
) -> bool:
    """Drop body blocks whose bbox sits inside a nearby figure's bbox.

    The parser sometimes leaves figure-internal text (Figure 3.2's "Document
    / Input sequences / Convert to tokens / ..." labels, page-headers, in-
    figure code captions) outside the figure cluster. Those blocks then get
    translated and rendered as standalone paragraphs immediately below the
    figure, which the user sees as garbage prose. We strain them at render
    time by checking whether each block's source bbox is fully contained in
    a figure bbox on the same page.
    """
    meta = block.source_span_json or {}
    bbox_payload = meta.get("source_bbox_json") or {}
    regions = bbox_payload.get("regions") if isinstance(bbox_payload, dict) else None
    if not isinstance(regions, list) or not regions:
        return False
    for region in regions:
        if not isinstance(region, dict):
            continue
        bbox = region.get("bbox")
        page = region.get("page_number")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        if not isinstance(page, int):
            continue
        outers = figure_bboxes_by_page.get(page) or []
        if not outers:
            continue
        if _bbox_is_inside_any(bbox, outers):
            return True
    return False


_ORPHAN_LEAD_PATTERN = re.compile(
    r"^\s*(?:"
    r"many\s|several\s|most\s|some\s|a\s+key\s|the\s+key\s|one\s+key\s|various\s"
    r"|although|however|while|whereas|because|since|when|if|but"
    r"|许多|一些|大多数|然而|虽然"
    r")",
    re.IGNORECASE,
)


def _looks_like_orphan_sentence_lead(text: str) -> bool:
    """True if this short HEADING-marked text is really a body fragment.

    Real subsection titles look like ``Embedding layers``, ``Sampling
    tokens``, etc. — usually a noun phrase, never an opening connector.
    Sliced body sentences like ``Many LLMs`` or ``However`` instead lead
    with a quantifier or contrast word and lack terminal punctuation.
    """
    s = (text or "").strip()
    if not s or len(s) > 30:
        return False
    if _HEADING_NUMBERED_LEAD.match(s):
        return False
    if _HEADING_SENTENCE_TAIL.search(s):
        return False
    return bool(_ORPHAN_LEAD_PATTERN.match(s))


def _looks_like_figure_internal_text(text: str) -> bool:
    """Distinguish figure-internal label fragments from coherent body prose.

    Figure-internal text is short, fragmented (many newlines, short lines)
    and rarely a complete sentence. Body prose has full sentences and a
    higher chars-per-line ratio. Used together with the figure↔caption
    ordinal sandwich + bbox containment to avoid suppressing real body
    paragraphs that just happen to sit on the same page as a tall figure.
    """
    s = (text or "").strip()
    if not s:
        return False
    # Real code goes through ``_looks_like_real_code``; if the source is
    # multi-line indented code or has obvious code syntax, it's almost
    # certainly figure-internal pseudo-code (Figure 3.11 case).
    if _looks_like_real_code(s):
        return True
    lines = [ln for ln in s.splitlines() if ln.strip()]
    if not lines:
        return False
    # Short, multi-line, fragment-y texts are figure-internal labels.
    avg_line_len = len(s) / max(len(lines), 1)
    if len(lines) >= 3 and avg_line_len < 40:
        return True
    # Coherent body prose almost always ends in sentence-final punctuation.
    last = s[-1]
    if last not in ".!?。！？”\"'":
        # No terminal punctuation + multi-line ⇒ probably a label group.
        if len(lines) >= 2:
            return True
    return False


def _block_zh_chunks(session, block) -> tuple[list[str], list[str]]:
    """Return (translated_chunks_in_ts_order, untranslated_source_texts)."""
    sentence_rows = session.execute(
        select(Sentence.id, Sentence.ordinal_in_block, Sentence.source_text)
        .where(Sentence.block_id == block.id)
        .where(Sentence.translatable.is_(True))
        .order_by(Sentence.ordinal_in_block.asc())
    ).all()
    if not sentence_rows:
        return [], []
    seen_target_ids: set[str] = set()
    rendered: list[tuple[int, str]] = []
    untranslated: list[str] = []
    for sentence_id, _, source_text in sentence_rows:
        target = session.execute(
            select(TargetSegment.id, TargetSegment.text_zh, TargetSegment.ordinal)
            .join(AlignmentEdge, AlignmentEdge.target_segment_id == TargetSegment.id)
            .where(AlignmentEdge.sentence_id == sentence_id)
            .where(TargetSegment.final_status != TargetSegmentStatus.SUPERSEDED)
            .order_by(TargetSegment.ordinal.asc())
            .limit(1)
        ).first()
        if target is None or not target[1]:
            untranslated.append(source_text)
            continue
        ts_id, text_zh, ts_ordinal = target
        if ts_id in seen_target_ids:
            continue
        seen_target_ids.add(ts_id)
        rendered.append((int(ts_ordinal or 0), text_zh))
    rendered.sort(key=lambda item: item[0])
    return [chunk for _, chunk in rendered], untranslated


def _heading_html(text: str) -> str:
    safe = html.escape(text.strip())
    # Demote everything to <h2> so the page <h1> remains the chapter title.
    return f"<h2>{safe}</h2>"


def _paragraph_html(chunks: list[str]) -> str:
    parts = []
    for chunk in chunks:
        parts.append(f"<p>{html.escape(chunk).replace(chr(10), '<br>')}</p>")
    return "\n".join(parts)


def _caption_html(chunks: list[str]) -> str:
    text = "  ".join(chunk.strip() for chunk in chunks)
    return f"<p class='caption'><em>{html.escape(text)}</em></p>"


def _code_html(chunks: list[str]) -> str:
    body = "\n".join(chunks)
    return f"<pre><code>{html.escape(body)}</code></pre>"


def _untranslated_html(sources: list[str]) -> str:
    if not sources:
        return ""
    items = "\n".join(
        f"<li>{html.escape(s)}</li>" for s in sources if s
    )
    return (
        "<details class='untranslated'>"
        "<summary>未翻译片段</summary>"
        f"<ul>{items}</ul>"
        "</details>"
    )


def _image_html(data_uri: str | None, alt_text: str | None) -> str:
    alt_clean = (alt_text or "").strip()
    alt_attr = html.escape(alt_clean) if alt_clean else ""
    if not data_uri:
        # Fallback when we couldn't render: keep a labelled placeholder so
        # the reader can see something is missing rather than silent gaps.
        body = f"<span>[图缺失] {alt_attr}</span>" if alt_attr else "<span>[图缺失]</span>"
        return f"<figure class='image-placeholder'>{body}</figure>"
    figure_inner = f"<img src=\"{data_uri}\" alt=\"{alt_attr}\" loading=\"lazy\">"
    if alt_attr:
        figure_inner += f"\n<figcaption>{alt_attr}</figcaption>"
    return f"<figure class='figure'>{figure_inner}</figure>"


_BODY_CALLOUT_LEAD = re.compile(
    # Body callouts ("NOTE / Using multiple dimensions...", "TIP / ...",
    # "WARNING / ...") sit in the body flow even when they happen to be
    # geographically near a figure's drawings. The parser sometimes
    # absorbs them into the figure bbox; this guard drops them before
    # they widen the rendered crop.
    r"^\s*(?:NOTE|TIP|WARNING|CAUTION|INFO|IMPORTANT|REMEMBER)\s*\n",
)


def _is_body_callout_text(text: str) -> bool:
    return bool(_BODY_CALLOUT_LEAD.match(text or ""))


_PAGE_HEADER_PATTERN = re.compile(
    # Running headers/footers at the top of every page:
    #   "34 / CHAPTER 3 / Transformers: How inputs become outputs"
    #   "3.2 Exploring the transformer architecture in detail / 39"
    # These are typeset as plain text blocks at y0 ~ 26pt, well above
    # any figure content. The parser bbox for vector_drawing figures
    # sometimes extends to y0 ~ 13pt (the page's drawn-stroke union
    # reaches close to the page edge), and the slack-expanded search
    # then sweeps these headers in. Catch by content + position.
    r"^\s*(?:\d+\s*[/\n]\s*CHAPTER\b|CHAPTER\s+\d+\b|\d+(?:\.\d+){1,2}\s+\S.*?[/\n]\s*\d+\s*$)",
    re.IGNORECASE | re.DOTALL,
)


def _looks_like_page_header(text: str, y0: float) -> bool:
    if y0 >= 50.0:
        return False
    return bool(_PAGE_HEADER_PATTERN.match((text or "")))


_REAL_CAPTION_PATTERN = re.compile(
    # Mirrors the strict parser pattern in `pdf.py::_FIGURE_CAPTION_PATTERN`:
    # caption-shaped only (uppercase first letter or punctuation separator),
    # not a body sentence like "Figure 3.1 describes ...". Matches Chinese
    # captions too, so re-translated rows still pass.
    r"^\s*(?:"
    r"(?:figure|fig\.?|table|tab\.?|image|diagram|chart)\s*"
    r"(?:[A-Z]\d+(?:[.\-–—]\d+)*[A-Za-z]?|\d+(?:[.\-–—]\d+)*[A-Za-z]?|[A-Z])"
    r"(?:[.:\-–—]\s+\S+|\s+(?-i:[A-Z])[^\n]{2,}|\s*$)"
    r"|"
    r"(?:图|表|示意图|插图)\s*[\d一-鿿]"
    r")",
    re.IGNORECASE,
)


def _is_real_caption_text(text: str) -> bool:
    """Reject body-text references masquerading as captions (DB-side legacy)."""
    return bool(_REAL_CAPTION_PATTERN.match((text or "").strip()))


_FIGURE_NUMBER_FROM_SOURCE = re.compile(
    r"^\s*(?:figure|fig\.?)\s*([A-Z]?\d+(?:[.\-–—]\d+)*[A-Za-z]?)",
    re.IGNORECASE,
)


def _ensure_figure_prefix(translated: str, source: str) -> str:
    """Prepend "图 N.M" to a translated caption when the source had it.

    Some translation models treat the "Figure N.M" prefix as metadata and
    silently drop it. The number is meaningful for the reader, so we re-add
    it from the source whenever the translation is missing one.
    """
    text = (translated or "").strip()
    if not text:
        return text
    if re.match(r"^\s*(?:图|表|示意图|插图|Figure|Fig\.?)\s*\d", text, re.IGNORECASE):
        return text
    m = _FIGURE_NUMBER_FROM_SOURCE.match(source or "")
    if not m:
        return text
    return f"图{m.group(1)} {text}"


def _linked_caption_anchor(block) -> str | None:
    """Return the source_anchor of the CAPTION block linked to this figure/image.

    Two sources cover both pre- and post-clustering layouts:
    - ``linked_caption_source_anchor`` is set by ``_link_artifact_captions``
      for IMAGE blocks emitted by the parser before clustering.
    - ``figure_cluster.caption_block_anchor`` is set by the figure-clustering
      pass for FIGURE blocks created from grouped image fragments.
    Note: ``Block`` exposes per-block metadata as ``source_span_json``.
    """
    meta = getattr(block, "source_span_json", None) or {}
    anchor = meta.get("linked_caption_source_anchor")
    if isinstance(anchor, str) and anchor:
        return anchor
    cluster = meta.get("figure_cluster")
    if isinstance(cluster, dict):
        anchor = cluster.get("caption_block_anchor")
        if isinstance(anchor, str) and anchor:
            return anchor
    return None


def _render_block(
    block,
    chunks: list[str],
    untranslated: list[str],
    image_data_uri: str | None,
    image_alt: str | None,
) -> str:
    btype = (block.block_type or "paragraph").lower()
    if btype in {"image", "figure"}:
        return _image_html(image_data_uri, image_alt)
    if not chunks and not untranslated:
        return ""
    if btype == "heading" and chunks:
        # Older parser revisions promoted short/body-fragment text to HEADING
        # (e.g. "Many LLMs", "Note: ...", figure-internal labels). Keep the
        # render side robust to those rows by demoting any HEADING whose
        # source isn't shaped like a section title — leading section number
        # OR a Chapter/Part/Appendix lead OR ≥60 chars of meaningful prose.
        # Check both the English source and the translated chunk so a
        # parser that didn't include the source-side connector still demotes
        # at render time.
        if not _looks_like_heading_source(block.source_text or "") or not _looks_like_heading_source(chunks[0]):
            return _paragraph_html(chunks)
        return _heading_html(chunks[0])
    if btype in {"code", "code_block"}:
        # The parser flags any block that mixes monospace tokens with body
        # text as CODE — but most of those are body paragraphs that just
        # happen to mention identifiers like ``stock``/``capital``/``dict``
        # inline. Demote to a regular paragraph unless the source actually
        # looks like code (multi-line with leading whitespace or
        # programming syntax markers).
        if not _looks_like_real_code(block.source_text or ""):
            return _paragraph_html(chunks)
        return _code_html(chunks)
    if btype in {"caption", "figure_caption"}:
        return _caption_html(chunks)
    return _paragraph_html(chunks)


# Require at least one dot in pure-numeric leads — "3.1 X" is a section
# title, "2 X" is a list item that the older parser sometimes promoted.
_REAL_CODE_LINE_PATTERN = re.compile(
    r"^\s{2,}\S"  # at least 2 leading spaces — typical code indentation
    r"|^\s*(?:def|class|return|import|from|if|elif|else|for|while|try|except|finally|with|yield|raise|print|let|var|const|function)\b"
    r"|^\s*[A-Za-z_]\w*\s*[=(]"  # assignment / call at line start
    r"|^\s*#",  # comment line
    re.MULTILINE,
)


def _looks_like_real_code(text: str) -> bool:
    """Distinguish actual code blocks from prose with inline-mono tokens."""
    s = (text or "").strip()
    if not s:
        return False
    lines = [ln for ln in s.splitlines() if ln.strip()]
    # Real code is almost always multi-line and structurally code-like.
    if len(lines) < 2:
        return False
    indented_lines = sum(1 for ln in lines if ln.startswith(("  ", "\t")))
    if indented_lines >= 2:
        return True
    # Single-line indent + multi-line code-keyword starts also count.
    if _REAL_CODE_LINE_PATTERN.search(s):
        # Require either indentation OR multi-line so a body paragraph that
        # incidentally starts with "If you ..." doesn't slip back in.
        if indented_lines >= 1 or sum(1 for ln in lines if _REAL_CODE_LINE_PATTERN.match(ln)) >= 2:
            return True
    return False


_HEADING_NUMBERED_LEAD = re.compile(r"^\s*(?:[A-Z]\s*\.|[Cc]hapter|[Pp]art|[Aa]ppendix|\d+\.\d+(?:\.\d+){0,2}\s)")
# Body-sentence-shaped text the older parser sometimes promoted to HEADING.
# Reject when the source ends in sentence-final punctuation OR opens with a
# connector that real titles never use (these are English; the source is
# always English for this book — translation stays a heading downstream).
_HEADING_DEMOTE_LEAD = re.compile(
    r"^\s*(?:"
    r"although|however|while|whereas|because|since"
    r"|note\s*[:.]|notes?\s*[:.]"
    r"|many\s|several\s|most\s|some\s"
    r"|a\s+key\s|the\s+key\s|one\s+key\s|various\s"
    # Chinese counterparts — cover the case where the parser already
    # stored Chinese text (rare) or the demote regex needs to look at
    # translated chunks downstream.
    r"|虽然|然而|许多|一些|有些|大多数"
    r"|注意\s*[：:.]|注\s*[：:.]"
    r"|一个关键|关键问题"
    r")",
    re.IGNORECASE,
)
# Bare list-item digits like "2 Random selection" or "3. Add information"
# slip through the section-numbered guard above (real section titles always
# carry at least one dot, e.g. "3.1"). Reject anything that looks like a
# list item — single digit, optional dot, then prose.
_HEADING_LIST_ITEM_LEAD = re.compile(r"^\s*\d{1,2}\.?\s+[A-Za-z]")
_HEADING_SENTENCE_TAIL = re.compile(r"[.。!?！？]\s*$")


def _looks_like_heading_source(text: str) -> bool:
    """Filter older-parser HEADINGs that were really body fragments.

    A heading-shaped string here is anything that *isn't* clearly a
    sentence: numbered section leads, capitalised short phrases, real
    single-line titles. We only demote when the source text matches the
    body-fragment shapes we know slip through (sentence-ending punctuation,
    connector lead-ins like "Although ...", "Many ...", or "Note: ...").
    """
    s = (text or "").strip()
    if not s:
        return False
    if _HEADING_NUMBERED_LEAD.match(s):
        return True
    if _HEADING_DEMOTE_LEAD.match(s):
        return False
    if _HEADING_LIST_ITEM_LEAD.match(s):
        return False
    if _HEADING_SENTENCE_TAIL.search(s):
        return False
    return True


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    max-width: 760px;
    margin: 2rem auto;
    padding: 0 1.25rem 4rem;
    font-family: -apple-system, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    font-size: 16px;
    line-height: 1.85;
    color: #222;
    background: #fafafa;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ color: #ddd; background: #181818; }}
    h1, h2 {{ color: #f0f0f0; }}
    blockquote {{ background: #222; color: #aaa; }}
    code, pre {{ background: #1f1f1f; }}
    .caption {{ color: #999; }}
  }}
  h1 {{ font-size: 2rem; line-height: 1.3; margin: 0 0 0.5rem; }}
  h1 small {{ display: block; font-size: 1rem; color: #666; font-weight: 400; margin-top: 0.25rem; }}
  h2 {{ font-size: 1.4rem; margin: 2.5rem 0 0.75rem; padding-bottom: 0.25rem; border-bottom: 1px solid rgba(127,127,127,.25); }}
  blockquote {{
    margin: 1rem 0; padding: .65rem 1rem;
    border-left: 4px solid #888; background: #efefef;
    color: #555; font-size: .92rem; border-radius: 0 4px 4px 0;
  }}
  p {{ margin: .85rem 0; }}
  pre {{ background: #f3f3f3; padding: .85rem 1rem; border-radius: 6px; overflow-x: auto; font-size: .92rem; }}
  code {{ background: #f3f3f3; padding: 0 .25rem; border-radius: 3px; }}
  pre code {{ background: transparent; padding: 0; }}
  .caption {{ font-size: .9rem; color: #555; }}
  .image-placeholder {{
    margin: 1.5rem 0; padding: 1rem;
    border: 1px dashed rgba(127,127,127,.4);
    text-align: center; color: #888; font-size: .9rem;
  }}
  figure.figure {{
    margin: 2rem 0; padding: 0;
    text-align: center;
  }}
  figure.figure img {{
    max-width: 100%;
    height: auto;
    border-radius: 6px;
    box-shadow: 0 1px 4px rgba(0,0,0,.08);
    background: #fff;
  }}
  figure.figure figcaption {{
    margin-top: .5rem;
    font-size: .88rem;
    color: #555;
    line-height: 1.5;
    white-space: pre-line;
  }}
  @media (prefers-color-scheme: dark) {{
    figure.figure img {{ background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,.4); }}
    figure.figure figcaption {{ color: #aaa; }}
  }}
  details.untranslated {{
    margin: .5rem 0; padding: .5rem .75rem;
    background: rgba(255, 240, 200, .6);
    border-left: 3px solid #d8a93a; border-radius: 0 4px 4px 0;
    font-size: .85rem; color: #6b5a1d;
  }}
  details.untranslated ul {{ margin: .5rem 0 .25rem; padding-left: 1.25rem; }}
  details.untranslated li {{ margin: .15rem 0; }}
  footer {{
    margin-top: 3rem; padding-top: 1rem;
    border-top: 1px solid rgba(127,127,127,.2);
    font-size: .85rem; color: #777; text-align: center;
  }}
</style>
</head>
<body>
<h1>{chapter_label} {chapter_title}<small>{source_label}</small></h1>
<blockquote>
译文范围: 章节内段落 {ord_lo}–{ord_hi}（共 {block_count} 个段落，渲染 {rendered_count} 个，{skipped_count} 个为图像/页眉/页码等不可译块）。
</blockquote>
{body}
<footer>由 book-agent 翻译管线生成 · 模型 {models_used}</footer>
</body>
</html>
"""


def _models_used_for_range(session) -> str:
    rows = session.execute(
        select(TranslationRun.model_name)
        .join(TargetSegment, TargetSegment.translation_run_id == TranslationRun.id)
        .join(AlignmentEdge, AlignmentEdge.target_segment_id == TargetSegment.id)
        .join(Sentence, Sentence.id == AlignmentEdge.sentence_id)
        .join(Block, Block.id == Sentence.block_id)
        .where(Block.chapter_id == CHAPTER_ID)
        .where(Block.ordinal >= ORDINAL_LO)
        .where(Block.ordinal <= ORDINAL_HI)
        .where(TargetSegment.final_status != TargetSegmentStatus.SUPERSEDED)
        .distinct()
    ).all()
    names = sorted({str(row[0]) for row in rows if row[0]})
    return ", ".join(names) if names else "deepseek-v4-flash"


def main() -> int:
    settings = get_settings()
    engine = build_engine(database_url=settings.database_url)
    factory = build_session_factory(engine=engine)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    rendered_blocks_html: list[str] = []
    untranslated_block_count = 0
    rendered_block_count = 0
    total_blocks = 0
    image_skip_reasons: dict[str, int] = {}
    image_render_count = 0
    models_used = ""
    with session_scope(factory) as session:
        document = session.get(Document, DOCUMENT_ID)
        chapter = session.get(Chapter, CHAPTER_ID)
        if document is None or chapter is None:
            raise SystemExit("Document or chapter not found")

        models_used = _models_used_for_range(session)

        blocks = (
            session.execute(
                select(Block)
                .where(Block.chapter_id == CHAPTER_ID)
                .where(Block.ordinal >= ORDINAL_LO)
                .where(Block.ordinal <= ORDINAL_HI)
                .order_by(Block.ordinal.asc())
            )
            .scalars()
            .all()
        )
        total_blocks = len(blocks)

        # Pre-pass: index CAPTION blocks by source_anchor and identify which
        # ones are linked to a FIGURE/IMAGE in this rendering window. Linked
        # captions render *inside* their figure as <figcaption>, so we skip
        # them when iterating in reading order — otherwise we get the same
        # caption twice (once English from alt_text, once Chinese as a
        # standalone paragraph) which is how the Figure-3.2/3.3 mismatch
        # surfaced visually.
        caption_by_anchor: dict[str, Block] = {}
        for blk in blocks:
            if (blk.block_type or "").lower() in {"caption", "figure_caption"} and blk.source_anchor:
                caption_by_anchor[blk.source_anchor] = blk
        consumed_caption_ids: set[str] = set()
        # Reject linkages where the linked caption's source text is actually
        # body prose (e.g. "Figure 3.1 describes the essential components...")
        # rather than a real caption (e.g. "Figure 3.1 The basic components...").
        # The earlier-generation parser used a permissive regex that let body
        # references through; we strain them out at render time so older DB
        # rows still produce a clean export.
        rejected_linkage_ids: set[str] = set()
        for blk in blocks:
            if (blk.block_type or "").lower() not in {"image", "figure"}:
                continue
            linked_anchor = _linked_caption_anchor(blk)
            if not linked_anchor or linked_anchor not in caption_by_anchor:
                continue
            cap_block = caption_by_anchor[linked_anchor]
            if not _is_real_caption_text(cap_block.source_text or ""):
                rejected_linkage_ids.add(blk.id)
                continue
            consumed_caption_ids.add(cap_block.id)

        # Identify the narrow "figure → leaked label → caption" sandwich
        # pattern: a non-figure block whose ordinal sits between a figure and
        # its linked caption is almost always figure-internal text the
        # parser failed to absorb (Figure 3.2's "Document / Convert to
        # tokens / Word embedding / ..." labels surfaced this way). We
        # combine the ordinal sandwich with bbox containment so wider body
        # paragraphs adjacent to a tall figure aren't accidentally dropped.
        figure_bboxes_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
        # Reuse the same expander that drives the render path so bbox
        # containment checks see the figure's actual extent — not just
        # the parser's drawing-stroke union, which routinely misses
        # whole sides (Fig 3.8 omits the left query box; Fig 3.11 omits
        # the top step labels). Without this, figure-internal annotation
        # text leaks into the body flow as orphan paragraphs.
        pdf_doc_for_bbox = None
        try:
            pdf_doc_for_bbox = _open_pdf()
        except Exception:
            pdf_doc_for_bbox = None
        for blk in blocks:
            if (blk.block_type or "").lower() not in {"image", "figure"}:
                continue
            blk_meta = blk.source_span_json or {}
            payload = blk_meta.get("source_bbox_json") or {}
            blk_regions = payload.get("regions") if isinstance(payload, dict) else None
            if not isinstance(blk_regions, list):
                continue
            for region in blk_regions:
                if not isinstance(region, dict):
                    continue
                bbox = region.get("bbox")
                page = region.get("page_number")
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    continue
                if not isinstance(page, int):
                    continue
                use_bbox = list(bbox)
                image_type = (blk_meta.get("image_type") or "").lower()
                if image_type == "text_only_figure":
                    use_bbox = _widen_text_only_figure_bbox(use_bbox)
                if pdf_doc_for_bbox is not None and (
                    image_type == "text_only_figure" or "vector" in image_type
                ):
                    try:
                        pg = pdf_doc_for_bbox[page - 1]
                        if image_type == "text_only_figure":
                            expanded = _expand_figure_bbox_to_drawings(
                                pg, tuple(use_bbox), page_rect=pg.rect
                            )
                        else:
                            expanded = _expand_figure_bbox_to_drawings(
                                pg, tuple(use_bbox), page_rect=pg.rect,
                                x_slack=80.0, y_slack=50.0,
                            )
                        use_bbox = list(expanded)
                    except Exception:
                        pass
                figure_bboxes_by_page.setdefault(page, []).append(tuple(use_bbox))

        # Build the set of ordinals that sit strictly between a figure and
        # its linked caption (inclusive of neither). These are candidates
        # for figure-internal-text suppression.
        block_by_anchor = {b.source_anchor: b for b in blocks if b.source_anchor}
        sandwich_ordinals: set[int] = set()
        for blk in blocks:
            if (blk.block_type or "").lower() not in {"image", "figure"}:
                continue
            cap_anchor = _linked_caption_anchor(blk)
            if not cap_anchor:
                continue
            cap_block = block_by_anchor.get(cap_anchor) or caption_by_anchor.get(cap_anchor)
            if cap_block is None:
                continue
            lo, hi = sorted([blk.ordinal, cap_block.ordinal])
            for ord_between in range(lo + 1, hi):
                sandwich_ordinals.add(ord_between)
        figure_internal_suppressed = 0

        # Pre-pass: detect heading fragments that the parser sliced off the
        # start of a body sentence (e.g. ord=204 "Many LLMs" + ord=205 "you
        # encounter today interpret tokens..."). The parser sees the all-
        # caps abbreviation "LLMs" as a chunk break and emits a HEADING.
        # Merge the fragment back into the next block so the body sentence
        # renders as one paragraph.
        merge_into_next: dict[str, str] = {}
        skip_render_block_ids: set[str] = set()
        block_index_by_ord = {b.ordinal: i for i, b in enumerate(blocks)}
        for i, blk in enumerate(blocks):
            btype = (blk.block_type or "").lower()
            src = (blk.source_text or "").strip()
            if not src:
                continue
            if btype != "heading":
                continue
            # Only consider short fragments that don't look like real titles.
            if not _looks_like_orphan_sentence_lead(src):
                continue
            if i + 1 >= len(blocks):
                continue
            next_blk = blocks[i + 1]
            next_src = (next_blk.source_text or "").strip()
            if not next_src or (next_blk.block_type or "").lower() not in {
                "paragraph",
                "list_item",
            }:
                continue
            # Skip when there's a chapter/page break between them — same
            # source page or contiguous in reading order is required.
            if (next_blk.ordinal - blk.ordinal) > 1:
                continue
            # Continuation marker: next block starts with a lowercase letter
            # (English) — real subsection headings are followed by capitalised
            # body text or another heading.
            first_char = next_src[:1]
            if not first_char.islower():
                continue
            merge_into_next[next_blk.id] = blk.id
            skip_render_block_ids.add(blk.id)

        for block in blocks:
            btype = (block.block_type or "").lower()
            if btype in {"caption", "figure_caption"} and block.id in consumed_caption_ids:
                continue
            # Suppress figure-internal text that the parser left outside the
            # figure cluster: fire when the block sits in a figure's ordinal
            # sandwich AND its bbox is contained in the (expanded) figure
            # bbox. Text-shape was a previous secondary check, but now that
            # ``figure_bboxes_by_page`` uses the drawings-anchored expanded
            # bbox, geographic containment alone is reliable — figure-
            # internal annotations like Fig 3.8's "We have a list of queries
            # ..." are full sentences that the old text-shape filter missed.
            if (
                btype not in {"image", "figure", "caption", "figure_caption"}
                and block.ordinal in sandwich_ordinals
                and _block_lies_inside_figure(block, figure_bboxes_by_page)
            ):
                figure_internal_suppressed += 1
                continue
            # Skip heading-fragments that we've decided to merge into their
            # next sibling — their text shows up at the start of that block.
            if block.id in skip_render_block_ids:
                continue
            chunks, untranslated = _block_zh_chunks(session, block)
            # When the prior heading-fragment ("Many LLMs") got skipped, the
            # translator usually already produced a complete sentence for the
            # follow-on block ("...you encounter today interpret tokens..."
            # → "你如今遇到的LLM使用一种名为Transformer..."). We don't need
            # to glue the orphan translation back in — its meaning is already
            # in the follow-on chunks. Tracked via merge_into_next for
            # diagnostics and future logic, but no chunk surgery here.
            image_data_uri = None
            image_alt: str | None = None
            if btype in {"image", "figure"}:
                image_data_uri, alt_fallback, skip_reason = _block_image_data(
                    session, block
                )
                if image_data_uri:
                    image_render_count += 1
                elif skip_reason:
                    image_skip_reasons[skip_reason] = (
                        image_skip_reasons.get(skip_reason, 0) + 1
                    )
                # Prefer the linked CAPTION block's *translated* text for the
                # figcaption — single source of truth, always Chinese, can't
                # disagree with the surrounding paragraph render.
                # Skip rejected linkages (body-text mistakenly identified as
                # caption by the older parser); render orphan instead.
                if block.id not in rejected_linkage_ids:
                    linked_anchor = _linked_caption_anchor(block)
                    if linked_anchor and linked_anchor in caption_by_anchor:
                        cap_block = caption_by_anchor[linked_anchor]
                        cap_chunks, _ = _block_zh_chunks(session, cap_block)
                        if cap_chunks:
                            image_alt = "  ".join(c.strip() for c in cap_chunks if c.strip())
                            # Translator sometimes drops the "Figure N.M"
                            # prefix when translating captions. Restore it
                            # so figure numbering is preserved in the export.
                            image_alt = _ensure_figure_prefix(
                                image_alt, cap_block.source_text or ""
                            )
                # alt_fallback comes from DocumentImage.alt_text, which the
                # older parser populated with whatever caption text it linked
                # — including body-text references. Strain those out by the
                # same caption-shape rule we use for in-window CAPTION blocks.
                if not image_alt and alt_fallback and _is_real_caption_text(alt_fallback):
                    image_alt = alt_fallback
            block_html = _render_block(
                block, chunks, untranslated, image_data_uri, image_alt
            )
            untrans_html = _untranslated_html(untranslated)
            combined = "\n".join(part for part in (block_html, untrans_html) if part)
            if not combined:
                untranslated_block_count += 1
                continue
            rendered_blocks_html.append(combined)
            rendered_block_count += 1

    output = HTML_TEMPLATE.format(
        title=html.escape(f"{CHAPTER_LABEL} {CHAPTER_TITLE} — {SOURCE_LABEL}"),
        chapter_label=html.escape(CHAPTER_LABEL),
        chapter_title=html.escape(CHAPTER_TITLE),
        source_label=html.escape(SOURCE_LABEL),
        ord_lo=ORDINAL_LO,
        ord_hi=ORDINAL_HI,
        block_count=total_blocks,
        rendered_count=rendered_block_count,
        skipped_count=untranslated_block_count,
        models_used=html.escape(models_used or "deepseek"),
        body="\n".join(rendered_blocks_html),
    )
    OUTPUT_PATH.write_text(output, encoding="utf-8")
    summary_lines = [
        f"[export] wrote {OUTPUT_PATH}",
        f"[export] rendered_blocks={rendered_block_count} "
        f"skipped_blocks={untranslated_block_count} "
        f"total_blocks={total_blocks} "
        f"figure_internal_suppressed={figure_internal_suppressed}",
        f"[export] images_rendered={image_render_count} "
        f"images_skipped={sum(image_skip_reasons.values())} "
        f"dpi=adaptive[{IMAGE_MIN_DPI}-{IMAGE_MAX_DPI}] "
        f"max_pixels={IMAGE_TARGET_MAX_PIXELS}",
    ]
    if image_skip_reasons:
        for reason, count in sorted(
            image_skip_reasons.items(), key=lambda kv: -kv[1]
        ):
            summary_lines.append(f"  - skip[{reason}]={count}")
    print("\n".join(summary_lines), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
