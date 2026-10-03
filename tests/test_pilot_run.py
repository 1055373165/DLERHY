# ruff: noqa: E402
"""The free pilot run: the first two chapters through the full pipeline, then the whole book reusing it.

docs/agent-upgrade/08-pilot-quality-gate.md §5 and the Q3 row of §11. Runs with the echo model.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import func, select

os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")

from book_agent.app.main import create_app
from book_agent.domain.enums import DocumentRunType, ExportStatus, ExportType, IssueStatus, PacketStatus
from book_agent.domain.models import Chapter
from book_agent.domain.models.ops import DocumentRun
from book_agent.domain.models.review import Export, ReviewIssue
from book_agent.domain.models.translation import TranslationPacket, TranslationRun
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.orchestrator.run_plan import plan_for_run
from book_agent.services.billing_category import CUSTOMER, PILOT
from book_agent.services.pilot import chapter_number, is_surrounding_matter, select_pilot_chapters
from tests.document_actions import SyncDocumentActionClient
from tests.test_api_workflow import CONTAINER_XML, RepairLoopBrokenWorker, _content_opf_with_chapters, _nav_xhtml_with_chapters


def _chapter(title: str, prefix: str, sentences: int = 30, extra: str = "") -> str:
    body = " ".join(f"{prefix} sentence number {index} explains one idea of the book." for index in range(1, sentences + 1))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1>{title}</h1>
    <p>{extra}{body}</p>
  </body>
</html>
"""


BOOK = [
    ("Preface", "preface.xhtml", _chapter("Preface", "Preface")),
    ("Part 1 Foundations", "part1.xhtml", _chapter("Part 1 Foundations", "Part", sentences=2)),
    ("1 Why agents", "ch1.xhtml", _chapter("1 Why agents", "Alpha")),
    ("2 The loop", "ch2.xhtml", _chapter("2 The loop", "Beta")),
    ("3 Tools", "ch3.xhtml", _chapter("3 Tools", "Gamma")),
    ("4 Memory", "ch4.xhtml", _chapter("4 Memory", "Delta")),
    ("5 Planning", "ch5.xhtml", _chapter("5 Planning", "Epsilon")),
]


class PilotPlanTests(unittest.TestCase):
    def test_a_pilot_request_scopes_translate_review_and_exports_to_its_chapters(self) -> None:
        plan = plan_for_run(
            DocumentRunType.TRANSLATE_FULL,
            {"run_request": {"scope": "pilot", "chapter_ids": ["c1", "c2"], "packet_ids": ["p1", "p2", "p3"]}},
        )
        self.assertTrue(plan.is_pilot and plan.tolerates_blockers)
        self.assertEqual((plan.chapter_ids, plan.packet_ids), (frozenset({"c1", "c2"}), frozenset({"p1", "p2", "p3"})))
        # The full pipeline: terminology (whole book), translate, model review, review, exports, then the layered QA.
        self.assertEqual(
            plan.stages, ("terminology", "translate", "model_review", "review", "bilingual_html", "merged_html", "pilot_qa", "pilot_repair")
        )

    def test_a_whole_book_plan_is_unscoped_and_can_skip_what_a_pilot_reviewed(self) -> None:
        plan = plan_for_run(
            DocumentRunType.TRANSLATE_FULL,
            {"run_request": {"terminology": "skip", "model_reviewed_chapter_ids": ["c1"]}},
        )
        self.assertFalse(plan.is_pilot or plan.tolerates_blockers)
        self.assertIsNone(plan.chapter_ids)
        self.assertIsNone(plan.packet_ids)
        self.assertNotIn("terminology", plan.stages)
        self.assertEqual(plan.model_review_skip_chapter_ids, frozenset({"c1"}))

    def test_a_pilot_without_packets_translates_nothing_rather_than_everything(self) -> None:
        plan = plan_for_run(DocumentRunType.TRANSLATE_FULL, {"run_request": {"scope": "pilot", "chapter_ids": []}})
        self.assertEqual(plan.packet_ids, frozenset())


class ChapterTitleTests(unittest.TestCase):
    def test_numbered_titles_and_the_matter_around_the_chapters(self) -> None:
        self.assertEqual(
            [chapter_number(title) for title in ("1 LLMs and the need for RAG", "Chapter 3: Tools", "3. Tools", "第十二章 规划")],
            [1, 3, 3, 12],
        )
        self.assertEqual([chapter_number(title) for title in ("3.2 Retrieval", "2024 was a year", "Chapter One")], [None] * 3)
        for title in ("Front Matter", "brief contents", "preface", "about the cover illustration", "Part 2 Creating RAG systems", "appendix OpenAI API Key", "index"):
            self.assertTrue(is_surrounding_matter(title), title)
        for title in ("Introduction", "1 What is an AI agent?", "Chapter One"):
            self.assertFalse(is_surrounding_matter(title), title)


class PilotRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'pilot.db'}", connect_args={"check_same_thread": False, "timeout": 30}
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

    def _import(self, chapters=BOOK) -> str:
        path = Path(self.tempdir.name) / f"book-{len(chapters)}-{time.monotonic_ns()}.epub"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", _content_opf_with_chapters([(title, href) for title, href, _ in chapters]))
            archive.writestr("OEBPS/nav.xhtml", _nav_xhtml_with_chapters([(title, href) for title, href, _ in chapters]))
            for _title, href, content in chapters:
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

    def _chapters(self, document_id: str) -> dict[str, str]:
        with self.session_factory() as session:
            return {
                chapter.title_src: str(chapter.id)
                for chapter in session.scalars(select(Chapter).where(Chapter.document_id == document_id)).all()
            }

    def _packet_status(self, document_id: str) -> dict[str, set[str]]:
        with self.session_factory() as session:
            rows = session.execute(
                select(Chapter.title_src, TranslationPacket.status)
                .join(Chapter, Chapter.id == TranslationPacket.chapter_id)
                .where(Chapter.document_id == document_id)
            ).all()
        out: dict[str, set[str]] = {}
        for title, status in rows:
            out.setdefault(title, set()).add(status.value)
        return out

    def test_the_pilot_is_the_first_two_numbered_chapters(self) -> None:
        document_id = self._import()
        with self.session_factory() as session:
            selection = select_pilot_chapters(session, document_id)
        self.assertTrue(selection.available)
        self.assertEqual(selection.rule, "numbered_chapters")
        self.assertEqual([chapter.title for chapter in selection.chapters], ["1 Why agents", "2 The loop"])

    def test_without_numbers_the_first_two_body_chapters_and_short_books_have_no_pilot(self) -> None:
        named = [
            ("Preface", "preface.xhtml", _chapter("Preface", "Preface")),
            ("Introduction", "intro.xhtml", _chapter("Introduction", "Intro")),
            ("The Loop", "loop.xhtml", _chapter("The Loop", "Loop")),
            ("Tools", "tools.xhtml", _chapter("Tools", "Tools")),
            ("Memory", "memory.xhtml", _chapter("Memory", "Memory")),
        ]
        document_id = self._import(named)
        short_id = self._import(named[:3])
        with self.session_factory() as session:
            selection = select_pilot_chapters(session, document_id)
            short = select_pilot_chapters(session, short_id)
        self.assertEqual((selection.rule, [c.title for c in selection.chapters]), ("first_body_chapters", ["Introduction", "The Loop"]))
        self.assertEqual(short.unavailable_reason, "short_book")
        response = self.client.post(f"/v1/documents/{short_id}/pilot", json={})
        self.assertEqual(response.status_code, 422)
        self.assertIn("直接翻译全书", response.json()["detail"])

    def test_pilot_then_whole_book_reuses_the_pilot(self) -> None:
        document_id = self._import()
        chapters = self._chapters(document_id)
        # Only the pilot endpoint makes a pilot: a client cannot scope (and so not charge) a run itself.
        refused = self.client.post(
            "/v1/runs",
            json={
                "document_id": document_id,
                "run_type": "translate_full",
                "requested_by": "t",
                "status_detail_json": {"run_request": {"scope": "pilot", "chapter_ids": [chapters["1 Why agents"]]}},
            },
        )
        self.assertEqual(refused.status_code, 422)

        before = self.client.get(f"/v1/documents/{document_id}/overview").json()
        self.assertEqual(before["pilot"]["state"], "not_started")
        self.assertEqual([c["title"] for c in before["pilot"]["chapters"]], ["1 Why agents", "2 The loop"])
        self.assertEqual(before["primary_action"], {"kind": "pilot", "label": "免费试译前两章"})
        self.assertIn("免费试译前两章", before["headline"])

        started = self.client.post(f"/v1/documents/{document_id}/pilot", json={"fast_reading": True})
        self.assertEqual(started.status_code, 202, started.text)
        pilot_id = started.json()["run_id"]
        self.assertEqual(self._wait(pilot_id)["status"], "succeeded")
        self.assertEqual(self.client.post(f"/v1/documents/{document_id}/pilot", json={}).status_code, 409)

        status = self._packet_status(document_id)
        self.assertEqual(status["1 Why agents"], {PacketStatus.TRANSLATED.value})
        self.assertEqual(status["2 The loop"], {PacketStatus.TRANSLATED.value})
        for title in ("Preface", "3 Tools", "4 Memory", "5 Planning"):
            self.assertNotIn(PacketStatus.TRANSLATED.value, status[title], title)

        pilot_chapters = {chapters["1 Why agents"], chapters["2 The loop"]}
        with self.session_factory() as session:
            run = session.get(DocumentRun, pilot_id)
            self.assertEqual(run.billing_category, PILOT)
            stages = (run.status_detail_json or {}).get("pipeline", {})
            bilingual = session.scalars(
                select(Export).where(Export.document_id == document_id, Export.export_type == ExportType.BILINGUAL_HTML)
            ).all()
            self.assertEqual({str(e.input_version_bundle_json.get("chapter_id")) for e in bilingual}, pilot_chapters)
            merged = session.scalars(
                select(Export).where(Export.document_id == document_id, Export.export_type == ExportType.MERGED_HTML)
            ).one()
            self.assertEqual(set(merged.input_version_bundle_json["chapter_scope"]), pilot_chapters)
            pilot_packets = session.scalars(
                select(TranslationPacket.id).where(TranslationPacket.chapter_id.in_(sorted(pilot_chapters)))
            ).all()
            pilot_runs = {
                packet_id: count
                for packet_id, count in session.execute(
                    select(TranslationRun.packet_id, func.count(TranslationRun.id)).group_by(TranslationRun.packet_id)
                ).all()
            }
        cached = stages.get("_cached_pipeline_stages") or stages.get("stages") or {}
        # The full pipeline ran on the pilot, terminology included.
        for stage in ("terminology", "translate", "model_review", "review", "bilingual_html", "merged_html"):
            self.assertEqual((cached.get(stage) or {}).get("status"), "succeeded", stage)
        self.assertEqual(int(cached["translate"].get("total_packet_count") or 0), len(pilot_packets))
        ready = self.client.get(f"/v1/documents/{document_id}/overview").json()
        self.assertEqual((ready["pilot"]["state"], ready["pilot"]["readable"]), ("ready", True))
        self.assertFalse(ready["readable"]["ready"])  # the whole book is not translated
        self.assertEqual(ready["primary_action"]["kind"], "start_book")
        self.assertEqual(ready["translation"]["scope"], "pilot")
        self.assertEqual(ready["translation"]["progress"], {"done": len(pilot_packets), "total": len(pilot_packets)})
        self.assertEqual(ready["pilot"]["problems"]["blocking"], 0)
        self.assertEqual(ready["cost"]["spent_usd"], 0.0)
        self.assertTrue(ready["headline"].startswith("试译完成"))
        news = self.client.get("/v1/inbox", params={"since": "2000-01-01T00:00:00+00:00"}).json()
        self.assertEqual([update["kind"] for update in news["updates"]], ["pilot_ready"])
        with self.session_factory() as session:
            from book_agent.core.config import get_settings
            from book_agent.services.cost_estimate import estimate_document_cost

            remaining = estimate_document_cost(session, document_id, get_settings(), terminology="skip", pending_only=True)
            whole = estimate_document_cost(session, document_id, get_settings())
        self.assertEqual(remaining.packet_count, whole.packet_count - len(pilot_packets))
        self.assertEqual(remaining.breakdown["terminology"]["token_in"], 0)
        self.assertLess(remaining.token_in, whole.token_in)
        rest = self.client.get(
            f"/v1/documents/{document_id}/cost-estimate", params={"terminology": "skip", "pending_only": "true"}
        ).json()
        self.assertEqual(rest["packet_count"], remaining.packet_count)

        merged_text = self.client.get(
            f"/v1/documents/{document_id}/exports/download", params={"export_type": "merged_html", "package": "preview"}
        ).text
        self.assertIn("Alpha sentence", merged_text)
        self.assertNotIn("Gamma sentence", merged_text)

        # The whole book: a paid run that does not redo the pilot.
        created = self.client.post(
            "/v1/runs",
            json={"document_id": document_id, "run_type": "translate_full", "requested_by": "t", "status_detail_json": {}},
        )
        self.assertEqual(created.status_code, 201, created.text)
        book_id = created.json()["run_id"]
        request = created.json()["status_detail_json"]["run_request"]
        self.assertEqual(request["after_pilot_run_id"], pilot_id)
        self.assertEqual(request["terminology"], "skip")
        self.assertEqual(set(request["model_reviewed_chapter_ids"]), pilot_chapters)
        self.client.post(f"/v1/runs/{book_id}/resume", json={"actor_id": "t"})
        self.assertEqual(self._wait(book_id)["status"], "succeeded")

        self.assertTrue(all(values == {PacketStatus.TRANSLATED.value} for values in self._packet_status(document_id).values()))
        with self.session_factory() as session:
            self.assertEqual(session.get(DocumentRun, book_id).billing_category, CUSTOMER)
            after = dict(
                session.execute(
                    select(TranslationRun.packet_id, func.count(TranslationRun.id)).group_by(TranslationRun.packet_id)
                ).all()
            )
            merged = session.scalars(
                select(Export).where(
                    Export.document_id == document_id,
                    Export.export_type == ExportType.MERGED_HTML,
                    Export.status == ExportStatus.SUCCEEDED,
                )
            ).one()
        done = self.client.get(f"/v1/documents/{document_id}/overview").json()
        self.assertEqual((done["pilot"]["state"], done["translation"]["scope"]), ("book_started", "book"))
        self.assertTrue(done["readable"]["ready"])
        self.assertEqual(done["primary_action"]["kind"], "read")
        # Pilot packets were not translated again.
        self.assertEqual(set(pilot_runs), {str(packet_id) for packet_id in pilot_packets})
        self.assertEqual({packet_id: after[packet_id] for packet_id in pilot_runs}, pilot_runs)
        book_stages = self._wait(book_id)["status_detail_json"]["pipeline"]
        book_cached = book_stages.get("_cached_pipeline_stages") or book_stages.get("stages") or {}
        self.assertNotIn("terminology", book_cached)
        self.assertIsNone(merged.input_version_bundle_json["chapter_scope"])

        # After the whole book: the layered QA and the repair loop on every chapter (全书质检).
        for stage in ("book_qa", "book_repair"):
            self.assertEqual((book_cached.get(stage) or {}).get("status"), "succeeded", stage)
        book_quality = done["book_quality"]
        self.assertIsNotNone(book_quality)
        self.assertEqual(book_quality["chapter_count"], len(chapters))
        self.assertIn(book_quality["quality"]["status"], {"passed", "warnings", "blocking"})
        self.assertEqual(book_quality["repair"]["status"], "finished")
        self.assertEqual(book_quality["repair"]["scope"], "book")
        report = self.client.get(f"/v1/documents/{document_id}/book-quality").json()
        self.assertEqual(report["scope"], "book")
        self.assertEqual(len(report["chapters"]), len(chapters))
        # The pilot's own report and card stay the pilot's.
        pilot_report = self.client.get(f"/v1/documents/{document_id}/pilot-quality").json()
        self.assertEqual(pilot_report["scope"], "pilot")
        self.assertEqual({chapter["chapter_id"] for chapter in pilot_report["chapters"]}, pilot_chapters)
        repair = self.client.get(f"/v1/documents/{document_id}/book-repair").json()
        self.assertEqual((repair["scope"], repair["run_id"]), ("book", book_id))
        self.assertIsNotNone(repair["budget_usd"] if repair["budget_usd"] is not None else repair["summary"]["budget"]["max_packets"])

    def test_translating_the_whole_book_directly_skips_the_pilot(self) -> None:
        document_id = self._import()
        created = self.client.post(
            "/v1/runs",
            json={"document_id": document_id, "run_type": "translate_full", "requested_by": "t", "status_detail_json": {}},
        )
        self.assertEqual(created.status_code, 201)
        self.assertNotIn("after_pilot_run_id", created.json()["status_detail_json"]["run_request"])
        overview = self.client.get(f"/v1/documents/{document_id}/overview").json()
        self.assertEqual((overview["pilot"]["state"], overview["translation"]["scope"]), ("skipped", "book"))
        self.assertEqual(self.client.post(f"/v1/documents/{document_id}/pilot", json={}).status_code, 409)

    def test_a_pilot_with_blockers_left_still_finishes_and_exports_with_them_listed(self) -> None:
        book = [
            *BOOK[:2],
            ("1 Why agents", "ch1.xhtml", _chapter("1 Why agents", "Alpha", extra="Selection bias matters. ")),
            *BOOK[3:],
        ]
        document_id = self._import(book)
        self.app.state.translation_worker = RepairLoopBrokenWorker()  # always leaves one sentence out
        started = self.client.post(f"/v1/documents/{document_id}/pilot", json={})
        self.assertEqual(started.status_code, 202, started.text)
        terminal = self._wait(started.json()["run_id"])
        self.assertEqual(terminal["status"], "succeeded", terminal.get("stop_reason"))
        with self.session_factory() as session:
            blockers = session.scalar(
                select(func.count(ReviewIssue.id)).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.blocking.is_(True),
                    ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
                )
            )
            merged = session.scalars(
                select(Export).where(Export.document_id == document_id, Export.export_type == ExportType.MERGED_HTML)
            ).one_or_none()
        self.assertGreaterEqual(blockers, 1)
        self.assertIsNotNone(merged)
        overview = self.client.get(f"/v1/documents/{document_id}/overview").json()
        self.assertEqual(overview["pilot"]["state"], "ready")
        self.assertGreaterEqual(overview["pilot"]["problems"]["blocking"], 1)
        self.assertIn("OMISSION", [entry["type"] for entry in overview["pilot"]["problems"]["by_type"]])
        # After the repair loop the headline says what is still blocking; the card lists it.
        self.assertRegex(overview["headline"], "个严重问题需要你看|个问题没有解决")
        stages = terminal["status_detail_json"]["pipeline"]
        review = (stages.get("_cached_pipeline_stages") or stages.get("stages") or {}).get("review") or {}
        self.assertGreaterEqual(int(review.get("blockers_left_for_reader") or 0), 1)


if __name__ == "__main__":
    unittest.main()
