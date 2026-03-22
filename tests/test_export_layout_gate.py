import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import (
    ActionType,
    ArtifactStatus,
    BlockType,
    ChapterStatus,
    Detector,
    DocumentStatus,
    ExportType,
    IssueStatus,
    JobScopeType,
    ProtectedPolicy,
    RootCauseLayer,
    Severity,
    SourceType,
)
from book_agent.domain.models import Block, Chapter, Document
from book_agent.domain.models.review import IssueAction, ReviewIssue
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.export import ExportRepository
from book_agent.services.export import ExportGateError, ExportService, MergedRenderBlock


class ExportLayoutGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed_exportable_chapter(self) -> tuple[str, str]:
        now = datetime.now(timezone.utc)
        document = Document(
            id="11111111-1111-4111-8111-111111111111",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="layout-gate-doc",
            source_path="/tmp/layout-gate.pdf",
            title="Layout Gate",
            status=DocumentStatus.ACTIVE,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        chapter = Chapter(
            id="22222222-2222-4222-8222-222222222222",
            document_id=document.id,
            ordinal=1,
            title_src="Chapter One",
            title_tgt="第一章",
            anchor_start=None,
            anchor_end=None,
            status=ChapterStatus.QA_CHECKED,
            summary_version=None,
            risk_level=None,
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        block = Block(
            id="33333333-3333-4333-8333-333333333333",
            chapter_id=chapter.id,
            ordinal=1,
            block_type=BlockType.CAPTION,
            source_text="Figure 1. Missing asset.",
            normalized_text="Figure 1. Missing asset.",
            source_anchor=None,
            source_span_json={},
            parse_confidence=0.9,
            protected_policy=ProtectedPolicy.TRANSLATE,
            status=ArtifactStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )

        with self.session_factory() as session:
            session.add(document)
            session.flush()
            session.add(chapter)
            session.flush()
            session.add(block)
            session.commit()
        return chapter.id, block.id

    def _layout_problem_block(self, *, chapter_id: str, block_id: str) -> MergedRenderBlock:
        return MergedRenderBlock(
            block_id=block_id,
            chapter_id=chapter_id,
            block_type=BlockType.CAPTION.value,
            render_mode="image_anchor_with_translated_caption",
            artifact_kind="figure",
            title=None,
            source_text="Figure 1. Missing asset.",
            target_text="图 1. 缺少图片资源。",
            source_metadata={},
            source_sentence_ids=[],
            target_segment_ids=[],
            is_expected_source_only=True,
            notice="图片锚点保留",
        )

    def _layout_valid_block(self, *, chapter_id: str, block_id: str) -> MergedRenderBlock:
        return MergedRenderBlock(
            block_id=block_id,
            chapter_id=chapter_id,
            block_type=BlockType.CAPTION.value,
            render_mode="image_anchor_with_translated_caption",
            artifact_kind="figure",
            title=None,
            source_text="Figure 1. Linked image asset.",
            target_text="图 1. 已关联图片资源。",
            source_metadata={"image_src": "assets/figure-1.png"},
            source_sentence_ids=[],
            target_segment_ids=[],
            is_expected_source_only=True,
            notice="图片锚点保留",
        )

    def test_assert_chapter_exportable_creates_blocking_layout_issue_and_reparse_action(self) -> None:
        chapter_id, block_id = self._seed_exportable_chapter()

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            with patch.object(
                ExportService,
                "_render_blocks_for_chapter",
                autospec=True,
                return_value=[self._layout_problem_block(chapter_id=chapter_id, block_id=block_id)],
            ):
                with self.assertRaisesRegex(ExportGateError, "layout validation issues") as exc_info:
                    service.assert_chapter_exportable(chapter_id, ExportType.BILINGUAL_HTML)

            issue = session.scalars(
                select(ReviewIssue).where(ReviewIssue.issue_type == "LAYOUT_VALIDATION_FAILURE")
            ).one()
            action = session.scalars(select(IssueAction).where(IssueAction.issue_id == issue.id)).one()

        exc = exc_info.exception
        self.assertEqual(exc.chapter_id, chapter_id)
        self.assertEqual(exc.issue_ids, [issue.id])
        self.assertEqual(len(exc.followup_actions), 1)
        self.assertEqual(exc.followup_actions[0].action_type, ActionType.REPARSE_CHAPTER.value)
        self.assertEqual(exc.followup_actions[0].scope_type, JobScopeType.CHAPTER.value)
        self.assertEqual(exc.followup_actions[0].scope_id, chapter_id)

        self.assertEqual(issue.root_cause_layer, RootCauseLayer.STRUCTURE)
        self.assertEqual(issue.severity, Severity.HIGH)
        self.assertTrue(issue.blocking)
        self.assertEqual(issue.detector, Detector.RULE)
        self.assertEqual(issue.status, IssueStatus.OPEN)
        self.assertEqual(issue.suggested_action, ActionType.REPARSE_CHAPTER.value)
        self.assertEqual(issue.evidence_json["reason"], "export_layout_validation")
        self.assertEqual(issue.evidence_json["layout_issue_count"], 1)
        self.assertEqual(issue.evidence_json["layout_issue_codes"], ["FIGURE_ASSET_MISSING"])

        self.assertEqual(action.action_type, ActionType.REPARSE_CHAPTER)
        self.assertEqual(action.scope_type, JobScopeType.CHAPTER)
        self.assertEqual(action.scope_id, chapter_id)

    def test_assert_chapter_exportable_resolves_export_layout_issue_after_structure_is_fixed(self) -> None:
        chapter_id, block_id = self._seed_exportable_chapter()

        with self.session_factory() as session:
            service = ExportService(ExportRepository(session))
            with patch.object(
                ExportService,
                "_render_blocks_for_chapter",
                autospec=True,
                return_value=[self._layout_problem_block(chapter_id=chapter_id, block_id=block_id)],
            ):
                with self.assertRaises(ExportGateError):
                    service.assert_chapter_exportable(chapter_id, ExportType.BILINGUAL_HTML)

            with patch.object(
                ExportService,
                "_render_blocks_for_chapter",
                autospec=True,
                return_value=[self._layout_valid_block(chapter_id=chapter_id, block_id=block_id)],
            ):
                service.assert_chapter_exportable(chapter_id, ExportType.BILINGUAL_HTML)

            issue = session.scalars(
                select(ReviewIssue).where(ReviewIssue.issue_type == "LAYOUT_VALIDATION_FAILURE")
            ).one()

        self.assertEqual(issue.status, IssueStatus.RESOLVED)
        self.assertEqual(issue.resolution_note, "Resolved by latest export-time layout validation check.")


if __name__ == "__main__":
    unittest.main()
