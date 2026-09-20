"""The reader's overview of a book (GET /v1/documents/{id}/overview) and the cross-book inbox."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from book_agent.domain.enums import (
    ApprovalStatus,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    ExportStatus,
    ExportType,
    SourceType,
)
from book_agent.domain.models import Document
from book_agent.domain.models.agent import Approval
from book_agent.domain.models.review import Export
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_control import RunControlService


class BookOverviewTests(unittest.TestCase):
    def setUp(self) -> None:
        import book_agent.domain.models  # noqa: F401
        from book_agent.core.config import get_settings

        self.engine = build_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        patcher = patch.dict(
            os.environ,
            {
                "BOOK_AGENT_APP_SCOPE": "smoke",
                "BOOK_AGENT_DATABASE_URL": "sqlite+pysqlite:///:memory:",
                "BOOK_AGENT_AUTH_MODE": "disabled",
                "BOOK_AGENT_RUN_EXECUTOR_ENABLED": "false",
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        from book_agent.app.main import create_app

        app = create_app()
        app.state.session_factory = self.session_factory
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        pdf = patch("book_agent.app.api.routes.overview.pdf_renderer_available", return_value=False)
        pdf.start()
        self.addCleanup(pdf.stop)
        with self.session_factory() as session:
            document = Document(source_type=SourceType.WEB, file_fingerprint="overview", title="Harness", status=DocumentStatus.ACTIVE)
            session.add(document)
            session.commit()
            self.document_id = document.id

    def _run(self, run_type: DocumentRunType, status: DocumentRunStatus, *, stop_reason: str | None = None, request: dict | None = None) -> str:
        with self.session_factory() as session:
            control = RunControlService(RunControlRepository(session))
            created = control.create_run(
                document_id=self.document_id,
                run_type=run_type,
                requested_by="test",
                status_detail_json={"run_request": request or {}},
            )
            run = RunControlRepository(session).get_run(created.run_id)
            run.status = status
            run.stop_reason = stop_reason
            session.commit()
            return created.run_id

    def _export(self, export_type: ExportType, status: ExportStatus = ExportStatus.SUCCEEDED) -> None:
        with self.session_factory() as session:
            session.add(Export(document_id=self.document_id, export_type=export_type, file_path=f"/srv/exports/{export_type.value}.html", status=status))
            session.commit()

    def _overview(self) -> dict:
        response = self.client.get(f"/v1/documents/{self.document_id}/overview")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_never_started(self) -> None:
        overview = self._overview()
        self.assertEqual(overview["translation"]["state"], "never_started")
        self.assertFalse(overview["readable"]["ready"])
        self.assertEqual(overview["primary_action"]["kind"], "start")
        self.assertEqual(overview["readable"]["formats"][0], "web_html")
        self.assertNotIn("rebuilt_pdf", overview["readable"]["formats"])

    def test_running_offers_stop_and_counts_segments(self) -> None:
        self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.RUNNING, request={"fast_reading": "on", "auto_started": True})
        overview = self._overview()
        self.assertEqual(overview["translation"]["state"], "running")
        self.assertTrue(overview["translation"]["fast_reading"])
        self.assertTrue(overview["translation"]["auto_started"])
        self.assertEqual(overview["primary_action"]["kind"], "stop")
        self.assertEqual(overview["needs_you"], [])

    def test_a_pause_only_a_person_can_fix_needs_you(self) -> None:
        run_id = self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.PAUSED, stop_reason="provider.not_configured")
        overview = self._overview()
        (item,) = overview["needs_you"]
        self.assertEqual((item["kind"], item["id"]), ("paused", run_id))
        self.assertEqual(item["action"]["target"], "/settings")
        self.assertEqual(overview["primary_action"]["kind"], "handle")
        self.assertIn("服务商", overview["translation"]["stop_reason_text"])

    def test_a_pause_a_person_chose_does_not_need_you(self) -> None:
        run_id = self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.PAUSED, stop_reason="user.paused")
        overview = self._overview()
        self.assertEqual(overview["needs_you"], [])
        self.assertEqual(overview["primary_action"], {"kind": "resume", "label": "继续翻译", "run_id": run_id})

    def test_pending_approvals_need_you_and_reach_the_inbox(self) -> None:
        self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.RUNNING)
        with self.session_factory() as session:
            session.add(
                Approval(
                    document_id=self.document_id,
                    kind="lock_term",
                    status=ApprovalStatus.PENDING,
                    policy_id="policy.write_irreversible",
                    payload_json={"tool": "lock_term", "arguments": {"source_term": "harness", "target_term": "驾驭框架"}},
                )
            )
            session.commit()
        overview = self._overview()
        (item,) = overview["needs_you"]
        self.assertEqual(item["title"], "锁定术语：harness → 驾驭框架")
        self.assertIn("1 件事等你处理", overview["headline"])
        inbox = self.client.get("/v1/inbox").json()
        self.assertEqual(inbox["count"], 1)
        self.assertEqual(inbox["items"][0]["document_title"], "Harness")

    def test_a_readable_book_stays_readable_when_the_latest_run_is_a_failed_export(self) -> None:
        self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.SUCCEEDED)
        self._export(ExportType.MERGED_HTML)
        self._export(ExportType.WEB_HTML)
        self._run(DocumentRunType.EXPORT_FULL, DocumentRunStatus.FAILED, stop_reason="stage.evidence_failed", request={"export_type": "rebuilt_pdf"})
        overview = self._overview()
        self.assertTrue(overview["readable"]["ready"])
        self.assertEqual(overview["translation"]["state"], "succeeded")
        self.assertEqual(overview["primary_action"]["kind"], "read")
        self.assertEqual(overview["headline"], "中文版已就绪。")

    def test_a_web_page_is_readable_once_its_chinese_page_exists(self) -> None:
        self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.RUNNING)
        self._export(ExportType.MERGED_HTML)
        self.assertFalse(self._overview()["readable"]["ready"])
        self._export(ExportType.WEB_HTML)
        self.assertTrue(self._overview()["readable"]["ready"])

    def test_background_exports_are_activity_not_translation(self) -> None:
        self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.SUCCEEDED)
        self._export(ExportType.WEB_HTML)
        self._run(DocumentRunType.EXPORT_FULL, DocumentRunStatus.RUNNING, request={"export_type": "rebuilt_pdf"})
        overview = self._overview()
        self.assertEqual(overview["activity"]["exporting"], ["rebuilt_pdf"])
        self.assertEqual(overview["translation"]["state"], "succeeded")

    def test_terminal_and_draining_states(self) -> None:
        # Draining last: a document has at most one active run.
        cases = {
            DocumentRunStatus.FAILED: "retry",
            DocumentRunStatus.CANCELLED: "retry",
            DocumentRunStatus.SUCCEEDED_WITH_WARNINGS: "none",
            DocumentRunStatus.DRAINING: "stop",
        }
        for status, action in cases.items():
            with self.subTest(status=status):
                self._run(DocumentRunType.TRANSLATE_FULL, status, stop_reason="stage.evidence_failed" if status == DocumentRunStatus.FAILED else None)
                overview = self._overview()
                self.assertEqual(overview["translation"]["state"], status.value)
                self.assertEqual(overview["primary_action"]["kind"], action)

    def test_finished_with_warnings_is_readable_and_says_so(self) -> None:
        self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.SUCCEEDED_WITH_WARNINGS)
        self._export(ExportType.WEB_HTML)
        overview = self._overview()
        self.assertIn("有些可选检查没有完成", overview["headline"])
        self.assertEqual(overview["primary_action"]["kind"], "read")

    def test_asking_for_an_export_while_the_book_is_busy_says_so(self) -> None:
        self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.RUNNING)
        response = self.client.post(f"/v1/documents/{self.document_id}/export", json={"export_type": "web_html"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("还有一项任务在进行", response.json()["detail"])

    def _finish(self, run_id: str, status: DocumentRunStatus, minutes_ago: int = 0, stop_reason: str | None = None) -> None:
        from datetime import datetime, timedelta, timezone

        with self.session_factory() as session:
            run = RunControlRepository(session).get_run(run_id)
            run.status = status
            run.stop_reason = stop_reason
            run.finished_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
            session.commit()

    def test_the_inbox_reports_translations_that_just_became_readable_or_failed(self) -> None:
        run_id = self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.RUNNING)
        self.assertEqual(self.client.get("/v1/inbox").json()["updates"], [])
        self._export(ExportType.WEB_HTML)
        self._finish(run_id, DocumentRunStatus.SUCCEEDED)
        (update,) = self.client.get("/v1/inbox").json()["updates"]
        self.assertEqual((update["kind"], update["run_id"], update["document_title"]), ("readable", run_id, "Harness"))

        failed = self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.RUNNING)
        self._finish(failed, DocumentRunStatus.FAILED, stop_reason="stage.evidence_failed")
        kinds = {item["run_id"]: item["kind"] for item in self.client.get("/v1/inbox").json()["updates"]}
        self.assertEqual(kinds[failed], "failed")

    def test_old_endings_are_not_news(self) -> None:
        run_id = self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.RUNNING)
        self._export(ExportType.WEB_HTML)
        self._finish(run_id, DocumentRunStatus.SUCCEEDED, minutes_ago=30)
        self.assertEqual(self.client.get("/v1/inbox").json()["updates"], [])
        since = "2000-01-01T00:00:00+00:00"
        self.assertEqual(len(self.client.get("/v1/inbox", params={"since": since}).json()["updates"]), 1)

    def test_overviews_of_several_books_for_the_home_page(self) -> None:
        self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.RUNNING)
        response = self.client.get("/v1/overviews", params={"ids": f"{self.document_id},00000000-0000-4000-8000-000000000000"})
        self.assertEqual(response.status_code, 200, response.text)
        (overview,) = response.json()["overviews"]
        self.assertEqual(overview["document_id"], self.document_id)
        self.assertEqual(overview["translation"]["state"], "running")
        self.assertIsNone(overview["cost"])

    def test_steps_carry_when_they_last_changed(self) -> None:
        run_id = self._run(DocumentRunType.TRANSLATE_FULL, DocumentRunStatus.RUNNING)
        with self.session_factory() as session:
            run = RunControlRepository(session).get_run(run_id)
            run.status_detail_json = {
                **(run.status_detail_json or {}),
                "pipeline": {"_cached_pipeline_stages": {"translate": {"status": "running", "updated_at": "2026-09-20T08:00:00+00:00"}}},
            }
            session.commit()
        steps = {step["key"]: step for step in self._overview()["translation"]["steps"]}
        self.assertEqual(steps["translate"]["at"], "2026-09-20T08:00:00+00:00")
        self.assertIsNone(steps["review"]["at"])

    def test_unknown_book_is_404(self) -> None:
        self.assertEqual(self.client.get("/v1/documents/00000000-0000-4000-8000-000000000000/overview").status_code, 404)


if __name__ == "__main__":
    unittest.main()
