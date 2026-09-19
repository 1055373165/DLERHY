"""Export QA: pure HTML checks, the verify_chapter CLI wrapper, and the issue-filing sensor."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from book_agent.domain.enums import ExportType, IssueStatus
from book_agent.domain.models.review import ReviewIssue
from book_agent.export.qa import audit_html, empty_block_check, heading_hierarchy_check, untranslated_ratio_check
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.export_qa import ISSUE_TYPE, ExportQaService
from book_agent.services.review import ReviewService
from book_agent.services.translation import TranslationService
from book_agent.services.workflows import DocumentWorkflowService
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML

ROOT = Path(__file__).resolve().parents[1]

BROKEN_HTML = """<h1>书名</h1><h3>跳级标题</h3>
<p>正文段落。</p><p>   </p><p>]</p>
<figure><img src='a.png' /><figcaption>图3.1 智能体循环的整体架构示意</figcaption></figure>
<p>图3.1 智能体循环的整体架构示意</p>"""


class QaCheckTests(unittest.TestCase):
    def test_structural_rules_flag_duplicates_and_orphans(self) -> None:
        report = audit_html(BROKEN_HTML, figure_prefix="3", figure_count=1, min_img_coverage=1)
        by_name = {check.name: check for check in report.checks}
        self.assertTrue(by_name["R1 figure_coverage"].ok)
        self.assertFalse(by_name["R3 caption_no_body_dup"].ok)
        self.assertFalse(by_name["R5 no_orphan_brackets"].ok)
        self.assertTrue(by_name["R6 image_render_coverage"].ok)
        self.assertNotIn("R7 source_fold_well_formed", by_name)

    def test_folded_chapters_are_reported(self) -> None:
        from book_agent.export.qa import chapter_sections_check

        self.assertTrue(chapter_sections_check([], 12).ok)
        folded = chapter_sections_check(["CHAPTER 9: 20 POPULAR RSI TRADING STRATEGIES"], 12)
        self.assertFalse(folded.ok)
        self.assertIn("CHAPTER 9", folded.detail)

    def test_product_checks(self) -> None:
        self.assertFalse(heading_hierarchy_check(BROKEN_HTML).ok)
        self.assertTrue(heading_hierarchy_check("<h1>a</h1><h2>b</h2><h3>c</h3><h1>d</h1>").ok)
        empty = empty_block_check(BROKEN_HTML)
        self.assertFalse(empty.ok)
        self.assertEqual(empty.data["empty_tags"], ["p"])
        self.assertTrue(untranslated_ratio_check(100, 0).ok)
        self.assertEqual(untranslated_ratio_check(100, 1).severity, "warning")
        self.assertEqual(untranslated_ratio_check(100, 5).severity, "error")

    def test_verify_chapter_cli_keeps_its_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "ch3.html"
            html_path.write_text(BROKEN_HTML, encoding="utf-8")
            report_path = Path(tmp) / "report.json"
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "verify_chapter.py"), "--html", str(html_path),
                 "--figure-prefix", "3", "--figure-count", "1", "--report-path", str(report_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 1, completed.stderr)
            self.assertIn("[FAIL] R5 no_orphan_brackets", completed.stdout)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(report["all_ok"])
            self.assertEqual(report["structure"]["figure"], 1)
            self.assertEqual({"name", "ok", "detail"}, set(report["checks"][0]))
            missing = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "verify_chapter.py"), "--html", str(Path(tmp) / "nope.html"),
                 "--figure-prefix", "3", "--figure-count", "1"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(missing.returncode, 2)


class ExportQaServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.engine = build_engine(f"sqlite+pysqlite:///{root / 'qa.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.export_root = root / "exports"
        epub_path = root / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        artifacts = BootstrapOrchestrator().bootstrap_epub(epub_path)
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            service = TranslationService(TranslationRepository(session))
            for packet in artifacts.translation_packets:
                service.execute_packet(packet.id)
            for chapter in artifacts.chapters:
                ReviewService(ReviewRepository(session)).review_chapter(chapter.id)
            session.commit()
        self.document_id = artifacts.document.id

    def _issues(self, session) -> list[ReviewIssue]:
        return list(session.scalars(select(ReviewIssue).where(ReviewIssue.issue_type == ISSUE_TYPE)).all())

    def test_clean_export_passes_and_a_broken_one_files_non_blocking_issues_until_fixed(self) -> None:
        with self.session_factory() as session:
            result = DocumentWorkflowService(session, export_root=self.export_root).export_document(self.document_id, ExportType.MERGED_HTML)
            html_path = Path(result.file_path)
            clean = ExportQaService(session).audit(self.document_id, ExportType.MERGED_HTML, [(None, html_path)])
            self.assertTrue(clean.all_ok, clean.failed_checks)
            self.assertTrue(Path(clean.report_paths[0]).is_file())
            self.assertEqual(self._issues(session), [])

            good_html = html_path.read_text(encoding="utf-8")
            html_path.write_text(good_html + "<p> </p>", encoding="utf-8")
            broken = ExportQaService(session).audit(self.document_id, ExportType.MERGED_HTML, [(None, html_path)])
            self.assertFalse(broken.all_ok)
            (issue,) = self._issues(session)
            self.assertEqual(issue.evidence_json["check"], "Q2 no_empty_blocks")
            self.assertFalse(issue.blocking)
            self.assertEqual(broken.opened_issue_ids, [issue.id])

            html_path.write_text(good_html, encoding="utf-8")
            fixed = ExportQaService(session).audit(self.document_id, ExportType.MERGED_HTML, [(None, html_path)])
            self.assertEqual(fixed.resolved_issue_ids, [issue.id])
            self.assertEqual(session.get(ReviewIssue, issue.id).status, IssueStatus.RESOLVED)

    def test_untranslated_sentences_are_reported(self) -> None:
        from book_agent.domain.enums import TargetSegmentStatus
        from book_agent.domain.models.translation import TargetSegment

        with self.session_factory() as session:
            result = DocumentWorkflowService(session, export_root=self.export_root).export_document(self.document_id, ExportType.MERGED_HTML)
            segment = session.scalars(select(TargetSegment)).first()
            segment.final_status = TargetSegmentStatus.SUPERSEDED
            session.flush()
            audit = ExportQaService(session).audit(self.document_id, ExportType.MERGED_HTML, [(None, Path(result.file_path))])
            failed = {check["name"]: check for check in audit.failed_checks}
            self.assertIn("Q3 untranslated_ratio", failed)
            self.assertEqual(failed["Q3 untranslated_ratio"]["data"]["untranslated"], 1)

    def test_other_export_types_are_not_audited(self) -> None:
        with self.session_factory() as session:
            audit = ExportQaService(session).audit(self.document_id, ExportType.MERGED_MARKDOWN, [(None, Path("x.md"))])
            self.assertTrue(audit.all_ok)
            self.assertEqual(audit.report_paths, [])


if __name__ == "__main__":
    unittest.main()
