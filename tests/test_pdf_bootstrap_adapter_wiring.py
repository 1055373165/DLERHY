# ruff: noqa: E402
"""Tests for PDF v2 M2.3 closure — settings-driven adapter wiring in bootstrap.

Verifies:

  1. With ``pdf_sanity_ocr_reextraction`` off (default), the default recovery
     service has NO OCR reextraction adapter — behaviour matches pre-M2.3.
  2. With it on, the factory attaches a `SuryaOcrReextractionAdapter`.
  3. The setting is read from ``BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION``.
  4. `PDFParser()` respects an explicitly-passed `recovery_service` even
     when the setting is on (explicit > settings).
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

from book_agent.core.config import Settings, get_settings
from book_agent.domain.structure.pdf import (
    PDFParser,
    PdfStructureRecoveryService,
    build_default_recovery_service,
)
from book_agent.ingestion.pdf.ocr_reextraction import NoOpOcrReextractionAdapter

_FLAG = "BOOK_AGENT_PDF_SANITY_OCR_REEXTRACTION"


class SettingTests(unittest.TestCase):
    def test_setting_defaults_to_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_FLAG, None)
            self.assertFalse(Settings().pdf_sanity_ocr_reextraction)

    def test_setting_reads_truthy_env_values(self) -> None:
        for value in ("1", "true", "TRUE", "yes", "On"):
            with mock.patch.dict(os.environ, {_FLAG: value}):
                self.assertTrue(Settings().pdf_sanity_ocr_reextraction, value)

    def test_setting_reads_falsy_env_values(self) -> None:
        for value in ("0", "false", "no", "off"):
            with mock.patch.dict(os.environ, {_FLAG: value}):
                self.assertFalse(Settings().pdf_sanity_ocr_reextraction, value)


class BuildDefaultRecoveryServiceTests(unittest.TestCase):
    def test_setting_off_builds_service_without_adapter(self) -> None:
        svc = build_default_recovery_service(settings=Settings(pdf_sanity_ocr_reextraction=False))
        self.assertIsInstance(svc, PdfStructureRecoveryService)
        self.assertIsNone(svc._ocr_reextraction_adapter)

    def test_setting_on_builds_service_with_surya_adapter(self) -> None:
        svc = build_default_recovery_service(settings=Settings(pdf_sanity_ocr_reextraction=True))
        self.assertEqual(type(svc._ocr_reextraction_adapter).__name__, "SuryaOcrReextractionAdapter")

    def test_ocr_reextraction_can_be_disallowed(self) -> None:
        svc = build_default_recovery_service(
            settings=Settings(pdf_sanity_ocr_reextraction=True),
            allow_ocr_reextraction=False,
        )
        self.assertIsNone(svc._ocr_reextraction_adapter)


class PDFParserWiringTests(unittest.TestCase):
    def _parser_with_env(self, value: str | None) -> PDFParser:
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        with mock.patch.dict(os.environ, {} if value is None else {_FLAG: value}, clear=False):
            if value is None:
                os.environ.pop(_FLAG, None)
            parser = PDFParser()
        return parser

    def test_default_parser_respects_setting_off(self) -> None:
        self.assertIsNone(self._parser_with_env(None).recovery_service._ocr_reextraction_adapter)

    def test_default_parser_respects_setting_on(self) -> None:
        self.assertIsNotNone(self._parser_with_env("true").recovery_service._ocr_reextraction_adapter)

    def test_explicit_recovery_service_wins_over_settings(self) -> None:
        explicit = PdfStructureRecoveryService(ocr_reextraction_adapter=NoOpOcrReextractionAdapter())
        parser = PDFParser(recovery_service=explicit)
        self.assertIs(parser.recovery_service, explicit)
        self.assertIsInstance(parser.recovery_service._ocr_reextraction_adapter, NoOpOcrReextractionAdapter)


if __name__ == "__main__":
    unittest.main()
