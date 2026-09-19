"""Automatic terminology consistency: survey renderings, tally, replace exact spans, lock, report."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path

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
    decide_canonical,
    exact_span,
    render_consistency_report_markdown,
    replace_rendering,
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
    <p>Another failure swing formed last week.</p>
    <p>No other tool can hold a candle to a candle chart.</p>
    <p>Each candle shows the open and the close.</p>
    <p>A red candle closes lower than it opens.</p>
  </body>
</html>
"""

# failure swing: 失败摆动 x3 (majority) vs 失败摇摆 x1; candle: K线 in its technical sense, 一个成语 once.
TRANSLATIONS = {
    "A failure swing appears at the top of the range.": "区间顶部出现失败摆动。",
    "Two failure swings confirm the reversal signal.": "两次失败摇摆确认了反转信号。",
    "The failure swing is a reliable warning.": "失败摆动是可靠的警示。",
    "Another failure swing formed last week.": "上周又形成了一次失败摆动。",
    "No other tool can hold a candle to a candle chart.": "没有别的工具能与蜡烛图相提并论。",
    "Each candle shows the open and the close.": "每根K线显示开盘价和收盘价。",
    "A red candle closes lower than it opens.": "阴K线的收盘价低于开盘价。",
}

SURVEY = {
    # (term, zh) -> (rendering, sense)
    ("failure swing", "区间顶部出现失败摆动。"): ("失败摆动", "term"),
    ("failure swing", "两次失败摇摆确认了反转信号。"): ("失败摇摆", "term"),
    ("failure swing", "失败摆动是可靠的警示。"): ("失败摆动", "term"),
    ("failure swing", "上周又形成了一次失败摆动。"): ("失败摆动", "term"),
    ("candle", "没有别的工具能与蜡烛图相提并论。"): ("相提并论", "other"),
    ("candle", "每根K线显示开盘价和收盘价。"): ("K线", "term"),
    ("candle", "阴K线的收盘价低于开盘价。"): ("K线", "term"),
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


class FakeSurveyClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_structured_object(self, *, model_name, system_prompt, user_prompt, response_schema, schema_name="x"):
        self.calls.append(schema_name)
        usage = TranslationUsage(token_in=100, token_out=20)
        if schema_name == "glossary_extraction":
            return {
                "terms": [
                    {"source_term": "failure swing", "target_term": "失败摆动", "term_type": "concept"},
                    {"source_term": "candle", "target_term": "K线", "term_type": "concept"},
                ]
            }, usage
        if schema_name == "term_survey":
            items = []
            for block in user_prompt.split("### Item id: ")[1:]:
                lines = block.splitlines()
                term = lines[1].removeprefix("Term: ")
                zh = next(line[4:] for line in lines if line.startswith("ZH: "))
                rendering, sense = SURVEY[(term, zh)]
                items.append({"id": lines[0].strip(), "rendering_zh": rendering, "sense": sense})
            return {"items": items}, usage
        raise AssertionError(f"unexpected call {schema_name}: the pass must not ask the model to edit text")


class TermConsistencyServiceTest(unittest.TestCase):
    def test_majority_rendering_replaces_minority_spans_and_other_senses_are_untouched(self) -> None:
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

                client = FakeSurveyClient()
                with session_scope(session_factory) as session:
                    report = TermConsistencyService(session, client, model_name="fake").run(document_id)
                with session_scope(session_factory) as session:
                    texts = {segment.text_zh for segment in session.scalars(select(TargetSegment))}
                    glossary = GlossaryService(session)
                    locked = glossary.get_locked_terms(document_id)
                    preferred = {
                        entry.source_term: (entry.target_term, entry.lock_level)
                        for entry in glossary.list_document_entries(document_id)
                    }
                    events = [event for event in session.scalars(select(Event)) if event.kind == "glossary.updated"]
            finally:
                engine.dispose()

        self.assertEqual(set(Counter(client.calls)), {"glossary_extraction", "term_survey"})
        self.assertIn("两次失败摆动确认了反转信号。", texts)
        self.assertNotIn("两次失败摇摆确认了反转信号。", texts)
        self.assertIn("没有别的工具能与蜡烛图相提并论。", texts)  # idiom: not a term occurrence
        by_term = {decision.source_term: decision for decision in report.decisions}
        swing = by_term["failure swing"]
        self.assertEqual((swing.canonical_zh, swing.expressed_segments, swing.consistent_before, swing.consistent_after), ("失败摆动", 4, 3, 4))
        candle = by_term["candle"]
        self.assertEqual((candle.canonical_zh, candle.other_sense_segments, candle.replaced), ("K线", 1, 0))
        self.assertEqual(report.edited_segments, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(locked, {})  # nothing LOCKED: review must not block on judged exceptions
        self.assertEqual(
            preferred,
            {"failure swing": ("失败摆动", LockLevel.PREFERRED), "candle": ("K线", LockLevel.PREFERRED)},
        )
        self.assertIn("严格一致率（只算标准译法）：83.3% → 100.0%", render_consistency_report_markdown(report))


class DecisionRulesTest(unittest.TestCase):
    def test_majority_wins_and_unclear_or_scattered_terms_are_skipped(self) -> None:
        self.assertEqual(decide_canonical(Counter({"失败摆动": 3, "失败摇摆": 1}), proposed="失败摇摆"), ("失败摆动", None))
        self.assertEqual(decide_canonical(Counter({"图表": 3, "走势图": 2}), proposed=None), (None, "no_clear_majority"))
        self.assertEqual(decide_canonical(Counter({"A": 1, "B": 1}), proposed="B"), (None, "no_clear_majority"))
        self.assertEqual(
            decide_canonical(Counter({"图表": 5, "走势图": 1, "价格图": 1, "图形": 1}), proposed="图表"),
            (None, "many_renderings_context_dependent"),
        )
        self.assertEqual(decide_canonical(Counter({"背离": 1}), proposed="背离"), (None, "too_few_occurrences"))

    def test_spans_must_exist_and_replacements_must_be_unambiguous(self) -> None:
        self.assertIsNone(exact_span("两次失败摇摆确认信号。", "失败摆动"))
        self.assertEqual(exact_span("两次失败摇摆确认信号。", " 失败摇摆 "), "失败摇摆")
        self.assertEqual(
            replace_rendering("两次失败摇摆确认信号。", "失败摇摆", "失败摆动", expected_occurrences=1), "两次失败摆动确认信号。"
        )
        self.assertIsNone(replace_rendering("小时图上的图形", "图", "图表", expected_occurrences=1))  # too short
        self.assertIsNone(replace_rendering("时间周期与周期指标", "周期", "时间框架", expected_occurrences=1))  # ambiguous
        self.assertIsNone(replace_rendering("头肩形态出现", "头肩形", "头肩形态", expected_occurrences=1))  # inside canonical

    def test_spans_with_extra_words_or_other_concepts_are_never_replaced(self) -> None:
        # Seen in a real run: replacing these dropped meaning or changed the concept.
        for text, rendering, canonical in (
            ("该指数继续上涨", "该指数", "指数"),
            ("散户投资者往往追涨", "散户投资者", "投资者"),
            ("Nifty 50 指数", "Nifty 50", "Nifty"),
            ("价格逆势上行", "逆势", "趋势"),
            ("突破时放量", "放量", "成交量"),
            ("RSI 上穿 50", "上穿", "交叉"),
            ("在那个时期", "时期", "周期"),
            ("或倒头肩形等形态时", "倒头肩形", "倒头肩形态"),  # short form: "倒头肩形态等形态"
            ("股票的日线RSI并未", "日线", "日线图"),  # short form: "日线图RSI"
        ):
            with self.subTest(rendering=rendering):
                self.assertIsNone(replace_rendering(text, rendering, canonical, expected_occurrences=1))
        self.assertEqual(
            replace_rendering("出现动量摆动指标背离", "动量摆动指标", "动量振荡指标", expected_occurrences=1),
            "出现动量振荡指标背离",
        )


if __name__ == "__main__":
    unittest.main()
