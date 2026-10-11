"""Table cells as translatable units: detection, segmentation, packets, and rendering (06 B-20)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from book_agent.domain.block_rules import block_is_context_translatable
from book_agent.domain.enums import BlockType
from book_agent.domain.structure.table_cells import is_translatable_cell, table_grid, translatable_cells
from book_agent.export.markup import (
    markdown_table_to_html,
    render_structured_table_html,
    render_table_html_metadata_aware,
    render_table_markdown_metadata_aware,
)

TABLE = "Tier | Latency | Notes\nBasic | 120 ms | Slow but cheap\nPro | 20 ms | API\nBasic | 90 ms | config_path=/etc/x"


class CellDetectionTests(unittest.TestCase):
    def test_words_are_translatable_numbers_codes_and_acronyms_are_not(self) -> None:
        for cell in ("Tier", "Slow but cheap", "Latency"):
            self.assertTrue(is_translatable_cell(cell), cell)
        for cell in ("", "120 ms", "20%", "API", "HTTP/2", "config_path=/etc/x", "https://x.io/a", "len(x)"):
            self.assertFalse(is_translatable_cell(cell), cell)

    def test_distinct_cells_in_reading_order_only_for_tables(self) -> None:
        text = TABLE
        self.assertEqual(translatable_cells("table", text, {}), ["Tier", "Latency", "Notes", "Basic", "Slow but cheap", "Pro"])
        self.assertEqual(translatable_cells(BlockType.TABLE, text, {"translatable": False}), [])
        self.assertEqual(translatable_cells("paragraph", text, {}), [])
        markdown = "| Name | Role |\n| --- | --- |\n| Alice | Maintainer |"
        self.assertEqual(table_grid("ignored", {"table_markdown": markdown}), [["Name", "Role"], ["Alice", "Maintainer"]])

    def test_tables_with_cell_text_join_translation_packets(self) -> None:
        text = TABLE
        table = SimpleNamespace(block_type=BlockType.TABLE, source_text=text, source_span_json={})
        numbers = SimpleNamespace(block_type=BlockType.TABLE, source_text="1 | 2\n3 | 4", source_span_json={})
        self.assertTrue(block_is_context_translatable(table))
        self.assertFalse(block_is_context_translatable(numbers))


class CellRenderingTests(unittest.TestCase):
    translations = {"Tier": "层级", "Slow but cheap": "慢但便宜"}

    def test_structured_and_markdown_tables_substitute_translations(self) -> None:
        text = TABLE
        html = render_structured_table_html(text, cell_translations=self.translations)
        self.assertIn(">层级</th>", html)
        self.assertIn(">慢但便宜</td>", html)
        self.assertIn(">120 ms</td>", html)
        self.assertNotIn("层级", render_structured_table_html(text))
        markdown = "| Tier | Latency |\n| --- | --- |\n| Basic | Slow but cheap |"
        self.assertIn("<th>层级</th>", markdown_table_to_html(markdown, cell_translations=self.translations))
        metadata = {"table_markdown": markdown, "table_cell_translations": self.translations}
        self.assertIn("<td>慢但便宜</td>", render_table_html_metadata_aware(metadata, ""))
        rendered_md = render_table_markdown_metadata_aware(metadata, "")
        self.assertIn("| 层级 | Latency |", rendered_md)
        self.assertIn("| --- | --- |", rendered_md)
        self.assertEqual(render_table_markdown_metadata_aware({"table_markdown": markdown}, ""), markdown)


class SharedGridTranslationTests(unittest.TestCase):
    """The model answered several cells with one segment: the whole translated grid (the Agent book's tables)."""

    def test_a_shared_translated_grid_is_split_back_cell_by_cell(self) -> None:
        from book_agent.domain.structure.table_cells import split_shared_cell_translations

        source = "| Model | Accuracy | Judged solvable |\n| GPT-5 mini | 4/20 (20%) | 8/20 (40%) |"
        whole = "| 模型 | 准确率 | 判定为可解 |\n| GPT-5 mini | 4/20 (20%) | 8/20 (40%) |"
        cells = {cell: whole for cell in ("Model", "Accuracy", "Judged solvable")}
        split = split_shared_cell_translations(table_grid(source), cells)
        self.assertEqual(split, {"Model": "模型", "Accuracy": "准确率", "Judged solvable": "判定为可解"})
        html = render_structured_table_html(source, cell_translations=split)
        self.assertIn(">模型</th>", html)
        self.assertNotIn("| 模型", html)

    def test_a_grid_whose_line_breaks_were_dropped_is_cut_back_into_rows(self) -> None:
        """Art of AI p42: the model answered the Miro table on one line ("... | AI 优势 | | Miro AI | ...")."""
        from book_agent.domain.structure.table_cells import split_shared_cell_translations

        source = "| Miro features | Human strengths |\n| Miro AI | Users provide input. |\n| AI Sidekicks | |"
        whole = "| Miro 功能 | 人类优势 | | Miro AI | 用户提供输入。 | | AI Sidekicks | |"
        cells = {cell: whole for cell in ("Miro features", "Human strengths", "Miro AI", "Users provide input.", "AI Sidekicks")}
        split = split_shared_cell_translations(table_grid(source), cells)
        self.assertEqual(
            split,
            {
                "Miro features": "Miro 功能",
                "Human strengths": "人类优势",
                "Miro AI": "Miro AI",
                "Users provide input.": "用户提供输入。",
                "AI Sidekicks": "AI Sidekicks",
            },
        )
        # A row boundary must be an empty cell; otherwise the shape does not fit and nothing is cut.
        wrong = "| Miro 功能 | 人类优势 | 多余 | Miro AI | 用户提供输入。 | | AI Sidekicks | |"
        self.assertEqual(split_shared_cell_translations(table_grid(source), {"Miro AI": wrong, "AI Sidekicks": wrong}), {"Miro AI": wrong, "AI Sidekicks": wrong})

    def test_a_row_answered_as_a_grid_for_its_only_sentence_cell_is_split(self) -> None:
        """Art of AI table 5.2: "MMLU" is no sentence, so the row's grid answer belongs to one cell."""
        from book_agent.domain.structure.table_cells import split_shared_cell_translations

        source = "| MMLU | Measures LLMs on many tasks. |\n| ARC | Tests reasoning on science questions. |"
        row1 = "| MMLU | 评测 LLM 在各类任务上的表现。 |"
        row2 = "| ARC | 用科学选择题测试推理能力。 |"
        cells = {"Measures LLMs on many tasks.": row1, "ARC": row2, "Tests reasoning on science questions.": row2}
        split = split_shared_cell_translations(table_grid(source), cells)
        self.assertEqual(
            split,
            {"Measures LLMs on many tasks.": "评测 LLM 在各类任务上的表现。", "ARC": "ARC", "Tests reasoning on science questions.": "用科学选择题测试推理能力。"},
        )

    def test_header_and_body_answered_apart_and_joined_are_split(self) -> None:
        """Art of AI table 4.4: only the header cells are sentences; the model answered the header row
        and the body rows as two segments, joined so the first line holds the header and a row."""
        from book_agent.domain.structure.table_cells import split_shared_cell_translations

        source = "| Timestamp | Recommendation clicks |\n| 2024-08-19 12:01 | 150 |\n| 2024-08-19 12:02 | 172 |"
        joined = "| 时间戳 | 推荐点击量 | | 2024-08-19 12:01 | 150 |\n| 2024-08-19 12:02 | 172 |"
        split = split_shared_cell_translations(table_grid(source), {"Timestamp": joined, "Recommendation clicks": joined})
        self.assertEqual(split, {"Timestamp": "时间戳", "Recommendation clicks": "推荐点击量"})

    def test_a_grid_without_outer_bars_is_split(self) -> None:
        from book_agent.domain.structure.table_cells import split_shared_cell_translations

        source = "| Type | Algorithm |\n| Clustering | K-means |"
        whole = "类型 | 算法 | | 聚类 | K均值"
        split = split_shared_cell_translations(table_grid(source), {"Type": whole, "Algorithm": whole, "Clustering": whole})
        self.assertEqual(split, {"Type": "类型", "Algorithm": "算法", "Clustering": "聚类"})

    def test_a_grid_of_another_shape_is_left_alone(self) -> None:
        from book_agent.domain.structure.table_cells import split_shared_cell_translations

        source = "| A | B |\n| alpha | beta |"
        cells = {"alpha": "| 甲 | 乙 | 丙 |", "beta": "| 甲 | 乙 | 丙 |"}
        self.assertEqual(split_shared_cell_translations(table_grid(source), cells), cells)
        self.assertEqual(split_shared_cell_translations(table_grid(source), {"alpha": "阿尔法"}), {"alpha": "阿尔法"})

    def test_long_cells_in_a_pipe_grid_are_still_a_table_not_prose(self) -> None:
        from book_agent.export.render_repair import is_translatable_prose_artifact_block

        text = (
            "| Strategy | Description | Related chapters |\n"
            "| Generate Retrieve Write | Constructing context with LLM-generated outputs Bringing information from "
            "external sources into context Saving from context to external storage | Chapter 7 (planning and reflection) "
            "Chapter 3 (tools), Chapter 5 (knowledge base), Chapter 6 (memory) |"
        )
        metadata = {"pdf_block_role": "table_like"}
        block = SimpleNamespace(block_type=BlockType.TABLE, source_text=text, source_span_json=metadata)
        self.assertFalse(is_translatable_prose_artifact_block(block, metadata, block_type=BlockType.TABLE, source_text=text))


if __name__ == "__main__":
    unittest.main()
