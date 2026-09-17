"""Export QA Agent: artifact queue, screenshots as images, non-blocking findings, plan wiring."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from book_agent.domain.enums import AgentTurnStatus, Detector, DocumentRunType, ExportType, IssueStatus, RootCauseLayer
from book_agent.domain.models.review import ReviewIssue
from book_agent.harness.agents.export_review import (
    ISSUE_TYPE,
    MODE_FULL,
    MODE_SAMPLED,
    MODE_SKIP,
    ExportReviewAgent,
    export_artifacts,
    render_export_screenshot,
    RenderExportScreenshotArgs,
)
from book_agent.harness.kernel.model import AgentStep, ToolCall
from book_agent.harness.kernel.turn import AgentTurnRunner
from book_agent.harness.tools.registry import ToolContext, ToolError
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.orchestrator.run_plan import plan_for_run
from book_agent.orchestrator.stage_gate import STAGE_DEPENDENCIES
from book_agent.services.export_qa import ExportQaService
from book_agent.services.review import ReviewService
from book_agent.services.translation import TranslationService
from book_agent.services.workflows import DocumentWorkflowService
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML

ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\nshot"


class ExportReviewAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        engine = build_engine(f"sqlite+pysqlite:///{root / 'review.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.session_factory = build_session_factory(engine=engine)
        epub_path = root / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        artifacts = BootstrapOrchestrator().bootstrap_epub(epub_path)
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            service = TranslationService(TranslationRepository(session))
            for packet in artifacts.translation_packets:
                service.execute_packet(packet.id)
            for chapter in artifacts.chapters:
                ReviewService(ReviewRepository(session)).review_chapter(chapter.id)
            workflow = DocumentWorkflowService(session, export_root=root / "exports")
            self.bilingual = workflow.export_document(artifacts.document.id, ExportType.BILINGUAL_HTML)
            merged = workflow.export_document(artifacts.document.id, ExportType.MERGED_HTML)
            ExportQaService(session).audit(artifacts.document.id, ExportType.MERGED_HTML, [(None, Path(merged.file_path))])
            session.commit()
        self.document_id = artifacts.document.id

    def test_queue_modes(self) -> None:
        with self.session_factory() as session:
            full = export_artifacts(session, self.document_id, mode=MODE_FULL)
            self.assertEqual({item["export_type"] for item in full}, {"bilingual_html", "merged_html"})
            self.assertEqual(export_artifacts(session, self.document_id, mode=MODE_SKIP), [])
            sampled = export_artifacts(session, self.document_id, mode=MODE_SAMPLED)
            self.assertEqual(sampled[0]["export_type"], "merged_html")

    def test_turn_reads_the_rule_report_looks_at_a_screenshot_and_files_a_finding(self) -> None:
        with self.session_factory() as session:
            seed = ExportReviewAgent(session).start_turn(document_id=self.document_id, model_name="vision", mode=MODE_FULL)
            session.commit()

        class ReaderModel:
            def __init__(self) -> None:
                self.current = None
                self.saw_image = False
                self.saw_report = False

            def step(self, *, model_name, messages, tools):
                last = messages[-1]
                if last["role"] == "user" and isinstance(last["content"], list):
                    self.saw_image = True
                    return AgentStep(
                        text=None,
                        tool_calls=[
                            ToolCall("p", "report_export_problem", {
                                "export_id": self.current, "kind": "untranslated_text", "visible_text": "Chapter One",
                                "description": "标题仍是英文。", "confidence": 0.8, "screen": 0,
                            }),
                            ToolCall("f", "finish_export_artifact", {"export_id": self.current}),
                        ],
                    )
                if last["role"] != "tool":
                    return AgentStep(text=None, tool_calls=[ToolCall("n", "next_export_artifact", {})])
                output = json.loads(last["content"]).get("output") or {}
                if output.get("done"):
                    return AgentStep(text="审读完毕。")
                if "text_excerpt" in output:
                    self.saw_report = "rule_qa_failed_checks" in output and bool(output["text_excerpt"])
                    self.current = output["export_id"]
                    return AgentStep(text=None, tool_calls=[ToolCall(f"s-{self.current}", "render_export_screenshot", {"export_id": self.current})])
                return AgentStep(text=None, tool_calls=[ToolCall(f"n-{self.current}", "next_export_artifact", {})])

        model = ReaderModel()
        outcome = AgentTurnRunner(
            session_factory=self.session_factory,
            model=model,
            registry=ExportReviewAgent.registry(),
            policy=ExportReviewAgent.policy(),
            tool_extras={"html_screenshotter": lambda path, screen: PNG},
        ).run(seed.turn_id)
        self.assertEqual(outcome.status, AgentTurnStatus.SUCCEEDED, outcome.stop_reason)
        self.assertTrue(model.saw_image and model.saw_report)
        with self.session_factory() as session:
            issues = session.scalars(select(ReviewIssue).where(ReviewIssue.issue_type == ISSUE_TYPE)).all()
            self.assertEqual(len(issues), 2)  # one per artifact
            self.assertTrue(all(issue.detector == Detector.MODEL and not issue.blocking for issue in issues))
            self.assertTrue(all(issue.root_cause_layer == RootCauseLayer.EXPORT and issue.status == IssueStatus.OPEN for issue in issues))

    def test_screenshot_needs_a_queued_artifact_and_reports_missing_playwright(self) -> None:
        with self.session_factory() as session:
            seed = ExportReviewAgent(session).start_turn(document_id=self.document_id, model_name="m", mode=MODE_FULL)
            ctx = ToolContext(session=session, document_id=self.document_id, agent_kind="export_review", turn_id=seed.turn_id)
            with self.assertRaisesRegex(ToolError, "not in this review queue"):
                render_export_screenshot(ctx, RenderExportScreenshotArgs(export_id="nope"))
            try:
                import playwright  # noqa: F401
            except ImportError:
                export_id = export_artifacts(session, self.document_id, mode=MODE_FULL)[0]["export_id"]
                with self.assertRaisesRegex(ToolError, "Playwright is not installed"):
                    render_export_screenshot(ctx, RenderExportScreenshotArgs(export_id=export_id))

    def test_plan_makes_export_review_opt_in_and_last(self) -> None:
        self.assertNotIn("export_review", plan_for_run(DocumentRunType.TRANSLATE_FULL, {}).stages)
        plan = plan_for_run(DocumentRunType.TRANSLATE_FULL, {"run_request": {"export_review": "sampled"}})
        self.assertEqual(plan.stages[-1], "export_review")
        self.assertIn("export_review", plan.agent_stages)
        self.assertNotIn("export_review", plan.export_stages)
        self.assertEqual(STAGE_DEPENDENCIES["export_review"], ("bilingual_html", "merged_html"))


if __name__ == "__main__":
    unittest.main()
