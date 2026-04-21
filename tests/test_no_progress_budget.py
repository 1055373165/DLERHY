# ruff: noqa: E402
"""Regression pins for the ``max_no_progress_seconds`` guardrail (P0.2b).

The guardrail lives in
:meth:`RunExecutionService.enforce_budget_guardrails` and pauses a run
when no successful work-item has completed for longer than the
configured window. It intentionally uses ``status_detail_json.last_progress.completed_at``
as the "last progress" clock so the guardrail can roll forward as the
run makes real progress, rather than firing on a fixed wall-clock
deadline (which is what :data:`max_wall_clock_seconds` already provides).
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import (
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun, RunBudget, WorkItem
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_execution import RunExecutionService


class NoProgressBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed_run(
        self,
        *,
        max_no_progress_seconds: int | None,
        last_progress_minutes_ago: float | None,
        run_started_minutes_ago: float,
        retryable_failed_count: int = 0,
    ) -> str:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"noprog-{uuid4()}",
                source_path="/tmp/noprog.epub",
                title="NoProgress",
                author="tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()
            detail: dict = {}
            if last_progress_minutes_ago is not None:
                last_at = now - timedelta(minutes=last_progress_minutes_ago)
                detail["last_progress"] = {
                    "packet_id": str(uuid4()),
                    "translation_run_id": str(uuid4()),
                    "completed_at": last_at.isoformat(),
                }
            run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="test",
                priority=100,
                status_detail_json=detail,
                started_at=now - timedelta(minutes=run_started_minutes_ago),
            )
            session.add(run)
            session.flush()
            if max_no_progress_seconds is not None:
                session.add(
                    RunBudget(
                        run_id=run.id,
                        max_no_progress_seconds=max_no_progress_seconds,
                    )
                )
            for _ in range(retryable_failed_count):
                session.add(
                    WorkItem(
                        run_id=run.id,
                        stage=WorkItemStage.TRANSLATE,
                        scope_type=WorkItemScopeType.PACKET,
                        scope_id=str(uuid4()),
                        status=WorkItemStatus.RETRYABLE_FAILED,
                        attempt=2,
                        priority=100,
                    )
                )
            session.commit()
            return run.id

    def _enforce(self, run_id: str):
        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            return execution.enforce_budget_guardrails(run_id=run_id)

    # ---- guardrail behaviour --------------------------------------------

    def test_no_budget_configured_is_a_noop(self) -> None:
        # A run without any RunBudget row must not be paused by the
        # no-progress guardrail — absence of a budget means "no cap".
        run_id = self._seed_run(
            max_no_progress_seconds=None,
            last_progress_minutes_ago=None,
            run_started_minutes_ago=60,
        )

        result = self._enforce(run_id)

        self.assertFalse(result.budget_exceeded)
        self.assertIsNone(result.stop_reason)
        self.assertEqual(result.run_summary.status, "running")

    def test_progress_within_window_does_not_pause(self) -> None:
        # Translation finished 1 minute ago, budget allows 10 minutes of
        # silence. The guardrail must stay quiet — this is the common
        # healthy-run path we don't want to disturb.
        run_id = self._seed_run(
            max_no_progress_seconds=600,
            last_progress_minutes_ago=1,
            run_started_minutes_ago=30,
        )

        result = self._enforce(run_id)

        self.assertFalse(result.budget_exceeded)
        self.assertEqual(result.run_summary.status, "running")

    def test_no_progress_since_last_success_pauses_run(self) -> None:
        # Last success was 11 minutes ago, budget allows 10 — the
        # classic "stuck on one chapter" failure mode. The guardrail
        # must PAUSE (not FAIL) so a ``resume_run`` after an operator
        # fix doesn't require a clean-retry cold start.
        run_id = self._seed_run(
            max_no_progress_seconds=600,
            last_progress_minutes_ago=11,
            run_started_minutes_ago=30,
            retryable_failed_count=3,
        )

        result = self._enforce(run_id)

        self.assertTrue(result.budget_exceeded)
        self.assertEqual(result.stop_reason, "budget.no_progress_exceeded")
        self.assertEqual(result.run_summary.status, "paused")
        detail = (
            (result.run_summary.status_detail_json.get("last_control") or {}).get("detail_json")
            or {}
        )
        self.assertEqual(detail.get("max_no_progress_seconds"), 600)
        self.assertGreaterEqual(int(detail.get("no_progress_seconds", 0)), 600)
        self.assertEqual(detail.get("stuck_retryable_failed_work_item_count"), 3)

    def test_no_progress_uses_run_started_at_when_nothing_has_succeeded(self) -> None:
        # A run that has NEVER produced a successful work-item has no
        # ``status_detail_json.last_progress`` entry. The guardrail must
        # fall back to ``run.started_at`` so stalled startup doesn't get
        # a free pass until the first success — which may never come.
        run_id = self._seed_run(
            max_no_progress_seconds=300,
            last_progress_minutes_ago=None,
            run_started_minutes_ago=10,
        )

        result = self._enforce(run_id)

        self.assertTrue(result.budget_exceeded)
        self.assertEqual(result.stop_reason, "budget.no_progress_exceeded")
        self.assertEqual(result.run_summary.status, "paused")


if __name__ == "__main__":
    unittest.main()
