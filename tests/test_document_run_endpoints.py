"""POST /documents/{id}/translate|review|export enqueue runs and return at once.

The run executor then carries out the requested action as a
translate_targeted / review_full / export_full run.
"""

import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

import tests.test_api_workflow as api_fixtures
from book_agent.app.main import create_app
from book_agent.domain.enums import ExportType, PacketStatus
from book_agent.domain.models import Chapter
from book_agent.domain.models.review import Export
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.orchestrator.pipeline_stage_cache import read_cached_stages

TERMINAL_STATUSES = {"succeeded", "succeeded_with_warnings", "failed", "paused", "cancelled"}


def _nonzero(counts: dict[str, int]) -> dict[str, int]:
    return {key: value for key, value in counts.items() if value}


class DocumentRunEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.root = Path(tempdir.name)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{self.root / 'runs.db'}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.app = create_app()
        self.app.state.session_factory = self.session_factory
        self.app.state.export_root = str(self.root / "exports")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self._stop_executor)

    def _stop_executor(self) -> None:
        executor = getattr(self.app.state, "document_run_executor", None)
        if executor is not None:
            executor.stop()
            self.app.state.document_run_executor = None

    def _bootstrap_document(self) -> str:
        epub_path = self.root / "book.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", api_fixtures.CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", api_fixtures.CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", api_fixtures.NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", api_fixtures.CHAPTER_XHTML)
        response = self.client.post("/v1/documents/bootstrap", json={"source_path": str(epub_path)})
        self.assertEqual(response.status_code, 201)
        return response.json()["document_id"]

    def _enqueue(self, path: str, **kwargs) -> dict:
        response = self.client.post(path, **kwargs)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def _wait_for_terminal(self, run_id: str, timeout_seconds: float = 60.0) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            payload = self.client.get(f"/v1/runs/{run_id}").json()
            if payload["status"] in TERMINAL_STATUSES:
                return payload
            time.sleep(0.1)
        self.fail(f"Run {run_id} did not finish within {timeout_seconds} seconds.")

    def _packet_statuses(self, document_id: str) -> dict[str, PacketStatus]:
        with self.session_factory() as session:
            return {
                packet.id: packet.status
                for packet in session.scalars(
                    select(TranslationPacket)
                    .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
                    .where(Chapter.document_id == document_id)
                ).all()
            }

    def test_translate_enqueues_targeted_run_scoped_to_requested_packets(self) -> None:
        document_id = self._bootstrap_document()
        packet_ids = sorted(self._packet_statuses(document_id))
        self.assertGreater(len(packet_ids), 1)
        target = packet_ids[0]

        run = self._enqueue(f"/v1/documents/{document_id}/translate", json={"packet_ids": [target]})

        self.assertEqual(run["run_type"], "translate_targeted")
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["status_detail_json"]["run_request"], {"packet_ids": [target]})
        finished = self._wait_for_terminal(run["run_id"])
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(_nonzero(finished["work_items"]["stage_counts"]), {"translate": 1})
        statuses = self._packet_statuses(document_id)
        self.assertEqual(statuses[target], PacketStatus.TRANSLATED)
        self.assertTrue(all(statuses[packet_id] == PacketStatus.BUILT for packet_id in packet_ids[1:]))

    def test_translate_review_export_runs_produce_a_downloadable_export(self) -> None:
        document_id = self._bootstrap_document()

        translate = self._enqueue(f"/v1/documents/{document_id}/translate", json={})
        self.assertEqual(self._wait_for_terminal(translate["run_id"])["status"], "succeeded")
        self.assertTrue(all(status == PacketStatus.TRANSLATED for status in self._packet_statuses(document_id).values()))

        review = self._enqueue(f"/v1/documents/{document_id}/review")
        self.assertEqual(review["run_type"], "review_full")
        review_done = self._wait_for_terminal(review["run_id"])
        self.assertEqual(review_done["status"], "succeeded")
        self.assertEqual(_nonzero(review_done["work_items"]["stage_counts"]), {"review": 1})

        export = self._enqueue(f"/v1/documents/{document_id}/export", json={"export_type": "merged_html"})
        self.assertEqual(export["run_type"], "export_full")
        export_done = self._wait_for_terminal(export["run_id"])
        self.assertEqual(export_done["status"], "succeeded")
        self.assertEqual(_nonzero(export_done["work_items"]["stage_counts"]), {"export": 1})
        # Export QA ran after the export and wrote its report; a sensor, it never fails the run.
        qa = read_cached_stages(export_done["status_detail_json"]["pipeline"])["merged_html"]["qa"]
        self.assertEqual(qa["export_type"], "merged_html")
        self.assertTrue(Path(qa["report_paths"][0]).is_file())
        with self.session_factory() as session:
            exports = session.scalars(
                select(Export).where(Export.document_id == document_id, Export.export_type == ExportType.MERGED_HTML)
            ).all()
        self.assertEqual(len(exports), 1)

        download = self.client.get(
            f"/v1/documents/{document_id}/exports/download",
            params={"export_type": "merged_html"},
        )
        self.assertEqual(download.status_code, 200)

    def test_export_run_blocked_by_gate_fails_with_gate_detail(self) -> None:
        document_id = self._bootstrap_document()

        run = self._enqueue(f"/v1/documents/{document_id}/export", json={"export_type": "bilingual_html"})

        finished = self._wait_for_terminal(run["run_id"])
        self.assertEqual(finished["status"], "failed")
        stage = read_cached_stages(finished["status_detail_json"]["pipeline"])["bilingual_html"]
        self.assertEqual(stage["status"], "failed")
        self.assertEqual(stage["error_class"], "ExportGateError")
        self.assertIn("message", stage["export_gate"])

    def test_actions_on_unknown_document_return_404(self) -> None:
        for path, body in (
            ("translate", {}),
            ("review", None),
            ("export", {"export_type": "merged_html"}),
        ):
            response = self.client.post(f"/v1/documents/missing-document/{path}", json=body)
            self.assertEqual(response.status_code, 404, path)


if __name__ == "__main__":
    unittest.main()
