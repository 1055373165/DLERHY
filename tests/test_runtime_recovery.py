"""Runtime correctness: pause/resume recovery, pause-time budgets, lease sweeps,
retry guards, review-time frontier yield, pool timeouts, targeted-run scope."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.domain.enums import (
    ArtifactStatus,
    BlockType,
    ChapterStatus,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    PacketStatus,
    PacketType,
    ProtectedPolicy,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
    WorkerLeaseStatus,
)
from book_agent.domain.models import Block, Chapter, Document
from book_agent.domain.models.ops import DocumentRun, WorkItem, WorkerLease
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.orchestrator.reconciler import Reconciler
from book_agent.orchestrator.run_plan import RUN_REQUEST_KEY
from book_agent.services.run_control import RunBudgetSummary, RunControlService, RunControlTransitionError
from book_agent.services.run_execution import RunExecutionService
from book_agent.workers.failures import FailureDisposition, classify_failure
from book_agent.workers.providers.openai_compatible import ProviderHTTPError


def _budget(**overrides) -> RunBudgetSummary:
    params = dict(
        max_wall_clock_seconds=None,
        max_total_cost_usd=None,
        max_total_token_in=None,
        max_total_token_out=None,
        max_retry_count_per_work_item=None,
        max_consecutive_failures=None,
        max_parallel_workers=1,
        max_parallel_requests_per_provider=None,
        max_auto_followup_attempts=None,
    )
    params.update(overrides)
    return RunBudgetSummary(**params)


class RuntimeRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        db_path = Path(self.tempdir.name) / "recovery.db"
        self.engine = build_engine(f"sqlite+pysqlite:///{db_path}", connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=str(Path(self.tempdir.name) / "exports"),
            translation_worker=None,
        )

    # --- fixtures -----------------------------------------------------------

    def _create_document(self, *, packet_ordinals: list[int]) -> tuple[str, list[str]]:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"recovery-{uuid4()}",
                source_path=str(Path(self.tempdir.name) / "book.epub"),
                title="Recovery",
                status=DocumentStatus.ACTIVE,
            )
            session.add(document)
            session.flush()
            chapter = Chapter(document_id=document.id, ordinal=1, title_src="One", status=ChapterStatus.PACKET_BUILT)
            session.add(chapter)
            session.flush()
            packet_ids: list[str] = []
            for ordinal in packet_ordinals:
                block = Block(
                    chapter_id=chapter.id,
                    ordinal=ordinal,
                    block_type=BlockType.PARAGRAPH,
                    source_text=f"packet {ordinal}",
                    protected_policy=ProtectedPolicy.TRANSLATE,
                    status=ArtifactStatus.ACTIVE,
                )
                session.add(block)
                session.flush()
                packet = TranslationPacket(
                    chapter_id=chapter.id,
                    block_start_id=block.id,
                    block_end_id=block.id,
                    packet_type=PacketType.TRANSLATE,
                    book_profile_version=1,
                    packet_json={
                        "packet_id": str(uuid4()),
                        "chapter_id": chapter.id,
                        "current_blocks": [{"block_id": block.id, "sentence_ids": []}],
                        "packet_ordinal": ordinal,
                        "input_version_bundle": {"chapter_id": chapter.id, "packet_ordinal": ordinal},
                        "runtime_state": {"stage": "translate", "substate": "ready", "packet_ordinal": ordinal},
                    },
                    risk_score=0.1,
                    status=PacketStatus.BUILT,
                )
                session.add(packet)
                session.flush()
                packet_ids.append(packet.id)
            session.commit()
            return document.id, packet_ids

    def _create_running_run(
        self,
        document_id: str,
        *,
        budget: RunBudgetSummary | None = None,
        run_type: DocumentRunType = DocumentRunType.TRANSLATE_FULL,
        status_detail_json: dict | None = None,
    ) -> str:
        with self.session_factory() as session:
            control = RunControlService(RunControlRepository(session))
            created = control.create_run(
                document_id=document_id,
                run_type=run_type,
                requested_by="test",
                budget=budget,
                # These tests drive the translate stage directly.
                status_detail_json=status_detail_json or {"run_request": {"terminology": "skip"}},
            )
            control.resume_run(created.run_id, actor_id="test")
            session.commit()
            return created.run_id

    def _seed_and_claim(self, run_id: str, packet_id: str, *, lease_seconds: int = 60):
        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            execution.seed_translate_work_items(run_id=run_id, packet_ids=[packet_id])
            claimed = execution.claim_next_translate_work_item(
                run_id=run_id, worker_name="test", worker_instance_id="w1", lease_seconds=lease_seconds
            )
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=lease_seconds)
            session.commit()
            return claimed

    def _summary(self, run_id: str):
        with self.session_factory() as session:
            return RunControlService(RunControlRepository(session)).get_run_summary(run_id)

    # --- R3: pause-class failures are recoverable --------------------------

    def test_paused_run_resumes_and_reclaims_the_same_packet(self) -> None:
        document_id, (packet_id,) = self._create_document(packet_ordinals=[1])
        run_id = self._create_running_run(document_id, budget=_budget(max_retry_count_per_work_item=1))
        claimed = self._seed_and_claim(run_id, packet_id)

        self.executor._complete_failure(
            run_id=run_id,
            claimed=claimed,
            exc=ProviderHTTPError(402, "Insufficient Balance"),
            stage_key="translate",
        )
        paused = self._summary(run_id)
        self.assertEqual(paused.status, "paused")
        self.assertEqual(paused.work_items.status_counts["retryable_failed"], 1)
        self.assertEqual(paused.status_detail_json["control_counters"]["consecutive_failures"], 1)

        with self.session_factory() as session:
            control = RunControlService(RunControlRepository(session))
            resumed = control.resume_run(run_id, actor_id="operator", note="topped up")
            self.assertEqual(resumed.status, "running")
            self.assertEqual(resumed.status_detail_json["control_counters"]["consecutive_failures"], 0)
            execution = RunExecutionService(RunControlRepository(session))
            reclaimed = execution.claim_next_translate_work_item(
                run_id=run_id, worker_name="test", worker_instance_id="w2", lease_seconds=60
            )
            self.assertIsNotNone(reclaimed)
            assert reclaimed is not None
            self.assertEqual(reclaimed.work_item_id, claimed.work_item_id)
            self.assertEqual(reclaimed.attempt, 2)
            session.commit()

    # --- R10: paused time does not count against budgets -------------------

    def test_paused_time_is_excluded_from_wall_clock_and_no_progress_budgets(self) -> None:
        document_id, (packet_id,) = self._create_document(packet_ordinals=[1])
        run_id = self._create_running_run(
            document_id, budget=_budget(max_wall_clock_seconds=600, max_no_progress_seconds=60)
        )
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            control = RunControlService(RunControlRepository(session))
            control.pause_run(run_id, actor_id="operator", note="hold")
            run = session.get(DocumentRun, run_id)
            # Started 65 minutes ago, paused for the last hour: 5 active minutes against a 10 minute budget.
            run.started_at = now - timedelta(minutes=65)
            detail = dict(run.status_detail_json)
            detail["pause_accounting"] = {"paused_at": (now - timedelta(minutes=60)).isoformat()}
            detail["last_progress"] = {"completed_at": (now - timedelta(minutes=62)).isoformat()}
            run.status_detail_json = detail
            session.commit()

        with self.session_factory() as session:
            control = RunControlService(RunControlRepository(session))
            resumed = control.resume_run(run_id, actor_id="operator")
            accounting = resumed.status_detail_json["pause_accounting"]
            self.assertGreaterEqual(accounting["paused_seconds_total"], 3590)
            self.assertNotIn("paused_at", accounting)
            session.commit()

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            guardrail = execution.enforce_budget_guardrails(run_id=run_id)
            self.assertFalse(guardrail.budget_exceeded, guardrail.stop_reason)
            self.assertEqual(guardrail.run_summary.status, "running")

    # --- R4: leases of paused runs are swept; retry waits for them ---------

    def test_supervisor_sweeps_expired_leases_of_paused_runs_and_retry_waits_for_inflight_work(self) -> None:
        document_id, (packet_id,) = self._create_document(packet_ordinals=[1])
        run_id = self._create_running_run(document_id)
        claimed = self._seed_and_claim(run_id, packet_id, lease_seconds=60)
        with self.session_factory() as session:
            RunControlService(RunControlRepository(session)).pause_run(run_id, actor_id="operator")
            session.commit()

        with self.session_factory() as session:
            control = RunControlService(RunControlRepository(session))
            with self.assertRaises(RunControlTransitionError) as ctx:
                control.retry_run(run_id, actor_id="operator")
            self.assertIn("in-flight", str(ctx.exception))

        # Nothing to sweep while the lease is still valid.
        self.assertEqual(self.executor._reclaim_inactive_run_leases(), [])

        with self.session_factory() as session:
            lease = session.execute(
                select(WorkerLease).where(WorkerLease.lease_token == claimed.lease_token)
            ).scalar_one()
            lease.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
            item = session.get(WorkItem, claimed.work_item_id)
            item.lease_expires_at = lease.lease_expires_at
            session.commit()

        self.assertEqual(self.executor._reclaim_inactive_run_leases(), [run_id])

        with self.session_factory() as session:
            lease = session.execute(
                select(WorkerLease).where(WorkerLease.lease_token == claimed.lease_token)
            ).scalar_one()
            self.assertEqual(lease.status, WorkerLeaseStatus.EXPIRED)
            item = session.get(WorkItem, claimed.work_item_id)
            self.assertEqual(item.status, WorkItemStatus.RETRYABLE_FAILED)
            control = RunControlService(RunControlRepository(session))
            retried = control.retry_run(run_id, actor_id="operator")
            self.assertEqual(retried.resume_from_run_id, run_id)
            self.assertEqual(session.get(DocumentRun, run_id).status, DocumentRunStatus.PAUSED)
            session.commit()

    # --- R5: the translate frontier yields to review/export threads -------

    def test_translate_stage_does_not_seed_while_a_review_work_item_is_in_flight(self) -> None:
        document_id, packet_ids = self._create_document(packet_ordinals=[1, 2])
        run_id = self._create_running_run(document_id)
        with self.session_factory() as session:
            session.add(
                WorkItem(
                    run_id=run_id,
                    stage=WorkItemStage.REVIEW,
                    scope_type=WorkItemScopeType.DOCUMENT,
                    scope_id=document_id,
                    status=WorkItemStatus.RUNNING,
                    attempt=1,
                    input_version_bundle_json={"document_id": document_id},
                )
            )
            session.commit()

        self.assertFalse(self.executor._process_translate_stage(run_id))
        with self.session_factory() as session:
            translate_items = session.scalars(
                select(WorkItem).where(WorkItem.run_id == run_id, WorkItem.stage == WorkItemStage.TRANSLATE)
            ).all()
        self.assertEqual(translate_items, [])

        with self.session_factory() as session:
            review_item = session.execute(
                select(WorkItem).where(WorkItem.run_id == run_id, WorkItem.stage == WorkItemStage.REVIEW)
            ).scalar_one()
            review_item.status = WorkItemStatus.SUCCEEDED
            session.commit()
        self.assertTrue(self.executor._process_translate_stage(run_id))

    # --- R11: pool exhaustion is transient ---------------------------------

    def test_connection_pool_timeout_is_retryable(self) -> None:
        classification = classify_failure(SQLAlchemyTimeoutError("QueuePool limit of size 10 overflow 20 reached"))
        self.assertEqual(classification.disposition, FailureDisposition.RETRY)
        self.assertEqual(classification.reason, "database.pool_timeout")

    # --- R13: targeted runs are judged on their own packets ----------------

    def test_targeted_run_projection_and_reconciler_use_the_packet_scope(self) -> None:
        document_id, packet_ids = self._create_document(packet_ordinals=[1, 2])
        targeted, other = packet_ids
        run_id = self._create_running_run(
            document_id,
            run_type=DocumentRunType.TRANSLATE_TARGETED,
            status_detail_json={
                RUN_REQUEST_KEY: {"packet_ids": [targeted]},
                "pipeline": {"stages": {"translate": {"status": "succeeded"}}},
            },
        )
        with self.session_factory() as session:
            session.add(
                WorkItem(
                    run_id=run_id,
                    stage=WorkItemStage.TRANSLATE,
                    scope_type=WorkItemScopeType.PACKET,
                    scope_id=targeted,
                    status=WorkItemStatus.SUCCEEDED,
                    attempt=1,
                    input_version_bundle_json={"packet_id": targeted},
                )
            )
            session.get(TranslationPacket, targeted).status = PacketStatus.TRANSLATED
            # The other packet stays BUILT: it is not this run's concern.
            self.assertEqual(session.get(TranslationPacket, other).status, PacketStatus.BUILT)
            session.commit()

        summary = self._summary(run_id)
        pipeline = summary.status_detail_json["pipeline"]
        stages = pipeline.get("_cached_pipeline_stages") or pipeline.get("stages")
        self.assertEqual(stages["translate"]["status"], "succeeded")

        with self.session_factory() as session:
            findings = Reconciler(session).check_run(run_id)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
