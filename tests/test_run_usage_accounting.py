"""Run spend has one source: llm.call.completed events attributed to the run."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from book_agent.app.main import create_app
from book_agent.core.run_context import bind_run_context, current_run_id
from book_agent.domain.enums import DocumentRunType, DocumentStatus, SourceType
from book_agent.domain.models import Document
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_control import RunBudgetSummary, RunControlService
from book_agent.translation.contracts import TranslationUsage
from book_agent.workers.llm_calls import observed_llm_call


class _NoopExecutor:
    def wake(self, _run_id: str | None = None) -> None:
        return None

    def stop(self) -> None:
        return None


class RunUsageAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        db_path = Path(self.tempdir.name) / "usage.db"
        self.engine = build_engine(f"sqlite+pysqlite:///{db_path}", connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _create_run(self, *, budget: RunBudgetSummary | None = None) -> str:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint="usage-fp",
                source_path=str(Path(self.tempdir.name) / "book.epub"),
                title="Usage",
                status=DocumentStatus.ACTIVE,
            )
            session.add(document)
            session.flush()
            control = RunControlService(RunControlRepository(session))
            summary = control.create_run(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                requested_by="test",
                budget=budget,
            )
            control.resume_run(summary.run_id, actor_id="test")
            session.commit()
            return summary.run_id

    def test_calls_inside_a_bound_run_context_are_attributed_to_the_run(self) -> None:
        run_id = self._create_run()
        self.assertIsNone(current_run_id())
        with self.session_factory() as session:
            with bind_run_context(run_id):
                self.assertEqual(current_run_id(), run_id)
                with observed_llm_call(session, call_kind="concept.resolve", model="m", chapter_id="ch") as call:
                    call.complete(TranslationUsage(token_in=30, token_out=12, total_tokens=42, cost_usd=0.02, latency_ms=9))
                with observed_llm_call(session, call_kind="terminology.survey", model="m", chapter_id="ch") as call:
                    call.complete(TranslationUsage(token_in=10, token_out=3, total_tokens=13, cost_usd=0.01, latency_ms=4))
            # Outside the context nothing is attributed.
            with observed_llm_call(session, call_kind="provider.test", model="m") as call:
                call.complete(TranslationUsage(token_in=999, token_out=1, total_tokens=1000, cost_usd=5.0))
            session.commit()
            self.assertIsNone(current_run_id())

            summary = RunControlService(RunControlRepository(session)).get_run_summary(run_id)
            usage = summary.status_detail_json["usage_summary"]

        self.assertEqual(usage["call_count"], 2)
        self.assertEqual(usage["token_in"], 40)
        self.assertEqual(usage["token_out"], 15)
        self.assertEqual(usage["total_tokens"], 55)
        self.assertAlmostEqual(usage["cost_usd"], 0.03, places=8)
        self.assertEqual(usage["latency_ms"], 13)

    def test_cost_endpoint_reports_totals_chapters_and_budget_headroom(self) -> None:
        run_id = self._create_run(
            budget=RunBudgetSummary(
                max_wall_clock_seconds=None,
                max_total_cost_usd=1.0,
                max_total_token_in=100,
                max_total_token_out=None,
                max_retry_count_per_work_item=None,
                max_consecutive_failures=None,
                max_parallel_workers=None,
                max_parallel_requests_per_provider=None,
                max_auto_followup_attempts=None,
            )
        )
        with self.session_factory() as session, bind_run_context(run_id):
            for chapter_id, cost in (("ch-a", 0.25), ("ch-b", 0.05), ("ch-a", 0.10)):
                with observed_llm_call(session, call_kind="translate", model="m", chapter_id=chapter_id) as call:
                    call.complete(TranslationUsage(token_in=10, token_out=5, total_tokens=15, cost_usd=cost, latency_ms=1))
            session.commit()

        app = create_app()
        app.state.session_factory = self.session_factory
        app.state.document_run_executor = _NoopExecutor()
        with TestClient(app) as client:
            response = client.get(f"/v1/runs/{run_id}/cost")
            missing = client.get("/v1/runs/00000000-0000-0000-0000-000000000000/cost")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["totals"]["call_count"], 3)
        self.assertEqual(payload["totals"]["token_in"], 30)
        self.assertAlmostEqual(payload["totals"]["cost_usd"], 0.40, places=8)
        self.assertEqual([c["chapter_id"] for c in payload["chapters"]], ["ch-a", "ch-b"])
        self.assertAlmostEqual(payload["chapters"][0]["cost_usd"], 0.35, places=8)
        self.assertAlmostEqual(payload["budget"]["remaining"]["cost_usd"], 0.60, places=8)
        self.assertEqual(payload["budget"]["remaining"]["token_in"], 70)
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
