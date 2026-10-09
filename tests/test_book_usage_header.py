"""Every reading copy opens with the book's model usage: calls, tokens and cost of everything the
book took (translation, review, quality checks, repairs, the pilot included), from the ledger."""

from __future__ import annotations

import unittest
import zipfile
from pathlib import Path

from book_agent.domain.enums import ExportType
from book_agent.export.reader_epub import build_reader_epub, reader_html
from book_agent.export.standalone import BookChapter, assemble_bilingual_book
from book_agent.services.usage_statement import document_usage
from tests.pilot_repair_fixture import FakeZhWorker, PilotBench


class BookUsageHeaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bench = PilotBench(FakeZhWorker(cost_per_call=0.0125))
        self.addCleanup(self.bench.close)

    def test_no_calls_no_usage(self) -> None:
        with self.bench.session_factory() as session:
            self.assertIsNone(document_usage(session, self.bench.document_id))

    def test_the_reading_copies_open_with_the_books_usage(self) -> None:
        chapter_ids = self.bench.translate_pilot()
        with self.bench.session_factory() as session:
            usage = document_usage(session, self.bench.document_id)
            self.assertIsNotNone(usage)
            self.assertGreater(usage.call_count, 0)
            self.assertEqual(usage.token_in, 1000 * usage.call_count)
            self.assertEqual(usage.token_out, 500 * usage.call_count)
            self.assertAlmostEqual(usage.cost_usd, 0.0125 * usage.call_count, places=6)
            summary = usage.summary_zh()
            self.assertIn(f"Token {1500 * usage.call_count:,}", summary)
            self.assertIn(f"US$ {0.0125 * usage.call_count:,.2f}", summary)
            workflow = self.bench.workflow(session)
            html_export = workflow.export_document(self.bench.document_id, ExportType.MERGED_HTML, chapter_ids=chapter_ids, despite_gate=True)
            md_export = workflow.export_document(self.bench.document_id, ExportType.MERGED_MARKDOWN, chapter_ids=chapter_ids, despite_gate=True)
            session.commit()
        merged = Path(html_export.file_path).read_text(encoding="utf-8")
        markdown = Path(md_export.file_path).read_text(encoding="utf-8")

        # HTML: under the title, before the first chapter.
        self.assertIn(summary, merged)
        self.assertLess(merged.index("</header>"), merged.index("class='book-usage'"))
        self.assertLess(merged.index("class='book-usage'"), merged.index("<section class='chapter'"))
        # Markdown: a line under the title, before the contents.
        head = markdown.split("\n## ", 1)[0]
        self.assertIn(f"> 翻译用量：{summary}", head)
        # The reader's download keeps it, its contents list after it; the EPUB's title page has it.
        reader = reader_html(merged)
        self.assertIn("class='book-usage'", reader)
        self.assertLess(reader.index("class='book-usage'"), reader.index("class='reader-toc'"))
        epub = build_reader_epub(merged, Path(html_export.file_path).parent, self.bench.root / "book.epub")
        with zipfile.ZipFile(epub) as archive:
            self.assertIn("翻译用量", archive.read("OEBPS/text/title.xhtml").decode("utf-8"))

    def test_the_bilingual_book_opens_with_the_usage(self) -> None:
        chapter = "<html><head></head><body><main class='page'><header class='hero'><h1>第一章</h1></header><p>一</p></main></body></html>"
        book = assemble_bilingual_book(
            title="书", subtitle=None, chapters=[BookChapter("第一章", chapter)], usage="模型调用 3 次 · Token 4,500"
        )
        self.assertLess(book.index("模型调用 3 次"), book.index("class='book-toc'"))
        self.assertNotIn("book-usage'", assemble_bilingual_book(title="书", subtitle=None, chapters=[BookChapter("第一章", chapter)]))


if __name__ == "__main__":
    unittest.main()
