"""The export gate evaluates chapters read-only; issue sync is a separate step."""

import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import delete, func, select

import tests.test_api_workflow as api_fixtures
from book_agent.domain.enums import ExportType, IssueStatus
from book_agent.domain.models import Sentence
from book_agent.domain.models.review import IssueAction, ReviewIssue
from book_agent.domain.models.translation import AlignmentEdge
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.services.export import ExportGateError
from book_agent.services.workflows import DocumentWorkflowService


class ExportGateEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        self.engine = build_engine(f"sqlite+pysqlite:///{root / 'gate.db'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.export_root = str(root / "exports")
        epub_path = root / "gate.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", api_fixtures.CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", api_fixtures.CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", api_fixtures.NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", api_fixtures.CHAPTER_XHTML)
        with session_scope(self.session_factory) as session:
            workflow = DocumentWorkflowService(session, export_root=self.export_root)
            self.document_id = workflow.bootstrap_document(epub_path).document_id
            workflow.translate_document(self.document_id)
            workflow.review_document(self.document_id)
            self.chapter_id = workflow.get_document_summary(self.document_id).chapters[0].chapter_id
        with session_scope(self.session_factory) as session:
            sentence_id = session.scalars(
                select(Sentence.id).where(Sentence.source_text.like("Pricing power%"))
            ).one()
            session.execute(delete(AlignmentEdge).where(AlignmentEdge.sentence_id == sentence_id))

    def _export_issue_count(self, session) -> int:
        return session.scalar(
            select(func.count()).select_from(ReviewIssue).where(ReviewIssue.issue_type == "ALIGNMENT_FAILURE")
        )

    def test_evaluation_finds_misalignment_without_writing(self) -> None:
        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root).export_service
            bundle = service.repository.load_chapter_bundle(self.chapter_id)

            evaluation = service.evaluate_chapter_gate(bundle, ExportType.BILINGUAL_HTML)

            self.assertFalse(evaluation.status_blocked)
            self.assertEqual(len(evaluation.alignment.issues), 1)
            self.assertIsNone(evaluation.layout)
            self.assertFalse(session.new)
            self.assertFalse(session.dirty)
            self.assertEqual(self._export_issue_count(session), 0)

    def test_syncing_the_evaluation_persists_issues_and_actions(self) -> None:
        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root).export_service
            bundle = service.repository.load_chapter_bundle(self.chapter_id)
            evaluation = service.evaluate_chapter_gate(bundle, ExportType.BILINGUAL_HTML)

            service.sync_gate_issues(bundle, evaluation)

            [issue] = evaluation.alignment_artifacts.issues
            self.assertEqual(self._export_issue_count(session), 1)
            self.assertEqual(session.get(ReviewIssue, issue.id).status, IssueStatus.OPEN)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(IssueAction).where(IssueAction.issue_id == issue.id)),
                1,
            )
            with self.assertRaises(ExportGateError) as raised:
                service._raise_for_gate(bundle, evaluation)
            self.assertEqual(raised.exception.issue_ids, [issue.id])

    def test_review_package_does_not_open_blocking_issues(self) -> None:
        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root).export_service
            service.export_review_package(self.chapter_id)
            self.assertEqual(self._export_issue_count(session), 0)

    def test_use_case_runs_the_gate_once_per_chapter(self) -> None:
        from unittest.mock import patch

        from book_agent.services.export import ExportService

        with session_scope(self.session_factory) as session:
            workflow = DocumentWorkflowService(session, export_root=self.export_root)
            with patch.object(ExportService, "_enforce_gate", autospec=True, return_value=None) as gate:
                workflow.export_document(self.document_id, ExportType.MERGED_HTML)
            chapter_count = len(workflow.get_document_summary(self.document_id).chapters)
            self.assertEqual(gate.call_count, chapter_count)

    def test_layout_issue_never_references_a_synthetic_block_id(self) -> None:
        from book_agent.services.export import _persisted_block_id

        with session_scope(self.session_factory) as session:
            service = DocumentWorkflowService(session, export_root=self.export_root).export_service
            bundle = service.repository.load_chapter_bundle(self.chapter_id)
            real = bundle.blocks[0].id
            self.assertEqual(_persisted_block_id(bundle, real), real)
            self.assertEqual(_persisted_block_id(bundle, f"{real}::leading-prose"), real)
            self.assertIsNone(_persisted_block_id(bundle, "not-a-block::refresh-split::2"))
            self.assertIsNone(_persisted_block_id(bundle, None))


if __name__ == "__main__":
    unittest.main()
