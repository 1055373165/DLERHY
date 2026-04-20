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

    def test_run_succeeds_when_only_required_stage_translate_is_green(self) -> None:
        # Post-P0.2a behaviour: translate is the sole *required* stage;
        # review / bilingual_html / merged_html are optional and, when
        # they have no work_items at all, ``StageStatusCalculator`` derives
        # them to NOT_STARTED which is interpreted as "not requested". With
        # translate terminal-green and nothing requested beyond it, the
        # reconciler must allow the run to reach SUCCEEDED. This pins the
        # classifier routing added in P0.2a.
        _, run_id = self._seed(
            translated_packet_count=1,
            built_packet_count=0,
            translate_work_item_statuses=[WorkItemStatus.SUCCEEDED],
        )

        summary = self._reconcile(run_id)

        self.assertEqual(summary.status, "succeeded")
        last_control_detail = (
            (summary.status_detail_json.get("last_control") or {}).get("detail_json") or {}
        )
        self.assertEqual(last_control_detail.get("run_outcome"), "succeeded")
        # Optional stages that were never requested must not be reported
        # as failed — only required-stage failures and optional-stage
        # failures are surfaced in the control-detail payload.
        self.assertNotIn("has_warnings", last_control_detail)

    def test_run_stays_running_when_no_physical_evidence_has_landed(self) -> None:
        # Liveness guard: a run with zero packets and zero work_items is
        # in the startup window where the frontier seeder has not yet
        # produced evidence. The reconciler must leave it RUNNING so the
        # seeder can make progress on the next loop iteration; otherwise
        # every newly-queued run would race to SUCCEEDED before any work
        # is scheduled. Protects against the reviewer-flagged regression
        # in commit 609fe59 (translate-optional + zero evidence would
        # otherwise classify as SUCCEEDED).
        _, run_id = self._seed(
            translated_packet_count=0,
            built_packet_count=0,
            translate_work_item_statuses=[],
        )

        summary = self._reconcile(run_id)

        self.assertEqual(summary.status, "running")

    def test_draining_run_pauses_when_claimable_work_remains(self) -> None:
        # DRAINING transitions are distinct from RUNNING: once the operator
        # has asked the system to drain, any still-claimable work means the
        # drain itself cannot be declared clean. ``reconcile`` must flip the
        # run to PAUSED with the drain-specific stop reason rather than
        # continuing to treat it as live.
        _, run_id = self._seed(
            translated_packet_count=0,
            built_packet_count=1,
            translate_work_item_statuses=[WorkItemStatus.PENDING],
            run_status=DocumentRunStatus.DRAINING,
        )

        summary = self._reconcile(run_id)

        self.assertEqual(summary.status, "paused")
        self.assertEqual(summary.stop_reason, "run.drain_complete_with_pending_items")

    def test_draining_run_fails_when_stage_evidence_failed(self) -> None:
        # Evidence-driven failure must fire regardless of whether the run was
        # RUNNING or DRAINING at the time the reconciler ran: a TERMINAL_FAILED
        # work item makes the translate stage FAILED, and a drained run that
        # hit a stage-level failure should land in FAILED (not PAUSED) with
        # the same stop_reason as the RUNNING-path counterpart.
        _, run_id = self._seed(
            translated_packet_count=0,
            built_packet_count=1,
            translate_work_item_statuses=[WorkItemStatus.TERMINAL_FAILED],
            run_status=DocumentRunStatus.DRAINING,
        )

        summary = self._reconcile(run_id)

        self.assertEqual(summary.status, "failed")
        self.assertEqual(summary.stop_reason, "stage.evidence_failed")

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
