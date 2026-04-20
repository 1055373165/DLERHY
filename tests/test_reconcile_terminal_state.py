# ruff: noqa: E402
"""Regression pins for :meth:`RunExecutionService.reconcile_run_terminal_state`.

Covers the Phase-2 state-consistency refactor: terminal transitions are now
evidence-driven via :class:`StageStatusCalculator`, not derived from raw
work-item counters. The production defect these tests guard against is
"run marked succeeded while 344 BUILT packets remain unseeded" — the
frontier-seeding pattern only creates one packet-scoped work item per
chapter at a time, so ``inflight=0`` does not imply ``translate=succeeded``.
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
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_execution import RunExecutionService


class ReconcileTerminalStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed(
        self,
        *,
        translated_packet_count: int,
        built_packet_count: int,
        translate_work_item_statuses: list[WorkItemStatus],
        run_status: DocumentRunStatus = DocumentRunStatus.RUNNING,
    ) -> tuple[str, str]:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"reconcile-{uuid4()}",
                source_path="/tmp/reconcile.epub",
                title="Reconcile",
                author="tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()
            chapter = Chapter(
                document_id=document.id,
                ordinal=1,
                status=ChapterStatus.PACKET_BUILT,
            )
            session.add(chapter)
            session.flush()
            for _ in range(translated_packet_count):
                session.add(
                    TranslationPacket(
                        chapter_id=chapter.id,
                        packet_type=PacketType.TRANSLATE,
                        book_profile_version=1,
                        status=PacketStatus.TRANSLATED,
                    )
                )
            for _ in range(built_packet_count):
                session.add(
                    TranslationPacket(
                        chapter_id=chapter.id,
                        packet_type=PacketType.TRANSLATE,
                        book_profile_version=1,
                        status=PacketStatus.BUILT,
                    )
                )
            run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=run_status,
                requested_by="test",
                priority=100,
                status_detail_json={"pipeline": {"stages": {}}},
            )
            session.add(run)
            session.flush()
            for status in translate_work_item_statuses:
                session.add(
                    WorkItem(
                        run_id=run.id,
                        stage=WorkItemStage.TRANSLATE,
                        scope_type=WorkItemScopeType.PACKET,
                        scope_id=str(uuid4()),
                        status=status,
                        attempt=1,
                        priority=100,
                    )
                )
            session.commit()
            return document.id, run.id

    def _reconcile(self, run_id: str):
        with self.session_factory() as session:
            service = RunExecutionService(RunControlRepository(session))
            return service.reconcile_run_terminal_state(run_id=run_id)

    # ---- regression tests -------------------------------------------------

    def test_run_stays_running_when_built_packets_remain(self) -> None:
        # Production defect replay: 1 translate work_item SUCCEEDED but
        # the chapter still has BUILT packets the frontier has not
        # seeded yet. Old code decided ``inflight=0 & claimable=0`` →
        # run.succeeded; new code consults StageStatusCalculator, finds
        # translate stage still RUNNING because packets are incomplete,
        # and refuses the transition.
        _, run_id = self._seed(
            translated_packet_count=1,
            built_packet_count=344,
            translate_work_item_statuses=[WorkItemStatus.SUCCEEDED],
        )

        summary = self._reconcile(run_id)

        self.assertEqual(summary.status, "running")

    def test_run_succeeds_when_every_packet_translated(self) -> None:
        # Happy path: every packet terminal TRANSLATED, every work_item
        # terminal SUCCEEDED, no optional stages in play. Calculator
        # derives all stages to SUCCEEDED (translate by packet ledger;
        # review/export by absence-of-work_items → NOT_STARTED, which
        # currently blocks succeeded). Adjust expectation once the
        # optional-stage layer (P0.2) lands. For now assert the run is
        # still RUNNING because review/export did not execute.
        _, run_id = self._seed(
            translated_packet_count=1,
            built_packet_count=0,
            translate_work_item_statuses=[WorkItemStatus.SUCCEEDED],
        )

        summary = self._reconcile(run_id)

        # With all four pipeline stages currently treated as required,
        # a translate-only dataset derives to NOT_STARTED for review /
        # bilingual_html / merged_html. The reconciler refuses to mark
        # the run succeeded until every stage is SUCCEEDED. This pins
        # the guard until P0.2 introduces optional-stage classification.
        self.assertEqual(summary.status, "running")

    def test_run_fails_when_translate_work_item_terminal_failed(self) -> None:
        # Evidence-driven failure: TERMINAL_FAILED work_item makes
        # :class:`StageStatusCalculator` return FAILED for translate,
        # so reconcile transitions the run to FAILED with a stage-level
        # stop_reason rather than the legacy counter-based reason.
        _, run_id = self._seed(
            translated_packet_count=0,
            built_packet_count=1,
            translate_work_item_statuses=[WorkItemStatus.TERMINAL_FAILED],
        )

        summary = self._reconcile(run_id)

        self.assertEqual(summary.status, "failed")
        self.assertEqual(summary.stop_reason, "stage.evidence_failed")
        last_control_detail = (
            (summary.status_detail_json.get("last_control") or {}).get("detail_json") or {}
        )
        self.assertIn("translate", last_control_detail.get("failed_stages", []))
        self.assertEqual(
            last_control_detail.get("stage_status", {}).get("translate"),
            "failed",
        )


if __name__ == "__main__":
    unittest.main()
