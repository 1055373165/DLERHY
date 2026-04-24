# ruff: noqa: E402
"""Unit tests for SuryaOcrReextractionAdapter (PDF v2 M2.3b).

The real Surya subprocess is never invoked — a fake `OcrPdfTextExtractor`
is injected to drive the matching / cost-guard / error-handling code paths
deterministically. We use a tiny real PDF (the clean_book golden fixture)
only so the subset-PDF construction step has real bytes to slice.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.structure.ocr_reextraction import (
    OcrReextractionRequest,
)
from book_agent.domain.structure.pdf import (
    PdfExtraction,
    PdfPage,
    PdfTextBlock,
)
from book_agent.domain.structure.surya_reextraction import (
    SuryaOcrReextractionAdapter,
    _normalize_bbox,
    _overlap_fraction,
)

from tests.golden_pdfs.fixtures import make_clean_book


def _surya_block(*, text: str, bbox: tuple[float, float, float, float]) -> PdfTextBlock:
    return PdfTextBlock(
        page_number=1,
        block_number=1,
        text=text,
        bbox=bbox,
        line_texts=[text],
        span_count=5,
        line_count=1,
        font_size_min=10.0,
        font_size_max=10.0,
        font_size_avg=10.0,
    )


def _surya_page(page_number: int, blocks: list[PdfTextBlock]) -> PdfPage:
    return PdfPage(
        page_number=page_number,
        width=1000.0,
        height=1400.0,
        blocks=blocks,
        image_blocks=[],
    )


class FakeExtractor:
    """Deterministic stand-in for OcrPdfTextExtractor.

    Given a scripted per-invocation sequence of PdfExtraction results,
    returns them in order. Records the paths it was called with.
    """

    def __init__(self, scripted: list[PdfExtraction]) -> None:
        self._scripted = list(scripted)
        self.paths: list[str] = []

    def extract(self, file_path, *, page_count=None) -> PdfExtraction:
        self.paths.append(str(file_path))
        if not self._scripted:
            raise RuntimeError("no more scripted responses")
        return self._scripted.pop(0)


class FailingExtractor:
    """Simulates a Surya subprocess failure."""

    def extract(self, file_path, *, page_count=None) -> PdfExtraction:
        raise RuntimeError("surya boom")


def _write_pdf(pdf_bytes: bytes) -> Path:
    fh = tempfile.NamedTemporaryFile(prefix="surya-test-", suffix=".pdf", delete=False)
    try:
        fh.write(pdf_bytes)
    finally:
        fh.close()
    return Path(fh.name)


class BboxHelperTests(unittest.TestCase):
    def test_normalize_bbox_valid(self) -> None:
        norm = _normalize_bbox((100.0, 200.0, 300.0, 400.0), (1000.0, 1000.0))
        self.assertEqual(norm, (0.1, 0.2, 0.3, 0.4))

    def test_normalize_bbox_degenerate(self) -> None:
        self.assertIsNone(_normalize_bbox((0.0, 0.0, 0.0, 0.0), (1000.0, 1000.0)))
        self.assertIsNone(_normalize_bbox((50.0, 50.0, 10.0, 10.0), (1000.0, 1000.0)))
        self.assertIsNone(_normalize_bbox((0.0, 0.0, 100.0, 100.0), (0.0, 1000.0)))

    def test_overlap_fraction_full_containment(self) -> None:
        target = (0.1, 0.1, 0.5, 0.5)
        # Candidate fully contains the target.
        candidate = (0.0, 0.0, 1.0, 1.0)
        self.assertAlmostEqual(_overlap_fraction(target, candidate), 1.0, places=3)

    def test_overlap_fraction_no_overlap(self) -> None:
        self.assertEqual(
            _overlap_fraction((0.0, 0.0, 0.2, 0.2), (0.5, 0.5, 0.8, 0.8)),
            0.0,
        )

    def test_overlap_fraction_partial(self) -> None:
        # Target 0.2x0.2 area, half covered by candidate.
        target = (0.0, 0.0, 0.2, 0.2)
        candidate = (0.1, 0.0, 0.3, 0.2)  # overlap = 0.1*0.2 = 0.02; target area = 0.04
        self.assertAlmostEqual(_overlap_fraction(target, candidate), 0.5, places=3)


class SuryaAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pdf_path = _write_pdf(make_clean_book())
        self.addCleanup(lambda: self.pdf_path.unlink(missing_ok=True))

    def _request(
        self,
        *,
        anchor: str,
        page: int,
        bbox: tuple[float, float, float, float],
        current_text: str = "corrupted",
        reason: str = "pua_high",
    ) -> OcrReextractionRequest:
        return OcrReextractionRequest(
            block_anchor=anchor,
            page_number=page,
            bbox=bbox,
            current_text=current_text,
            failure_reason=reason,
        )

    def test_empty_requests_returns_empty(self) -> None:
        adapter = SuryaOcrReextractionAdapter(extractor=FakeExtractor([]))
        out = adapter.reextract_blocks(str(self.pdf_path), [])
        self.assertEqual(out, {})
        metrics = adapter.last_metrics
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics.requests_seen, 0)
        self.assertEqual(metrics.pages_ocrd, 0)
        self.assertFalse(metrics.cost_guard_tripped)

    def test_cost_guard_trips_when_too_many_pages(self) -> None:
        adapter = SuryaOcrReextractionAdapter(
            extractor=FakeExtractor([]),
            max_failed_pages_per_doc=2,
        )
        # 3 unique pages → over the cap.
        requests = [
            self._request(anchor=f"a{i}", page=i, bbox=(0, 0, 100, 100))
            for i in (1, 2, 3)
        ]
        out = adapter.reextract_blocks(str(self.pdf_path), requests)
        self.assertEqual(out, {})
        self.assertTrue(adapter.last_metrics.cost_guard_tripped)
        self.assertEqual(adapter.last_metrics.pages_ocrd, 0)

    def test_surya_failure_degrades_gracefully(self) -> None:
        adapter = SuryaOcrReextractionAdapter(extractor=FailingExtractor())
        requests = [self._request(anchor="a1", page=1, bbox=(72, 72, 500, 200))]
        out = adapter.reextract_blocks(str(self.pdf_path), requests)
        self.assertEqual(out, {})
        metrics = adapter.last_metrics
        self.assertIsNotNone(metrics)
        self.assertIsNotNone(metrics.error)
        self.assertIn("surya_extract_failed", metrics.error)

    def test_happy_path_matches_bbox_and_rewrites(self) -> None:
        # Clean book page 1 has dimensions 612x792 pt. Place the request
        # bbox in the upper half and script Surya to return a matching
        # block in the same relative region.
        scripted = PdfExtraction(
            title=None,
            author=None,
            metadata={"pdf_extractor": "surya_ocr"},
            pages=[
                _surya_page(
                    1,
                    blocks=[
                        _surya_block(
                            text="OCR replacement sentence for the top of page.",
                            bbox=(100.0, 100.0, 900.0, 300.0),
                        ),
                        _surya_block(
                            text="Unrelated block at the bottom.",
                            bbox=(100.0, 1100.0, 900.0, 1350.0),
                        ),
                    ],
                ),
            ],
            outline_entries=[],
        )
        adapter = SuryaOcrReextractionAdapter(
            extractor=FakeExtractor([scripted]),
            bbox_overlap_threshold=0.1,
        )
        # Request bbox in PDF points (~top half of 612x792 page).
        out = adapter.reextract_blocks(
            str(self.pdf_path),
            [self._request(anchor="top", page=1, bbox=(72, 72, 540, 240))],
        )
        self.assertIn("top", out)
        self.assertIn("OCR replacement", out["top"])
        # The unrelated bottom block must NOT be in the top request's text.
        self.assertNotIn("Unrelated block", out["top"])
        self.assertEqual(adapter.last_metrics.pages_ocrd, 1)
        self.assertEqual(adapter.last_metrics.replacements_returned, 1)

    def test_no_overlap_falls_back_to_full_page_text(self) -> None:
        # Request bbox that doesn't overlap any Surya block. Adapter's
        # fallback is to return the full page's OCR text rather than
        # leave the block with corrupted content.
        scripted = PdfExtraction(
            title=None,
            author=None,
            metadata={},
            pages=[
                _surya_page(
                    1,
                    blocks=[
                        _surya_block(
                            text="Only block on page.",
                            bbox=(100.0, 100.0, 900.0, 300.0),
                        ),
                    ],
                ),
            ],
            outline_entries=[],
        )
        adapter = SuryaOcrReextractionAdapter(extractor=FakeExtractor([scripted]))
        # Request bbox is far outside any Surya block (bottom-right corner).
        out = adapter.reextract_blocks(
            str(self.pdf_path),
            [self._request(anchor="none", page=1, bbox=(500, 700, 600, 780))],
        )
        self.assertIn("none", out)
        self.assertIn("Only block on page", out["none"])

    def test_request_for_nonexistent_page_is_skipped(self) -> None:
        scripted = PdfExtraction(
            title=None,
            author=None,
            metadata={},
            pages=[_surya_page(1, blocks=[_surya_block(text="x", bbox=(0, 0, 100, 100))])],
            outline_entries=[],
        )
        adapter = SuryaOcrReextractionAdapter(extractor=FakeExtractor([scripted]))
        # clean_book fixture is 2 pages; ask for page 99 which doesn't exist.
        out = adapter.reextract_blocks(
            str(self.pdf_path),
            [self._request(anchor="ghost", page=99, bbox=(0, 0, 100, 100))],
        )
        self.assertEqual(out, {})


if __name__ == "__main__":
    unittest.main()
