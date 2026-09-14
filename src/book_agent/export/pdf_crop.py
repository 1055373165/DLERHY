"""PDF figure crop geometry.

Caption-anchored and layout-guided bounding boxes, render scale and crop saving.
"""


from __future__ import annotations

import re
from pathlib import Path

from book_agent.domain.enums import (
    BlockType,
)
from book_agent.export.common import (
    _DOCUMENT_IMAGE_MATERIALIZATION_VERSION,
    _PDF_IMAGE_MAX_RENDER_SCALE,
    _PDF_IMAGE_MIN_RENDER_SCALE,
    _PDF_IMAGE_TARGET_LONG_EDGE_PX,
)
from book_agent.export.models import (
    MergedRenderBlock,
    _PdfPageLayoutBlock,
)


def pdf_asset_crop_spec(block: MergedRenderBlock) -> tuple[str, int, list[float]] | None:
    source_bbox_json = block.source_metadata.get("source_bbox_json")
    if not isinstance(source_bbox_json, dict):
        return None
    regions = source_bbox_json.get("regions")
    if not isinstance(regions, list) or not regions or not isinstance(regions[0], dict):
        return None
    first_region = regions[0]
    page_number = first_region.get("page_number")
    bbox = first_region.get("bbox")
    if not isinstance(page_number, int) or not isinstance(bbox, list) or len(bbox) != 4:
        return None
    try:
        bbox_values = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return None
    if bbox_values[2] <= bbox_values[0] or bbox_values[3] <= bbox_values[1]:
        return None
    if block.block_type == BlockType.CAPTION.value and block.render_mode == "image_anchor_with_translated_caption":
        return ("caption_anchor", page_number, bbox_values)
    return ("direct", page_number, bbox_values)


def caption_anchored_pdf_crop_bbox(page: object, caption_bbox: list[float]) -> list[float] | None:
    page_rect = getattr(page, "rect", None)
    if page_rect is None:
        return None
    page_x0 = float(getattr(page_rect, "x0", 0.0))
    page_y0 = float(getattr(page_rect, "y0", 0.0))
    page_x1 = float(getattr(page_rect, "x1", 0.0))
    page_y1 = float(getattr(page_rect, "y1", 0.0))
    if page_x1 <= page_x0 or page_y1 <= page_y0:
        return None
    fallback_bbox = default_caption_anchored_pdf_crop_bbox(
        page_x0=page_x0,
        page_y0=page_y0,
        page_x1=page_x1,
        page_y1=page_y1,
        caption_bbox=caption_bbox,
    )
    if fallback_bbox is None:
        return None
    layout_blocks = page_layout_blocks(page)
    if not layout_blocks:
        return fallback_bbox
    image_bbox = best_caption_aligned_image_bbox(
        layout_blocks=layout_blocks,
        caption_bbox=caption_bbox,
        page_bounds=[page_x0, page_y0, page_x1, page_y1],
    )
    if image_bbox is not None:
        return image_bbox
    return trim_caption_crop_bbox_with_text_blocks(
        fallback_bbox=fallback_bbox,
        layout_blocks=layout_blocks,
        caption_bbox=caption_bbox,
        page_bounds=[page_x0, page_y0, page_x1, page_y1],
    )


def layout_guided_pdf_crop_bbox(page: object, seed_bbox: list[float]) -> list[float] | None:
    page_rect = getattr(page, "rect", None)
    if page_rect is None:
        return seed_bbox
    page_bounds = [
        float(getattr(page_rect, "x0", 0.0)),
        float(getattr(page_rect, "y0", 0.0)),
        float(getattr(page_rect, "x1", 0.0)),
        float(getattr(page_rect, "y1", 0.0)),
    ]
    if page_bounds[2] <= page_bounds[0] or page_bounds[3] <= page_bounds[1]:
        return seed_bbox
    layout_blocks = page_layout_blocks(page)
    if not layout_blocks:
        return seed_bbox
    image_bbox = best_seed_aligned_image_bbox(
        layout_blocks=layout_blocks,
        seed_bbox=seed_bbox,
        page_bounds=page_bounds,
    )
    if image_bbox is not None:
        return image_bbox
    return trim_direct_crop_bbox_with_text_blocks(
        seed_bbox=seed_bbox,
        layout_blocks=layout_blocks,
        page_bounds=page_bounds,
    )


def default_caption_anchored_pdf_crop_bbox(
    *,
    page_x0: float,
    page_y0: float,
    page_x1: float,
    page_y1: float,
    caption_bbox: list[float],
) -> list[float] | None:
    caption_x0, caption_y0, caption_x1, _caption_y1 = caption_bbox
    bottom = max(page_y0 + 48.0, caption_y0 - 10.0)
    top = max(page_y0 + 36.0, bottom - min(420.0, max(180.0, (page_y1 - page_y0) * 0.48)))
    left = max(page_x0 + 36.0, caption_x0 - 72.0)
    right = min(page_x1 - 36.0, caption_x1 + 72.0)
    if right - left < 80.0:
        left = page_x0 + 36.0
        right = page_x1 - 36.0
    if bottom - top < 80.0:
        return None
    return [left, top, right, bottom]


def page_layout_blocks(page: object) -> list[_PdfPageLayoutBlock]:
    getter = getattr(page, "get_text", None)
    if getter is None:
        return []
    try:
        raw_blocks = getter("blocks")
    except Exception:
        return []
    parsed: list[_PdfPageLayoutBlock] = []
    for raw in raw_blocks or []:
        if not isinstance(raw, (list, tuple)) or len(raw) < 4:
            continue
        try:
            bbox = [float(raw[index]) for index in range(4)]
        except (TypeError, ValueError):
            continue
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        text = str(raw[4]).strip() if len(raw) >= 5 and isinstance(raw[4], str) else ""
        block_type = next((value for value in reversed(raw[5:]) if isinstance(value, int)), None)
        parsed.append(_PdfPageLayoutBlock(bbox=bbox, text=text, block_type=block_type))
    return parsed


def best_caption_aligned_image_bbox(
    *,
    layout_blocks: list[_PdfPageLayoutBlock],
    caption_bbox: list[float],
    page_bounds: list[float],
) -> list[float] | None:
    caption_x0, caption_y0, caption_x1, _caption_y1 = caption_bbox
    corridor = [
        max(page_bounds[0] + 24.0, caption_x0 - 120.0),
        page_bounds[1],
        min(page_bounds[2] - 24.0, caption_x1 + 120.0),
        caption_y0,
    ]
    best_bbox: list[float] | None = None
    best_score: tuple[float, float, float, float] | None = None
    for block in layout_blocks:
        if not looks_like_page_image_block(block):
            continue
        if block.bbox[3] > caption_y0 + 8.0:
            continue
        gap = max(0.0, caption_y0 - block.bbox[3])
        if gap > 120.0:
            continue
        overlap = bbox_horizontal_overlap_ratio(block.bbox, corridor)
        center_distance = abs(((block.bbox[0] + block.bbox[2]) / 2.0) - ((caption_x0 + caption_x1) / 2.0))
        if overlap < 0.18 and center_distance > 220.0:
            continue
        area = max(1.0, (block.bbox[2] - block.bbox[0]) * (block.bbox[3] - block.bbox[1]))
        score = (
            gap,
            -overlap,
            center_distance,
            -area,
        )
        if best_score is None or score < best_score:
            best_score = score
            best_bbox = [
                max(page_bounds[0] + 12.0, block.bbox[0] - 12.0),
                max(page_bounds[1] + 12.0, block.bbox[1] - 12.0),
                min(page_bounds[2] - 12.0, block.bbox[2] + 12.0),
                min(caption_y0 - 6.0, block.bbox[3] + 12.0),
            ]
    if best_bbox is None or best_bbox[2] - best_bbox[0] < 80.0 or best_bbox[3] - best_bbox[1] < 80.0:
        return None
    return best_bbox


def trim_caption_crop_bbox_with_text_blocks(
    *,
    fallback_bbox: list[float],
    layout_blocks: list[_PdfPageLayoutBlock],
    caption_bbox: list[float],
    page_bounds: list[float],
) -> list[float] | None:
    left, top, right, bottom = fallback_bbox
    caption_y0 = float(caption_bbox[1])
    interfering_text_blocks = [
        block
        for block in layout_blocks
        if looks_like_page_text_block(block)
        and block.bbox[3] <= caption_y0 + 2.0
        and bbox_horizontal_overlap_ratio(block.bbox, [left, top, right, bottom]) >= 0.35
        and block.bbox[1] < bottom
    ]
    if interfering_text_blocks:
        trimmed_top = max(block.bbox[3] for block in interfering_text_blocks) + 12.0
        top = max(top, trimmed_top)
    top = max(page_bounds[1] + 36.0, min(top, caption_y0 - 96.0))
    if bottom - top < 80.0:
        return fallback_bbox
    return [left, top, right, bottom]


def best_seed_aligned_image_bbox(
    *,
    layout_blocks: list[_PdfPageLayoutBlock],
    seed_bbox: list[float],
    page_bounds: list[float],
) -> list[float] | None:
    seed_center_x = (seed_bbox[0] + seed_bbox[2]) / 2.0
    seed_center_y = (seed_bbox[1] + seed_bbox[3]) / 2.0
    candidates: list[_PdfPageLayoutBlock] = []
    for block in layout_blocks:
        if not looks_like_page_image_block(block):
            continue
        overlap = bbox_overlap_area(block.bbox, seed_bbox)
        horizontal = bbox_horizontal_overlap_ratio(block.bbox, seed_bbox)
        center_distance = abs(((block.bbox[0] + block.bbox[2]) / 2.0) - seed_center_x) + abs(
            ((block.bbox[1] + block.bbox[3]) / 2.0) - seed_center_y
        )
        if overlap <= 0.0 and horizontal < 0.28 and center_distance > 180.0:
            continue
        candidates.append(block)
    if not candidates:
        return None
    primary = max(
        candidates,
        key=lambda block: (
            bbox_overlap_area(block.bbox, seed_bbox),
            bbox_horizontal_overlap_ratio(block.bbox, seed_bbox),
            -abs(((block.bbox[0] + block.bbox[2]) / 2.0) - seed_center_x),
        ),
    )
    merged_bbox = list(primary.bbox)
    for block in candidates:
        if block is primary:
            continue
        if bbox_overlap_area(block.bbox, merged_bbox) > 0.0 or bbox_horizontal_overlap_ratio(
            block.bbox,
            merged_bbox,
        ) >= 0.45:
            merged_bbox = [
                min(merged_bbox[0], block.bbox[0]),
                min(merged_bbox[1], block.bbox[1]),
                max(merged_bbox[2], block.bbox[2]),
                max(merged_bbox[3], block.bbox[3]),
            ]
    padded = [
        max(page_bounds[0] + 10.0, merged_bbox[0] - 10.0),
        max(page_bounds[1] + 10.0, merged_bbox[1] - 10.0),
        min(page_bounds[2] - 10.0, merged_bbox[2] + 10.0),
        min(page_bounds[3] - 10.0, merged_bbox[3] + 10.0),
    ]
    if padded[2] - padded[0] < 72.0 or padded[3] - padded[1] < 72.0:
        return None
    return padded


def trim_direct_crop_bbox_with_text_blocks(
    *,
    seed_bbox: list[float],
    layout_blocks: list[_PdfPageLayoutBlock],
    page_bounds: list[float],
) -> list[float] | None:
    left, top, right, bottom = seed_bbox
    top_text_blocks = [
        block
        for block in layout_blocks
        if looks_like_page_text_block(block)
        and bbox_horizontal_overlap_ratio(block.bbox, [left, top, right, bottom]) >= 0.35
        and block.bbox[1] <= top + (bottom - top) * 0.3
    ]
    bottom_text_blocks = [
        block
        for block in layout_blocks
        if looks_like_page_text_block(block)
        and bbox_horizontal_overlap_ratio(block.bbox, [left, top, right, bottom]) >= 0.35
        and block.bbox[3] >= bottom - (bottom - top) * 0.3
    ]
    if top_text_blocks:
        top = max(top, max(block.bbox[3] for block in top_text_blocks) + 8.0)
    if bottom_text_blocks:
        bottom = min(bottom, min(block.bbox[1] for block in bottom_text_blocks) - 8.0)
    top = max(page_bounds[1] + 12.0, top)
    bottom = min(page_bounds[3] - 12.0, bottom)
    if bottom - top < 72.0:
        return seed_bbox
    return [left, top, right, bottom]


def looks_like_page_image_block(block: _PdfPageLayoutBlock) -> bool:
    if block.block_type == 1:
        return True
    return not block.text.strip() and (block.bbox[2] - block.bbox[0]) >= 96.0 and (block.bbox[3] - block.bbox[1]) >= 96.0


def looks_like_page_text_block(block: _PdfPageLayoutBlock) -> bool:
    if block.block_type == 1:
        return False
    return bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", block.text))


def bbox_horizontal_overlap_ratio(left: list[float], right: list[float]) -> float:
    overlap = min(left[2], right[2]) - max(left[0], right[0])
    if overlap <= 0:
        return 0.0
    left_width = max(left[2] - left[0], 1.0)
    right_width = max(right[2] - right[0], 1.0)
    return overlap / min(left_width, right_width)


def bbox_overlap_area(left_bbox: list[float], right_bbox: list[float]) -> float:
    left = max(float(left_bbox[0]), float(right_bbox[0]))
    top = max(float(left_bbox[1]), float(right_bbox[1]))
    right = min(float(left_bbox[2]), float(right_bbox[2]))
    bottom = min(float(left_bbox[3]), float(right_bbox[3]))
    if right <= left or bottom <= top:
        return 0.0
    return (right - left) * (bottom - top)


def preferred_pdf_crop_pixel_size(
    block: MergedRenderBlock | None,
    document_image: object | None,
) -> tuple[int | None, int | None]:
    width_px = getattr(document_image, "width_px", None) if document_image is not None else None
    height_px = getattr(document_image, "height_px", None) if document_image is not None else None
    if not isinstance(width_px, int) and block is not None:
        candidate = block.source_metadata.get("image_width_px")
        if isinstance(candidate, (int, float)):
            width_px = int(candidate)
    if not isinstance(height_px, int) and block is not None:
        candidate = block.source_metadata.get("image_height_px")
        if isinstance(candidate, (int, float)):
            height_px = int(candidate)
    return (
        width_px if isinstance(width_px, int) and width_px > 0 else None,
        height_px if isinstance(height_px, int) and height_px > 0 else None,
    )


def document_image_needs_refresh(
    document_image: object,
    *,
    expected_vias: set[str],
) -> bool:
    metadata = dict(getattr(document_image, "metadata_json", {}) or {})
    if metadata.get("materialized_via") not in expected_vias:
        return True
    if metadata.get("storage_status") != "materialized":
        return True
    version = metadata.get("materialized_version")
    try:
        return int(version) < _DOCUMENT_IMAGE_MATERIALIZATION_VERSION
    except (TypeError, ValueError):
        return True


def pdf_crop_render_scale(
    rect: object,
    *,
    desired_width_px: int | None = None,
    desired_height_px: int | None = None,
) -> float:
    rect_width = float(getattr(rect, "width", 0.0) or 0.0)
    rect_height = float(getattr(rect, "height", 0.0) or 0.0)
    longest_edge = max(rect_width, rect_height, 1.0)
    scale_candidates = [_PDF_IMAGE_MIN_RENDER_SCALE, _PDF_IMAGE_TARGET_LONG_EDGE_PX / longest_edge]
    if desired_width_px and rect_width > 0:
        scale_candidates.append(desired_width_px / rect_width)
    if desired_height_px and rect_height > 0:
        scale_candidates.append(desired_height_px / rect_height)
    return max(1.0, min(max(scale_candidates), _PDF_IMAGE_MAX_RENDER_SCALE))


def save_pdf_asset(
    document: object,
    page: object,
    rect: object,
    target_path: Path,
    *,
    original_asset: dict[str, object] | None = None,
    desired_width_px: int | None = None,
    desired_height_px: int | None = None,
) -> tuple[str, float | None, str | None]:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if original_asset is None:
        original_asset = probe_pdf_original_asset(document, page, rect)
    original_bytes = original_asset.get("image_bytes")
    if isinstance(original_bytes, (bytes, bytearray)):
        target_path.write_bytes(bytes(original_bytes))
        return (
            str(original_asset.get("materialized_via") or "pdf_original_image"),
            None,
            str(original_asset.get("availability") or "single_embedded_image"),
        )
    render_scale = pdf_crop_render_scale(
        rect,
        desired_width_px=desired_width_px,
        desired_height_px=desired_height_px,
    )
    save_pdf_crop(
        page,
        rect,
        target_path,
        desired_width_px=desired_width_px,
        desired_height_px=desired_height_px,
    )
    return (
        "pdf_export_crop",
        render_scale,
        str(original_asset.get("availability") or "no_matching_embedded_image"),
    )


def save_pdf_crop(
    page: object,
    rect: object,
    target_path: Path,
    *,
    desired_width_px: int | None = None,
    desired_height_px: int | None = None,
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = None
    render_scale = pdf_crop_render_scale(
        rect,
        desired_width_px=desired_width_px,
        desired_height_px=desired_height_px,
    )
    if render_scale > 1.0:
        try:
            import fitz

            matrix = fitz.Matrix(render_scale, render_scale)
            pixmap = page.get_pixmap(matrix=matrix, clip=rect, alpha=False)
        except (ImportError, AttributeError, TypeError):
            pixmap = None
    if pixmap is None:
        pixmap = page.get_pixmap(clip=rect, alpha=False)
    try:
        pixmap.save(str(target_path))
    finally:
        pixmap = None


def probe_pdf_original_asset(
    document: object,
    page: object,
    rect: object,
) -> dict[str, object]:
    get_images = getattr(page, "get_images", None)
    get_image_rects = getattr(page, "get_image_rects", None)
    extract_image = getattr(document, "extract_image", None)
    if get_images is None or get_image_rects is None or extract_image is None:
        return {"availability": "no_embedded_image_support"}
    rect_x0 = getattr(rect, "x0", None)
    rect_y0 = getattr(rect, "y0", None)
    rect_x1 = getattr(rect, "x1", None)
    rect_y1 = getattr(rect, "y1", None)
    if not all(isinstance(value, (int, float)) for value in (rect_x0, rect_y0, rect_x1, rect_y1)):
        return {"availability": "invalid_crop_rect"}
    crop_area = max((float(rect_x1) - float(rect_x0)) * (float(rect_y1) - float(rect_y0)), 1.0)
    best_match: tuple[float, int] | None = None
    overlap_matches: list[tuple[float, int]] = []
    try:
        images = get_images(full=True)
    except Exception:
        return {"availability": "page_image_enumeration_failed"}
    if not images:
        drawings_getter = getattr(page, "get_drawings", None)
        has_drawings = False
        if drawings_getter is not None:
            try:
                has_drawings = bool(drawings_getter())
            except Exception:
                has_drawings = False
        return {
            "availability": "vector_only_page_artifact" if has_drawings else "no_embedded_images_on_page"
        }
    for image in images or []:
        if not isinstance(image, (list, tuple)) or not image:
            continue
        try:
            xref = int(image[0])
        except (TypeError, ValueError):
            continue
        try:
            image_rects = get_image_rects(xref)
        except Exception:
            continue
        for image_rect in image_rects or []:
            overlap_area = rect_overlap_area(
                [float(rect_x0), float(rect_y0), float(rect_x1), float(rect_y1)],
                [
                    float(getattr(image_rect, "x0", 0.0)),
                    float(getattr(image_rect, "y0", 0.0)),
                    float(getattr(image_rect, "x1", 0.0)),
                    float(getattr(image_rect, "y1", 0.0)),
                ],
            )
            overlap_ratio = overlap_area / crop_area
            if overlap_ratio >= 0.01:
                overlap_matches.append((overlap_ratio, xref))
            if overlap_ratio < 0.35:
                continue
            if best_match is None or overlap_ratio > best_match[0]:
                best_match = (overlap_ratio, xref)
    if best_match is None:
        overlap_matches.sort(reverse=True)
        if len(overlap_matches) >= 2:
            return {
                "availability": "fragmented_embedded_images",
                "fragment_count": len({xref for _ratio, xref in overlap_matches}),
                "best_overlap_ratio": round(overlap_matches[0][0], 4),
            }
        return {"availability": "no_matching_embedded_image"}
    try:
        extracted_image = extract_image(best_match[1])
    except Exception:
        return {"availability": "embedded_image_extract_failed"}
    image_bytes = extracted_image.get("image")
    if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
        return {"availability": "embedded_image_extract_failed"}
    return {
        "image_bytes": bytes(image_bytes),
        "extension": normalize_asset_extension(extracted_image.get("ext") or ".png"),
        "materialized_via": "pdf_original_image",
        "availability": "single_embedded_image",
    }


def extract_pdf_original_image(
    document: object,
    page: object,
    rect: object,
) -> tuple[bytes, str] | None:
    original_asset = probe_pdf_original_asset(document, page, rect)
    image_bytes = original_asset.get("image_bytes")
    extension = original_asset.get("extension")
    if not isinstance(image_bytes, (bytes, bytearray)) or not isinstance(extension, str):
        return None
    return (bytes(image_bytes), extension)


def document_image_asset_suffix(document_image: object | None, *, default_ext: str) -> str:
    if document_image is None:
        return normalize_asset_extension(default_ext)
    storage_path = str(getattr(document_image, "storage_path", "") or "").strip()
    if storage_path:
        storage_suffix = normalize_asset_extension(Path(storage_path).suffix)
        if storage_suffix != ".png" or Path(storage_path).suffix:
            return storage_suffix
    metadata = dict(getattr(document_image, "metadata_json", {}) or {})
    return normalize_asset_extension(metadata.get("image_ext") or default_ext)


def normalize_asset_extension(extension: object) -> str:
    if not isinstance(extension, str):
        return ".png"
    normalized = extension.strip().lower()
    if not normalized:
        return ".png"
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    if not re.fullmatch(r"\.[a-z0-9]+", normalized):
        return ".png"
    return normalized


def rect_overlap_area(left_rect: list[float], right_rect: list[float]) -> float:
    left = max(float(left_rect[0]), float(right_rect[0]))
    top = max(float(left_rect[1]), float(right_rect[1]))
    right = min(float(left_rect[2]), float(right_rect[2]))
    bottom = min(float(left_rect[3]), float(right_rect[3]))
    if right <= left or bottom <= top:
        return 0.0
    return (right - left) * (bottom - top)
