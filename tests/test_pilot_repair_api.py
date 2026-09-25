"""The pilot repair through the app: the repair's record, the reader's questions, the dev side's tickets.

GET /documents/{id}/pilot-repair, the page images attached to a question, the overview's pilot
block and needs_you, the inbox, answering a question, and GET/PATCH /pilot-defects.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from book_agent.domain.enums import DocumentRunStatus, DocumentRunType
from book_agent.domain.models.agent import Approval
from book_agent.domain.models.ops import DocumentRun
from book_agent.orchestrator.run_plan import CHAPTER_IDS_REQUEST_KEY, RUN_REQUEST_KEY, SCOPE_PILOT, SCOPE_REQUEST_KEY
from book_agent.services.pilot_quality import PilotQualityService
from book_agent.services.pilot_repair import PilotRepairService
from tests.pilot_repair_fixture import FakeZhWorker, PilotBench, edit_translation, sentence_ids


class PilotRepairApiTests(unittest.TestCase):
    def setUp(self) -> None:
        from book_agent.core.config import get_settings

        self.worker = FakeZhWorker(retranslation_leaks=True)
        self.bench = PilotBench(self.worker)
        self.addCleanup(self.bench.close)
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
        app.state.session_factory = self.bench.session_factory
        app.state.export_root = str(self.bench.export_root)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        pdf = patch("book_agent.app.api.routes.overview.pdf_renderer_available", return_value=False)
        pdf.start()
        self.addCleanup(pdf.stop)

        # A pilot with a sentence left untranslated (retranslating it leaks a program note, so the
        # repair is rolled back) and a stray glyph (no path repairs it).
        with self.bench.session_factory() as session:
            (omitted,) = sentence_ids(session, self.bench.document_id, "Teams track the score")
        self.worker.omit_sentences.add(omitted)
        chapter_ids = self.bench.translate_pilot()
        self.bench.export(chapter_ids)
        with self.bench.session_factory() as session:
            (glyph,) = sentence_ids(session, self.bench.document_id, "The first technique reads")
            edit_translation(session, glyph, lambda text: text.replace("这一句", "这¡句"))
            run = DocumentRun(
                document_id=self.bench.document_id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.SUCCEEDED,
                requested_by="test",
                billing_category="pilot",
                status_detail_json={RUN_REQUEST_KEY: {SCOPE_REQUEST_KEY: SCOPE_PILOT, CHAPTER_IDS_REQUEST_KEY: chapter_ids}},
            )
            session.add(run)
            session.flush()
            PilotQualityService(session).run(self.bench.document_id, run=run, trigger="pilot_run")
            session.commit()
            self.run_id = run.id
            repair = PilotRepairService(session, workflow_factory=self.bench.workflow).run_for_pilot(run)
            self.repair_id = repair.id

    def test_the_repair_record_and_its_questions(self) -> None:
        payload = self.client.get(f"/v1/documents/{self.bench.document_id}/pilot-repair").json()
        self.assertEqual((payload["repair_id"], payload["status"], payload["outcome"]), (self.repair_id, "finished", "escalated"))
        self.assertEqual(payload["run_id"], self.run_id)
        (attempt,) = payload["attempts"]
        self.assertEqual((attempt["tool"], attempt["outcome"]), ("retranslate_packets", "reverted"))
        self.assertEqual(sorted(question["check_id"] for question in payload["questions"]), ["L6.untranslated_sentence", "L7.stray_glyph"])
        image = next(question for question in payload["questions"] if question["images"])["images"][0]
        response = self.client.get(f"/v1/documents/{self.bench.document_id}/pilot-repair/evidence/{image}")
        self.assertEqual((response.status_code, response.headers["content-type"]), (200, "image/png"))
        self.assertEqual(self.client.get(f"/v1/documents/{self.bench.document_id}/pilot-repair/evidence/..%2Fx.png").status_code, 404)

    def test_the_overview_and_the_inbox_ask_the_reader_without_holding_the_pilot_up(self) -> None:
        overview = self.client.get(f"/v1/documents/{self.bench.document_id}/overview").json()
        repair = overview["pilot"]["repair"]
        self.assertEqual((repair["status"], repair["questions"]), ("finished", 2))
        self.assertIn("等你回答", repair["text"])
        questions = [item for item in overview["needs_you"] if item["kind"] == "question"]
        self.assertEqual(len(questions), 2)
        self.assertTrue(all(item["title"].endswith("？") for item in questions))
        self.assertEqual((questions[0]["action"]["approve_label"], questions[0]["action"]["reject_label"]), ("是", "否"))
        # The pilot is ready to read and to decide on: the questions do not take over.
        self.assertEqual(overview["pilot"]["state"], "ready")
        self.assertEqual(overview["primary_action"]["kind"], "start_book")
        self.assertTrue(overview["headline"].startswith("试译完成"), overview["headline"])

        inbox = self.client.get("/v1/inbox").json()
        self.assertEqual(sum(1 for item in inbox["items"] if item["kind"] == "question"), 2)

        answered = self.client.post(f"/v1/approvals/{questions[0]['id']}/approve", json={"decided_by": "human:reader"})
        self.assertEqual(answered.status_code, 200, answered.text)
        with self.bench.session_factory() as session:
            approval = session.get(Approval, questions[0]["id"])
            self.assertEqual((approval.status.value, approval.decided_by), ("approved", "human:reader"))
        overview = self.client.get(f"/v1/documents/{self.bench.document_id}/overview").json()
        self.assertEqual(len([item for item in overview["needs_you"] if item["kind"] == "question"]), 1)

    def test_the_repair_can_be_run_again_by_hand_but_not_twice_at_once(self) -> None:
        started: list[tuple[str, float | None]] = []

        class _Executor:
            def start_pilot_repair(self, document_id: str, *, budget_usd: float | None = None) -> str:
                started.append((document_id, budget_usd))
                return "r-new"

        with patch("book_agent.app.runtime.document_run_executor.ensure_document_run_executor", return_value=_Executor()):
            response = self.client.post(f"/v1/documents/{self.bench.document_id}/pilot-repair", json={"budget_usd": 0.1})
            self.assertEqual((response.status_code, response.json()["repair_id"]), (202, "r-new"))
            self.assertEqual(started, [(self.bench.document_id, 0.1)])
            from book_agent.domain.models.review import PilotRepair

            with self.bench.session_factory() as session:
                session.get(PilotRepair, self.repair_id).status = "running"
                session.commit()
            self.assertEqual(self.client.post(f"/v1/documents/{self.bench.document_id}/pilot-repair").status_code, 409)

    def test_the_dev_side_lists_and_closes_the_defect_tickets(self) -> None:
        tickets = self.client.get("/v1/pilot-defects").json()["tickets"]
        (glyph,) = [ticket for ticket in tickets if ticket["check_id"] == "L7.stray_glyph"]
        self.assertEqual((glyph["component"], glyph["classification_rule"], glyph["status"]), ("parser", 3, "open"))
        self.assertTrue(glyph["evidence"][0]["block_id"])
        self.assertEqual(glyph["impact"]["findings_after"], 1)
        # The book-specific problem has no ticket.
        self.assertFalse([ticket for ticket in tickets if ticket["check_id"] == "L6.untranslated_sentence"])
        closed = self.client.patch(f"/v1/pilot-defects/{glyph['ticket_id']}", json={"status": "fixed", "note": "commit abc123"})
        self.assertEqual(closed.json()["status"], "fixed")
        self.assertEqual(self.client.get("/v1/pilot-defects").json()["count"], len(tickets) - 1)
        self.assertEqual(self.client.patch(f"/v1/pilot-defects/{glyph['ticket_id']}", json={"status": "gone"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
