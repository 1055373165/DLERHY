"""A book in parts: its chapters are listed under each part in every reading copy's contents."""

from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from book_agent.export import markup
from book_agent.export.book_parts import part_depths
from book_agent.export.models import MergedRenderBlock
from book_agent.export.reader_epub import build_reader_epub, nested_list, reader_html, split_merged_book


def _chapter(anchor: str, title: str, depth: int) -> str:
    tag = f"h{2 + depth}"
    return (
        f"<section class='chapter' id='chapter-{anchor}'><div class='chapter-head'><{tag}>{title}</{tag}></div>"
        f"<section class='block paragraph'><div class='zh'>{title}的正文。</div></section></section>"
    )


BOOK = (
    "<html><head></head><body><main class='book-main'><header class='hero' id='top'><h1>书</h1></header>"
    + _chapter("a", "前言", 0)
    + _chapter("b", "第 1 部分 基础", 0)
    + _chapter("c", "第 1 章 引言", 1)
    + _chapter("d", "第 2 章 模型", 1)
    + _chapter("e", "第 2 部分 进阶", 0)
    + _chapter("f", "第 3 章 记忆", 1)
    + _chapter("g", "后记", 0)
    + "</main></body></html>"
)


class PartDepthsTest(unittest.TestCase):
    def test_a_parts_numbered_chapters_sit_under_it(self) -> None:
        titles = ["卷首", "前言", "第 1 部分 AI 智能体的解剖", "第 1 章 引言", "第 2 章 大语言模型",
                  "第 2 部分 专用智能体", "第 8 章 多智能体系统", "后记", "附录 OpenAI API 密钥", "索引"]
        self.assertEqual(part_depths(titles), [0, 0, 0, 1, 1, 0, 1, 0, 0, 0])
        self.assertEqual(part_depths(["Part I Basics", "Chapter 1 Intro", "2 The loop", "Appendix A"]), [0, 1, 1, 0])
        # A book without parts lists every chapter at the top.
        self.assertEqual(part_depths(["前言", "第 1 章 引言", "第 2 章 模型"]), [0, 0, 0])


class NestedContentsTest(unittest.TestCase):
    def test_the_reader_contents_nest_chapters_under_their_part(self) -> None:
        _title, chapters = split_merged_book(BOOK)
        self.assertEqual([c.depth for c in chapters], [0, 0, 1, 1, 0, 1, 0])
        toc = reader_html(BOOK).split("<nav class='reader-toc'", 1)[1].split("</nav>", 1)[0]
        ET.fromstring(toc[toc.index("<ol>"):])  # well-formed
        self.assertIn("第 1 部分 基础</a><ol><li><a href='#chapter-c'>第 1 章 引言</a></li>", toc)
        self.assertEqual(nested_list([(0, "a"), (0, "P"), (1, "c"), (0, "z")]), "<ol><li>a</li><li>P<ol><li>c</li></ol></li><li>z</li></ol>")

    def test_the_epub_navigation_nests_chapters_under_their_part(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            epub = build_reader_epub(BOOK, Path(tmp), Path(tmp) / "book.epub")
            with zipfile.ZipFile(epub) as archive:
                nav = ET.fromstring(archive.read("OEBPS/text/nav.xhtml"))
                ncx = ET.fromstring(archive.read("OEBPS/toc.ncx"))
        ns = {"x": "http://www.w3.org/1999/xhtml"}
        top = nav.findall(".//x:nav/x:ol/x:li", ns)
        self.assertEqual(["".join(li.find("x:a", ns).itertext()) for li in top], ["前言", "第 1 部分 基础", "第 2 部分 进阶", "后记"])
        self.assertEqual(["".join(a.itertext()) for a in top[1].findall("x:ol/x:li/x:a", ns)], ["第 1 章 引言", "第 2 章 模型"])
        n = {"n": "http://www.daisy.org/z3986/2005/ncx/"}
        part = [p for p in ncx.findall("n:navMap/n:navPoint", n) if p.find("n:navLabel/n:text", n).text == "第 1 部分 基础"][0]
        self.assertEqual([p.find("n:navLabel/n:text", n).text for p in part.findall("n:navPoint", n)], ["第 1 章 引言", "第 2 章 模型"])


class MarkdownHeadingShiftTest(unittest.TestCase):
    def test_a_chapter_inside_a_part_has_its_sections_one_level_down(self) -> None:
        block = MergedRenderBlock(
            block_id="b", chapter_id="c", block_type="heading", render_mode="zh_primary_with_optional_source",
            artifact_kind=None, title=None, source_text="1.1 Agents", target_text="1.1 智能体",
            source_metadata={"heading_level": 2}, source_sentence_ids=[], target_segment_ids=[],
            is_expected_source_only=False, notice=None,
        )
        self.assertEqual(markup.render_block_markdown(block), "### 1.1 智能体")
        self.assertEqual(markup.render_block_markdown(block, heading_shift=1), "#### 1.1 智能体")
        deep = MergedRenderBlock(**{**{f: getattr(block, f) for f in block.__dataclass_fields__}, "source_metadata": {"heading_level": 5}})
        self.assertEqual(markup.render_block_markdown(deep, heading_shift=1), "###### 1.1 智能体")


if __name__ == "__main__":
    unittest.main()
