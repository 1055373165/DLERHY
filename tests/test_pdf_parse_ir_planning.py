# ruff: noqa: E402

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.core.ids import stable_id
from book_agent.domain.enums import DocumentStatus, SourceType
from book_agent.domain.models import Document
from book_agent.domain.structure.models import ParsedBlock, ParsedChapter, ParsedDocument
from book_agent.services.bootstrap import ParseService
from book_agent.services.parse_ir import ParseIrService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _AcademicPdfParser:
    def parse(self, _file_path: str | Path, profile: dict | None = None) -> ParsedDocument:
        _ = profile
        return ParsedDocument(
            title="Academic PDF Planning",
            author="Test Author",
            language="en",
            chapters=[
                ParsedChapter(
                    chapter_id="chapter-1",
                    href="chapter1.xhtml",
                    title="Introduction",
                    blocks=[
                        ParsedBlock(
                            block_type="heading",
                            text="Introduction",
                            source_path="chapter1.xhtml",
                            ordinal=1,
                            anchor="h1",
                            metadata={"heading_level": 1},
                            parse_confidence=0.97,
                        ),
                        ParsedBlock(
                            block_type="paragraph",
                            text="This paragraph comes from a multi-column academic PDF.",
                            source_path="chapter1.xhtml",
                            ordinal=2,
                            anchor="p1",
                            metadata={},
                            parse_confidence=0.95,
                        ),
                    ],
                    metadata={"source_page_start": 1, "source_page_end": 2},
                )
            ],
            metadata={
                "pdf_profile": {
                    "pdf_kind": "mixed_pdf",
                    "page_count": 2,
                    "has_extractable_text": True,
                    "outline_present": True,
                    "layout_risk": "high",
                    "ocr_required": True,
                    "extractor_kind": "pymupdf",
                    "average_text_density": 180.0,
                    "average_span_count": 4.0,
                    "multi_column_page_count": 1,
                    "fragment_page_count": 1,
                    "suspicious_page_numbers": [2],
                    "recovery_lane": "academic_paper",
                    "trailing_reference_page_count": 1,
                    "academic_paper_candidate": True,
                },
                "pdf_page_evidence": {
                    "schema_version": 1,
                    "extractor_kind": "pymupdf",
                    "page_count": 2,
                    "pdf_outline_entries": [
                        {"level": 1, "title": "Introduction", "page_number": 1},
                    ],
                    "pdf_pages": [
                        {
                            "page_number": 1,
                            "raw_block_count": 2,
                            "raw_image_block_count": 0,
                            "recovered_block_count": 2,
                            "page_family": "body",
                            "page_family_source": "body",
                            "page_family_heading": "Introduction",
                            "content_family": "body",
                            "backmatter_cue": None,
                            "backmatter_cue_source": None,
                            "is_toc_page": False,
                            "has_strong_heading": True,
                            "layout_signals": [],
                            "page_layout_risk": "low",
                            "page_layout_reasons": [],
                            "extraction_intent": "native_text",
                            "extraction_intent_scope": "page",
                            "extraction_intent_reasons": ["text_preserving"],
                            "layout_suspect": False,
                            "role_counts": {"heading": 1, "body": 1},
                            "recovery_flags": [],
                            "toc_entries": [],
                            "appendix_nested_subheadings": [],
                            "matched_footnote_count": 0,
                            "orphan_footnote_count": 0,
                            "relocated_footnote_count": 0,
                            "max_footnote_segment_count": 0,
                        },
                        {
                            "page_number": 2,
                            "raw_block_count": 2,
                            "raw_image_block_count": 0,
                            "recovered_block_count": 2,
                            "page_family": "body",
                            "page_family_source": "body",
                            "page_family_heading": "Introduction",
                            "content_family": "body",
                            "backmatter_cue": None,
                            "backmatter_cue_source": None,
                            "is_toc_page": False,
                            "has_strong_heading": False,
                            "layout_signals": ["multi_column", "academic_first_page_asymmetric"],
                            "page_layout_risk": "high",
                            "page_layout_reasons": ["multi_column", "academic_first_page_asymmetric"],
                            "extraction_intent": "hybrid_merge",
                            "extraction_intent_scope": "page",
                            "extraction_intent_reasons": [
                                "multi_column",
                                "academic_first_page_asymmetric",
                                "academic_paper_lane",
                            ],
                            "layout_suspect": True,
                            "role_counts": {"body": 2},
                            "recovery_flags": ["multi_column"],
                            "toc_entries": [],
                            "appendix_nested_subheadings": [],
                            "matched_footnote_count": 0,
                            "orphan_footnote_count": 0,
                            "relocated_footnote_count": 0,
                            "max_footnote_segment_count": 0,
                        },
                    ],
                },
            },
        )


class _FakePdfIngestDocument:
    def ingest(self, file_path: str | Path):
        now = _utcnow()
        document = Document(
            id=stable_id("document", "pdf-parse-ir-planning"),
            source_type=SourceType.PDF_MIXED,
            file_fingerprint="fingerprint-pdf-parse-ir-planning",
            source_path=str(file_path),
            title=None,
            author=None,
            src_lang=None,
            tgt_lang="zh",
            status=DocumentStatus.INGESTED,
            parser_version=1,
            segmentation_version=1,
            metadata_json={
                "pdf_profile": {
                    "pdf_kind": "mixed_pdf",
                    "page_count": 2,
                    "has_extractable_text": True,
                    "outline_present": True,
                    "layout_risk": "high",
                    "ocr_required": True,
                    "extractor_kind": "pymupdf",
                    "average_text_density": 180.0,
                    "average_span_count": 4.0,
                    "multi_column_page_count": 1,
                    "fragment_page_count": 1,
                    "suspicious_page_numbers": [2],
                    "recovery_lane": "academic_paper",
                    "trailing_reference_page_count": 1,
                    "academic_paper_candidate": True,
                }
            },
            created_at=now,
            updated_at=now,
        )

        class _Job:
            id = stable_id("job", "ingest", document.id)

        return document, _Job()


class PdfParseIrPlanningTests(unittest.TestCase):
    def test_pdf_page_planning_enters_canonical_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            source_path = Path(tempdir) / "academic.pdf"
            source_path.write_bytes(b"fake pdf bytes")
            parse_ir_service = ParseIrService(output_root=Path(tempdir) / "parse-ir")
            parse_service = ParseService(
                pdf_parser=_AcademicPdfParser(),
                ocr_pdf_parser=_AcademicPdfParser(),
                parse_ir_service=parse_ir_service,
            )
            ingest_service = _FakePdfIngestDocument()

            document, _job = ingest_service.ingest(source_path)
            artifacts = parse_service.parse(document, source_path)

            sidecar_path = Path(artifacts.parse_revision.canonical_ir_path or "")
            self.assertTrue(sidecar_path.is_file())
            self.assertEqual(artifacts.document.metadata_json["parse_ir"]["page_plan_count"], 2)
            self.assertEqual(artifacts.document.metadata_json["parse_ir"]["page_plan_intents"]["1"], "native_text")
            self.assertEqual(artifacts.document.metadata_json["parse_ir"]["page_plan_intents"]["2"], "hybrid_merge")

            payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
            canonical_ir = payload["canonical_ir"]
            self.assertEqual(canonical_ir["metadata"]["planning_scope"], "page")
            self.assertEqual(canonical_ir["metadata"]["page_plan_count"], 2)
            self.assertEqual(canonical_ir["page_plans"][0]["intent"], "native_text")
            self.assertEqual(canonical_ir["page_plans"][1]["intent"], "hybrid_merge")
            self.assertIn("multi_column", canonical_ir["page_plans"][1]["risk_reasons"])
            page_plan_nodes = [node for node in canonical_ir["nodes"] if node["node_type"] == "page_extraction_plan"]
            self.assertEqual(len(page_plan_nodes), 2)
            self.assertEqual(page_plan_nodes[1]["metadata"]["intent"], "hybrid_merge")
            self.assertEqual(page_plan_nodes[1]["source_ref"], "pdf://page/2")
