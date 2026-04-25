"""TATR (Table Transformer) adapter — TATR-a (PDF v2 M3.2 follow-up).

Microsoft Table Transformer (`microsoft/table-transformer-detection` and
`microsoft/table-transformer-structure-recognition`) is the de-facto SoTA
for unstructured table recognition. This module wraps it behind the
`PageImageTableExtractor` protocol so the rest of the pipeline can call
TATR without depending directly on `torch` / `transformers` / `PIL`.

Layered exactly like M2.3a/M2.3b:

  * **TATR-a (THIS module)** — adapter shell: protocol, request shape,
    NoOp default, cost-guard, bbox→text mapping, graceful degradation
    when ML deps are absent. Includes an overridable
    `_run_tatr_inference` hook that real implementations fill in.

  * **TATR-b (NEXT)** — production weights: implements
    `_run_tatr_inference` against transformers.AutoModelForObjectDetection.
    Lazy-loaded inside the method, never at module top-level, so installs
    without ML deps don't crash.

  * **TATR-c (AFTER)** — wire into `ParseService.parse` so each PDF's
    detected table blocks get TATR-recovered markdown, gated by env flag.

The text-only `TableExtractorAdapter` from `services.table_extractor`
remains in place for the heuristic path. TATR's protocol is **separate**
because its inputs are page images + PDF coordinates, not text strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

from book_agent.services.table_extractor import (
    TableStructure,
    _render_markdown_table,
)


# A single rectangle in PDF user-space (points). Top-left origin
# matches PyMuPDF's `page.rect` convention.
PdfBbox = tuple[float, float, float, float]


@dataclass(slots=True, frozen=True)
class PageTableExtractionRequest:
    """One per page (or per page-region) the caller wants TATR to scan.

    `page_text_blocks` carries pre-extracted PyMuPDF text blocks so the
    adapter can map TATR's detected cell bboxes back to text without
    re-opening the PDF. Each entry is `(bbox, text)` in PDF user-space.
    """

    pdf_path: str
    page_number: int  # 1-indexed
    page_dimensions: tuple[float, float]  # (width_pt, height_pt)
    region_bbox: PdfBbox | None = None  # None = scan full page
    page_text_blocks: tuple[tuple[PdfBbox, str], ...] = ()


@dataclass(slots=True, frozen=True)
class TatrCell:
    """A single cell as returned by the structure recognizer."""

    row: int
    column: int
    bbox: PdfBbox  # in PDF user-space
    is_header: bool = False
    text: str = ""


@dataclass(slots=True, frozen=True)
class TatrTable:
    """One TATR-detected table on a page."""

    bbox: PdfBbox  # outer table bbox in PDF user-space
    cells: tuple[TatrCell, ...]
    confidence: float


@dataclass(slots=True)
class TatrAdapterMetrics:
    """Per-document telemetry."""

    pages_seen: int = 0
    tables_detected: int = 0
    tables_returned: int = 0
    cost_guard_tripped: bool = False
    deps_missing: bool = False
    error: str | None = None


class PageImageTableExtractor(Protocol):
    """Page-level / region-level table extractor protocol.

    A real implementation reads the PDF, renders the page to an image,
    runs detection + structure-recognition, and maps cells back into
    PDF coordinate space. Returning an empty list is the "nothing
    confidently recovered" signal — callers fall back to the heuristic
    `TableExtractorAdapter` from `services.table_extractor`.
    """

    def extract(
        self, request: PageTableExtractionRequest
    ) -> list[TableStructure]:
        ...


class NoOpPageImageTableExtractor:
    """Default — returns no tables.

    Ships as the safe default so M3 can land without forcing ML deps.
    """

    def extract(
        self, request: PageTableExtractionRequest
    ) -> list[TableStructure]:
        return []


# --- Cell-text mapping helpers ---


_CELL_OVERLAP_THRESHOLD: Final[float] = 0.4


def _bbox_overlap_fraction(target: PdfBbox, candidate: PdfBbox) -> float:
    ix0 = max(target[0], candidate[0])
    iy0 = max(target[1], candidate[1])
    ix1 = min(target[2], candidate[2])
    iy1 = min(target[3], candidate[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    target_area = (target[2] - target[0]) * (target[3] - target[1])
    if target_area <= 0.0:
        return 0.0
    return inter / target_area


def map_cell_text(
    cell_bbox: PdfBbox,
    page_text_blocks: list[tuple[PdfBbox, str]] | tuple[tuple[PdfBbox, str], ...],
    *,
    overlap_threshold: float = _CELL_OVERLAP_THRESHOLD,
) -> str:
    """Concatenate text from PyMuPDF blocks whose bbox sufficiently
    overlaps the cell. Empty string when no block lands inside.
    """
    pieces: list[str] = []
    for bbox, text in page_text_blocks:
        if not text or not text.strip():
            continue
        if _bbox_overlap_fraction(bbox, cell_bbox) >= overlap_threshold:
            pieces.append(text.strip())
    return " ".join(pieces).strip()


def tatr_table_to_markdown(table: TatrTable) -> str:
    """Render a TATR table into a markdown grid by row/column indices.

    Cells with `is_header=True` make up the header row when their row
    index is the minimum across the table; otherwise headers are
    rendered inline as bolded text.
    """
    if not table.cells:
        return ""
    rows = sorted({cell.row for cell in table.cells})
    cols = sorted({cell.column for cell in table.cells})
    grid: dict[tuple[int, int], str] = {}
    for cell in table.cells:
        grid[(cell.row, cell.column)] = cell.text
    cell_rows: list[tuple[str, ...]] = []
    for r in rows:
        row_cells = tuple(grid.get((r, c), "") for c in cols)
        cell_rows.append(row_cells)
    return _render_markdown_table(cell_rows)


# --- TATR adapter ---


class TatrTableExtractor:
    """Adapter shell for the Microsoft Table Transformer.

    The forward pass (`_run_tatr_inference`) is intentionally a hook —
    TATR-b fills it in once `torch`/`transformers`/`PIL` land in the
    install bundle. Until then the shell still exercises:

      * Cost-guard (per-document table cap)
      * Lazy-load failure → graceful degradation
      * Cell-text mapping back from page text blocks
      * Markdown rendering

    Tests inject a fake `_run_tatr_inference` to verify all of the
    above without loading 100MB of model weights.
    """

    def __init__(
        self,
        *,
        max_tables_per_doc: int = 50,
        dpi: int = 200,
        cell_overlap_threshold: float = _CELL_OVERLAP_THRESHOLD,
    ) -> None:
        self._max_tables = max(1, int(max_tables_per_doc))
        self._dpi = int(dpi)
        self._cell_overlap_threshold = float(cell_overlap_threshold)
        self._tables_returned_total = 0
        self._models_loaded = False
        self._models_unavailable = False
        self._last_metrics = TatrAdapterMetrics()

    @property
    def last_metrics(self) -> TatrAdapterMetrics:
        return self._last_metrics

    def reset_metrics(self) -> None:
        self._last_metrics = TatrAdapterMetrics()
        self._tables_returned_total = 0

    # --- Public protocol implementation ---

    def extract(
        self, request: PageTableExtractionRequest
    ) -> list[TableStructure]:
        self._last_metrics.pages_seen += 1

        if self._tables_returned_total >= self._max_tables:
            self._last_metrics.cost_guard_tripped = True
            return []

        if not self._ensure_models_loaded():
            self._last_metrics.deps_missing = True
            return []

        try:
            tatr_tables = self._run_tatr_inference(request)
        except Exception as exc:  # pragma: no cover - defensive
            self._last_metrics.error = f"tatr_inference_failed:{type(exc).__name__}"
            return []

        self._last_metrics.tables_detected += len(tatr_tables)
        if not tatr_tables:
            return []

        # Map cells to text + render markdown.
        results: list[TableStructure] = []
        for tt in tatr_tables:
            if self._tables_returned_total >= self._max_tables:
                self._last_metrics.cost_guard_tripped = True
                break
            populated_cells: list[TatrCell] = []
            for cell in tt.cells:
                text = map_cell_text(
                    cell.bbox,
                    request.page_text_blocks,
                    overlap_threshold=self._cell_overlap_threshold,
                )
                populated_cells.append(
                    TatrCell(
                        row=cell.row,
                        column=cell.column,
                        bbox=cell.bbox,
                        is_header=cell.is_header,
                        text=text or cell.text,
                    )
                )
            populated = TatrTable(
                bbox=tt.bbox,
                cells=tuple(populated_cells),
                confidence=tt.confidence,
            )
            md = tatr_table_to_markdown(populated)
            if not md:
                continue
            # Drop tables that produced markdown but every cell text is
            # blank — this indicates the cell-text mapping found nothing
            # to anchor on (PDF has only graphics in this region, or the
            # text-blocks list was empty). Better to fall through to the
            # heuristic adapter than emit an empty grid.
            if not any(cell.text.strip() for cell in populated.cells):
                continue
            cells_grid = _cells_to_grid(populated)
            results.append(
                TableStructure(
                    cells=cells_grid,
                    markdown=md,
                    confidence=populated.confidence,
                )
            )
            self._tables_returned_total += 1

        self._last_metrics.tables_returned += len(results)
        return results

    # --- Hooks for TATR-b / tests ---

    def _ensure_models_loaded(self) -> bool:
        """Lazy-load the TATR weights via transformers.

        Returns False when ML deps are unavailable; the caller degrades
        gracefully. Subclasses (FakeTatr in tests, future variants)
        bypass this by setting `_models_loaded=True` directly.
        """
        if self._models_loaded:
            return True
        if self._models_unavailable:
            return False
        try:
            import importlib.util

            for mod in ("torch", "transformers", "PIL"):
                if importlib.util.find_spec(mod) is None:
                    self._models_unavailable = True
                    return False
            # All deps present — TATR-b loads real weights.
            from transformers import (  # type: ignore
                AutoImageProcessor,
                TableTransformerForObjectDetection,
            )

            self._image_processor = AutoImageProcessor.from_pretrained(
                "microsoft/table-transformer-detection"
            )
            self._detection_model = (
                TableTransformerForObjectDetection.from_pretrained(
                    "microsoft/table-transformer-detection"
                )
            )
            self._structure_image_processor = (
                AutoImageProcessor.from_pretrained(
                    "microsoft/table-transformer-structure-recognition"
                )
            )
            self._structure_model = (
                TableTransformerForObjectDetection.from_pretrained(
                    "microsoft/table-transformer-structure-recognition"
                )
            )
            import torch  # type: ignore

            self._torch = torch
            self._models_loaded = True
            return True
        except Exception as exc:  # pragma: no cover - hard to test without real deps
            self._models_unavailable = True
            self._last_metrics.error = (
                f"tatr_model_load_failed:{type(exc).__name__}"
            )
            return False

    def _run_tatr_inference(
        self, request: PageTableExtractionRequest
    ) -> list[TatrTable]:
        """Run TATR detection + structure recognition for the page.

        TATR-b implementation:
          1. Render the page (or region_bbox) via PyMuPDF at `self._dpi`.
          2. Detection model → list of (image-space) table bboxes.
          3. For each table: crop the image and run the structure
             recognizer → cells with bboxes.
          4. Convert image-space bboxes back to PDF user-space.

        Subclasses can override this method to inject scripted output
        (see `FakeTatr` in tests) without loading any weights — that is
        the canonical way to unit-test routing/cost-guard logic.
        """
        if not self._models_loaded:
            return []

        try:
            import fitz  # type: ignore
            from PIL import Image  # type: ignore
        except ImportError:  # pragma: no cover
            return []

        torch = self._torch  # type: ignore

        # Open page and render.
        doc = fitz.open(request.pdf_path)
        try:
            if request.page_number < 1 or request.page_number > doc.page_count:
                return []
            page = doc.load_page(request.page_number - 1)
            zoom = float(self._dpi) / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            clip = (
                fitz.Rect(*request.region_bbox)
                if request.region_bbox is not None
                else None
            )
            pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
            image = Image.frombytes(
                "RGB", (pix.width, pix.height), pix.samples
            )
            image_origin_x = request.region_bbox[0] if request.region_bbox else 0.0
            image_origin_y = request.region_bbox[1] if request.region_bbox else 0.0
        finally:
            doc.close()

        # 1. Detection.
        det_inputs = self._image_processor(images=image, return_tensors="pt")
        with torch.no_grad():
            det_outputs = self._detection_model(**det_inputs)
        target_sizes = torch.tensor([image.size[::-1]])  # (h, w)
        det_results = self._image_processor.post_process_object_detection(
            det_outputs, threshold=0.7, target_sizes=target_sizes
        )[0]

        tables: list[TatrTable] = []
        for score, label, box in zip(
            det_results["scores"].tolist(),
            det_results["labels"].tolist(),
            det_results["boxes"].tolist(),
        ):
            if self._detection_model.config.id2label.get(int(label), "") != "table":
                continue
            x0_img, y0_img, x1_img, y1_img = box
            table_crop = image.crop((x0_img, y0_img, x1_img, y1_img))

            # 2. Structure recognition on the crop.
            sr_inputs = self._structure_image_processor(
                images=table_crop, return_tensors="pt"
            )
            with torch.no_grad():
                sr_outputs = self._structure_model(**sr_inputs)
            sr_target_sizes = torch.tensor([table_crop.size[::-1]])
            sr_results = (
                self._structure_image_processor.post_process_object_detection(
                    sr_outputs, threshold=0.6, target_sizes=sr_target_sizes
                )[0]
            )

            cells = self._structure_results_to_cells(
                sr_results=sr_results,
                table_origin_image=(x0_img, y0_img),
                image_origin_pdf=(image_origin_x, image_origin_y),
                zoom=zoom,
                structure_id2label=self._structure_model.config.id2label,
            )
            if not cells:
                continue
            # Outer table bbox in PDF user-space.
            table_bbox = self._image_to_pdf_bbox(
                (x0_img, y0_img, x1_img, y1_img),
                image_origin=(image_origin_x, image_origin_y),
                zoom=zoom,
            )
            tables.append(
                TatrTable(
                    bbox=table_bbox,
                    cells=tuple(cells),
                    confidence=float(score),
                )
            )
        return tables

    def _structure_results_to_cells(
        self,
        *,
        sr_results,
        table_origin_image: tuple[float, float],
        image_origin_pdf: tuple[float, float],
        zoom: float,
        structure_id2label: dict[int, str],
    ) -> list[TatrCell]:
        """Convert structure-recognition raw output to row/col-indexed cells.

        TATR's structure model returns rows, columns, and headers as
        separate detections. We intersect rows × columns to produce
        cell rectangles, then test header membership for `is_header`.
        """
        rows: list[tuple[float, float, float, float]] = []
        columns: list[tuple[float, float, float, float]] = []
        header_rects: list[tuple[float, float, float, float]] = []
        for label, box in zip(
            sr_results["labels"].tolist(),
            sr_results["boxes"].tolist(),
        ):
            name = structure_id2label.get(int(label), "")
            if name == "table row":
                rows.append(tuple(box))
            elif name == "table column":
                columns.append(tuple(box))
            elif name == "table column header":
                header_rects.append(tuple(box))

        if not rows or not columns:
            return []

        rows.sort(key=lambda b: b[1])
        columns.sort(key=lambda b: b[0])

        cells: list[TatrCell] = []
        for r_idx, row_box in enumerate(rows):
            for c_idx, col_box in enumerate(columns):
                cell_x0 = max(row_box[0], col_box[0])
                cell_y0 = max(row_box[1], col_box[1])
                cell_x1 = min(row_box[2], col_box[2])
                cell_y1 = min(row_box[3], col_box[3])
                if cell_x1 <= cell_x0 or cell_y1 <= cell_y0:
                    continue
                # Translate from crop-space → page-image-space → PDF.
                abs_image_bbox = (
                    table_origin_image[0] + cell_x0,
                    table_origin_image[1] + cell_y0,
                    table_origin_image[0] + cell_x1,
                    table_origin_image[1] + cell_y1,
                )
                pdf_bbox = self._image_to_pdf_bbox(
                    abs_image_bbox,
                    image_origin=image_origin_pdf,
                    zoom=zoom,
                )
                is_header = any(
                    _bbox_overlap_fraction(
                        (cell_x0, cell_y0, cell_x1, cell_y1), header_rect
                    )
                    >= 0.5
                    for header_rect in header_rects
                )
                cells.append(
                    TatrCell(
                        row=r_idx,
                        column=c_idx,
                        bbox=pdf_bbox,
                        is_header=is_header,
                    )
                )
        return cells

    def _image_to_pdf_bbox(
        self,
        image_bbox: tuple[float, float, float, float],
        *,
        image_origin: tuple[float, float],
        zoom: float,
    ) -> PdfBbox:
        """Inverse of `fitz.Matrix(zoom, zoom)` plus the region_bbox offset.

        `image_origin` is the PDF-space top-left of the rendered image
        (origin (0,0) for a full-page render, region_bbox top-left for
        a clipped render).
        """
        x0_pdf = image_origin[0] + image_bbox[0] / zoom
        y0_pdf = image_origin[1] + image_bbox[1] / zoom
        x1_pdf = image_origin[0] + image_bbox[2] / zoom
        y1_pdf = image_origin[1] + image_bbox[3] / zoom
        return (x0_pdf, y0_pdf, x1_pdf, y1_pdf)


def _cells_to_grid(table: TatrTable) -> tuple[tuple[str, ...], ...]:
    if not table.cells:
        return ()
    rows = sorted({cell.row for cell in table.cells})
    cols = sorted({cell.column for cell in table.cells})
    grid: dict[tuple[int, int], str] = {
        (cell.row, cell.column): cell.text for cell in table.cells
    }
    return tuple(
        tuple(grid.get((r, c), "") for c in cols) for r in rows
    )
