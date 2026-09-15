"""Automatic terminology consistency: decide, minimally edit, validate, lock, report."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select

import tests.test_api_workflow as api_fixtures
from book_agent.domain.enums import LockLevel
from book_agent.domain.models import Sentence
from book_agent.domain.models.ops import Event
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.term_consistency import (
    TermConsistencyService,
    accept_segment_edit,
    render_consistency_report_markdown,
)
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.translation.contracts import TranslationUsage
from book_agent.workers.translator import EchoTranslationWorker

CHAPTER = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Signals</h1>
    <p>A failure swing appears at the top of the range.</p>
    <p>Two failure swings confirm the reversal signal.</p>
    <p>The failure swing is a reliable warning.</p>
  </body>
</html>
"""

TRANSLATIONS = {
    "A failure swing appears at the top of the range.": "区间顶部出现失败摆动。",
    "Two failure swings confirm the reversal signal.": "两次失败摇摆确认了反转信号。",
    "The failure swing is a reliable warning.": "失败摇摆是可靠的警示。",
}


def _write_book(root: Path) -> Path:
    chapters = [("Signals", "chapter1.xhtml")]
    path = root / "signals.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", api_fixtures.CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", api_fixtures._content_opf_with_chapters(chapters))
        archive.writestr("OEBPS/nav.xhtml", api_fixtures._nav_xhtml_with_chapters(chapters))
        archive.writestr("OEBPS/chapter1.xhtml", CHAPTER)
    return path


class FakeTerminologyClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_structured_object(self, *, model_name, system_prompt, user_prompt, response_schema, schema_name="x"):
        self.calls.append(schema_name)
        usage = TranslationUsage(token_in=100, token_out=20)
        if schema_name == "glossary_extraction":
            return {"terms": [{"source_term": "failure swing", "target_term": "失败摆动", "term_type": "concept"}]}, usage
        if schema_name == "term_decisions":
            return {
                "decisions": [
                    {"source_term": "Failure Swing", "canonical_zh": "失败摆动", "accepted_variants": ["失败摆动形态"], "context_dependent": False}
                ]
            }, usage
        if schema_name == "terminology_edits":
            segments: list[dict[str, Any]] = []
            for block in user_prompt.split("### Segment id: ")[1:]:
                segment_id = block.split("\n", 1)[0].strip()
                zh = next(line[4:] for line in block.splitlines() if line.startswith("ZH: "))
                segments.append({"id": segment_id, "text_zh": zh.replace("失败摇摆", "失败摆动"), "changed": True})
            return {"segments": segments}, usage
        raise AssertionError(schema_name)


class TermConsistencyServiceTest(unittest.TestCase):
    def test_harmonizes_inconsistent_segments_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            engine = build_engine(f"sqlite+pysqlite:///{root / 'terms.db'}")
            try:
                Base.metadata.create_all(engine)
                session_factory = build_session_factory(engine=engine)
                exports = str(root / "exports")
                with session_scope(session_factory) as session:
                    document_id = DocumentWorkflowService(session, export_root=exports).bootstrap_document(_write_book(root)).document_id
                with session_scope(session_factory) as session:
                    DocumentWorkflowService(session, export_root=exports, translation_worker=EchoTranslationWorker()).translate_document(document_id)
                with session_scope(session_factory) as session:
                    rows = session.execute(
                        select(TargetSegment, Sentence.source_text)
                        .join(AlignmentEdge, AlignmentEdge.target_segment_id == TargetSegment.id)
                        .join(Sentence, Sentence.id == AlignmentEdge.sentence_id)
                    ).all()
                    for segment, source_text in rows:
                        segment.text_zh = TRANSLATIONS.get(source_text, segment.text_zh)

                client = FakeTerminologyClient()
                with session_scope(session_factory) as session:
                    report = TermConsistencyService(session, client, model_name="fake").run(document_id)
                with session_scope(session_factory) as session:
                    texts = sorted(
                        segment.text_zh for segment in session.scalars(select(TargetSegment)) if "失败" in segment.text_zh
                    )
                    locked = GlossaryService(session).list_document_entries(document_id)
                    events = [event for event in session.scalars(select(Event)) if event.kind == "glossary.updated"]
            finally:
                engine.dispose()

        self.assertEqual(client.calls, ["glossary_extraction", "term_decisions", "terminology_edits"])
        self.assertEqual(texts, ["两次失败摆动确认了反转信号。", "区间顶部出现失败摆动。", "失败摆动是可靠的警示。"])
        decision = report.decisions[0]
        self.assertEqual((decision.segment_count, decision.consistent_before, decision.consistent_after), (3, 1, 3))
        self.assertAlmostEqual(report.consistency(after=False), 1 / 3)
        self.assertEqual(report.consistency(after=True), 1.0)
        self.assertEqual([item.status for item in report.edits], ["edited", "edited"])
        self.assertEqual(len(events), 2)
        self.assertEqual(
            [(entry.source_term, entry.target_term, entry.lock_level, entry.target_variants_json) for entry in locked],
            [("failure swing", "失败摆动", LockLevel.LOCKED, ["失败摆动形态"])],
        )
        markdown = render_consistency_report_markdown(report)
        self.assertIn("33.3% → 100.0%", markdown)


class AcceptSegmentEditTest(unittest.TestCase):
    def test_small_term_replacement_is_accepted(self) -> None:
        self.assertIsNone(accept_segment_edit("两次失败摇摆确认了反转信号。", "两次失败摆动确认了反转信号。", [["失败摆动"]]))

    def test_rewrites_and_missing_terms_are_rejected(self) -> None:
        self.assertEqual(
            accept_segment_edit("两次失败摇摆确认了反转信号。", "反转信号。", [["失败摆动"]]),
            "term_missing_after_edit",
        )
        self.assertEqual(
            accept_segment_edit(
                "两次失败摇摆确认了反转信号。",
                "失败摆动出现两次之后，交易者便可以相当有把握地认定趋势即将发生逆转。",
                [["失败摆动"]],
            ),
            "length_changed_too_much",
        )
        self.assertEqual(
            accept_segment_edit("两次失败摇摆确认了反转信号。", "失败摆动再现，市场随后调头向下。", [["失败摆动"]]),
            "edit_too_large",
        )


if __name__ == "__main__":
    unittest.main()
