"""The parse health report through the app: written after import and after a structure refresh, read over the API.

GET /v1/documents/{id}/parse-health returns the latest report; POST runs the checks again (books imported
before the checks existed have none). The book overview carries a short summary for the UI.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from book_agent.domain.models import ParseHealthReport
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from tests.test_api_workflow import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML

_BODY = (
    "Retrieval gives a language model facts it never saw during training, and this paragraph is long "
    "enough to read as ordinary running prose in the body of the chapter, line after line. "
)


def _write_parted_book(path: Path) -> None:
    """A text PDF with an outline: a preface, a part and two chapters nested under it, every page full of prose."""
    import fitz

    document = fitz.open()
    titles = {2: "Preface", 3: "Part 1 Basics", 4: "1 First steps", 6: "2 Second steps"}
    for number in range(1, 8):
        page = document.new_page(width=531, height=666)
        top = 72
        if number in titles:
            page.insert_text((72, 110), titles[number], fontname="tibo", fontsize=22)
            top = 140
        page.insert_textbox(fitz.Rect(72, top, 460, 620), _BODY * 7, fontname="tiro", fontsize=10)
    document.set_toc(
        [
            [1, "Tiny Book", 1],
            [1, "Preface", 2],
            [1, "Part 1 Basics", 3],
            [2, "1 First steps", 4],
            [2, "2 Second steps", 6],
        ]
    )
    document.save(path)

PDF_CHECKS = {
    "chapters_vs_outline",
    "chapter_titles",
    "front_matter_sections",
    "stray_glyphs",
    "pseudo_headings",
    "line_break_hyphens",
    "figure_regions",
    "image_fragments",
    "caption_folds",
    "duplicated_labels",
    "headings_as_text",
    "figure_residue",
}


class ParseHealthApiTests(unittest.TestCase):
    def setUp(self) -> None:
        import book_agent.domain.models  # noqa: F401
        from book_agent.core.config import get_settings

        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.engine = build_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        # Deleting a book removes its runs and reports by cascade, as on Postgres.
        from sqlalchemy import event

        event.listen(self.engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
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
        app.state.export_root = str(self.root / "exports")
        app.state.upload_root = str(self.root / "uploads")
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        pdf = patch("book_agent.app.api.routes.overview.pdf_renderer_available", return_value=False)
        pdf.start()
        self.addCleanup(pdf.stop)

    def _bootstrap(self, path: Path) -> str:
        response = self.client.post("/v1/documents/bootstrap", json={"source_path": str(path)})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["document_id"]

    def _reports(self, document_id: str) -> list[ParseHealthReport]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(ParseHealthReport).where(ParseHealthReport.document_id == document_id).order_by(ParseHealthReport.created_at)
                ).all()
            )

    def test_a_book_not_translated_yet_imports_again_with_the_current_parser(self) -> None:
        upload = self.root / "uploads" / "first"
        upload.mkdir(parents=True)
        pdf_path = upload / "parted.pdf"
        _write_parted_book(pdf_path)
        document_id = self._bootstrap(pdf_path)
        (first_report,) = self._reports(document_id)
        # A download that tried to export it (and failed: nothing translated) leaves no translations.
        from book_agent.domain.enums import DocumentRunStatus, DocumentRunType
        from book_agent.domain.models.ops import DocumentRun

        with self.session_factory() as session:
            session.add(DocumentRun(document_id=document_id, run_type=DocumentRunType.EXPORT_FULL, status=DocumentRunStatus.FAILED, requested_by="test", status_detail_json={}))
            session.commit()
        self.assertTrue(self.client.get(f"/v1/documents/{document_id}/overview").json()["can_reimport"])

        response = self.client.post(f"/v1/documents/{document_id}/reimport")
        self.assertEqual(response.status_code, 200, response.text)
        # The same book (its id comes from the file), parsed and checked again from a copy of its file.
        self.assertEqual(response.json()["document_id"], document_id)
        # Checked again on import (Postgres deletes the old report with the book; SQLite keeps it).
        latest = self.client.get(f"/v1/documents/{document_id}/parse-health").json()
        self.assertNotEqual(latest["report_id"], first_report.id)
        self.assertEqual(latest["trigger"], "bootstrap")
        from book_agent.domain.models import Document

        with self.session_factory() as session:
            source = Path(session.get(Document, document_id).source_path)
        self.assertTrue(source.is_file())
        self.assertNotEqual(source, pdf_path)
        self.assertFalse(pdf_path.exists())

        self.assertFalse(self.client.get(f"/v1/documents/{document_id}/overview").json()["reimport_discards_pilot"])

        # After the pilot alone it imports again, and the pilot goes with the old parse.
        with self.session_factory() as session:
            pilot = DocumentRun(
                document_id=document_id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="test",
                billing_category="pilot",
                status_detail_json={"run_request": {"scope": "pilot"}},
            )
            session.add(pilot)
            session.commit()
            pilot_id = pilot.id
        # Not while it runs.
        busy = self.client.post(f"/v1/documents/{document_id}/reimport")
        self.assertEqual(busy.status_code, 409)
        self.assertIn("还有任务在进行", busy.json()["detail"])
        with self.session_factory() as session:
            session.get(DocumentRun, pilot_id).status = DocumentRunStatus.SUCCEEDED
            session.commit()
        overview = self.client.get(f"/v1/documents/{document_id}/overview").json()
        self.assertTrue(overview["can_reimport"])
        self.assertTrue(overview["reimport_discards_pilot"])
        again = self.client.post(f"/v1/documents/{document_id}/reimport")
        self.assertEqual(again.status_code, 200, again.text)
        with self.session_factory() as session:
            self.assertIsNone(session.get(DocumentRun, pilot_id))
        self.assertFalse(self.client.get(f"/v1/documents/{document_id}/overview").json()["reimport_discards_pilot"])

        # Once the whole book is asked for, the parse holds work: no re-import.
        run = self.client.post("/v1/runs", json={"document_id": document_id, "run_type": "translate_full", "requested_by": "test"})
        self.assertIn(run.status_code, (200, 201), run.text)
        with self.session_factory() as session:
            for queued in session.scalars(select(DocumentRun).where(DocumentRun.document_id == document_id)).all():
                queued.status = DocumentRunStatus.SUCCEEDED
            session.commit()
        self.assertFalse(self.client.get(f"/v1/documents/{document_id}/overview").json()["can_reimport"])
        refused = self.client.post(f"/v1/documents/{document_id}/reimport")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("已经开始翻译全书", refused.json()["detail"])

    def test_a_pdf_import_writes_a_report_the_api_and_the_overview_read(self) -> None:
        pdf_path = self.root / "parted.pdf"
        _write_parted_book(pdf_path)
        document_id = self._bootstrap(pdf_path)

        (report,) = self._reports(document_id)
        self.assertEqual(report.trigger, "bootstrap")

        response = self.client.get(f"/v1/documents/{document_id}/parse-health")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["report_id"], report.id)
        checks = {check["name"]: check for check in payload["checks"]}
        self.assertEqual(set(checks), PDF_CHECKS)
        # The outline (a part and the two chapters nested under it) is split the way the outline says.
        self.assertTrue(checks["chapters_vs_outline"]["passed"], checks["chapters_vs_outline"])
        self.assertEqual(checks["chapters_vs_outline"]["denominator"], 3)
        self.assertTrue(checks["front_matter_sections"]["passed"], checks["front_matter_sections"])
        self.assertEqual(checks["stray_glyphs"]["count"], 0)
        for check in payload["checks"]:
            self.assertIn(check["severity"], {"blocking", "major", "minor"})
            self.assertEqual(check["passed"], check["count"] <= check["allowed"])
        self.assertIn(payload["status"], {"passed", "warnings", "blocking"})

        overview = self.client.get(f"/v1/documents/{document_id}/overview").json()
        summary = overview["parse_health"]
        self.assertEqual(summary["status"], payload["status"])
        self.assertEqual(summary["check_count"], len(PDF_CHECKS))
        self.assertEqual(summary["needs_attention"], payload["status"] == "blocking")
        # A finding never stops a translation from starting (that is the trial gate's job); a new book
        # starts with its free pilot.
        self.assertEqual(overview["primary_action"]["kind"], "pilot")

    def test_a_structure_refresh_and_a_manual_run_add_reports(self) -> None:
        pdf_path = self.root / "parted.pdf"
        _write_parted_book(pdf_path)
        document_id = self._bootstrap(pdf_path)

        refreshed = self.client.post(f"/v1/documents/{document_id}/structure-refresh")
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        manual = self.client.post(f"/v1/documents/{document_id}/parse-health")
        self.assertEqual(manual.status_code, 200, manual.text)

        self.assertEqual([report.trigger for report in self._reports(document_id)], ["bootstrap", "structure_refresh", "manual"])
        latest = self.client.get(f"/v1/documents/{document_id}/parse-health").json()
        self.assertEqual((latest["report_id"], latest["trigger"]), (manual.json()["report_id"], "manual"))

    def test_an_epub_gets_the_checks_that_apply_to_it(self) -> None:
        epub_path = self.root / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        document_id = self._bootstrap(epub_path)
        payload = self.client.get(f"/v1/documents/{document_id}/parse-health").json()
        self.assertEqual(
            {check["name"] for check in payload["checks"]}, {"chapter_titles", "stray_glyphs", "pseudo_headings", "caption_folds"}
        )
        self.assertEqual(payload["status"], "passed")

    def test_no_report_and_no_document_are_not_found(self) -> None:
        self.assertEqual(self.client.get("/v1/documents/00000000-0000-4000-8000-00000000abcd/parse-health").status_code, 404)
        self.assertEqual(self.client.post("/v1/documents/00000000-0000-4000-8000-00000000abcd/parse-health").status_code, 404)


if __name__ == "__main__":
    unittest.main()
