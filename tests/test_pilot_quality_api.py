# ruff: noqa: E402
"""The pilot's layered QA as a pipeline stage and an API (services/pilot_quality.py).

docs/agent-upgrade/08-pilot-quality-gate.md §6. A pilot run ends with its ``pilot_qa`` stage, which
keeps a report per run; the book overview says each layer's verdict; the report can be read and
run again by hand. Runs with the echo model, whose "ZH::" prefix is a leaked program marker: the
pilot still goes to the reader, with the blocking finding listed.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from sqlalchemy import select

os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")

from book_agent.app.main import create_app
from book_agent.domain.enums import DocumentRunType
from book_agent.domain.models import Chapter
from book_agent.domain.models.ops import DocumentRun
from book_agent.domain.models.review import PilotQualityReport
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.orchestrator.run_plan import PILOT_QA_STAGE, plan_for_run
from book_agent.orchestrator.stage_status import StageStatus, StageStatusCalculator
from book_agent.services.pilot_quality import PilotQualityService, exported_chapters, latest_report
from tests.document_actions import SyncDocumentActionClient
from tests.test_api_workflow import CONTAINER_XML, _content_opf_with_chapters, _nav_xhtml_with_chapters
from tests.test_pilot_run import BOOK


class PilotQaPlanTests(unittest.TestCase):
    def test_only_pilot_runs_plan_the_qa_stage_and_it_comes_last(self) -> None:
        pilot = plan_for_run(DocumentRunType.TRANSLATE_FULL, {"run_request": {"scope": "pilot", "chapter_ids": ["c1"]}})
        self.assertEqual(pilot.stages[-1], PILOT_QA_STAGE)
        book = plan_for_run(DocumentRunType.TRANSLATE_FULL, {"run_request": {}})
        self.assertNotIn(PILOT_QA_STAGE, book.stages)
        reviewed = plan_for_run(
            DocumentRunType.TRANSLATE_FULL, {"run_request": {"scope": "pilot", "export_review": "sampled"}}
        )
        self.assertEqual(reviewed.stages[-2:], ("export_review", PILOT_QA_STAGE))

    def test_exported_chapters_are_read_from_the_merged_html(self) -> None:
        html = (
            "<main><section class='chapter' id='chapter-c1'><div class='chapter-head'><h2>1 为什么</h2></div>"
            "<section class='block paragraph'><div class='zh'>正文</div></section></section>"
            "<section class='chapter' id='chapter-c3'><div class='chapter-head'><h2>3 工具</h2></div></section></main>"
        )
        chapters = exported_chapters(html)
        self.assertEqual([(chapter.chapter_id, chapter.title) for chapter in chapters], [("c1", "1 为什么"), ("c3", "3 工具")])


class PilotQualityRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'pilot-qa.db'}", connect_args={"check_same_thread": False, "timeout": 30}
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.app = create_app()
        self.app.state.session_factory = self.session_factory
        self.app.state.export_root = str(Path(self.tempdir.name) / "exports")
        self.app.state.upload_root = str(Path(self.tempdir.name) / "uploads")
        self.client = SyncDocumentActionClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self._stop_executor)

    def _stop_executor(self) -> None:
        executor = getattr(self.app.state, "document_run_executor", None)
        if executor is not None:
            executor.stop()
            self.app.state.document_run_executor = None

    def _import(self) -> str:
        path = Path(self.tempdir.name) / f"book-{time.monotonic_ns()}.epub"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", _content_opf_with_chapters([(title, href) for title, href, _ in BOOK]))
            archive.writestr("OEBPS/nav.xhtml", _nav_xhtml_with_chapters([(title, href) for title, href, _ in BOOK]))
            for _title, href, content in BOOK:
                archive.writestr(f"OEBPS/{href}", content)
        response = self.client.post("/v1/documents/bootstrap", json={"source_path": str(path)})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["document_id"]

    def _wait(self, run_id: str, timeout: float = 60.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            payload = self.client.get(f"/v1/runs/{run_id}").json()
            if payload["status"] in {"succeeded", "succeeded_with_warnings", "failed", "paused", "cancelled"}:
                return payload
            time.sleep(0.2)
        self.fail(f"run {run_id} did not finish")

    def test_the_pilot_ends_with_its_layered_qa_and_the_reader_sees_it(self) -> None:
        document_id = self._import()
        self.assertEqual(self.client.get(f"/v1/documents/{document_id}/pilot-quality").status_code, 404)
        self.assertIsNone(self.client.get(f"/v1/documents/{document_id}/overview").json()["pilot"]["quality"])

        started = self.client.post(f"/v1/documents/{document_id}/pilot", json={"fast_reading": True})
        self.assertEqual(started.status_code, 202, started.text)
        pilot_id = started.json()["run_id"]
        # Blocking findings do not fail the pilot: it is shown to the reader with them listed.
        self.assertEqual(self._wait(pilot_id)["status"], "succeeded")

        with self.session_factory() as session:
            reports = session.scalars(select(PilotQualityReport).where(PilotQualityReport.run_id == pilot_id)).all()
            self.assertEqual(len(reports), 1)
            report = reports[0]
            self.assertEqual(report.trigger, "pilot_run")
            run = session.get(DocumentRun, pilot_id)
            self.assertEqual(
                StageStatusCalculator(session).stage_status(pilot_id, run.document_id, PILOT_QA_STAGE), StageStatus.SUCCEEDED
            )
            chapter_titles = {
                str(chapter.id): chapter.title_src
                for chapter in session.scalars(select(Chapter).where(Chapter.document_id == document_id)).all()
            }
            cached = ((run.status_detail_json or {}).get("pipeline") or {}).get("_cached_pipeline_stages") or {}
        self.assertEqual([chapter_titles[chapter_id] for chapter_id in report.chapter_ids_json], ["1 Why agents", "2 The loop"])
        self.assertEqual(cached[PILOT_QA_STAGE]["status"], "succeeded")
        self.assertEqual(cached[PILOT_QA_STAGE]["report_id"], report.id)

        payload = self.client.get(f"/v1/documents/{document_id}/pilot-quality").json()
        self.assertEqual(payload["report_id"], report.id)
        self.assertEqual(payload["run_id"], pilot_id)
        self.assertEqual([layer["layer"] for layer in payload["layers"]], ["L1", "L2", "L3", "L4", "L5", "L6", "L7"])
        self.assertEqual([chapter["title"] for chapter in payload["chapters"]], ["1 Why agents", "2 The loop"])
        self.assertEqual(payload["export_source"], "file")
        # The echo model writes "ZH::" before every translation: a leaked program marker, blocking.
        l7 = payload["layers"][6]
        self.assertEqual(l7["status"], "blocking")
        leaked = [finding for finding in l7["findings"] if finding["check_id"] == "L7.leaked_marker"]
        self.assertTrue(leaked)
        self.assertEqual(leaked[0]["evidence"]["marker"], "ZH::")
        self.assertEqual(set(leaked[0]["location"]) >= {"chapter_id", "block_id", "packet_id"}, True)
        self.assertEqual(payload["status"], "blocking")
        self.assertGreater(payload["counts"]["blocking"], 0)

        overview = self.client.get(f"/v1/documents/{document_id}/overview").json()
        self.assertEqual(overview["pilot"]["state"], "ready")
        quality = overview["pilot"]["quality"]
        self.assertEqual((quality["report_id"], quality["status"]), (report.id, "blocking"))
        self.assertEqual([layer["layer"] for layer in quality["layers"]], ["L1", "L2", "L3", "L4", "L5", "L6", "L7"])
        self.assertEqual(quality["layers"][6]["counts"]["blocking"], l7["counts"]["blocking"])
        self.assertIn(("pilot_qa", "试译质检"), [(step["key"], step["label"]) for step in overview["translation"]["steps"]])

        # Run again by hand: a new report for the same pilot; the latest is the one shown.
        again = self.client.post(f"/v1/documents/{document_id}/pilot-quality")
        self.assertEqual(again.status_code, 200, again.text)
        self.assertNotEqual(again.json()["report_id"], report.id)
        self.assertEqual(again.json()["trigger"], "manual")
        self.assertEqual(self.client.get(f"/v1/documents/{document_id}/pilot-quality").json()["report_id"], again.json()["report_id"])

        # The whole-book run after it has no QA stage and writes no report.
        book = self.client.post(
            "/v1/runs",
            json={"document_id": document_id, "run_type": "translate_full", "requested_by": "t", "status_detail_json": {}},
        )
        self.assertIn(book.status_code, {200, 201, 202}, book.text)
        with self.session_factory() as session:
            book_run = session.get(DocumentRun, book.json()["run_id"])
            self.assertNotIn(PILOT_QA_STAGE, plan_for_run(book_run.run_type, book_run.status_detail_json).stages)

    def test_a_qa_error_is_kept_and_does_not_fail_the_pilot(self) -> None:
        document_id = self._import()
        with self.session_factory() as session:
            run = DocumentRun(
                document_id=document_id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status="succeeded",
                requested_by="t",
                billing_category="pilot",
                status_detail_json={"run_request": {"scope": "pilot"}},
            )
            session.add(run)
            session.flush()
            with mock.patch("book_agent.services.pilot_quality.pilot_qa.run_layers", side_effect=RuntimeError("boom")):
                report = PilotQualityService(session).run_for_pilot(run)
            session.commit()
            self.assertEqual(report.status, "error")
            self.assertEqual(report.error_text, "RuntimeError: boom")
            self.assertEqual(latest_report(session, document_id).id, report.id)
        payload = self.client.get(f"/v1/documents/{document_id}/pilot-quality").json()
        self.assertEqual((payload["status"], payload["layers"]), ("error", []))

    def test_rerun_without_pilot_chapters_is_refused(self) -> None:
        missing = self.client.post("/v1/documents/00000000-0000-0000-0000-000000000000/pilot-quality")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
