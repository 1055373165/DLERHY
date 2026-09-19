"""Structure Agent (advisory): suspect page selection, page tools, image messages, plan wiring."""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from book_agent.domain.enums import (
    AgentTurnStatus,
    ArtifactStatus,
    BlockType,
    ChapterStatus,
    Detector,
    DocumentRunType,
    DocumentStatus,
    ProtectedPolicy,
    RootCauseLayer,
    SourceType,
)
from book_agent.domain.models import Block, Chapter, Document
from book_agent.domain.models.review import ReviewIssue
from book_agent.harness.agents.structure import (
    ISSUE_TYPE,
    MODE_FULL,
    MODE_SAMPLED,
    MODE_SKIP,
    SAMPLED_PAGE_LIMIT,
    StructureAgent,
    render_pdf_page_png,
    suspect_pages,
)
from book_agent.harness.kernel.messages import assemble_messages
from book_agent.harness.kernel.model import AgentStep, ToolCall
from book_agent.harness.kernel.turn import AgentTurnRunner
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.orchestrator.run_plan import plan_for_run
from book_agent.orchestrator.stage_gate import STAGE_DEPENDENCIES
from book_agent.workers.providers.openai_compatible import OpenAICompatibleTranslationClient

PNG = b"\x89PNG\r\n\x1a\nfake"


def _evidence(pages: dict[int, tuple[str, bool]]) -> dict:
    return {
        "pdf_pages": [
            {"page_number": number, "page_layout_risk": risk, "layout_suspect": suspect, "page_layout_reasons": ["columns"]}
            for number, (risk, suspect) in pages.items()
        ]
    }


class StructureAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        engine = build_engine(
            f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'structure.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.session_factory = build_session_factory(engine=engine)
        self.pdf_path = Path(self.tempdir.name) / "book.pdf"
        import fitz

        with fitz.open() as pdf:
            for number in range(1, 4):
                pdf.new_page().insert_text((72, 72), f"Page {number} heading")
            pdf.save(str(self.pdf_path))
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.PDF_TEXT,
                file_fingerprint=f"structure-{uuid4()}",
                source_path=str(self.pdf_path),
                title="Structure",
                status=DocumentStatus.ACTIVE,
                metadata_json={"pdf_page_evidence": _evidence({1: ("low", False), 2: ("high", True), 3: ("medium", True)})},
            )
            session.add(document)
            session.flush()
            chapter = Chapter(document_id=document.id, ordinal=1, title_src="One", status=ChapterStatus.PACKET_BUILT)
            session.add(chapter)
            session.flush()
            blocks = []
            for ordinal, (page, text, block_type) in enumerate(
                [(1, "Intro", BlockType.PARAGRAPH), (2, "Page 2 heading", BlockType.PARAGRAPH), (2, "Body text.", BlockType.PARAGRAPH), (3, "More", BlockType.PARAGRAPH)],
                start=1,
            ):
                block = Block(
                    chapter_id=chapter.id,
                    ordinal=ordinal,
                    block_type=block_type,
                    source_text=text,
                    protected_policy=ProtectedPolicy.TRANSLATE,
                    status=ArtifactStatus.ACTIVE,
                    source_span_json={"source_page_start": page, "source_page_end": page, "pdf_block_role": "body", "source_bbox_json": {"x0": 72}},
                )
                session.add(block)
                blocks.append(block)
            session.commit()
            self.document_id = document.id
            self.block_ids = [block.id for block in blocks]

    def test_suspect_pages_by_risk_and_mode(self) -> None:
        with self.session_factory() as session:
            document = session.get(Document, self.document_id)
            self.assertEqual(suspect_pages(document, mode=MODE_SAMPLED), [2, 3])
            self.assertEqual(suspect_pages(document, mode=MODE_SKIP), [])
            many = Document(source_type=SourceType.PDF_TEXT, metadata_json={"pdf_page_evidence": _evidence({n: ("high", True) for n in range(1, 40)})})
            self.assertEqual(len(suspect_pages(many, mode=MODE_SAMPLED)), SAMPLED_PAGE_LIMIT)
            self.assertEqual(len(suspect_pages(many, mode=MODE_FULL)), 39)
            self.assertEqual(suspect_pages(Document(source_type=SourceType.EPUB, metadata_json={}), mode=MODE_FULL), [])

    def test_real_pdf_page_renders_to_png(self) -> None:
        png = render_pdf_page_png(self.pdf_path, 2, dpi=40)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_turn_sees_the_page_image_and_files_a_non_blocking_suggestion(self) -> None:
        with self.session_factory() as session:
            seed = StructureAgent(session).start_turn(document_id=self.document_id, model_name="vision", mode=MODE_SAMPLED)
            session.commit()
        heading_block = self.block_ids[1]

        class VisionModel:
            def __init__(self) -> None:
                self.seen_messages: list[list[dict]] = []
                self.page = None

            def step(self, *, model_name, messages, tools):
                self.seen_messages.append(messages)
                last = messages[-1]
                if last["role"] == "user" and isinstance(last["content"], list):
                    return AgentStep(
                        text=None,
                        tool_calls=[
                            ToolCall("r", "report_structure_problem", {
                                "page_number": self.page, "kind": "missed_heading", "block_ids": [heading_block],
                                "suggestion": "This line is the section heading, not body text.", "confidence": 0.9,
                            }),
                            ToolCall("f", "finish_page", {"page_number": self.page}),
                        ],
                    )
                if last["role"] != "tool":
                    return AgentStep(text=None, tool_calls=[ToolCall("n0", "next_suspect_page", {})])
                output = json.loads(last["content"]).get("output") or {}
                if output.get("done"):
                    return AgentStep(text="检查完毕。")
                if "blocks" in output:
                    self.page = output["page_number"]
                    if self.page == 2:
                        return AgentStep(text=None, tool_calls=[ToolCall(f"i{self.page}", "render_page_image", {"page_number": self.page})])
                    return AgentStep(text=None, tool_calls=[ToolCall(f"f{self.page}", "finish_page", {"page_number": self.page})])
                return AgentStep(text=None, tool_calls=[ToolCall(f"n{len(self.seen_messages)}", "next_suspect_page", {})])

        model = VisionModel()
        runner = AgentTurnRunner(
            session_factory=self.session_factory,
            model=model,
            registry=StructureAgent.registry(),
            policy=StructureAgent.policy(),
            tool_extras={"page_renderer": lambda path, page: PNG},
        )
        outcome = runner.run(seed.turn_id)
        self.assertEqual(outcome.status, AgentTurnStatus.SUCCEEDED, outcome.stop_reason)

        image_turn = next(messages for messages in model.seen_messages if isinstance(messages[-1]["content"], list))
        image_message = image_turn[-1]
        self.assertEqual(image_turn[-2]["role"], "tool")
        self.assertEqual(image_message["content"][1]["image_url"]["url"], "data:image/png;base64," + base64.b64encode(PNG).decode())
        self.assertNotIn("_images", image_turn[-2]["content"])

        with self.session_factory() as session:
            issue = session.scalars(select(ReviewIssue).where(ReviewIssue.issue_type == ISSUE_TYPE)).one()
            self.assertEqual((issue.detector, issue.root_cause_layer, issue.blocking), (Detector.MODEL, RootCauseLayer.STRUCTURE, False))
            self.assertEqual(issue.block_id, heading_block)
            self.assertEqual(issue.evidence_json["kind"], "missed_heading")
            items = AgentLedgerRepository(session).list_items(seed.turn_id)
            stored = [item for item in items if (item.content_json or {}).get("images")]
            self.assertEqual(len(stored), 1)
            self.assertGreaterEqual(stored[0].token_count, 1000)

    def test_image_messages_wait_for_the_tool_run_and_convert_for_responses(self) -> None:
        from types import SimpleNamespace

        from book_agent.domain.enums import AgentItemKind

        items = [
            SimpleNamespace(kind=AgentItemKind.ASSISTANT, content_json={"text": "", "tool_calls": [{"call_id": "a", "name": "render_page_image", "arguments": {}}, {"call_id": "b", "name": "next_suspect_page", "arguments": {}}]}),
            SimpleNamespace(kind=AgentItemKind.TOOL_RESULT, content_json={"call_id": "a", "name": "render_page_image", "result": {"ok": True}, "images": [{"media_type": "image/png", "data": "QUJD"}]}),
            SimpleNamespace(kind=AgentItemKind.TOOL_RESULT, content_json={"call_id": "b", "name": "next_suspect_page", "result": {"ok": True}}),
        ]
        messages = assemble_messages(items)
        self.assertEqual([m["role"] for m in messages], ["assistant", "tool", "tool", "user"])
        converted = OpenAICompatibleTranslationClient.__new__(OpenAICompatibleTranslationClient)._responses_input_from_messages(messages)
        self.assertEqual(converted[-1]["content"][1], {"type": "input_image", "image_url": "data:image/png;base64,QUJD"})

    def test_bad_block_ids_are_rejected(self) -> None:
        from book_agent.harness.agents.structure import ReportStructureProblemArgs, report_structure_problem
        from book_agent.harness.tools.registry import ToolContext, ToolError

        with self.session_factory() as session:
            ctx = ToolContext(session=session, document_id=self.document_id, agent_kind="structure", turn_id="t")
            with self.assertRaisesRegex(ToolError, "blocks not on page 3"):
                report_structure_problem(
                    ctx,
                    ReportStructureProblemArgs(page_number=3, kind="bad_merge", block_ids=[self.block_ids[1]], suggestion="x", confidence=0.5),
                )

    def test_plan_makes_structure_review_opt_in_and_first(self) -> None:
        self.assertNotIn("structure_review", plan_for_run(DocumentRunType.TRANSLATE_FULL, {}).stages)
        plan = plan_for_run(DocumentRunType.TRANSLATE_FULL, {"run_request": {"structure_review": "sampled"}})
        self.assertEqual(plan.stages[0], "structure_review")
        self.assertEqual(plan.structure_review_mode, "sampled")
        self.assertIn("structure_review", STAGE_DEPENDENCIES["terminology"])


if __name__ == "__main__":
    unittest.main()
