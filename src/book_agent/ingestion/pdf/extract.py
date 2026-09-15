"""PDF text-layer extractors (PyMuPDF and a basic fallback) and the file profiler."""


from __future__ import annotations

import ctypes
import re
import sys
import zlib
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol

from book_agent.domain.structure.text_layer_sanity import assess_text as _assess_text_layer_sanity
from book_agent.ingestion.pdf.classify import (
    _early_page_has_title_signal,
    _page_has_centered_title_signal,
    _page_has_column_fragment_signature,
    _page_has_multi_column_signature,
    _page_has_single_column_academic_first_page_signal,
    _page_has_title_overlap_signal,
    _trailing_reference_page_count,
)
from book_agent.ingestion.pdf.models import (
    PdfExtraction,
    PdfFileProfile,
    PdfImageBlock,
    PdfOutlineEntry,
    PdfPage,
    PdfTextBlock,
)
from book_agent.ingestion.text import (
    _MONOSPACE_FONT_PATTERNS,
    _normalize_multiline_text,
    _normalize_text,
    _safe_mean,
)


_BOLD_FONT_NAME = re.compile(r"bold|black|heavy|semibold|demi", re.IGNORECASE)


def _span_is_bold(span: dict[str, Any]) -> bool:
    # PyMuPDF sets flag bit 16 for bold; subset fonts often only say so in the name.
    return bool(int(span.get("flags", 0) or 0) & 16) or bool(_BOLD_FONT_NAME.search(str(span.get("font", ""))))


_MIN_RULED_TABLE_CELLS = 4
_MAX_RULED_TABLE_AVG_CELL_CHARS = 80


def _has_ruling_grid(drawings: list[dict[str, Any]]) -> bool:
    """At least three horizontal and two vertical rules (lines or hairline rectangles)."""
    horizontal = vertical = 0
    for drawing in drawings:
        for item in drawing.get("items") or ():
            kind = item[0]
            if kind == "l":
                start, end = item[1], item[2]
                width, height = abs(end.x - start.x), abs(end.y - start.y)
            elif kind == "re":
                rect = item[1]
                width, height = abs(rect.width), abs(rect.height)
            else:
                continue
            if width >= 8.0 and height <= 2.0:
                horizontal += 1
            elif height >= 8.0 and width <= 2.0:
                vertical += 1
            if horizontal >= 3 and vertical >= 2:
                return True
    return False


_BULLET_MARK_MIN_SIZE = 1.5
_BULLET_MARK_MAX_SIZE = 7.0
_BULLET_MAX_GAP = 36.0


def _vector_bullet_marks(drawings: list[dict[str, Any]]) -> list[tuple[float, float, float, float]]:
    """Small filled, roughly square shapes: list bullets drawn as vector dots or squares."""
    marks: list[tuple[float, float, float, float]] = []
    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is None or drawing.get("fill") is None:
            continue
        width, height = float(rect[2]) - float(rect[0]), float(rect[3]) - float(rect[1])
        if not (_BULLET_MARK_MIN_SIZE <= width <= _BULLET_MARK_MAX_SIZE):
            continue
        if not (_BULLET_MARK_MIN_SIZE <= height <= _BULLET_MARK_MAX_SIZE) or abs(width - height) > 1.5:
            continue
        marks.append((float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])))
    return marks


def _split_vector_bullet_blocks(
    blocks: list[PdfTextBlock],
    line_bboxes_by_block: dict[int, list[tuple[float, float, float, float]]],
    drawings: list[dict[str, Any]],
) -> list[PdfTextBlock]:
    """Split a text block into list items at lines preceded by a vector bullet mark.

    Such bullets are not in the text layer, so a whole bulleted list otherwise
    arrives as one paragraph. Each item gets a "• " marker, the same text a
    glyph bullet would have produced.
    """
    marks = _vector_bullet_marks(drawings)
    if not marks:
        return blocks
    result: list[PdfTextBlock] = []
    for block in blocks:
        line_bboxes = line_bboxes_by_block.get(id(block))
        if not line_bboxes or len(line_bboxes) != len(block.line_texts):
            result.append(block)
            continue
        starts = [
            any(
                mark[2] <= bbox[0]
                and bbox[0] - mark[2] <= _BULLET_MAX_GAP
                and bbox[1] - 1.0 <= (mark[1] + mark[3]) / 2 <= bbox[3] + 1.0
                for mark in marks
            )
            for bbox in line_bboxes
        ]
        if not any(starts):
            result.append(block)
            continue
        segment_starts = [index for index, is_start in enumerate(starts) if is_start]
        if segment_starts[0] != 0:
            segment_starts.insert(0, 0)
        for position, start in enumerate(segment_starts):
            end = segment_starts[position + 1] if position + 1 < len(segment_starts) else len(line_bboxes)
            segment_lines = list(block.line_texts[start:end])
            if starts[start]:
                segment_lines[0] = f"\u2022 {segment_lines[0]}"
            segment_bboxes = line_bboxes[start:end]
            result.append(
                replace(
                    block,
                    text=_normalize_multiline_text("\n".join(segment_lines)),
                    bbox=(
                        min(bbox[0] for bbox in segment_bboxes),
                        min(bbox[1] for bbox in segment_bboxes),
                        max(bbox[2] for bbox in segment_bboxes),
                        max(bbox[3] for bbox in segment_bboxes),
                    ),
                    line_texts=segment_lines,
                    line_count=len(segment_lines),
                    line_styles=block.line_styles[start:end],
                    raw_text=None,
                )
            )
    return result


_LINE_ENUMERATOR = re.compile(r"^(\d{1,3})[.)]\s+\S")


def _lines_are_consecutive_enumeration(lines: list[str]) -> bool:
    matches = [_LINE_ENUMERATOR.match(line) for line in lines]
    if len(lines) < 2 or not all(matches):
        return False
    numbers = [int(match.group(1)) for match in matches]
    return numbers == list(range(numbers[0], numbers[0] + len(numbers)))


def _split_enumerated_line_blocks(
    blocks: list[PdfTextBlock],
    line_bboxes_by_block: dict[int, list[tuple[float, float, float, float]]],
) -> list[PdfTextBlock]:
    """Split a block whose every line is the next item of a numbered list ("1. ...", "2. ...").

    Such a list arrives as one text block; past a few lines it is no longer
    recognised as a list item and reads as one run-on paragraph.
    """
    result: list[PdfTextBlock] = []
    for block in blocks:
        line_bboxes = line_bboxes_by_block.get(id(block))
        if (
            not line_bboxes
            or len(line_bboxes) != len(block.line_texts)
            or not _lines_are_consecutive_enumeration(block.line_texts)
        ):
            result.append(block)
            continue
        for index, line in enumerate(block.line_texts):
            result.append(
                replace(
                    block,
                    text=line,
                    bbox=line_bboxes[index],
                    line_texts=[line],
                    line_count=1,
                    line_styles=block.line_styles[index : index + 1],
                    raw_text=None,
                )
            )
    return result


def _table_cell_text(cell: Any) -> str:
    # Merged cells come back as None; "|" would split the recovered row.
    return " ".join(str(cell or "").split()).replace("|", "/")


def _looks_like_data_table(rows: list[list[str]]) -> bool:
    if len(rows) < 2 or max((len(row) for row in rows), default=0) < 2:
        return False
    cells = [cell for row in rows for cell in row if cell]
    if len(cells) < _MIN_RULED_TABLE_CELLS:
        return False
    return sum(len(cell) for cell in cells) / len(cells) <= _MAX_RULED_TABLE_AVG_CELL_CHARS


class PdfTextExtractor(Protocol):
    def extract(self, file_path: str | Path) -> PdfExtraction:
        ...


class PyMuPDFTextExtractor:
    def __init__(self, *, image_output_dir: str | Path | None = None) -> None:
        if image_output_dir is not None:
            self._image_output_dir = Path(image_output_dir)
        else:
            # Auto-create a temp directory so images are always materialized.
            import tempfile

            self._image_output_dir = Path(tempfile.mkdtemp(prefix="book-agent-pdf-images-"))

    def extract(self, file_path: str | Path) -> PdfExtraction:
        try:
            import fitz
        except ImportError as exc:  # pragma: no cover - exercised via runtime failure path.
            raise RuntimeError(
                "PDF support requires PyMuPDF. Run `uv sync` to install PDF dependencies."
            ) from exc

        document = fitz.open(str(file_path))
        try:
            metadata = {key: value for key, value in (document.metadata or {}).items() if value}
            outline_entries = self._extract_outline(document)
            pages: list[PdfPage] = []
            # Track xrefs already extracted to avoid duplicates across pages.
            materialized_xrefs: dict[int, str] = {}

            for page_index in range(document.page_count):
                page = document.load_page(page_index)
                text_dict = page.get_text("dict", sort=False)
                blocks: list[PdfTextBlock] = []
                line_bboxes_by_block: dict[int, list[tuple[float, float, float, float]]] = {}
                image_blocks: list[PdfImageBlock] = []
                for block_number, block in enumerate(text_dict.get("blocks", []), start=1):
                    if block.get("type") == 1:
                        bbox = tuple(float(value) for value in block.get("bbox", (0, 0, 0, 0)))
                        width_px = (
                            int(block["width"])
                            if isinstance(block.get("width"), (int, float))
                            else None
                        )
                        height_px = (
                            int(block["height"])
                            if isinstance(block.get("height"), (int, float))
                            else None
                        )
                        image_ext = str(block["ext"]).strip().lower() if block.get("ext") else None
                        # Materialize embedded image via xref extraction.
                        mat_path, xref = self._materialize_embedded_image(
                            document, page, bbox, page_index + 1, block_number,
                            materialized_xrefs=materialized_xrefs,
                        )
                        image_blocks.append(
                            PdfImageBlock(
                                page_number=page_index + 1,
                                block_number=block_number,
                                bbox=bbox,
                                width_px=width_px,
                                height_px=height_px,
                                image_ext=image_ext,
                                materialized_path=mat_path,
                                xref=xref,
                            )
                        )
                        continue
                    if block.get("type") != 0:
                        continue
                    lines: list[str] = []
                    span_count = 0
                    font_sizes: list[float] = []
                    font_names_set: set[str] = set()
                    mono_span_count = 0
                    total_span_count = 0
                    line_styles: list[tuple[float, bool]] = []
                    line_bboxes: list[tuple[float, float, float, float]] = []
                    for line in block.get("lines", []):
                        parts: list[str] = []
                        size_weights: dict[float, int] = {}
                        line_bold = True
                        for span in line.get("spans", []):
                            text = span.get("text", "")
                            if not text:
                                continue
                            parts.append(text)
                            span_count += 1
                            total_span_count += 1
                            size = float(span.get("size", 0.0) or 0.0)
                            font_sizes.append(size)
                            fn = span.get("font", "")
                            if fn:
                                font_names_set.add(fn)
                                if _MONOSPACE_FONT_PATTERNS.search(fn):
                                    mono_span_count += 1
                            visible = len(text.strip())
                            if visible:
                                size_weights[round(size, 1)] = size_weights.get(round(size, 1), 0) + visible
                                line_bold = line_bold and _span_is_bold(span)
                        normalized_line = _normalize_text("".join(parts))
                        if normalized_line:
                            lines.append(normalized_line)
                            dominant_size = max(size_weights, key=size_weights.__getitem__) if size_weights else 0.0
                            line_styles.append((dominant_size, line_bold and bool(size_weights)))
                            line_bboxes.append(tuple(float(value) for value in line.get("bbox", (0, 0, 0, 0))))

                    text = _normalize_multiline_text("\n".join(lines))
                    if not text:
                        continue

                    # For monospace-dominant blocks, capture raw text preserving
                    # original whitespace (indentation + newlines) via clip extraction.
                    raw_text: str | None = None
                    font_names_frozen = frozenset(font_names_set)
                    is_monospace_dominant = (
                        total_span_count > 0
                        and mono_span_count / total_span_count >= 0.7
                    )
                    if is_monospace_dominant and len(lines) >= 2:
                        block_bbox = block.get("bbox", (0, 0, 0, 0))
                        try:
                            import fitz as _fitz
                            clip_rect = _fitz.Rect(*block_bbox)
                            raw_text = page.get_text("text", clip=clip_rect)
                            if raw_text:
                                raw_text = raw_text.rstrip()
                        except Exception:
                            raw_text = None

                    blocks.append(
                        PdfTextBlock(
                            page_number=page_index + 1,
                            block_number=block_number,
                            text=text,
                            bbox=tuple(float(value) for value in block.get("bbox", (0, 0, 0, 0))),
                            line_texts=lines,
                            span_count=span_count,
                            line_count=len(lines),
                            font_size_min=min(font_sizes) if font_sizes else 0.0,
                            font_size_max=max(font_sizes) if font_sizes else 0.0,
                            font_size_avg=_safe_mean(font_sizes),
                            font_names=font_names_frozen,
                            raw_text=raw_text,
                            line_styles=tuple(line_styles),
                        )
                    )
                    line_bboxes_by_block[id(blocks[-1])] = line_bboxes

                try:
                    drawings = page.get_drawings()
                except Exception:
                    drawings = []
                blocks = _split_vector_bullet_blocks(blocks, line_bboxes_by_block, drawings)
                blocks = _split_enumerated_line_blocks(blocks, line_bboxes_by_block)
                blocks = self._merge_ruled_table_blocks(page, blocks, drawings)
                image_blocks.extend(
                    self._extract_vector_drawing_blocks(
                        page,
                        page_number=page_index + 1,
                        start_block_number=len(text_dict.get("blocks", [])) + 1,
                        drawings=drawings,
                    )
                )

                # Text-layer sanity assessment (PDF v2 M1.2). Runs per page
                # over the concatenated text of all non-image blocks. A failure
                # here means the page's text layer should not be trusted and
                # downstream routing (M1.5/M2) should escalate to OCR.
                concatenated_page_text = "\n".join(b.text for b in blocks)
                sanity_report = _assess_text_layer_sanity(concatenated_page_text)
                page_sanity: dict[str, Any] = {
                    "ok": sanity_report.ok,
                    "reason": sanity_report.reason,
                    "metrics": sanity_report.metrics,
                }

                pages.append(
                    PdfPage(
                        page_number=page_index + 1,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        blocks=blocks,
                        image_blocks=image_blocks,
                        text_layer_sanity=page_sanity,
                    )
                )

            return PdfExtraction(
                title=metadata.get("title") or None,
                author=metadata.get("author") or None,
                metadata={**metadata, "pdf_extractor": "pymupdf"},
                pages=pages,
                outline_entries=outline_entries,
            )
        finally:
            document.close()

    def _extract_vector_drawing_blocks(
        self,
        page: Any,
        *,
        page_number: int,
        start_block_number: int,
        drawings: list[dict[str, Any]] | None = None,
    ) -> list[PdfImageBlock]:
        if drawings is None:
            try:
                drawings = page.get_drawings()
            except Exception:
                return []

        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        drawing_bboxes: list[tuple[float, float, float, float]] = []
        for drawing in drawings:
            rect = drawing.get("rect")
            if rect is None:
                continue
            bbox = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
            if not self._looks_like_vector_drawing_figure_bbox(bbox, page_width, page_height):
                continue
            drawing_bboxes.append(bbox)

        image_blocks: list[PdfImageBlock] = []
        for offset, bbox in enumerate(
            self._cluster_vector_drawing_bboxes(drawing_bboxes, page_width, page_height)
        ):
            image_blocks.append(
                PdfImageBlock(
                    page_number=page_number,
                    block_number=start_block_number + offset,
                    bbox=self._expand_vector_drawing_bbox(bbox, page_width, page_height),
                    image_type="vector_drawing",
                )
            )
        return image_blocks

    def _merge_ruled_table_blocks(
        self,
        page: Any,
        blocks: list[PdfTextBlock],
        drawings: list[dict[str, Any]],
    ) -> list[PdfTextBlock]:
        """Replace the per-cell text blocks of a ruled table with one block of pipe rows.

        PyMuPDF emits every cell of a vector-lined table as its own text line,
        so the table otherwise reaches recovery as a column of numbers.
        ``find_tables`` is slow, so it only runs on pages whose drawings form
        a grid of horizontal and vertical rules.
        """
        if not blocks or not _has_ruling_grid(drawings):
            return blocks
        try:
            tables = page.find_tables().tables
        except Exception:
            return blocks
        for table in tables:
            try:
                rows = [[_table_cell_text(cell) for cell in row] for row in table.extract()]
            except Exception:
                continue
            rows = [row for row in rows if any(row)]
            if not _looks_like_data_table(rows):
                continue
            x0, y0, x1, y1 = (float(value) for value in table.bbox)
            members = [
                block
                for block in blocks
                if x0 - 2 <= (block.bbox[0] + block.bbox[2]) / 2 <= x1 + 2
                and y0 - 2 <= (block.bbox[1] + block.bbox[3]) / 2 <= y1 + 2
            ]
            if not members:
                continue
            row_lines = ["| " + " | ".join(row) + " |" for row in rows]
            font_sizes = [block.font_size_avg for block in members if block.font_size_avg > 0]
            table_block = PdfTextBlock(
                page_number=members[0].page_number,
                block_number=min(block.block_number for block in members),
                text="\n".join(row_lines),
                bbox=(x0, y0, x1, y1),
                line_texts=row_lines,
                span_count=sum(block.span_count for block in members),
                line_count=len(row_lines),
                font_size_min=min((block.font_size_min for block in members), default=0.0),
                font_size_max=max((block.font_size_max for block in members), default=0.0),
                font_size_avg=_safe_mean(font_sizes),
                font_names=frozenset().union(*(block.font_names for block in members)),
                ruled_table=True,
            )
            first_index = blocks.index(members[0])
            member_ids = {id(block) for block in members}
            remaining = [block for block in blocks if id(block) not in member_ids]
            remaining.insert(min(first_index, len(remaining)), table_block)
            blocks = remaining
        return blocks

    def _looks_like_vector_drawing_figure_bbox(
        self,
        bbox: tuple[float, float, float, float],
        page_width: float,
        page_height: float,
    ) -> bool:
        width = max(0.0, bbox[2] - bbox[0])
        height = max(0.0, bbox[3] - bbox[1])
        area = width * height
        if width < 18.0 or height < 18.0:
            return False
        if area < max(1800.0, page_width * page_height * 0.0025):
            return False
        if width >= page_width * 0.92 and height <= page_height * 0.035:
            return False
        if height >= page_height * 0.92 and width <= page_width * 0.035:
            return False
        return True

    def _cluster_vector_drawing_bboxes(
        self,
        bboxes: list[tuple[float, float, float, float]],
        page_width: float,
        page_height: float,
    ) -> list[tuple[float, float, float, float]]:
        if not bboxes:
            return []

        clusters: list[tuple[float, float, float, float]] = []
        x_threshold = max(24.0, page_width * 0.08)
        y_threshold = max(24.0, page_height * 0.08)

        for bbox in sorted(bboxes, key=lambda item: (round(item[1], 2), round(item[0], 2))):
            merged = False
            for index, cluster in enumerate(clusters):
                horizontal_gap = max(cluster[0] - bbox[2], bbox[0] - cluster[2], 0.0)
                vertical_gap = max(cluster[1] - bbox[3], bbox[1] - cluster[3], 0.0)
                center_delta_x = abs(((cluster[0] + cluster[2]) / 2.0) - ((bbox[0] + bbox[2]) / 2.0))
                if horizontal_gap <= x_threshold and vertical_gap <= y_threshold and center_delta_x <= page_width * 0.45:
                    clusters[index] = self._union_drawing_bbox(cluster, bbox)
                    merged = True
                    break
            if not merged:
                clusters.append(bbox)

        filtered_clusters: list[tuple[float, float, float, float]] = []
        min_cluster_area = max(12000.0, page_width * page_height * 0.018)
        for cluster in clusters:
            width = max(0.0, cluster[2] - cluster[0])
            height = max(0.0, cluster[3] - cluster[1])
            if width * height < min_cluster_area:
                continue
            if width < page_width * 0.22 and height < page_height * 0.12:
                continue
            filtered_clusters.append(cluster)
        return filtered_clusters

    def _expand_vector_drawing_bbox(
        self,
        bbox: tuple[float, float, float, float],
        page_width: float,
        page_height: float,
    ) -> tuple[float, float, float, float]:
        x_pad = min(48.0, page_width * 0.08)
        y_pad = min(40.0, page_height * 0.06)
        return (
            max(0.0, bbox[0] - x_pad),
            max(0.0, bbox[1] - y_pad),
            min(page_width, bbox[2] + x_pad),
            min(page_height, bbox[3] + y_pad),
        )

    def _union_drawing_bbox(
        self,
        left: tuple[float, float, float, float],
        right: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        return (
            min(left[0], right[0]),
            min(left[1], right[1]),
            max(left[2], right[2]),
            max(left[3], right[3]),
        )

    def _materialize_embedded_image(
        self,
        document: Any,
        page: Any,
        bbox: tuple[float, ...],
        page_number: int,
        block_number: int,
        *,
        materialized_xrefs: dict[int, str],
    ) -> tuple[str | None, int | None]:
        """Extract the embedded image nearest to *bbox* and save to disk.

        Returns ``(materialized_path, xref)`` or ``(None, None)`` when the
        image output directory is not configured or extraction fails.
        """
        if self._image_output_dir is None:
            return None, None

        try:
            import fitz
        except ImportError:
            return None, None

        try:
            page_images = page.get_images(full=True)
        except Exception:
            return None, None

        if not page_images:
            return None, None

        # Find the image whose bbox is closest to the block bbox.
        best_xref: int | None = None
        best_distance = float("inf")
        bx0, by0, bx2, by2 = bbox[:4]
        bcx, bcy = (bx0 + bx2) / 2.0, (by0 + by2) / 2.0

        for img_info in page_images:
            xref = int(img_info[0])
            # Fast dedup: if already materialized, reuse path.
            if xref in materialized_xrefs:
                return materialized_xrefs[xref], xref
            # Estimate image position by checking all image instances on page.
            try:
                img_rects = page.get_image_rects(img_info)
            except Exception:
                img_rects = []
            for rect in img_rects:
                cx = (rect.x0 + rect.x1) / 2.0
                cy = (rect.y0 + rect.y1) / 2.0
                dist = abs(cx - bcx) + abs(cy - bcy)
                if dist < best_distance:
                    best_distance = dist
                    best_xref = xref

        if best_xref is None:
            # Fallback: pick the first image on the page.
            best_xref = int(page_images[0][0])

        if best_xref in materialized_xrefs:
            return materialized_xrefs[best_xref], best_xref

        try:
            pix = fitz.Pixmap(document, best_xref)
            # Convert CMYK/other colour spaces to RGB for broad compatibility.
            if pix.n - pix.alpha > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            output_dir = self._image_output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            filename = f"p{page_number:04d}_b{block_number:03d}.png"
            output_path = output_dir / filename
            pix.save(str(output_path))
            materialized_xrefs[best_xref] = str(output_path)
            return str(output_path), best_xref
        except Exception:
            return None, None

    def _extract_outline(self, document: Any) -> list[PdfOutlineEntry]:
        outline_entries: list[PdfOutlineEntry] = []
        for item in document.get_toc() or []:
            if len(item) < 3:
                continue
            level, title, page_number = item[:3]
            if not title or not page_number:
                continue
            outline_entries.append(
                PdfOutlineEntry(
                    level=int(level),
                    title=_normalize_text(str(title)),
                    page_number=max(1, int(page_number)),
                )
            )
        return outline_entries


class BasicPdfTextExtractor:
    def extract(self, file_path: str | Path) -> PdfExtraction:
        raw_pdf = Path(file_path).read_bytes()
        objects = self._read_objects(raw_pdf)
        page_object_ids = self._ordered_page_object_ids(objects)
        metadata = self._read_metadata(raw_pdf, objects)
        pages = self._extract_pages(objects, page_object_ids)
        page_count_hint = (
            len(page_object_ids)
            or self._fallback_page_count_hint(objects)
            or self._coregraphics_page_count_hint(file_path)
        )
        outline_entries = self._extract_outline_entries(
            objects,
            {object_id: page_number for page_number, object_id in enumerate(page_object_ids, start=1)},
        )
        return PdfExtraction(
            title=metadata.get("title"),
            author=metadata.get("author"),
            metadata={
                **metadata,
                "pdf_extractor": "basic",
                **({"page_count_hint": page_count_hint} if page_count_hint else {}),
            },
            pages=pages,
            outline_entries=outline_entries,
        )

    def _read_objects(self, raw_pdf: bytes) -> dict[int, bytes]:
        objects: dict[int, bytes] = {}
        for match in re.finditer(rb"(?ms)(\d+)\s+\d+\s+obj\s*(.*?)\s*endobj", raw_pdf):
            objects[int(match.group(1))] = match.group(2)
        return objects

    def _read_metadata(self, raw_pdf: bytes, objects: dict[int, bytes]) -> dict[str, Any]:
        info_match = re.search(rb"/Info\s+(\d+)\s+\d+\s+R", raw_pdf)
        if not info_match:
            return {}
        info_body = objects.get(int(info_match.group(1)))
        if not info_body:
            return {}
        metadata = {}
        for metadata_field in ("Title", "Author"):
            value = self._extract_literal_string(info_body, f"/{metadata_field}".encode("ascii"))
            if value:
                metadata[metadata_field.casefold()] = value
        return metadata

    def _ordered_page_object_ids(self, objects: dict[int, bytes]) -> list[int]:
        return [
            object_id
            for object_id, body in sorted(
                (
                    (object_id, body)
                    for object_id, body in objects.items()
                    if b"/Type /Page" in body and b"/Type /Pages" not in body
                ),
                key=lambda item: item[0],
            )
        ]

    def _fallback_page_count_hint(self, objects: dict[int, bytes]) -> int:
        counts = [
            int(match.group(1))
            for body in objects.values()
            for match in re.finditer(rb"/Type\s*/Pages\b.*?/Count\s+(\d+)", body, re.S)
        ]
        return max(counts, default=0)

    def _coregraphics_page_count_hint(self, file_path: str | Path) -> int:
        if sys.platform != "darwin":
            return 0
        try:
            coregraphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")

            create_provider = coregraphics.CGDataProviderCreateWithFilename
            create_provider.argtypes = [ctypes.c_char_p]
            create_provider.restype = ctypes.c_void_p

            create_document = coregraphics.CGPDFDocumentCreateWithProvider
            create_document.argtypes = [ctypes.c_void_p]
            create_document.restype = ctypes.c_void_p

            get_page_count = coregraphics.CGPDFDocumentGetNumberOfPages
            get_page_count.argtypes = [ctypes.c_void_p]
            get_page_count.restype = ctypes.c_size_t

            release_document = coregraphics.CGPDFDocumentRelease
            release_document.argtypes = [ctypes.c_void_p]
            release_document.restype = None

            release_provider = coregraphics.CGDataProviderRelease
            release_provider.argtypes = [ctypes.c_void_p]
            release_provider.restype = None

            provider = create_provider(str(Path(file_path).resolve()).encode("utf-8"))
            if not provider:
                return 0
            document = create_document(provider)
            if not document:
                release_provider(provider)
                return 0
            try:
                return int(get_page_count(document))
            finally:
                release_document(document)
                release_provider(provider)
        except Exception:
            return 0

    def _extract_pages(self, objects: dict[int, bytes], page_object_ids: list[int]) -> list[PdfPage]:
        pages: list[PdfPage] = []
        enable_positioned_blocks = len(page_object_ids) <= 24
        for page_number, object_id in enumerate(page_object_ids, start=1):
            body = objects.get(object_id)
            if body is None:
                continue
            media_box = self._parse_media_box(body)
            width = media_box[2] - media_box[0]
            height = media_box[3] - media_box[1]
            content_streams = self._page_content_streams(body, objects)
            blocks: list[PdfTextBlock] = []
            block_number = 0
            for stream in content_streams:
                for block in self._parse_stream_blocks(
                    stream,
                    page_number,
                    height,
                    width,
                    enable_positioned_blocks=enable_positioned_blocks,
                ):
                    block_number += 1
                    blocks.append(
                        PdfTextBlock(
                            page_number=page_number,
                            block_number=block_number,
                            text=block["text"],
                            bbox=block["bbox"],
                            line_texts=[block["text"]],
                            span_count=max(1, len(block["text"])),
                            line_count=1,
                            font_size_min=block["font_size"],
                            font_size_max=block["font_size"],
                            font_size_avg=block["font_size"],
                        )
                    )
            pages.append(
                PdfPage(
                    page_number=page_number,
                    width=width,
                    height=height,
                    blocks=blocks,
                )
            )
        return pages

    def _extract_outline_entries(
        self,
        objects: dict[int, bytes],
        page_number_by_object: dict[int, int],
    ) -> list[PdfOutlineEntry]:
        outline_entries: list[PdfOutlineEntry] = []
        for object_id, body in sorted(objects.items()):
            title = self._extract_literal_string(body, b"/Title")
            if not title:
                continue
            page_object_id = self._extract_outline_page_object_id(body)
            if page_object_id is None:
                continue
            page_number = page_number_by_object.get(page_object_id)
            if page_number is None:
                continue
            outline_entries.append(
                PdfOutlineEntry(
                    level=self._outline_level(object_id, objects),
                    title=_normalize_text(title),
                    page_number=page_number,
                )
            )
        return outline_entries

    def _extract_outline_page_object_id(self, body: bytes) -> int | None:
        direct_match = re.search(rb"/Dest\s*\[\s*(\d+)\s+\d+\s+R", body, re.S)
        if direct_match:
            return int(direct_match.group(1))
        action_match = re.search(rb"/A\s*<<.*?/D\s*\[\s*(\d+)\s+\d+\s+R", body, re.S)
        if action_match:
            return int(action_match.group(1))
        return None

    def _outline_level(self, object_id: int, objects: dict[int, bytes]) -> int:
        level = 1
        seen = {object_id}
        current_object_id = object_id
        while True:
            body = objects.get(current_object_id)
            if body is None:
                return level
            parent_match = re.search(rb"/Parent\s+(\d+)\s+\d+\s+R", body)
            if not parent_match:
                return level
            parent_object_id = int(parent_match.group(1))
            if parent_object_id in seen:
                return level
            seen.add(parent_object_id)
            parent_body = objects.get(parent_object_id)
            if parent_body is None:
                return level
            if self._extract_literal_string(parent_body, b"/Title"):
                level += 1
            current_object_id = parent_object_id

    def _parse_media_box(self, body: bytes) -> tuple[float, float, float, float]:
        match = re.search(
            rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]",
            body,
        )
        if not match:
            return (0.0, 0.0, 595.0, 842.0)
        return tuple(float(value) for value in match.groups())  # type: ignore[return-value]

    def _page_content_streams(self, page_body: bytes, objects: dict[int, bytes]) -> list[bytes]:
        content_refs: list[int] = []
        array_match = re.search(rb"/Contents\s*\[(.*?)\]", page_body, re.S)
        if array_match:
            content_refs.extend(int(value) for value in re.findall(rb"(\d+)\s+\d+\s+R", array_match.group(1)))
        else:
            single_match = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", page_body)
            if single_match:
                content_refs.append(int(single_match.group(1)))

        streams: list[bytes] = []
        for ref in content_refs:
            body = objects.get(ref)
            if not body or b"stream" not in body:
                continue
            dictionary, stream = body.split(b"stream", 1)
            stream, _end_marker, _tail = stream.partition(b"endstream")
            stream = stream.lstrip(b"\r\n").rstrip(b"\r\n")
            if b"/FlateDecode" in dictionary:
                try:
                    stream = zlib.decompress(stream)
                except zlib.error:
                    continue
            streams.append(stream)
        return streams

    def _parse_stream_blocks(
        self,
        stream: bytes,
        page_number: int,
        page_height: float,
        page_width: float,
        *,
        enable_positioned_blocks: bool,
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for segment in re.findall(rb"BT(.*?)ET", stream, re.S):
            if enable_positioned_blocks and self._should_use_positioned_segment_parsing(segment, page_width):
                positioned_blocks = self._parse_positioned_segment_blocks(
                    segment,
                    page_number=page_number,
                    page_height=page_height,
                    page_width=page_width,
                )
                if positioned_blocks:
                    blocks.extend(positioned_blocks)
                    continue

            font_size = self._extract_font_size(segment)
            x, y = self._extract_position(segment)
            normalized_text = _normalize_text(self._extract_segment_text(segment))
            if not normalized_text:
                continue
            blocks.append(
                {
                    "page_number": page_number,
                    "text": normalized_text,
                    "font_size": font_size,
                    "bbox": self._estimated_bbox(
                        normalized_text,
                        font_size=font_size,
                        x=x,
                        y=y,
                        page_height=page_height,
                        page_width=page_width,
                        width_scale=0.5,
                    ),
                }
            )
        return blocks

    def _should_use_positioned_segment_parsing(self, segment: bytes, page_width: float) -> bool:
        tm_positions = [
            (float(x), float(y))
            for x, y in re.findall(
                rb"[-\d.]+\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s+([-\d.]+)\s+([-\d.]+)\s+Tm",
                segment,
            )
        ]
        if len(tm_positions) < 6:
            return False
        if len(re.findall(rb"\[(.*?)\]\s*TJ", segment, re.S)) < 6 and len(re.findall(rb"\((.*?)(?<!\\)\)\s*Tj", segment, re.S)) < 6:
            return False

        left_count = sum(1 for x, _y in tm_positions if x <= page_width * 0.32)
        right_count = sum(1 for x, _y in tm_positions if x >= page_width * 0.45)
        distinct_rows = len({round(y, 1) for _x, y in tm_positions})
        return left_count >= 3 and right_count >= 3 and distinct_rows >= 3

    def _extract_font_size(self, segment: bytes) -> float:
        match = re.findall(rb"/F\d+\s+([-\d.]+)\s+Tf", segment)
        if match:
            return float(match[-1])
        return 12.0

    def _extract_position(self, segment: bytes) -> tuple[float, float]:
        tm_matches = re.findall(rb"1\s+0\s+0\s+1\s+([-\d.]+)\s+([-\d.]+)\s+Tm", segment)
        if tm_matches:
            x, y = tm_matches[-1]
            return float(x), float(y)
        td_matches = re.findall(rb"([-\d.]+)\s+([-\d.]+)\s+Td", segment)
        if td_matches:
            x, y = td_matches[-1]
            return float(x), float(y)
        return 0.0, 0.0

    def _parse_positioned_segment_blocks(
        self,
        segment: bytes,
        *,
        page_number: int,
        page_height: float,
        page_width: float,
    ) -> list[dict[str, Any]]:
        token_pattern = re.compile(
            rb"/F\d+\s+[-\d.]+\s+Tf"
            rb"|(?:[-\d.]+\s+){5}[-\d.]+\s+Tm"
            rb"|[-\d.]+\s+[-\d.]+\s+Td"
            rb"|\[(?:.*?)\]\s*TJ"
            rb"|\((?:.*?)(?<!\\)\)\s*Tj",
            re.S,
        )
        operations = list(token_pattern.finditer(segment))
        if not operations:
            return []

        blocks: list[dict[str, Any]] = []
        current_font_size = self._extract_font_size(segment)
        current_parts: list[str] = []
        current_font_sizes: list[float] = []
        current_origin: tuple[float, float] | None = None
        current_cursor: tuple[float, float] | None = None

        def flush_current() -> None:
            nonlocal current_parts, current_font_sizes, current_origin
            normalized_text = _normalize_text(" ".join(part for part in current_parts if part))
            if not normalized_text or current_origin is None:
                current_parts = []
                current_font_sizes = []
                current_origin = None
                return
            font_size = _safe_mean(current_font_sizes) if current_font_sizes else current_font_size
            blocks.append(
                {
                    "page_number": page_number,
                    "text": normalized_text,
                    "font_size": font_size,
                    "bbox": self._estimated_bbox(
                        normalized_text,
                        font_size=font_size,
                        x=current_origin[0],
                        y=current_origin[1],
                        page_height=page_height,
                        page_width=page_width,
                        width_scale=0.32,
                    ),
                }
            )
            current_parts = []
            current_font_sizes = []
            current_origin = None

        for match in operations:
            token = match.group(0)
            if token.endswith(b" Tf"):
                font_size_match = re.search(rb"/F\d+\s+([-\d.]+)\s+Tf$", token)
                if font_size_match:
                    current_font_size = float(font_size_match.group(1))
                continue

            if token.endswith(b" Tm"):
                position = self._tm_position(token)
                if position is None:
                    continue
                if self._starts_new_positioned_block(
                    current_origin,
                    position,
                    current_parts=current_parts,
                    font_size=current_font_size,
                    page_width=page_width,
                ):
                    flush_current()
                current_cursor = position
                if current_origin is None:
                    current_origin = position
                continue

            if token.endswith(b" Td"):
                if current_cursor is None:
                    continue
                position = self._td_position(token, current_cursor)
                if position is None:
                    continue
                if self._starts_new_positioned_block(
                    current_origin,
                    position,
                    current_parts=current_parts,
                    font_size=current_font_size,
                    page_width=page_width,
                ):
                    flush_current()
                current_cursor = position
                if current_origin is None:
                    current_origin = position
                continue

            text = self._extract_text_token(token)
            if not text:
                continue
            if current_cursor is None:
                current_cursor = (0.0, 0.0)
            if current_origin is None:
                current_origin = current_cursor
            current_parts.append(text)
            current_font_sizes.append(current_font_size)

        flush_current()
        return blocks

    def _tm_position(self, token: bytes) -> tuple[float, float] | None:
        match = re.search(rb"([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm$", token)
        if not match:
            return None
        return float(match.group(5)), float(match.group(6))

    def _td_position(
        self,
        token: bytes,
        current_cursor: tuple[float, float],
    ) -> tuple[float, float] | None:
        match = re.search(rb"([-\d.]+)\s+([-\d.]+)\s+Td$", token)
        if not match:
            return None
        return current_cursor[0] + float(match.group(1)), current_cursor[1] + float(match.group(2))

    def _extract_text_token(self, token: bytes) -> str:
        stripped = token.rstrip()
        if stripped.endswith(b"Tj"):
            match = re.search(rb"\((.*?)(?<!\\)\)\s*Tj$", token, re.S)
            if not match:
                return ""
            return self._decode_pdf_string(match.group(1))
        if stripped.endswith(b"TJ"):
            parts = [
                self._decode_pdf_string(match)
                for match in re.findall(rb"\((.*?)(?<!\\)\)", token, re.S)
            ]
            return " ".join(part for part in parts if part)
        return ""

    def _starts_new_positioned_block(
        self,
        current_origin: tuple[float, float] | None,
        next_position: tuple[float, float],
        *,
        current_parts: list[str],
        font_size: float,
        page_width: float,
    ) -> bool:
        if current_origin is None or not current_parts:
            return False

        vertical_delta = abs(next_position[1] - current_origin[1])
        if vertical_delta >= max(6.0, font_size * 0.75):
            return True

        current_text = _normalize_text(" ".join(current_parts))
        horizontal_delta = abs(next_position[0] - current_origin[0])
        if (
            len(current_text) >= 60
            and horizontal_delta >= page_width * 0.3
            and (
                (current_origin[0] <= page_width * 0.32 and next_position[0] >= page_width * 0.45)
                or (current_origin[0] >= page_width * 0.45 and next_position[0] <= page_width * 0.32)
            )
        ):
            return True
        return False

    def _estimated_bbox(
        self,
        text: str,
        *,
        font_size: float,
        x: float,
        y: float,
        page_height: float,
        page_width: float,
        width_scale: float,
    ) -> tuple[float, float, float, float]:
        estimated_width = max(font_size * width_scale * len(text), font_size * 2)
        top = max(0.0, page_height - y - font_size)
        bottom = min(page_height, top + font_size * 1.2)
        return (x, top, min(x + estimated_width, page_width), bottom)

    def _extract_segment_text(self, segment: bytes) -> str:
        parts = [self._decode_pdf_string(match) for match in re.findall(rb"\((.*?)(?<!\\)\)\s*Tj", segment, re.S)]
        for array_match in re.findall(rb"\[(.*?)\]\s*TJ", segment, re.S):
            parts.extend(self._decode_pdf_string(match) for match in re.findall(rb"\((.*?)(?<!\\)\)", array_match, re.S))
        return " ".join(part for part in parts if part)

    def _extract_literal_string(self, body: bytes, key: bytes) -> str | None:
        match = re.search(key + rb"\s*\((.*?)(?<!\\)\)", body, re.S)
        if not match:
            return None
        return self._decode_pdf_string(match.group(1))

    def _decode_pdf_string(self, payload: bytes) -> str:
        decoded = payload.decode("latin1")
        decoded = decoded.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
        return decoded.strip()


class DefaultPdfTextExtractor:
    def __init__(
        self,
        extractors: list[PdfTextExtractor] | None = None,
        *,
        image_output_dir: str | Path | None = None,
    ):
        self.extractors = extractors or [
            PyMuPDFTextExtractor(image_output_dir=image_output_dir),
            BasicPdfTextExtractor(),
        ]

    def extract(self, file_path: str | Path) -> PdfExtraction:
        last_error: Exception | None = None
        for extractor in self.extractors:
            try:
                return extractor.extract(file_path)
            except RuntimeError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        raise RuntimeError("No PDF extractor is configured")


class PdfFileProfiler:
    def __init__(self, extractor: PdfTextExtractor | None = None):
        self.extractor = extractor or DefaultPdfTextExtractor()

    def profile(self, file_path: str | Path) -> PdfFileProfile:
        extraction = self.extractor.extract(file_path)
        return self.profile_from_extraction(extraction)

    def profile_from_extraction(self, extraction: PdfExtraction) -> PdfFileProfile:
        page_count_hint = int(extraction.metadata.get("page_count_hint", 0) or 0)
        page_count = max(len(extraction.pages), page_count_hint)
        extractor_kind = str(extraction.metadata.get("pdf_extractor") or "").strip() or None
        if page_count == 0:
            return PdfFileProfile(
                pdf_kind="scanned_pdf",
                page_count=0,
                has_extractable_text=False,
                outline_present=bool(extraction.outline_entries),
                layout_risk="high",
                ocr_required=True,
                extractor_kind=extractor_kind,
            )

        text_density = [sum(len(_normalize_text(block.text)) for block in page.blocks) for page in extraction.pages]
        pages_with_text = sum(1 for density in text_density if density >= 32)
        has_extractable_text = pages_with_text > 0
        text_ratio = pages_with_text / page_count
        total_blocks = sum(len(page.blocks) for page in extraction.pages)
        total_spans = sum(block.span_count for page in extraction.pages for block in page.blocks)
        multi_column_pages: list[int] = []
        fragment_pages: list[int] = []
        suspicious_pages: list[int] = []
        for page in extraction.pages:
            has_multi_column = _page_has_multi_column_signature(page)
            has_fragment = _page_has_column_fragment_signature(page)
            if has_multi_column:
                multi_column_pages.append(page.page_number)
            if has_fragment:
                fragment_pages.append(page.page_number)
            if has_multi_column or has_fragment:
                suspicious_pages.append(page.page_number)

        if not has_extractable_text or text_ratio < 0.2:
            pdf_kind = "scanned_pdf"
            ocr_required = True
        elif text_ratio < 0.9:
            pdf_kind = "mixed_pdf"
            ocr_required = True
        else:
            pdf_kind = "text_pdf"
            ocr_required = False

        fragment_ratio = (len(fragment_pages) / page_count) if page_count else 0.0
        trailing_reference_page_count = _trailing_reference_page_count(extraction.pages)
        first_page = extraction.pages[0] if extraction.pages else None
        first_page_title_signal = bool(
            first_page is not None
            and (
                _page_has_centered_title_signal(first_page)
                or _page_has_title_overlap_signal(first_page, extraction.title)
            )
        )
        early_page_title_signal = _early_page_has_title_signal(extraction.pages, extraction.title, window=2)
        single_column_pymupdf_academic_signal = bool(
            extractor_kind == "pymupdf"
            and first_page is not None
            and len(multi_column_pages) <= 1
            and trailing_reference_page_count >= 2
            and _page_has_single_column_academic_first_page_signal(first_page, extraction.title)
        )
        academic_paper_candidate = bool(
            pdf_kind == "text_pdf"
            and page_count <= 24
            and trailing_reference_page_count >= 1
            and (
                (
                    extractor_kind == "basic"
                    and len(suspicious_pages) >= 2
                    and first_page_title_signal
                )
                or (
                    extractor_kind == "pymupdf"
                    and (
                        len(multi_column_pages) >= 2
                        or bool(extraction.outline_entries)
                        or single_column_pymupdf_academic_signal
                    )
                    and (early_page_title_signal or bool(extraction.title))
                )
            )
        )
        basic_fragment_only_layout = bool(
            extractor_kind == "basic"
            and pdf_kind == "text_pdf"
            and not multi_column_pages
            and len(fragment_pages) >= 5
            and page_count >= 40
            and fragment_ratio <= 0.4
        )
        outlined_localized_multi_column_book = bool(
            pdf_kind == "text_pdf"
            and not academic_paper_candidate
            and extractor_kind == "pymupdf"
            and bool(extraction.outline_entries)
            and page_count >= 80
            and len(suspicious_pages) >= 2
            and not fragment_pages
            and (len(suspicious_pages) / page_count) <= 0.12
            and len(suspicious_pages) <= max(12, min(40, int(page_count * 0.15)))
        )

        if pdf_kind == "scanned_pdf":
            layout_risk = "high"
        elif pdf_kind == "mixed_pdf":
            layout_risk = "high" if suspicious_pages else "medium"
        elif academic_paper_candidate:
            layout_risk = "medium"
        elif outlined_localized_multi_column_book:
            layout_risk = "medium"
        elif basic_fragment_only_layout:
            layout_risk = "medium"
        elif len(suspicious_pages) >= 2:
            layout_risk = "high"
        elif suspicious_pages:
            layout_risk = "medium"
        else:
            layout_risk = "low"

        return PdfFileProfile(
            pdf_kind=pdf_kind,
            page_count=page_count,
            has_extractable_text=has_extractable_text,
            outline_present=bool(extraction.outline_entries),
            layout_risk=layout_risk,
            ocr_required=ocr_required,
            extractor_kind=extractor_kind,
            average_text_density=_safe_mean([float(value) for value in text_density]),
            average_span_count=(total_spans / total_blocks) if total_blocks else 0.0,
            multi_column_page_count=len(multi_column_pages),
            fragment_page_count=len(fragment_pages),
            suspicious_page_numbers=suspicious_pages,
            recovery_lane=(
                "academic_paper"
                if academic_paper_candidate
                else "outlined_book"
                if outlined_localized_multi_column_book
                else None
            ),
            trailing_reference_page_count=trailing_reference_page_count,
            academic_paper_candidate=academic_paper_candidate,
        )
