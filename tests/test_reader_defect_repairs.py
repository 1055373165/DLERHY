"""Reader-visible defects of the whole Agent book, found by the layered QA and repaired on a book
parsed before the parser fixes, without a re-import.

- A booktabs table (rules only under the header and at the bottom) whose body rows an older parse
  kept as one row, every cell holding all rows' text: ``L3.table_rows_merged`` compares the parsed
  rows with the PDF's and ``structure_edit.reextract_table`` rebuilds the block from the page and
  translates only its cells.
- A cut-short copy of an annotation clipped out of a listing's box (``L1.clipped_text_copy``).
- A numbered item whose translation opened with a bullet (``L4.list_marker_doubled``).
- A prompt callout whose lines a retranslation joined (``L4.callout_lines_collapsed``).
- A part titled only "第 1 部分" (``L1.part_title_unnamed``), and numbered titles in mixed forms.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import fitz
from sqlalchemy import select

from book_agent.domain.enums import BlockType
from book_agent.domain.models import Block, Sentence, StructureEdit
from book_agent.domain.quality import pilot_qa
from book_agent.domain.quality.pilot_qa import ExportedChapter, QaBlock, QaChapter
from book_agent.infra.repositories.review import active_target_texts
from book_agent.services.parse_revision_fork import ParseRevisionForkService
from tests.pilot_repair_fixture import block_with_text, edit_translation
from tests.test_pilot_repair import PilotRepairBenchCase, _findings

_COLUMNS = (102.0, 220.0, 312.0, 403.0)
_TOP, _HEADER_BOTTOM, _BOTTOM = 350.0, 366.0, 404.0


def _draw_booktabs_table(pdf_path: Path, page_number: int) -> tuple[float, float, float, float]:
    """Table 4.3 of the Agent book: its two body rows share one cell box."""
    document = fitz.open(pdf_path)
    page = document[page_number - 1]
    for y in (_TOP, _HEADER_BOTTOM, _BOTTOM):
        page.draw_line((_COLUMNS[0], y), (_COLUMNS[-1], y))
    for x in _COLUMNS:
        page.draw_line((x, _TOP), (x, _BOTTOM))
    for column, text in enumerate(["Condition", "Correct", "Total cost"]):
        page.insert_text((_COLUMNS[column] + 4, _TOP + 12), text, fontsize=9)
    for row, cells in enumerate([["With web search", "7/10", "$1.89"], ["Without web search", "2/10", "$0.26"]]):
        for column, text in enumerate(cells):
            page.insert_text((_COLUMNS[column] + 4, _HEADER_BOTTOM + 12 + row * 16), text, fontsize=9)
    document.saveIncr()
    document.close()
    return (_COLUMNS[0], _TOP, _COLUMNS[-1], _BOTTOM)


class TableRowsRepairTests(PilotRepairBenchCase):
    def _inject_merged_table(self) -> str:
        bbox = _draw_booktabs_table(self.bench.pdf_path, 3)
        with self.bench.session_factory() as session:
            block = block_with_text(session, self.bench.document_id, "A generation pipeline retrieves")
            block.block_type = BlockType.TABLE
            block.source_text = "| Condition | Correct | Total cost |\n| With web search Without web search | 7/10 2/10 | $1.89 $0.26 |"
            block.normalized_text = " ".join(block.source_text.split())
            block.source_span_json = {
                **(block.source_span_json or {}),
                "pdf_block_role": "table_like",
                "source_bbox_json": {"regions": [{"page_number": 3, "bbox": list(bbox)}]},
            }
            session.flush()
            ParseRevisionForkService(session).resegment_blocks(self.bench.document_id, block_ids=[block.id], reason="inject")
            session.commit()
            return block.id

    def test_a_table_whose_rows_were_merged_is_read_again_and_only_its_cells_translated(self) -> None:
        table_id = self._inject_merged_table()
        self.translate()
        with self.bench.session_factory() as session:
            # The first translation answered the whole table in its first cell (as on the Agent book).
            header = session.scalars(select(Sentence).where(Sentence.block_id == table_id, Sentence.source_text == "Condition")).one()
            edit_translation(session, header.id, lambda _text: "| 条件 | 正确数 | 总成本 |\n| 使用网页搜索 | 7/10 | $1.89 |")
            session.commit()
        before = self.qa()
        merged = _findings(before, "L3.table_rows_merged")
        self.assertEqual([finding["evidence"]["pdf_rows"] for finding in merged], [3])
        with self.bench.session_factory() as session:
            others = [
                sentence.id
                for sentence in session.scalars(select(Sentence).where(Sentence.retired_by_revision_id.is_(None))).all()
                if sentence.block_id != table_id
            ]
            translations_before = active_target_texts(session, others)
        calls_before = len(self.worker.calls)

        payload, final = self.repair()

        kept = self.attempts(payload, "kept")
        rebuilt = next(attempt for attempt in kept if attempt["path"] == "reextract_table")
        self.assertEqual(rebuilt["params"]["rows"], 3)
        self.assertEqual(_findings(final, "L3.table_rows_merged"), [])
        # One model call, for the table's cells only; nothing else in its packet was translated again.
        self.assertEqual(len(self.worker.calls) - calls_before, 1)
        self.assertTrue(rebuilt["params"]["cells_only_packets"])
        with self.bench.session_factory() as session:
            table = session.get(Block, table_id)
            self.assertEqual(
                table.source_text,
                "| Condition | Correct | Total cost |\n| With web search | 7/10 | $1.89 |\n| Without web search | 2/10 | $0.26 |",
            )
            cells = session.scalars(
                select(Sentence).where(Sentence.block_id == table_id, Sentence.retired_by_revision_id.is_(None), Sentence.translatable.is_(True))
            ).all()
            cell_texts = active_target_texts(session, [cell.id for cell in cells])
            self.assertTrue(cells)
            self.assertTrue(all(cell_texts.get(cell.id) for cell in cells), [cell.source_text for cell in cells])
            # The carried whole-table answer is gone: every cell has its own translation.
            self.assertFalse([text for text in cell_texts.values() if "|" in text])
            self.assertEqual(active_target_texts(session, others), translations_before)
            self.assertEqual(
                session.scalars(select(StructureEdit.kind).where(StructureEdit.document_id == self.bench.document_id)).all(),
                ["rebuild_table"],
            )

    def test_a_malformed_answer_for_the_cells_keeps_the_rebuilt_table(self) -> None:
        """The Illustrated Guide's structure refresh of 18 chapters was undone by one malformed model
        answer for one packet's new sentences: that packet is retranslated whole instead."""
        table_id = self._inject_merged_table()
        self.translate()
        self.worker.malformed_calls = 1

        payload, final = self.repair()

        kept = self.attempts(payload, "kept")
        rebuilt = next(attempt for attempt in kept if attempt["path"] == "reextract_table")
        self.assertEqual(rebuilt["params"]["rows"], 3)
        self.assertEqual(rebuilt["params"]["cells_only_packets"], [])
        self.assertEqual(_findings(final, "L3.table_rows_merged"), [])
        with self.bench.session_factory() as session:
            cells = session.scalars(
                select(Sentence).where(Sentence.block_id == table_id, Sentence.retired_by_revision_id.is_(None), Sentence.translatable.is_(True))
            ).all()
            cell_texts = active_target_texts(session, [cell.id for cell in cells])
            self.assertTrue(all(cell_texts.get(cell.id) for cell in cells), [cell.source_text for cell in cells])


def _block(block_id: str, block_type: str, source: str, target: str = "", html: str = "", **kw) -> QaBlock:
    return QaBlock(
        block_id=block_id,
        chapter_id=kw.pop("chapter_id", "c1"),
        block_type=block_type,
        render_mode=kw.pop("render_mode", "zh_primary_with_optional_source"),
        artifact_kind=kw.pop("artifact_kind", None),
        source_text=source,
        target_text=target,
        html=html,
        page=kw.pop("page", 10),
        **kw,
    )


def _inputs(blocks, **kw):
    return pilot_qa.PilotQaInputs(chapters=kw.pop("chapters", []), blocks=blocks, sentences=kw.pop("sentences", []), **kw)


class ReaderDefectChecksTests(unittest.TestCase):
    def test_merged_table_rows_are_found_against_the_page(self) -> None:
        table = _block(
            "t1",
            "table",
            "| Condition | Correct | Total cost |\n| With web search Without web search | 7/10 2/10 | $1.89 $0.26 |",
            artifact_kind="table",
            regions=((130, (102.0, 556.0, 403.0, 603.0)),),
        )
        rows = [["Condition", "Correct", "Total cost"], ["With web search", "7/10", "$1.89"], ["Without web search", "2/10", "$0.26"]]
        runs = {run.check_id: run for run in pilot_qa.check_tables(_inputs([table], table_rows=lambda page, bbox: rows))}
        finding = runs["L3.table_rows_merged"].findings[0]
        self.assertEqual((finding.evidence["pdf_rows"], finding.evidence["parsed_rows"]), (3, 2))
        self.assertEqual(finding.severity, pilot_qa.BLOCKING)
        # The same rows on the page: nothing to report; without the PDF: not checked.
        same = {run.check_id: run for run in pilot_qa.check_tables(_inputs([table], table_rows=lambda page, bbox: rows[:2]))}
        self.assertEqual(same["L3.table_rows_merged"].findings, [])
        self.assertEqual({run.check_id: run for run in pilot_qa.check_tables(_inputs([table]))}["L3.table_rows_merged"].findings, [])

    def test_a_bullet_in_front_of_a_numbered_item_and_collapsed_callout_lines(self) -> None:
        item = _block("li", "list_item", "1 A router agent receives the user’s request.", "• 由路由智能体接收用户请求。", html="<div class='zh'>• 由路由</div>")
        fine = _block("li2", "list_item", "• Agent tree composition", "• 智能体树组合", html="<div class='zh'>智能体树组合</div>")
        prompt = "You are evaluating an AI-generated answer.\nUser request:\nSummarize the key findings.\nRubric:\nRespond PASS or FAIL."
        collapsed = _block(
            "co",
            "quote",
            prompt,
            "你正在评估……",
            html="<section class='block callout callout-prose'><p class='callout-line'>你正在评估一个回答。 用户请求： 总结关键发现。</p></section>",
        )
        kept = _block(
            "ck",
            "quote",
            prompt,
            "你正在评估……",
            html="<section class='block callout callout-prose'>" + "<p class='callout-line'>行</p>" * 5 + "</section>",
        )
        runs = {run.check_id: run for run in pilot_qa.check_code_and_format(_inputs([item, fine, collapsed, kept]))}
        self.assertEqual([finding.target["id"] for finding in runs["L4.list_marker_doubled"].findings], ["li"])
        self.assertTrue(runs["L4.list_marker_doubled"].findings[0].evidence["shown"])
        self.assertEqual([finding.target["id"] for finding in runs["L4.callout_lines_collapsed"].findings], ["co"])
        self.assertEqual(runs["L4.callout_lines_collapsed"].findings[0].evidence["source_lines"], 5)

    def test_a_cut_short_copy_of_the_annotation_beside_it(self) -> None:
        copy = _block("cut", "code", "Move back by the ov to maintain con chunks<code", parsed_text="Move back by the ov\nto maintain con\nchunks<code")
        whole = _block(
            "whole",
            "paragraph",
            "Move back by the overlap amount to maintain context between chunks<code></code>.",
            parsed_text="Move back by the overlap amount\nto maintain context between\nchunks<code></code>.",
        )
        run = pilot_qa.check_clipped_copies(_inputs([copy, whole]))
        self.assertEqual([(finding.target["id"], finding.evidence["copy_of"]) for finding in run.findings], [("cut", "whole")])

    def test_an_english_paragraph_the_reader_sees_untranslated(self) -> None:
        shown = _block(
            "out",
            "paragraph",
            "The output shows a dramatic reduction in token usage: Total tokens: 37312 Selected tokens: 304",
            html="<div class='zh'>The output shows a dramatic reduction in token usage: Total tokens: 37312</div>",
            font_names=("JetBrainsMonoNL-Regular",),
        )
        runs = {run.check_id: run for run in pilot_qa.check_purity(_inputs([shown]))}
        finding = runs["L7.untranslated_prose"].findings[0]
        self.assertEqual(finding.evidence["detail"], "shown_untranslated")
        self.assertTrue(finding.evidence["code_face"])

    def test_numbered_titles_in_another_form_and_a_part_without_its_name(self) -> None:
        chapters = [
            QaChapter("c1", 9, "1 What is an AI agent?", None, 22, 38),
            QaChapter("c2", 11, "3 Enabling actions: Tool use", None, 64, 90),
            QaChapter("p1", 8, "Part 1 Building your first LLM agent", None, 20, 21),
        ]
        exported = [
            ExportedChapter("c1", "1 什么是 AI 智能体？", ""),
            ExportedChapter("c2", "第 3 章 使能行动：工具使用", ""),
            ExportedChapter("p1", "第 1 部分", ""),
        ]
        missing, titles, unnamed = pilot_qa.check_exported_chapters(_inputs([], chapters=chapters, exported=exported))
        self.assertEqual([finding.target["id"] for finding in titles.findings], ["c1"])
        self.assertEqual([finding.target["id"] for finding in unnamed.findings], ["p1"])

    def test_a_paragraph_kept_twice_by_an_older_parse_is_found_but_not_a_repeat_pages_apart(self) -> None:
        text = "You've done your homework and evaluated the available opportunities. To make their decision clearer."
        first = _block("b1", "paragraph", text)
        table = _block("t1", "table", "| A | B |\n| 1 | 2 |", artifact_kind="table")
        copy = _block("b2", "paragraph", text.replace(" To", "\nTo"))
        later = [_block(f"x{index}", "paragraph", f"Another paragraph number {index} with enough words in it.") for index in range(3)]
        repeat = _block("b9", "paragraph", text)
        next_page = _block("b10", "paragraph", text, page=11)
        run = pilot_qa.check_duplicated_blocks(_inputs([first, table, copy, *later, repeat, next_page]))
        self.assertEqual([(finding.target["id"], finding.evidence["copy_of"]) for finding in run.findings], [("b2", "b1")])
        self.assertEqual(run.findings[0].severity, pilot_qa.BLOCKING)

    def test_every_new_check_has_a_repair_path(self) -> None:
        from book_agent.domain.quality.pilot_repair import paths_for

        expected = {
            "L3.table_rows_merged": "structure_edit.reextract_table",
            "L1.clipped_text_copy": "structure_edit.hide_clipped_copy",
            "L1.duplicated_block": "structure_edit.hide_duplicate_copy",
            "L1.part_title_unnamed": "structure_edit.split_part_name",
            "L4.list_marker_doubled": "segment_edit.strip_doubled_list_marker",
            "L4.callout_lines_collapsed": "retranslate_packets.callout_line_by_line",
            "L7.untranslated_prose": "structure_edit.relabel_code_face_as_code",
        }
        for check_id, path in expected.items():
            with self.subTest(check_id=check_id):
                self.assertIn(check_id, pilot_qa.CHECKS)
                self.assertEqual(paths_for(check_id)[0].name, path)


class PartNameOnPageTests(unittest.TestCase):
    def test_the_display_lines_of_a_part_page_are_its_name(self) -> None:
        from book_agent.services.pilot_repair_tools import PilotRepairToolbox

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "part.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((380, 100), "Part 1", fontsize=30)
            page.insert_textbox(fitz.Rect(200, 130, 560, 230), "Building your first\nLLM agent", fontsize=30, align=1)
            page.insert_textbox(fitz.Rect(126, 260, 480, 400), "The term AI agent is used broadly. " * 6, fontsize=10)
            document.save(path)
            toolbox = SimpleNamespace(_pdf=lambda: fitz.open(path))
            name = PilotRepairToolbox._part_name_on_page(toolbox, 1)
        self.assertEqual(name, "Building your first LLM agent")


if __name__ == "__main__":
    unittest.main()
