"""Single-file HTML downloads: images embedded, bilingual chapters assembled into one book, zip on request."""

from __future__ import annotations

import base64
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from book_agent.domain.enums import ExportType
from book_agent.domain.models import Chapter
from book_agent.export.standalone import (
    BookChapter,
    assemble_bilingual_book,
    drop_unused_katex,
    inline_local_assets,
    local_references,
)
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.review import ReviewService
from book_agent.services.translation import TranslationService
from book_agent.services.workflows import DocumentWorkflowService
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML

ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
KATEX = (
    "<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css'>"
    "<script src='https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js'></script>"
    "<script>document.querySelectorAll('.katex-source').forEach(el => {try { katex.render(el.textContent, el); } catch(e) {}});</script>"
)


class StandaloneHelpersTests(unittest.TestCase):
    def test_local_images_are_embedded_and_nothing_else_is_touched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets" / "pdf-images").mkdir(parents=True)
            (root / "assets" / "pdf-images" / "a b.png").write_bytes(PNG)
            document = (
                "<img src='assets/pdf-images/a%20b.png'><img src=\"assets/missing.png\">"
                "<a href='#toc'>x</a><img src='https://example.com/x.png'><img src='../outside.png'>"
            )
            result = inline_local_assets(document, root)
        self.assertIn("src='data:image/png;base64,", result)
        self.assertIn('src="assets/missing.png"', result)
        self.assertIn("href='#toc'", result)
        self.assertIn("https://example.com/x.png", result)
        self.assertIn("../outside.png", result)
        self.assertEqual(local_references(result), ["assets/missing.png", "../outside.png"])

    def test_the_formula_renderer_goes_only_when_no_element_needs_it(self) -> None:
        without = drop_unused_katex(f"<head>{KATEX}</head><p>plain</p>")
        self.assertNotIn("katex", without)
        with_formula = f"<head>{KATEX}</head><div class='katex-source'>x^2</div>"
        self.assertEqual(drop_unused_katex(with_formula), with_formula)

    def test_chapters_become_one_book_with_a_table_of_contents(self) -> None:
        chapter = (
            "<html><head><style>.page{color:red}</style></head><body><main class='page'>"
            "<header class='hero'><div class='hero-kicker'>Chapter Export</div><h1>{title}</h1></header>"
            "<section class='usage-summary' aria-label='Translation usage summary'><ul class='usage-list'><li>43 calls</li></ul></section>"
            "<section class='chapter-body'><p>{body}</p></section></main>" + KATEX + "</body></html>"
        )
        book = assemble_bilingual_book(
            title="交易者",
            subtitle="Dear Traders",
            chapters=[
                BookChapter("Chapter One", chapter.replace("{title}", "第一章").replace("{body}", "一")),
                BookChapter("第二章", chapter.replace("{title}", "第二章").replace("{body}", "二")),
            ],
        )
        self.assertEqual(book.count("<style>.page{color:red}</style>"), 1)
        self.assertIn("<a href='#chapter-1'>第一章</a>", book)
        self.assertIn("id='chapter-2'", book)
        self.assertNotIn("usage-summary", book)
        self.assertNotIn("Chapter Export", book)
        self.assertEqual(book.count("class='hero-kicker'"), 1)  # only the book's own banner
        self.assertEqual(book.count("katex.min.js"), 1)
        self.assertLess(book.index("<p>一</p>"), book.index("<p>二</p>"))


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        engine = build_engine(f"sqlite+pysqlite:///{root / 'dl.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.session_factory = build_session_factory(engine=engine)
        self.export_root = root / "exports"
        epub = root / "sample.epub"
        with zipfile.ZipFile(epub, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        artifacts = BootstrapOrchestrator().bootstrap_epub(epub)
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            translation = TranslationService(TranslationRepository(session))
            for packet in artifacts.translation_packets:
                translation.execute_packet(packet.id)
            for chapter in artifacts.chapters:
                ReviewService(ReviewRepository(session)).review_chapter(chapter.id)
            workflow = DocumentWorkflowService(session, export_root=self.export_root)
            workflow.export_document(artifacts.document.id, ExportType.MERGED_HTML)
            for chapter in artifacts.chapters:
                workflow.export_service.export_bilingual_html(chapter.id, enforce_gate=False)
            session.commit()
            self.chapter_id = session.scalar(select(Chapter.id))
        self.document_id = artifacts.document.id
        # An image beside the exports, referenced the way PDF figures are.
        document_dir = self.export_root / self.document_id
        (document_dir / "assets" / "pdf-images").mkdir(parents=True, exist_ok=True)
        (document_dir / "assets" / "pdf-images" / "fig.png").write_bytes(PNG)
        for html_file in document_dir.glob("*.html"):
            text = html_file.read_text(encoding="utf-8")
            html_file.write_text(text.replace("</main>", "<img src='assets/pdf-images/fig.png'></main>"), encoding="utf-8")

        patcher = patch.dict(os.environ, {"BOOK_AGENT_AUTH_MODE": "disabled", "BOOK_AGENT_RUN_EXECUTOR_ENABLED": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        from book_agent.app.main import create_app
        from book_agent.core.config import get_settings

        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        app = create_app()
        app.state.session_factory = self.session_factory
        app.state.export_root = str(self.export_root)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _get(self, path: str):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text[:300])
        return response

    def test_book_downloads_are_single_self_contained_html_files(self) -> None:
        base = f"/v1/documents/{self.document_id}/exports/download"
        merged = self._get(f"{base}?export_type=merged_html")
        self.assertTrue(merged.headers["content-type"].startswith("text/html"))
        self.assertIn(".html", merged.headers["content-disposition"])
        self.assertIn("src='data:image/png;base64,", merged.text)
        self.assertEqual(local_references(merged.text), [])

        bilingual = self._get(f"{base}?export_type=bilingual_html")
        self.assertTrue(bilingual.headers["content-type"].startswith("text/html"))
        self.assertIn("class='book-toc'", bilingual.text)
        self.assertIn("class='book-chapter'", bilingual.text)
        self.assertEqual(local_references(bilingual.text), [])

        zipped = self._get(f"{base}?export_type=bilingual_html&package=zip")
        self.assertEqual(zipped.headers["content-type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
            self.assertTrue(any(name.endswith("fig.png") for name in archive.namelist()))

    def test_the_chinese_book_downloads_with_a_table_of_contents_and_without_run_statistics(self) -> None:
        merged = self._get(f"/v1/documents/{self.document_id}/exports/download?export_type=merged_html").text
        self.assertIn("class='reader-toc'", merged)
        self.assertNotIn("usage-summary'", merged.split("</style>")[-1])

    def test_the_chinese_book_downloads_as_a_valid_epub(self) -> None:
        import xml.etree.ElementTree as ET

        response = self._get(f"/v1/documents/{self.document_id}/exports/download?export_type=merged_html&package=epub")
        self.assertEqual(response.headers["content-type"], "application/epub+zip")
        self.assertIn(".epub", response.headers["content-disposition"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            self.assertEqual(names[0], "mimetype")
            self.assertEqual(archive.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            for name in names:
                if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                    ET.fromstring(archive.read(name))  # well-formed XML
            self.assertTrue(any(name.startswith("OEBPS/images/") for name in names))
            chapters = [name for name in names if name.startswith("OEBPS/text/chapter-")]
            self.assertTrue(chapters)
            chapter = archive.read(chapters[0]).decode("utf-8")
            self.assertIn('src="../images/img0001.png"', chapter)
            self.assertNotIn("<script", chapter)
        import fitz

        with tempfile.NamedTemporaryFile(suffix=".epub") as handle:
            handle.write(response.content)
            handle.flush()
            book = fitz.open(handle.name)
            self.assertGreater(book.page_count, 0)
            self.assertTrue(book.get_toc())

        bad = self.client.get(f"/v1/documents/{self.document_id}/exports/download?export_type=bilingual_html&package=epub")
        self.assertEqual(bad.status_code, 422)

    def test_exports_from_an_older_renderer_are_refused_until_re_exported(self) -> None:
        from book_agent.domain.models.review import Export

        with self.session_factory() as session:
            export = session.scalars(select(Export).where(Export.export_type == ExportType.MERGED_HTML)).one()
            export.input_version_bundle_json = {**export.input_version_bundle_json, "renderer_version": 1}
            session.commit()
        url = f"/v1/documents/{self.document_id}/exports/download?export_type=merged_html"
        stale = self.client.get(url)
        self.assertEqual(stale.status_code, 404)
        self.assertIn("renderer version 1", stale.json()["detail"])
        with self.session_factory() as session:
            DocumentWorkflowService(session, export_root=self.export_root).export_document(self.document_id, ExportType.MERGED_HTML)
            session.commit()
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_chapter_downloads_embed_their_images_too(self) -> None:
        response = self._get(f"/v1/documents/{self.document_id}/chapters/{self.chapter_id}/exports/download?export_type=bilingual_html")
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        self.assertIn("src='data:image/png;base64,", response.text)

    def test_a_missing_image_falls_back_to_the_zip(self) -> None:
        (self.export_root / self.document_id / "assets" / "pdf-images" / "fig.png").unlink()
        (self.export_root / self.document_id / "assets" / "pdf-images" / "other.png").write_bytes(PNG)
        response = self._get(f"/v1/documents/{self.document_id}/exports/download?export_type=merged_html")
        self.assertEqual(response.headers["content-type"], "application/zip")


if __name__ == "__main__":
    unittest.main()
