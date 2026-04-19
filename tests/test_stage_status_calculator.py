# ruff: noqa: E402
"""Phase 1 unit tests for :mod:`book_agent.orchestrator.stage_status`.

These lock in the derivation rules so the Reconciler (Phase 2) can trust
the calculator as the single source of truth for pipeline-stage status.
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
from book_agent.domain.models.ops import DocumentRun, StageTransition, WorkItem
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.orchestrator.stage_status import (
    StageStatus,
    StageStatusCalculator,
    StageTransitionLogger,
)


class StageStatusCalculatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed(
        self,
        *,
        translated: int = 0,
        pending: int = 0,
        failed_packet: int = 0,
    ) -> tuple[str, str]:
        with self.session_factory() as session:
            doc = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"calc-{uuid4()}",
                source_path="/tmp/calc.epub",
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
            for _ in range(translated):
                session.add(TranslationPacket(
                    chapter_id=chapter.id,
                    packet_type=PacketType.TRANSLATE,
                    book_profile_version=1,
                    status=PacketStatus.TRANSLATED,
                ))
            for _ in range(pending):
                session.add(TranslationPacket(
                    chapter_id=chapter.id,
                    packet_type=PacketType.TRANSLATE,
                    book_profile_version=1,
                    status=PacketStatus.BUILT,
                ))
            for _ in range(failed_packet):
                session.add(TranslationPacket(
                    chapter_id=chapter.id,
                    packet_type=PacketType.TRANSLATE,
                    book_profile_version=1,
                    status=PacketStatus.FAILED,
                ))
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
            return doc.id, run.id

    def _add_wi(self, run_id: str, stage: WorkItemStage,
                status: WorkItemStatus,
                export_type: ExportType | None = None) -> None:
        with self.session_factory() as session:
            bundle = {"export_type": export_type.value} if export_type else {}
            session.add(WorkItem(
                run_id=run_id,
                stage=stage,
                scope_type=WorkItemScopeType.PACKET
                    if stage == WorkItemStage.TRANSLATE
                    else WorkItemScopeType.DOCUMENT
                    if stage == WorkItemStage.REVIEW
                    else WorkItemScopeType.EXPORT,
                scope_id=str(uuid4()),
                status=status,
                attempt=1,
                input_version_bundle_json=bundle,
            ))
            session.commit()

    def test_translate_not_started_when_no_packets_no_workitems(self) -> None:
        doc_id, run_id = self._seed()
        with self.session_factory() as session:
            self.assertEqual(
                StageStatusCalculator(session).stage_status(run_id, doc_id, "translate"),
                StageStatus.NOT_STARTED,
            )

    def test_translate_running_when_pending_packets(self) -> None:
        doc_id, run_id = self._seed(translated=82, pending=348)
        for _ in range(82):
            self._add_wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.SUCCEEDED)
        for _ in range(348):
            self._add_wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.PENDING)
        with self.session_factory() as session:
            ev = StageStatusCalculator(session).stage_evidence(run_id, doc_id, "translate")
        self.assertEqual(ev.status, StageStatus.RUNNING)
        self.assertEqual(ev.total_packets, 430)
        self.assertEqual(ev.translated_packets, 82)
        self.assertEqual(ev.succeeded_work_items, 82)

    def test_translate_succeeded_only_when_everything_aligned(self) -> None:
        doc_id, run_id = self._seed(translated=3)
        for _ in range(3):
            self._add_wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.SUCCEEDED)
        with self.session_factory() as session:
            self.assertEqual(
                StageStatusCalculator(session).stage_status(run_id, doc_id, "translate"),
                StageStatus.SUCCEEDED,
            )

    def test_translate_failed_when_any_packet_failed(self) -> None:
        doc_id, run_id = self._seed(translated=2, failed_packet=1)
        for _ in range(3):
            self._add_wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.SUCCEEDED)
        with self.session_factory() as session:
            self.assertEqual(
                StageStatusCalculator(session).stage_status(run_id, doc_id, "translate"),
                StageStatus.FAILED,
            )

    def test_review_succeeded_requires_succeeded_workitem(self) -> None:
        doc_id, run_id = self._seed(translated=1)
        self._add_wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.SUCCEEDED)
        with self.session_factory() as session:
            calc = StageStatusCalculator(session)
            self.assertEqual(calc.stage_status(run_id, doc_id, "review"),
                             StageStatus.NOT_STARTED)
        self._add_wi(run_id, WorkItemStage.REVIEW, WorkItemStatus.RUNNING)
        with self.session_factory() as session:
            self.assertEqual(
                StageStatusCalculator(session).stage_status(run_id, doc_id, "review"),
                StageStatus.RUNNING,
            )

    def test_bilingual_and_merged_are_isolated_by_export_type(self) -> None:
        doc_id, run_id = self._seed(translated=1)
        self._add_wi(run_id, WorkItemStage.TRANSLATE, WorkItemStatus.SUCCEEDED)
        self._add_wi(run_id, WorkItemStage.EXPORT, WorkItemStatus.SUCCEEDED,
                     export_type=ExportType.BILINGUAL_HTML)
        self._add_wi(run_id, WorkItemStage.EXPORT, WorkItemStatus.PENDING,
                     export_type=ExportType.MERGED_HTML)
        with self.session_factory() as session:
            calc = StageStatusCalculator(session)
            self.assertEqual(
                calc.stage_status(run_id, doc_id, "bilingual_html"),
                StageStatus.SUCCEEDED,
            )
            self.assertEqual(
                calc.stage_status(run_id, doc_id, "merged_html"),
                StageStatus.RUNNING,
            )

    def test_unknown_stage_raises(self) -> None:
        doc_id, run_id = self._seed()
        with self.session_factory() as session:
            with self.assertRaises(ValueError):
                StageStatusCalculator(session).stage_status(run_id, doc_id, "xyz")


class StageTransitionLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed_run(self) -> str:
        with self.session_factory() as session:
            doc = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"log-{uuid4()}",
                source_path="/tmp/log.epub",
                title="t", author="a", src_lang="en", tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1, segmentation_version=1,
            )
            session.add(doc)
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
            return run.id

    def test_record_appends_row_in_caller_transaction(self) -> None:
        run_id = self._seed_run()
        with self.session_factory() as session:
            StageTransitionLogger(session).record(
                run_id=run_id,
                stage="translate",
                from_status="running",
                to_status="succeeded",
                triggered_by="main_loop",
                reason="all packets translated",
            )
            session.commit()
        with self.session_factory() as session:
            rows = list(session.scalars(
                __import__("sqlalchemy").select(StageTransition)
            ).all())
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].stage, "translate")
            self.assertEqual(rows[0].from_status, "running")
            self.assertEqual(rows[0].to_status, "succeeded")
            self.assertIn("test_stage_status_calculator.py", rows[0].caused_by_code)

    def test_record_rolls_back_with_caller_transaction(self) -> None:
        run_id = self._seed_run()
        try:
            with self.session_factory() as session:
                StageTransitionLogger(session).record(
                    run_id=run_id, stage="translate",
                    from_status="running", to_status="succeeded",
                    triggered_by="main_loop",
                )
                raise RuntimeError("oops")
        except RuntimeError:
            pass
        with self.session_factory() as session:
            count = session.scalar(
                __import__("sqlalchemy").select(
                    __import__("sqlalchemy").func.count(StageTransition.id)
                )
            )
            self.assertEqual(count, 0,
                "audit row must rollback with the caller's transaction")


if __name__ == "__main__":
    unittest.main()
