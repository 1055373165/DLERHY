"""Surya-backed OCR re-extraction adapter (PDF v2 M2.3b).

Wraps the existing `OcrPdfTextExtractor` (Surya subprocess runner) into
the `OcrReextractionAdapter` contract so sanity-failed pages can actually
be repaired in production.

Design choices:

  * **Subset PDF, not whole-doc OCR.** For a 500-page book with 3 failed
    pages, running Surya on the whole doc is wasteful and slow. We build
    a subset PDF containing only the failed pages via PyMuPDF
    `doc.select([...])` and invoke Surya on it. This makes OCR cost
    proportional to the number of failed pages, not document length.

  * **Bbox matching in relative coordinates.** The request's bbox is in
    PDF user-space points; Surya returns bboxes in image-pixel
    coordinates at whatever DPI its rasterizer uses. We normalize both
    to [0, 1] using the respective page dimensions and match by
    containment/overlap. This sidesteps DPI-guessing entirely.

  * **Hard cost cap.** `max_failed_pages_per_doc` defaults to 20. If a
    document somehow has more sanity-failed pages than that, we return
    `{}` and let the caller decide — either the whole document is too
    corrupted for repair (and should be routed to OCR wholesale by the
    bootstrap layer) or the sanity gate is producing false positives
    (in which case blasting Surya at 100+ pages is not the right fix).

  * **Fault tolerance.** Any Surya subprocess failure, subset-PDF
    failure, or matching failure degrades to "no replacements" for the
    affected page. The calling wiring preserves the original block text
    when no replacement is returned. We never crash the pipeline here.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from book_agent.domain.structure.ocr_reextraction import (
    OcrReextractionRequest,
)

if TYPE_CHECKING:
    from book_agent.domain.structure.ocr import OcrPdfTextExtractor
    from book_agent.domain.structure.pdf import PdfExtraction, PdfTextBlock


# Bbox overlap threshold for matching a request bbox to a Surya block bbox.
# If the intersection of the two (in normalized coordinates) covers at
# least this fraction of the request bbox's area, we consider the Surya
# block to belong to the request. Tuned low because sanity-failed blocks'
# original bboxes often come from corrupted text-layer boundaries that
# don't cleanly align with the visually-correct Surya blocks.
_BBOX_OVERLAP_THRESHOLD: float = 0.25


@dataclass(slots=True, frozen=True)
class SuryaAdapterMetrics:
    """Observability for a single `reextract_blocks` invocation.

    Telemetry consumers can sum these across documents to track Surya
    usage and cost-guard hits.
    """

    requests_seen: int
    pages_requested: int
    pages_ocrd: int
    replacements_returned: int
    cost_guard_tripped: bool
    error: str | None


class SuryaOcrReextractionAdapter:
    """Concrete adapter backed by Surya OCR via the existing integration."""

    def __init__(
        self,
        *,
        extractor: "OcrPdfTextExtractor | None" = None,
        max_failed_pages_per_doc: int = 20,
        bbox_overlap_threshold: float = _BBOX_OVERLAP_THRESHOLD,
    ) -> None:
        # Lazy-import so modules that never use Surya don't pay the cost
        # of loading its deps (subprocess / transformers / ...).
        if extractor is None:
            from book_agent.domain.structure.ocr import OcrPdfTextExtractor as _Ext

            extractor = _Ext()
        self._extractor = extractor
        self._max_failed_pages = max(1, int(max_failed_pages_per_doc))
        self._overlap_threshold = float(bbox_overlap_threshold)
        self._last_metrics: SuryaAdapterMetrics | None = None

    @property
    def last_metrics(self) -> SuryaAdapterMetrics | None:
        """Most recent invocation's telemetry (None before first call)."""
        return self._last_metrics

    def reextract_blocks(
        self,
        pdf_path: str,
        requests: list[OcrReextractionRequest],
    ) -> dict[str, str]:
        requests_seen = len(requests)
        if not requests:
            self._last_metrics = SuryaAdapterMetrics(
                requests_seen=0,
                pages_requested=0,
                pages_ocrd=0,
                replacements_returned=0,
                cost_guard_tripped=False,
                error=None,
            )
            return {}

        # Group requests by page.
        pages_requested = sorted({int(req.page_number) for req in requests if req.page_number > 0})

        # Cost guard.
        if len(pages_requested) > self._max_failed_pages:
            self._last_metrics = SuryaAdapterMetrics(
                requests_seen=requests_seen,
                pages_requested=len(pages_requested),
                pages_ocrd=0,
                replacements_returned=0,
                cost_guard_tripped=True,
                error=None,
            )
            return {}

        pdf_path_obj = Path(pdf_path)
        try:
            pdf_page_dims = self._page_dimensions(pdf_path_obj, pages_requested)
        except Exception as exc:
            self._last_metrics = SuryaAdapterMetrics(
                requests_seen=requests_seen,
                pages_requested=len(pages_requested),
                pages_ocrd=0,
                replacements_returned=0,
                cost_guard_tripped=False,
                error=f"page_dim_read_failed:{type(exc).__name__}",
            )
            return {}

        try:
            subset_path, pages_in_order = self._build_subset_pdf(
                pdf_path_obj, pages_requested
            )
        except Exception as exc:
            self._last_metrics = SuryaAdapterMetrics(
                requests_seen=requests_seen,
                pages_requested=len(pages_requested),
                pages_ocrd=0,
                replacements_returned=0,
                cost_guard_tripped=False,
                error=f"subset_build_failed:{type(exc).__name__}",
            )
            return {}

        try:
            try:
                surya_extraction = self._extractor.extract(str(subset_path))
            except Exception as exc:
                self._last_metrics = SuryaAdapterMetrics(
                    requests_seen=requests_seen,
                    pages_requested=len(pages_requested),
                    pages_ocrd=0,
                    replacements_returned=0,
                    cost_guard_tripped=False,
                    error=f"surya_extract_failed:{type(exc).__name__}",
                )
                return {}
        finally:
            subset_path.unlink(missing_ok=True)

        replacements = self._match_requests_to_surya(
            requests=requests,
            pages_in_order=pages_in_order,
            pdf_page_dims=pdf_page_dims,
            surya_extraction=surya_extraction,
        )
        self._last_metrics = SuryaAdapterMetrics(
            requests_seen=requests_seen,
            pages_requested=len(pages_requested),
            pages_ocrd=len(surya_extraction.pages),
            replacements_returned=len(replacements),
            cost_guard_tripped=False,
            error=None,
        )
        return replacements

    def _page_dimensions(
        self,
        pdf_path: Path,
        page_numbers: Iterable[int],
    ) -> dict[int, tuple[float, float]]:
        import fitz  # lazy — only when adapter actually runs

        dims: dict[int, tuple[float, float]] = {}
        doc = fitz.open(str(pdf_path))
        try:
            page_count = doc.page_count
            for page_number in page_numbers:
                idx = page_number - 1
                if idx < 0 or idx >= page_count:
                    continue
                page = doc.load_page(idx)
                dims[page_number] = (float(page.rect.width), float(page.rect.height))
        finally:
            doc.close()
        return dims

    def _build_subset_pdf(
        self,
        pdf_path: Path,
        page_numbers: list[int],
    ) -> tuple[Path, list[int]]:
        """Write a temporary PDF containing just `page_numbers`.

        Returns (subset_path, pages_in_order). `pages_in_order` is the
        list of ORIGINAL 1-indexed page numbers in the order they appear
        in the subset PDF (surya_extraction.pages[i] corresponds to
        pages_in_order[i]).
        """
        import fitz  # lazy

        tmp = tempfile.NamedTemporaryFile(
            prefix="book-agent-surya-subset-",
            suffix=".pdf",
            delete=False,
        )
        subset_path = Path(tmp.name)
        tmp.close()

        src = fitz.open(str(pdf_path))
        subset = fitz.open()
        pages_in_order: list[int] = []
        try:
            total = src.page_count
            for page_number in page_numbers:
                idx = page_number - 1
                if idx < 0 or idx >= total:
                    continue
                subset.insert_pdf(src, from_page=idx, to_page=idx)
                pages_in_order.append(page_number)
            subset.save(str(subset_path))
        finally:
            subset.close()
            src.close()
        return subset_path, pages_in_order

    def _match_requests_to_surya(
        self,
        *,
        requests: list[OcrReextractionRequest],
        pages_in_order: list[int],
        pdf_page_dims: dict[int, tuple[float, float]],
        surya_extraction: "PdfExtraction",
    ) -> dict[str, str]:
        # Build: original_page → Surya PdfPage
        surya_by_original: dict[int, "PdfPage"] = {}
        for surya_page, original_page in zip(surya_extraction.pages, pages_in_order):
            surya_by_original[original_page] = surya_page

        replacements: dict[str, str] = {}
        for req in requests:
            surya_page = surya_by_original.get(req.page_number)
            if surya_page is None or not surya_page.blocks:
                continue
            pdf_dims = pdf_page_dims.get(req.page_number)
            if not pdf_dims or pdf_dims[0] <= 0 or pdf_dims[1] <= 0:
                continue
            surya_dims = (float(surya_page.width), float(surya_page.height))
            if surya_dims[0] <= 0 or surya_dims[1] <= 0:
                continue
            text = self._match_bbox(
                request_bbox=req.bbox,
                pdf_dims=pdf_dims,
                surya_dims=surya_dims,
                surya_blocks=surya_page.blocks,
            )
            if text:
                replacements[req.block_anchor] = text
        return replacements

    def _match_bbox(
        self,
        *,
        request_bbox: tuple[float, float, float, float],
        pdf_dims: tuple[float, float],
        surya_dims: tuple[float, float],
        surya_blocks: list["PdfTextBlock"],
    ) -> str:
        """Return concatenated Surya block text whose bboxes overlap enough
        with `request_bbox` (both normalised to [0, 1]).

        Fallback: if no Surya block overlaps the request bbox (common when
        the request's original bbox was wildly wrong due to corrupted
        text-layer), return the full page's OCR text as a safer guess
        than leaving the block unchanged.
        """
        req_norm = _normalize_bbox(request_bbox, pdf_dims)
        if req_norm is None:
            # Degenerate request bbox; fall back to full page text.
            return _concat_block_text(surya_blocks)

        matched_texts: list[str] = []
        for block in surya_blocks:
            block_norm = _normalize_bbox(block.bbox, surya_dims)
            if block_norm is None:
                continue
            if _overlap_fraction(req_norm, block_norm) >= self._overlap_threshold:
                matched_texts.append(block.text)

        if not matched_texts:
            return _concat_block_text(surya_blocks)
        return "\n\n".join(t for t in matched_texts if t)


def _normalize_bbox(
    bbox: tuple[float, float, float, float] | list[float],
    dims: tuple[float, float],
) -> tuple[float, float, float, float] | None:
    if bbox is None or len(bbox) < 4:
        return None
    width, height = dims
    if width <= 0 or height <= 0:
        return None
    x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    if x1 <= x0 or y1 <= y0:
        return None
    return (
        max(0.0, min(1.0, x0 / width)),
        max(0.0, min(1.0, y0 / height)),
        max(0.0, min(1.0, x1 / width)),
        max(0.0, min(1.0, y1 / height)),
    )


def _overlap_fraction(
    target: tuple[float, float, float, float],
    candidate: tuple[float, float, float, float],
) -> float:
    """Intersection area divided by TARGET area, both in [0,1]-normalized space."""
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


def _concat_block_text(blocks: list["PdfTextBlock"]) -> str:
    pieces = [b.text.strip() for b in blocks if b.text and b.text.strip()]
    return "\n\n".join(pieces)
