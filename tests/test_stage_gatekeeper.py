# ruff: noqa: E402
"""Phase 2 unit tests for :mod:`book_agent.orchestrator.stage_gate`.

The gatekeeper is a pure composition of :class:`StageStatusCalculator`
and ``STAGE_DEPENDENCIES``; these tests exist to pin down the DAG so
adding a future stage (e.g. ``zh_epub``) can't quietly break the
upstream-must-be-SUCCEEDED invariant.
"""

import os
import sys
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import (
    ChapterStatus,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    ExportType,
    PacketStatus,
    PacketType,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import DocumentRun, WorkItem
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.orchestrator.stage_gate import (
    STAGE_DEPENDENCIES,
    StageGateKeeper,
)
from book_agent.orchestrator.stage_status import StageStatus


class StageGateKeeperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed(self) -> tuple[str, str, str]:
        with self.session_factory() as session:
            doc = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"gate-{uuid4()}",
                source_path="/tmp/gate.epub",
                title="t", author="a", src_lang="en", tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1, segmentation_version=1,
            )
            session.add(doc)
            session.flush()
            chapter = Chapter(
                document_id=doc.id, ordinal=1,
                status=ChapterStatus.PACKET_BUILT,
            )
            session.add(chapter)
            session.flush()
            run = DocumentRun(
                document_id=doc.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="test",
                priority=100,
                status_detail_json={},
            )
            session.add(run)
            session.commit()
            return doc.id, run.id, chapter.id

    def _packet(self, chapter_id: str, status: PacketStatus) -> None:
        with self.session_factory() as session:
            session.add(TranslationPacket(
                chapter_id=chapter_id,
                packet_type=PacketType.TRANSLATE,
                book_profile_version=1,
                status=status,
            ))
            session.commit()

    def _wi(
        self,
        run_id: str,
        stage: WorkItemStage,
        status: WorkItemStatus,
        *,
        export_type: ExportType | None = None,
    ) -> None:
        with self.session_factory() as session:
            bundle = {"export_type": export_type.value} if export_type else {}
            scope_type = (
                WorkItemScopeType.PACKET if stage == WorkItemStage.TRANSLATE
                else WorkItemScopeType.DOCUMENT if stage == WorkItemStage.REVIEW
                else WorkItemScopeType.EXPORT
            )
            session.add(WorkItem(
                run_id=run_id,
                stage=stage,
                scope_type=scope_type,
                scope_id=str(uuid4()),
                status=status,
                attempt=1,
                input_version_bundle_json=bundle,
            ))
            session.commit()

    # --- DAG declaration ------------------------------------------------

    def test_stage_dependencies_chain_is_linear(self) -> None:
        self.assertEqual(STAGE_DEPENDENCIES["translate"], ())
        self.assertEqual(STAGE_DEPENDENCIES["review"], ("translate",))
        self.assertEqual(
            STAGE_DEPENDENCIES["bilingual_html"], ("translate", "review"),
        )
        self.assertEqual(
            STAGE_DEPENDENCIES["merged_html"],
            ("translate", "review", "bilingual_html"),
        )

    # --- gate rejection scenarios --------------------------------------

    def test_translate_can_always_start(self) -> None:
        doc_id, run_id, _ch = self._seed()
        with self.session_factory() as session:
            decision = StageGateKeeper(session).evaluate(
                run_id, doc_id, "translate"
            )
        self.assertTrue(decision.can_start)
        self.assertEqual(decision.blocked_by, ())

    def test_review_blocked_while_translate_running(self) -> None:
        doc_id, run_id, chapter_id = self._seed()
        self._packet(chapter_id, PacketStatus.TRANSLATED)
        self._packet(chapter_id, PacketStatus.BUILT)
        self._wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.SUCCEEDED)
        self._wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.PENDING)
        with self.session_factory() as session:
            decision = StageGateKeeper(session).evaluate(run_id, doc_id, "review")
        self.assertFalse(decision.can_start)
        self.assertEqual(decision.blocked_by, ("translate",))
        self.assertEqual(
            decision.upstream_statuses["translate"], StageStatus.RUNNING,
        )

    def test_bilingual_blocked_without_review_succeeded(self) -> None:
        doc_id, run_id, chapter_id = self._seed()
        self._packet(chapter_id, PacketStatus.TRANSLATED)
        self._wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.SUCCEEDED)
        self._wi(run_id, WorkItemStage.REVIEW, WorkItemStatus.RUNNING)
        with self.session_factory() as session:
            decision = StageGateKeeper(session).evaluate(
                run_id, doc_id, "bilingual_html"
            )
        self.assertFalse(decision.can_start)
        self.assertEqual(decision.blocked_by, ("review",))

    def test_merged_blocked_when_bilingual_pending(self) -> None:
        doc_id, run_id, chapter_id = self._seed()
        self._packet(chapter_id, PacketStatus.TRANSLATED)
        self._wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.SUCCEEDED)
        self._wi(run_id, WorkItemStage.REVIEW, WorkItemStatus.SUCCEEDED)
        self._wi(
            run_id, WorkItemStage.EXPORT, WorkItemStatus.PENDING,
            export_type=ExportType.BILINGUAL_HTML,
        )
        with self.session_factory() as session:
            decision = StageGateKeeper(session).evaluate(
                run_id, doc_id, "merged_html"
            )
        self.assertFalse(decision.can_start)
        self.assertEqual(decision.blocked_by, ("bilingual_html",))

    def test_merged_starts_when_all_upstream_succeeded(self) -> None:
        doc_id, run_id, chapter_id = self._seed()
        self._packet(chapter_id, PacketStatus.TRANSLATED)
        self._wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.SUCCEEDED)
        self._wi(run_id, WorkItemStage.REVIEW, WorkItemStatus.SUCCEEDED)
        self._wi(
            run_id, WorkItemStage.EXPORT, WorkItemStatus.SUCCEEDED,
            export_type=ExportType.BILINGUAL_HTML,
        )
        with self.session_factory() as session:
            decision = StageGateKeeper(session).evaluate(
                run_id, doc_id, "merged_html"
            )
        self.assertTrue(decision.can_start)
        self.assertEqual(decision.blocked_by, ())

    def test_unknown_stage_raises(self) -> None:
        doc_id, run_id, _ = self._seed()
        with self.session_factory() as session:
            with self.assertRaises(ValueError):
                StageGateKeeper(session).evaluate(run_id, doc_id, "nonsense")


if __name__ == "__main__":
    unittest.main()
