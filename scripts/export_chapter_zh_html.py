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
            clip = fitz.Rect(*_resolve_vector_drawing_bbox(page, tuple(bbox)))
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
    data_uri, skip_reason = _render_image_data_uri(
        page_number=page_number,
        bbox=list(bbox),
        image_type=image_row.image_type,
    )
    return data_uri, image_row.alt_text, skip_reason


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


def _linked_caption_anchor(block) -> str | None:
    """Return the source_anchor of the CAPTION block linked to this figure/image.

    Two sources cover both pre- and post-clustering layouts:
    - ``linked_caption_source_anchor`` is set by ``_link_artifact_captions``
      for IMAGE blocks emitted by the parser before clustering.
    - ``figure_cluster.caption_block_anchor`` is set by the figure-clustering
      pass for FIGURE blocks created from grouped image fragments.
    """
    meta = block.source_metadata or {}
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
        return _heading_html(chunks[0])
    if btype in {"code", "code_block"}:
        return _code_html(chunks)
    if btype in {"caption", "figure_caption"}:
        return _caption_html(chunks)
    return _paragraph_html(chunks)


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
        for blk in blocks:
            if (blk.block_type or "").lower() not in {"image", "figure"}:
                continue
            linked_anchor = _linked_caption_anchor(blk)
            if linked_anchor and linked_anchor in caption_by_anchor:
                consumed_caption_ids.add(caption_by_anchor[linked_anchor].id)

        for block in blocks:
            btype = (block.block_type or "").lower()
            if btype in {"caption", "figure_caption"} and block.id in consumed_caption_ids:
                continue
            chunks, untranslated = _block_zh_chunks(session, block)
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
                linked_anchor = _linked_caption_anchor(block)
                if linked_anchor and linked_anchor in caption_by_anchor:
                    cap_block = caption_by_anchor[linked_anchor]
                    cap_chunks, _ = _block_zh_chunks(session, cap_block)
                    if cap_chunks:
                        image_alt = "  ".join(c.strip() for c in cap_chunks if c.strip())
                if not image_alt:
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
        f"total_blocks={total_blocks}",
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
