# ruff: noqa: E402

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.pdf_scan_corpus_acceptance import evaluate_larger_corpus_acceptance
from scripts.phase3_integration_gate import (
    ACCEPTANCE_TEST_MATRIX,
    REQUIRED_LANE_CONTRACT_TAGS,
    evaluate_phase3_integration_gate,
)

from book_agent.domain.enums import ExportType
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.export import ExportService
from book_agent.services.workflows import DocumentWorkflowService
from tests.test_persistence_and_review import (
    CONTAINER_XML,
    LITERALISM_XHTML,
    LiteralismWorker,
    NAV_XHTML,
)


class Phase3IntegrationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _bootstrap_custom_epub_to_db(
        self,
        chapters: list[tuple[str, str, str]],
    ) -> str:
        manifest_items = [
            '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />'
        ]
        spine_items: list[str] = []
        for index, (_title, href, _content) in enumerate(chapters, start=1):
            item_id = f"chap{index}"
            manifest_items.append(
                f'    <item id="{item_id}" href="{href}" media-type="application/xhtml+xml" />'
            )
            spine_items.append(f'    <itemref idref="{item_id}" />')

        content_opf = "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">',
                '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
                '    <dc:title>Business Strategy Handbook</dc:title>',
                '    <dc:creator>Test Author</dc:creator>',
                '    <dc:language>en</dc:language>',
                "  </metadata>",
                "  <manifest>",
                *manifest_items,
                "  </manifest>",
                "  <spine>",
                *spine_items,
                "  </spine>",
                "</package>",
            ]
        )

        tmpdir = Path(tempfile.mkdtemp(prefix="book-agent-phase3-integration-"))
        self.addCleanup(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
        epub_path = tmpdir / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", content_opf)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            for _title, href, content in chapters:
                archive.writestr(f"OEBPS/{href}", content)

        artifacts = BootstrapOrchestrator().bootstrap_epub(epub_path)
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
        return artifacts.document.id

    def test_phase3_integration_snapshot_records_lane_acceptance_matrix_and_contract_coverage(self) -> None:
        snapshot = evaluate_phase3_integration_gate(ROOT)

        self.assertIsNone(snapshot["current_wave_id"])
        self.assertEqual(snapshot["phase_plan_status"], "phase-3-complete")
        self.assertEqual(snapshot["gate_node"]["node_id"], "mdu-18.1.1")
        self.assertEqual(snapshot["gate_node"]["contract_tags"], ["phase-3-checkpoint"])
        self.assertTrue(snapshot["checks"]["lane_docs_complete"]["passed"])
        self.assertTrue(snapshot["checks"]["lane_acceptance_artifacts_present"]["passed"])
        self.assertTrue(snapshot["checks"]["lane_statuses_done"]["passed"])
        self.assertTrue(snapshot["checks"]["contract_coverage"]["passed"])
        self.assertTrue(snapshot["checks"]["integration_preconditions_ready"]["passed"])

        for lane_id, expected_tags in REQUIRED_LANE_CONTRACT_TAGS.items():
            lane_snapshot = snapshot["checks"]["contract_coverage"]["lanes"][lane_id]
            self.assertEqual(lane_snapshot["actual_tags"], sorted(expected_tags))
            self.assertFalse(lane_snapshot["missing_tags"])
            self.assertGreaterEqual(len(ACCEPTANCE_TEST_MATRIX[lane_id]), 1)

        self.assertIn(
            "tests.test_pdf_scan_corpus_acceptance.PdfScanCorpusAcceptanceTests.test_locked_larger_corpus_acceptance_passes_phase3_thresholds",
            snapshot["acceptance_matrix"]["lane-pdf-scan-scale"],
        )
        self.assertIn(
            "tests.test_review_naturalness_acceptance.ReviewNaturalnessAcceptanceTests.test_guided_followup_clears_literalism_benchmark_under_locked_contract",
            snapshot["acceptance_matrix"]["lane-review-naturalness"],
        )

    def test_phase3_integration_gate_keeps_locked_larger_corpus_acceptance_green(self) -> None:
        snapshot = evaluate_larger_corpus_acceptance(repo_root=ROOT)

        self.assertTrue(snapshot["overall_passed"])
        self.assertTrue(snapshot["checks"]["slice_repair_acceptance"]["passed"])
        self.assertTrue(snapshot["checks"]["readable_rescue_exports"]["passed"])

    def test_non_blocking_style_drift_does_not_block_rebuilt_delivery_exports(self) -> None:
        document_id = self._bootstrap_custom_epub_to_db(
            [("Chapter One", "chapter1.xhtml", LITERALISM_XHTML)]
        )

        def _fake_render(_service, html_path: Path, pdf_path: Path) -> None:
            self.assertTrue(Path(html_path).name.endswith(".html"))
            pdf_path.write_bytes(b"%PDF-1.4\n% phase3-integration\n")

        with tempfile.TemporaryDirectory() as outdir:
            with self.session_factory() as session:
                workflow = DocumentWorkflowService(
                    session,
                    export_root=outdir,
                    translation_worker=LiteralismWorker(),
                )
                workflow.translate_document(document_id)
                review = workflow.review_document(document_id)

                self.assertGreater(review.total_issue_count, 0)
                self.assertEqual(review.chapter_results[0].blocking_issue_count, 0)
                self.assertIsNotNone(review.chapter_results[0].naturalness_summary)
                assert review.chapter_results[0].naturalness_summary is not None
                self.assertTrue(review.chapter_results[0].naturalness_summary.advisory_only)
                self.assertGreaterEqual(
                    review.chapter_results[0].naturalness_summary.style_drift_issue_count,
                    1,
                )

                merged_markdown = workflow.export_document(document_id, ExportType.MERGED_MARKDOWN)
                rebuilt_epub = workflow.export_document(document_id, ExportType.REBUILT_EPUB)
                with patch.object(
                    ExportService,
                    "_render_rebuilt_pdf_from_html",
                    autospec=True,
                    side_effect=_fake_render,
                ):
                    rebuilt_pdf = workflow.export_document(document_id, ExportType.REBUILT_PDF)
                session.commit()
                merged_markdown_path = Path(merged_markdown.file_path)
                rebuilt_epub_path = Path(rebuilt_epub.file_path)
                rebuilt_pdf_path = Path(rebuilt_pdf.file_path)
                rebuilt_epub_manifest = json.loads(
                    Path(rebuilt_epub.manifest_path).read_text(encoding="utf-8")
                )
                rebuilt_pdf_manifest = json.loads(
                    Path(rebuilt_pdf.manifest_path).read_text(encoding="utf-8")
                )

                self.assertTrue(merged_markdown_path.exists())
                self.assertTrue(rebuilt_epub_path.exists())
                self.assertTrue(rebuilt_pdf_path.exists())
                self.assertEqual(rebuilt_epub_manifest["export_type"], "rebuilt_epub")
                self.assertEqual(rebuilt_pdf_manifest["export_type"], "rebuilt_pdf")
                self.assertEqual(
                    rebuilt_epub_manifest["derived_from_exports"],
                    ["merged_html", "merged_markdown"],
                )
                self.assertEqual(
                    rebuilt_pdf_manifest["derived_from_exports"],
                    ["merged_html", "merged_markdown"],
                )
                self.assertTrue(rebuilt_pdf_path.read_bytes().startswith(b"%PDF-1.4"))


if __name__ == "__main__":
    unittest.main()
