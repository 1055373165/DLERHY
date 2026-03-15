import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from book_agent.domain.enums import DocumentStatus, DocumentRunType, SourceType
from book_agent.domain.models import Document
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_control import RunBudgetSummary, RunControlService
from book_agent.services.run_execution import RunExecutionService


class RunExecutionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _create_document(self) -> str:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"run-execution-{uuid4()}",
                source_path="/tmp/run-execution.epub",
                title="Run Execution Document",
                author="Run Execution Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.commit()
            return document.id

    def _create_running_run(self, *, budget: RunBudgetSummary | None = None) -> str:
        document_id = self._create_document()
        with self.session_factory() as session:
            repository = RunControlRepository(session)
            control = RunControlService(repository)
            run = control.create_run(
                document_id=document_id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                requested_by="test-runner",
                budget=budget,
            )
            resumed = control.resume_run(run.run_id, actor_id="test-runner", note="start")
            session.commit()
            return resumed.run_id

    def test_run_execution_success_lifecycle_updates_usage_and_terminal_state(self) -> None:
        run_id = self._create_running_run()
        packet_id = str(uuid4())

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            execution.seed_translate_work_items(run_id=run_id, packet_ids=[packet_id])
            claimed = execution.claim_next_translate_work_item(
                run_id=run_id,
                worker_name="test.translate",
                worker_instance_id="worker-1",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=60)
            self.assertTrue(execution.heartbeat_work_item(lease_token=claimed.lease_token, lease_seconds=60))
            execution.complete_translate_success(
                lease_token=claimed.lease_token,
                packet_id=packet_id,
                translation_run_id=str(uuid4()),
                token_in=120,
                token_out=45,
                cost_usd=0.0035,
                latency_ms=750,
            )
            summary = execution.reconcile_run_terminal_state(run_id=run_id)

        self.assertEqual(summary.status, "succeeded")
        self.assertEqual(summary.work_items.status_counts["succeeded"], 1)
        self.assertEqual(summary.status_detail_json["usage_summary"]["token_in"], 120)
        self.assertEqual(summary.status_detail_json["usage_summary"]["token_out"], 45)
        self.assertEqual(summary.status_detail_json["usage_summary"]["latency_ms"], 750)
        self.assertAlmostEqual(summary.status_detail_json["usage_summary"]["cost_usd"], 0.0035, places=8)

    def test_reclaim_expired_lease_requeues_work_item_and_increments_attempt_on_reclaim(self) -> None:
        run_id = self._create_running_run(
            budget=RunBudgetSummary(
                max_wall_clock_seconds=None,
                max_total_cost_usd=None,
                max_total_token_in=None,
                max_total_token_out=None,
                max_retry_count_per_work_item=2,
                max_consecutive_failures=None,
                max_parallel_workers=1,
                max_parallel_requests_per_provider=1,
                max_auto_followup_attempts=1,
            )
        )
        packet_id = str(uuid4())

        with self.session_factory() as session:
            repository = RunControlRepository(session)
            execution = RunExecutionService(repository)
            execution.seed_translate_work_items(run_id=run_id, packet_ids=[packet_id])
            claimed = execution.claim_next_translate_work_item(
                run_id=run_id,
                worker_name="test.translate",
                worker_instance_id="worker-2",
                lease_seconds=30,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=30)

            lease = repository.get_active_lease_by_token(claimed.lease_token)
            work_item = repository.get_work_item(claimed.work_item_id)
            expired_at = datetime.now(timezone.utc) - timedelta(minutes=2)
            lease.lease_expires_at = expired_at
            work_item.lease_expires_at = expired_at
            session.flush()

            reclaimed = execution.reclaim_expired_leases(run_id=run_id)
            self.assertEqual(reclaimed.expired_lease_count, 1)
            self.assertEqual(reclaimed.reclaimed_work_item_ids, [claimed.work_item_id])

            reclaimed_claim = execution.claim_next_translate_work_item(
                run_id=run_id,
                worker_name="test.translate",
                worker_instance_id="worker-3",
                lease_seconds=30,
            )

        self.assertIsNotNone(reclaimed_claim)
        assert reclaimed_claim is not None
        self.assertEqual(reclaimed_claim.attempt, 2)

    def test_budget_guardrail_pauses_run_when_cost_limit_is_exceeded(self) -> None:
        run_id = self._create_running_run(
            budget=RunBudgetSummary(
                max_wall_clock_seconds=None,
                max_total_cost_usd=0.001,
                max_total_token_in=None,
                max_total_token_out=None,
                max_retry_count_per_work_item=1,
                max_consecutive_failures=None,
                max_parallel_workers=1,
                max_parallel_requests_per_provider=1,
                max_auto_followup_attempts=1,
            )
        )
        packet_id = str(uuid4())

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            execution.seed_translate_work_items(run_id=run_id, packet_ids=[packet_id])
            claimed = execution.claim_next_translate_work_item(
                run_id=run_id,
                worker_name="test.translate",
                worker_instance_id="worker-4",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=60)
            execution.complete_translate_success(
                lease_token=claimed.lease_token,
                packet_id=packet_id,
                translation_run_id=str(uuid4()),
                token_in=10,
                token_out=5,
                cost_usd=0.005,
                latency_ms=100,
            )
            guardrail = execution.enforce_budget_guardrails(run_id=run_id)

        self.assertTrue(guardrail.budget_exceeded)
        self.assertEqual(guardrail.stop_reason, "budget.cost_exceeded")
        self.assertEqual(guardrail.run_summary.status, "paused")

    def test_consecutive_failure_budget_fails_run(self) -> None:
        run_id = self._create_running_run(
            budget=RunBudgetSummary(
                max_wall_clock_seconds=None,
                max_total_cost_usd=None,
                max_total_token_in=None,
                max_total_token_out=None,
                max_retry_count_per_work_item=0,
                max_consecutive_failures=1,
                max_parallel_workers=1,
                max_parallel_requests_per_provider=1,
                max_auto_followup_attempts=1,
            )
        )
        packet_id = str(uuid4())

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            execution.seed_translate_work_items(run_id=run_id, packet_ids=[packet_id])
            claimed = execution.claim_next_translate_work_item(
                run_id=run_id,
                worker_name="test.translate",
                worker_instance_id="worker-5",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=60)
            execution.complete_work_item_failure(
                lease_token=claimed.lease_token,
                error_class="RuntimeError",
                error_detail_json={"message": "boom"},
                retryable=False,
            )
            guardrail = execution.enforce_budget_guardrails(run_id=run_id)

        self.assertTrue(guardrail.budget_exceeded)
        self.assertEqual(guardrail.stop_reason, "budget.consecutive_failures_exceeded")
        self.assertEqual(guardrail.run_summary.status, "failed")


if __name__ == "__main__":
    unittest.main()
