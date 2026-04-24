# ruff: noqa: E402
"""Tests for DocIR → Block.source_span_json persistence (PDF v2 M2.8).

Verifies that when bootstrap materialises a `ParsedBlock` into a `Block`
row, the DocIR-level fields (translatability, provenance,
confidence_breakdown, style_hints) survive into `source_span_json` under
stable keys, and the existing metadata is not lost.

Tests drive the bootstrap pipeline with a fabricated `ParsedDocument`
that exercises every field — we don't need a real PDF for this.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event, select
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import BlockType, DocumentStatus, SourceType
from book_agent.domain.models import Chapter, Document
from book_agent.domain.structure.models import (
    PROVENANCE_OCR,
    PROVENANCE_TEXT_LAYER,
    TRANSLATE_ALL,
    TRANSLATE_NONE,
    ParsedBlock,
)
from book_agent.services.bootstrap import ParseService


class DocIRPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ParseService()
        self.now = datetime.now(timezone.utc)
        self.document = Document(
            id="doc-1",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fp-1",
            source_path="test.pdf",
            title="t",
            author="a",
            src_lang="en",
            tgt_lang="zh",
            status=DocumentStatus.ACTIVE,
            parser_version=1,
            segmentation_version=1,
        )
        self.chapter = Chapter(
            id="chap-1",
            document_id="doc-1",
            ordinal=1,
        )

    def _build(self, parsed: ParsedBlock):
        return self.service._build_block(self.document, self.chapter, parsed, self.now)

    def test_clean_paragraph_has_default_docir_fields(self) -> None:
        parsed = ParsedBlock(
            block_type="paragraph",
            text="A clean English paragraph.",
            source_path="pdf://page/1",
            ordinal=1,
            anchor="p1-b1",
            metadata={"source_page_start": 1},
        )
        block = self._build(parsed)
        span = block.source_span_json
        self.assertEqual(span["docir_translatability"], TRANSLATE_ALL)
        self.assertEqual(span["docir_provenance"], PROVENANCE_TEXT_LAYER)
        self.assertEqual(span["docir_confidence_breakdown"], {})
        self.assertEqual(span["docir_style_hints"], {})

    def test_code_block_persists_translate_none_and_style_hints(self) -> None:
        parsed = ParsedBlock(
            block_type="code",
            text="print('hello')",
            source_path="pdf://page/1",
            ordinal=2,
            anchor="p1-b2",
            metadata={"source_page_start": 1},
            translatability=TRANSLATE_NONE,
            style_hints={"is_mono": True},
        )
        block = self._build(parsed)
        self.assertEqual(block.source_span_json["docir_translatability"], TRANSLATE_NONE)
        self.assertEqual(block.source_span_json["docir_style_hints"], {"is_mono": True})

    def test_sanity_failed_block_persists_provenance_and_breakdown(self) -> None:
        parsed = ParsedBlock(
            block_type="paragraph",
            text="corrupted text goes here",
            source_path="pdf://page/2",
            ordinal=3,
            anchor="p2-b1",
            metadata={"source_page_start": 2},
            translatability=TRANSLATE_ALL,
            provenance=PROVENANCE_OCR,
            confidence_breakdown={"sanity_ok": False, "sanity_reason": "pua_high"},
        )
        block = self._build(parsed)
        self.assertEqual(block.source_span_json["docir_provenance"], PROVENANCE_OCR)
        breakdown = block.source_span_json["docir_confidence_breakdown"]
        self.assertEqual(breakdown.get("sanity_ok"), False)
        self.assertEqual(breakdown.get("sanity_reason"), "pua_high")

    def test_existing_metadata_preserved_alongside_docir(self) -> None:
        parsed = ParsedBlock(
            block_type="paragraph",
            text="Intro.",
            source_path="pdf://page/1",
            ordinal=1,
            anchor="p1-b1",
            metadata={"source_page_start": 1, "pdf_block_role": "body"},
        )
        block = self._build(parsed)
        span = block.source_span_json
        # Existing keys.
        self.assertEqual(span["source_path"], "pdf://page/1")
        self.assertEqual(span["anchor"], "p1-b1")
        self.assertEqual(span["source_page_start"], 1)
        self.assertEqual(span["pdf_block_role"], "body")
        # DocIR keys.
        self.assertIn("docir_translatability", span)
        self.assertIn("docir_provenance", span)


if __name__ == "__main__":
    unittest.main()
