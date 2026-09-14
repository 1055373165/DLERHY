"""Behavior of the server-side run loop.

- Run budgets are enforced on every tick. Previously
  ``RunExecutionService.enforce_budget_guardrails`` was only called by tests and a
  live-run script, so a run with an exhausted budget kept executing.
- A transient database error (e.g. losing a unique-index race while seeding work
  items) must not fail the whole run; any other unhandled error still does.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.domain.enums import DocumentRunType, DocumentStatus, SourceType
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_control import RunBudgetSummary, RunControlService


class ExecutorRunLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root="artifacts/exports",
            translation_worker=None,
            state_reconciler_interval_seconds=0,
            poll_interval_seconds=0,
        )

    def _create_running_run(self, budget: RunBudgetSummary | None) -> str:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"budget-guardrail-{uuid4()}",
                source_path="/tmp/budget-guardrail.epub",
                title="Budget Guardrail Document",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()
            control = RunControlService(RunControlRepository(session))
            run = control.create_run(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                requested_by="budget-test",
                budget=budget,
            )
            control.resume_run(run.run_id, actor_id="budget-test", note="start")
            session.commit()
            return run.run_id

    def _age_run(self, run_id: str, *, seconds: int) -> None:
        with self.session_factory() as session:
            run = session.get(DocumentRun, run_id)
            started = datetime.now(timezone.utc) - timedelta(seconds=seconds)
            run.started_at = started
            run.created_at = started
            session.commit()

    def test_run_loop_pauses_run_when_wall_clock_budget_is_exhausted(self) -> None:
        run_id = self._create_running_run(
            RunBudgetSummary(
                max_wall_clock_seconds=60,
                max_total_cost_usd=None,
                max_total_token_in=None,
                max_total_token_out=None,
                max_retry_count_per_work_item=None,
                max_consecutive_failures=None,
                max_parallel_workers=None,
                max_parallel_requests_per_provider=None,
                max_auto_followup_attempts=None,
            )
        )
        self._age_run(run_id, seconds=3600)

        self.executor._run_loop(run_id)

        with self.session_factory() as session:
            summary = RunControlService(RunControlRepository(session)).get_run_summary(run_id)
        self.assertEqual(summary.status, "paused")
        self.assertEqual(summary.stop_reason, "budget.wall_clock_exceeded")

    def test_guardrail_is_noop_without_budget(self) -> None:
        run_id = self._create_running_run(None)
        self._age_run(run_id, seconds=3600)

        self.assertFalse(self.executor._enforce_budget_guardrails(run_id))

        with self.session_factory() as session:
            summary = RunControlService(RunControlRepository(session)).get_run_summary(run_id)
        self.assertEqual(summary.status, "running")

    def _run_status(self, run_id: str) -> tuple[str, str | None]:
        with self.session_factory() as session:
            summary = RunControlService(RunControlRepository(session)).get_run_summary(run_id)
        return summary.status, summary.stop_reason

    def test_transient_integrity_error_does_not_fail_run(self) -> None:
        run_id = self._create_running_run(None)
        calls: list[int] = []

        def _flaky_translate_stage(_run_id: str) -> bool:
            calls.append(1)
            if len(calls) == 1:
                raise IntegrityError("INSERT INTO work_items", {}, Exception("unique violation"))
            self.executor._stop_event.set()
            return True

        with patch.object(self.executor, "_process_translate_stage", side_effect=_flaky_translate_stage):
            with self.assertLogs("book_agent.app.runtime.document_run_executor", level="WARNING"):
                self.executor._run_loop(run_id)

        self.assertEqual(len(calls), 2)
        self.assertEqual(self._run_status(run_id), ("running", None))

    def test_unexpected_error_fails_run(self) -> None:
        run_id = self._create_running_run(None)

        with patch.object(self.executor, "_process_translate_stage", side_effect=RuntimeError("boom")):
            with self.assertLogs("book_agent.app.runtime.document_run_executor", level="ERROR"):
                self.executor._run_loop(run_id)

        self.assertEqual(self._run_status(run_id), ("failed", "runner.unhandled_exception"))


if __name__ == "__main__":
    unittest.main()
