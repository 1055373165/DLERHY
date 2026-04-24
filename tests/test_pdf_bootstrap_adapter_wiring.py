# ruff: noqa: E402
"""Tests for PDF v2 M2.3 closure — env-flag-driven adapter wiring in bootstrap.

Verifies:

  1. With the flag OFF (default), the default recovery service has NO
     OCR reextraction adapter — behaviour matches pre-M2.3.
  2. With the flag ON, the factory attaches a `SuryaOcrReextractionAdapter`.
  3. `PDFParser()` respects an explicitly-passed `recovery_service` even
     when the flag is ON (explicit > env).
  4. An explicit caller can still instantiate a recovery service with
     its own adapter and pass it through.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.structure.pdf import (
    PDFParser,
    PdfStructureRecoveryService,
    _sanity_ocr_reextraction_enabled,
    build_default_recovery_service,
)
from book_agent.domain.structure.ocr_reextraction import (
    NoOpOcrReextractionAdapter,
)


class FeatureFlagTests(unittest.TestCase):
    def test_flag_default_is_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION", None)
            self.assertFalse(_sanity_ocr_reextraction_enabled())

    def test_flag_on_via_various_truthy_values(self) -> None:
        for value in ("1", "true", "TRUE", "yes", "On"):
            with mock.patch.dict(
                os.environ,
                {"BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION": value},
            ):
                self.assertTrue(
                    _sanity_ocr_reextraction_enabled(),
                    f"flag not recognized as on for value={value!r}",
                )

    def test_flag_off_via_falsy_values(self) -> None:
        for value in ("0", "false", "no", "", " "):
            with mock.patch.dict(
                os.environ,
                {"BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION": value},
            ):
                self.assertFalse(
                    _sanity_ocr_reextraction_enabled(),
                    f"flag wrongly on for value={value!r}",
                )


class BuildDefaultRecoveryServiceTests(unittest.TestCase):
    def test_flag_off_builds_service_without_adapter(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION", None)
            svc = build_default_recovery_service()
        self.assertIsInstance(svc, PdfStructureRecoveryService)
        self.assertIsNone(svc._ocr_reextraction_adapter)

    def test_flag_on_builds_service_with_surya_adapter(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION": "1"},
        ):
            svc = build_default_recovery_service()
        self.assertIsNotNone(svc._ocr_reextraction_adapter)
        # Concrete class check — it's the Surya-backed one.
        adapter_cls_name = type(svc._ocr_reextraction_adapter).__name__
        self.assertEqual(adapter_cls_name, "SuryaOcrReextractionAdapter")


class PDFParserWiringTests(unittest.TestCase):
    def test_default_parser_respects_flag_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION", None)
            parser = PDFParser()
        self.assertIsNone(parser.recovery_service._ocr_reextraction_adapter)

    def test_default_parser_respects_flag_on(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION": "true"},
        ):
            parser = PDFParser()
        self.assertIsNotNone(parser.recovery_service._ocr_reextraction_adapter)

    def test_explicit_recovery_service_wins_over_env(self) -> None:
        # Caller passes an explicit service with NoOp adapter; env flag on
        # must not override the caller's intent.
        explicit = PdfStructureRecoveryService(
            ocr_reextraction_adapter=NoOpOcrReextractionAdapter()
        )
        with mock.patch.dict(
            os.environ,
            {"BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION": "1"},
        ):
            parser = PDFParser(recovery_service=explicit)
        self.assertIs(parser.recovery_service, explicit)
        self.assertIsInstance(
            parser.recovery_service._ocr_reextraction_adapter,
            NoOpOcrReextractionAdapter,
        )


if __name__ == "__main__":
    unittest.main()
