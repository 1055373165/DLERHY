import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import ExportType, SourceType
from book_agent.domain.structure import web
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.ingestion.web import WebCaptureError, capture_web_page
from book_agent.services.url_guard import UnsafeUrlError, ensure_public_http_url
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.translator import EchoTranslationWorker

PAGE = """<!DOCTYPE html>
<html lang="en-US"><head>
<meta charset="iso-8859-1">
<title>When AI builds itself \\ Example</title>
<link rel="stylesheet" href="/static/site.css" media="screen">
<link rel="preload" as="script" href="/static/app.js">
<script src="/static/app.js"></script>
<style>.hero{background:url(img/bg.png)}</style>
</head>
<body class="page">
<nav><ul><li><a class="nav-link" href="/research">Research</a></li><li><a href="/news">News</a></li></ul></nav>
<main>
<header><p class="kicker">Essay</p></header>
<h1 class="title">When AI builds itself</h1>
<p class="body">The rate is <a class="inline" href="https://metr.org/">accelerating fast</a>, and <b class="x">bold claims</b> need <em>care</em>.</p>
<div class="card">Loose card text<p>Nested paragraph.</p></div>
<pre><code>print("code stays")</code></pre>
<p translate="no">Brand Name Stays</p>
<img src="chart.png" srcset="chart.png 1x, chart@2x.png 2x" alt="Chart">
<p>2026</p>
</main>
<footer><ul><li><a href="/terms">Terms of service</a></li><li><a href="/privacy">Privacy</a></li><li>Help center</li></ul></footer>
<script>window.__DATA__ = {"text": "Hydration English"};</script>
</body></html>
"""

CSS = "@font-face{font-family:Sans;src:url(fonts/sans.woff2) format('woff2')}body{background:url('../img/paper.png')}"


def _transport(pages: dict[str, httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        response = pages.get(str(request.url))
        return response if response is not None else httpx.Response(404, text="missing")

    return httpx.MockTransport(handler)


def _site() -> dict[str, httpx.Response]:
    return {
        "https://example.com/post": httpx.Response(301, headers={"location": "https://example.com/articles/post"}),
        "https://example.com/articles/post": httpx.Response(
            200, headers={"content-type": "text/html; charset=utf-8"}, text=PAGE
        ),
        "https://example.com/static/site.css": httpx.Response(200, headers={"content-type": "text/css"}, text=CSS),
        "https://example.com/static/fonts/sans.woff2": httpx.Response(200, content=b"wOF2fake"),
    }


def _capture(target: Path, pages: dict[str, httpx.Response] | None = None, **kwargs):
    with httpx.Client(transport=_transport(pages or _site())) as client:
        return capture_web_page("https://example.com/post", target, client=client, **kwargs)


class HtmlTreeTests(unittest.TestCase):
    def test_parse_and_serialize_is_lossless(self) -> None:
        document = (
            "<!doctype html><!-- c --><html><body><p>One &amp; two &#8217; <br>three<p>implied"
            "<ul><li>a<li>b</ul><img src=x alt='y'/><script>if (a<b) {}</script></div></body></html>"
        )
        self.assertEqual(web.serialize(web.parse_html(document)), document)

    def test_marking_narrows_to_links_wraps_loose_text_and_skips_code(self) -> None:
        root = web.parse_html(PAGE)
        web.mark_translatable_nodes(root)
        marked = {web.node_source_text(element)[0]: element for _node, element in web.marked_nodes(root)}
        texts = list(marked)
        self.assertEqual(marked["Research"].attrs.get("class"), "nav-link")  # the link itself, not its <li>
        self.assertIn("Loose card text", texts)  # wrapped in a span beside the nested <p>
        self.assertIn("Nested paragraph.", texts)
        self.assertIn(
            "The rate is [accelerating fast][1], and **bold claims** need *care*.",
            texts,
        )
        joined = " ".join(texts)
        for kept in ("code stays", "Brand Name Stays", "Hydration", "2026"):
            self.assertNotIn(kept, joined)

    def test_translation_restores_the_original_inline_tags(self) -> None:
        root = web.parse_html(PAGE)
        web.mark_translatable_nodes(root)
        node = next(
            node for node, element in web.marked_nodes(root) if element.tag == "p" and element.attrs.get("class") == "body"
        )
        page, patched = web.translate_web_page(
            web.serialize(root), {node: "速度[增长很快][1]，**大胆的说法**需要*谨慎*，*星号* & <tag>"}
        )
        self.assertEqual(patched, 1)
        self.assertIn(
            '<p class="body">速度<a class="inline" href="https://metr.org/">增长很快</a>，'
            '<b class="x">大胆的说法</b>需要<em>谨慎</em>，<em>星号</em> &amp; &lt;tag&gt;</p>',
            page,
        )
        self.assertNotIn(web.NODE_ATTR, page)
        self.assertIn('<html lang="zh-CN">', page)

    def test_footnote_sups_and_their_wrapped_links_keep_their_tags(self) -> None:
        root = web.parse_html(
            '<p>Claude wrote it.<sup style="font-size:.75em">3</sup> See '
            '<sup class="fn"><a href="#n4">4</a></sup> too.</p>'
        )
        web.mark_translatable_nodes(root)
        [(node, element)] = web.marked_nodes(root)
        self.assertEqual(web.node_source_text(element)[0], "Claude wrote it.[3][1] See [4][2] too.")
        page, _ = web.translate_web_page(web.serialize(root), {node: "Claude 写的。[3][1]另见[4][2]。"})
        self.assertIn(
            '<p>Claude 写的。<sup style="font-size:.75em">3</sup>另见<sup class="fn"><a href="#n4">4</a></sup>。</p>',
            page,
        )

    def test_markers_the_source_did_not_have_stay_literal(self) -> None:
        self.assertEqual(web.render_translation("a *b* [c][3] `d`", web.InlineTags()), "a *b* [c][3] `d`")


class CaptureTests(unittest.TestCase):
    def test_snapshot_inlines_styles_drops_scripts_and_absolutizes_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            capture = _capture(Path(tmpdir))
            snapshot = capture.path.read_text(encoding="utf-8")
        self.assertEqual(capture.final_url, "https://example.com/articles/post")
        self.assertEqual(capture.stylesheet_count, 1)
        self.assertNotIn("<script", snapshot)
        self.assertNotIn("preload", snapshot)
        self.assertIn('<style data-ba-href="https://example.com/static/site.css" media="screen">', snapshot)
        self.assertIn('url("https://example.com/img/paper.png")', snapshot)
        self.assertIn("data:font/woff2;base64,", snapshot)
        self.assertIn('url("https://example.com/articles/img/bg.png")', snapshot)
        self.assertIn('href="https://example.com/research"', snapshot)
        self.assertIn('srcset="https://example.com/articles/chart.png 1x, https://example.com/articles/chart@2x.png 2x"', snapshot)
        self.assertIn('<meta charset="utf-8">', snapshot)
        self.assertNotIn("iso-8859-1", snapshot)
        self.assertIn(f'<meta name="{web.SOURCE_URL_META}" content="https://example.com/post">', snapshot)

    def test_every_hop_passes_the_guard(self) -> None:
        seen: list[str] = []

        def guard(url: str) -> None:
            seen.append(url)
            if "/articles/" in url:
                raise WebCaptureError("blocked")

        with tempfile.TemporaryDirectory() as tmpdir, self.assertRaises(WebCaptureError):
            _capture(Path(tmpdir), guard=guard)
        self.assertEqual(seen, ["https://example.com/post", "https://example.com/articles/post"])

    def test_private_hosts_are_refused_by_the_url_guard(self) -> None:
        with self.assertRaises(UnsafeUrlError):
            ensure_public_http_url("http://intranet.local/", resolver=lambda _host: ["10.0.0.5"], label="url")

    def test_non_html_and_empty_pages_are_refused(self) -> None:
        pdf = {"https://example.com/post": httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")}
        empty = {
            "https://example.com/post": httpx.Response(
                200, headers={"content-type": "text/html"}, text="<html><body><div id=root></div><script>x()</script></body></html>"
            )
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(WebCaptureError, "不是网页"):
                _capture(Path(tmpdir), pdf)
            with self.assertRaisesRegex(WebCaptureError, "没有找到可翻译"):
                _capture(Path(tmpdir), empty)

    def test_bad_urls_are_refused_before_any_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, self.assertRaises(WebCaptureError):
            capture_web_page("ftp://example.com/x", Path(tmpdir), client=httpx.Client(transport=_transport({})))


class WebPageWorkflowTests(unittest.TestCase):
    def test_capture_translate_and_export_the_same_page_in_chinese(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            engine = build_engine(f"sqlite+pysqlite:///{root / 'web.db'}")
            try:
                Base.metadata.create_all(engine)
                session_factory = build_session_factory(engine=engine)
                exports = str(root / "exports")
                capture = _capture(root / "uploads")
                with session_scope(session_factory) as session:
                    summary = DocumentWorkflowService(session, export_root=exports).bootstrap_document(capture.path)
                    document_id = summary.document_id
                with session_scope(session_factory) as session:
                    workflow = DocumentWorkflowService(
                        session, export_root=exports, translation_worker=EchoTranslationWorker()
                    )
                    document = workflow.export_repository.get_document(document_id)
                    self.assertEqual(document.source_type, SourceType.WEB)
                    # The <title> carries the site name; the book is titled by the article itself.
                    self.assertEqual(document.title, "When AI builds itself")
                    self.assertEqual(document.metadata_json.get("source_url"), "https://example.com/post")
                    workflow.translate_document(document_id)
                    workflow.review_document(document_id)
                with session_scope(session_factory) as session:
                    result = DocumentWorkflowService(session, export_root=exports).export_document(
                        document_id, ExportType.WEB_HTML
                    )
                page = Path(result.file_path).read_text(encoding="utf-8")
                reading = {}
                for export_type in (ExportType.MERGED_HTML, ExportType.MERGED_MARKDOWN):
                    with session_scope(session_factory) as session:
                        exported = DocumentWorkflowService(session, export_root=exports).export_document(
                            document_id, export_type
                        )
                    reading[export_type] = Path(exported.file_path).read_text(encoding="utf-8")
                with session_scope(session_factory) as session:
                    title_tgt = DocumentWorkflowService(session, export_root=exports).export_repository.get_document(
                        document_id
                    ).title_tgt
            finally:
                engine.dispose()

        self.assertIn('<a class="nav-link" href="https://example.com/research">ZH::Research</a>', page)
        self.assertIn('<h1 class="title">ZH::When AI builds itself</h1>', page)
        self.assertIn('<a class="inline" href="https://metr.org/">accelerating fast</a>', page)
        self.assertIn("<title>ZH::When AI builds itself", page)
        self.assertIn('<pre><code>print("code stays")</code></pre>', page)
        self.assertIn('<html lang="zh-CN">', page)
        self.assertNotIn(web.NODE_ATTR, page)
        self.assertNotIn("<script", page)
        # The reading formats read the page as an article: markers become words, menus and footer go.
        for export_type, text in reading.items():
            self.assertIn("accelerating fast", text, export_type)
            self.assertNotIn("][1]", text, export_type)
            self.assertNotIn("Terms of service", text, export_type)
            self.assertNotIn("ZH::Research", text, export_type)
        self.assertNotIn("**", reading[ExportType.MERGED_HTML])
        # The Markdown is read from the translated page: its links, emphasis, code and pictures stay.
        markdown = reading[ExportType.MERGED_MARKDOWN]
        self.assertTrue(markdown.startswith("# ZH::When AI builds itself\n\n"), markdown[:80])
        self.assertEqual(markdown.count("When AI builds itself"), 1)
        self.assertIn("ZH::The rate is [accelerating fast](https://metr.org/), and **bold claims** need *care*.", markdown)
        self.assertIn('```\nprint("code stays")\n```', markdown)
        self.assertIn("![Chart](https://example.com/articles/chart.png)", markdown)
        # The page is titled in Chinese as its own heading reads.
        self.assertEqual(title_tgt, "ZH::When AI builds itself")
        # The article's own heading titles the chapter once; the <title> with the site name is gone.
        merged = reading[ExportType.MERGED_HTML]
        self.assertIn("<h2>ZH::When AI builds itself</h2>", merged)
        self.assertEqual(merged.count("<h2>ZH::When AI builds itself</h2>"), 1)
        # The page's Chinese title is that heading's translation (the document title, the <title>).
        self.assertIn("<title>ZH::When AI builds itself</title>", merged)
        self.assertNotIn("<div class='zh'>ZH::When AI builds itself \\ Example", merged)


class PageTitleTests(unittest.TestCase):
    def test_the_site_name_is_dropped_only_when_the_tail_names_the_site(self) -> None:
        from book_agent.domain.structure.web import article_title

        url = "https://www.anthropic.com/institute/x"
        # The <title> is the article heading plus the site name.
        self.assertEqual(article_title("When AI builds itself \\ Anthropic", "When AI builds itself", url), "When AI builds itself")
        # No heading captured: the tail still names the site.
        self.assertEqual(article_title("Scaling Managed Agents \\ Anthropic", None, url), "Scaling Managed Agents")
        self.assertEqual(article_title("Attention Is All You Need - arXiv", None, "https://arxiv.org/abs/1"), "Attention Is All You Need")
        # A dash that belongs to the title, and a tail that is not the site, both stay.
        self.assertEqual(
            article_title("The Rise and Fall - a history", None, "https://example.com/x"),
            "The Rise and Fall - a history",
        )
        # A title that is only the site name stays as it is.
        self.assertEqual(article_title("Anthropic", None, url), "Anthropic")
        self.assertEqual(article_title(None, "Just the heading", url), "Just the heading")


class WebRunPlanTests(unittest.TestCase):
    def test_a_full_run_of_a_web_page_also_exports_the_chinese_page(self) -> None:
        from book_agent.domain.enums import DocumentRunType, DocumentStatus
        from book_agent.domain.models import Document
        from book_agent.infra.repositories.run_control import RunControlRepository
        from book_agent.orchestrator.run_plan import plan_for_run
        from book_agent.orchestrator.stage_gate import STAGE_DEPENDENCIES
        from book_agent.services.run_control import RunControlService

        engine = build_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        plans = {}
        with session_scope(build_session_factory(engine=engine)) as session:
            control = RunControlService(RunControlRepository(session))
            for source_type in (SourceType.WEB, SourceType.EPUB):
                document = Document(
                    source_type=source_type, file_fingerprint=f"plan-{source_type}", title="t", status=DocumentStatus.ACTIVE
                )
                session.add(document)
                session.flush()
                run = control.create_run(document_id=document.id, run_type=DocumentRunType.TRANSLATE_FULL, requested_by="test")
                plans[source_type] = plan_for_run(run.run_type, run.status_detail_json)

        web_stages = plans[SourceType.WEB].stages
        self.assertEqual(web_stages[web_stages.index("merged_html") + 1], "web_html")
        self.assertIn("web_html", plans[SourceType.WEB].export_stages)
        self.assertNotIn("web_html", plans[SourceType.EPUB].stages)
        self.assertIn("merged_html", STAGE_DEPENDENCIES["web_html"])


class WebChromeTests(unittest.TestCase):
    def test_menus_and_footer_are_packed_together_and_left_out_of_the_terminology_sample(self) -> None:
        from sqlalchemy import select

        from book_agent.domain.context.builders import is_web_chrome
        from book_agent.domain.models import Block, Chapter
        from book_agent.domain.models.translation import TranslationPacket
        from book_agent.harness.agents.terminology import TerminologyAgent

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            engine = build_engine(f"sqlite+pysqlite:///{root / 'web.db'}")
            try:
                Base.metadata.create_all(engine)
                session_factory = build_session_factory(engine=engine)
                capture = _capture(root / "uploads")
                with session_scope(session_factory) as session:
                    document_id = DocumentWorkflowService(session, export_root=str(root / "exports")).bootstrap_document(
                        capture.path
                    ).document_id
                with session_scope(session_factory) as session:
                    blocks = list(
                        session.scalars(
                            select(Block).join(Chapter, Chapter.id == Block.chapter_id)
                            .where(Chapter.document_id == document_id).order_by(Block.ordinal)
                        )
                    )
                    chrome = {block.source_text for block in blocks if is_web_chrome(block)}
                    packets = list(
                        session.scalars(
                            select(TranslationPacket).join(Chapter, Chapter.id == TranslationPacket.chapter_id)
                            .where(Chapter.document_id == document_id)
                        )
                    )
                    packed = [
                        {block["text"] for block in packet.packet_json["current_blocks"]} for packet in packets
                    ]
                    sample = TerminologyAgent(session)._sample_texts(document_id, mode="sampled", max_chars=40_000)
            finally:
                engine.dispose()

        # The article's own <header> is content; the site menus and footer are chrome.
        self.assertIn("Essay", {block.source_text for block in blocks} - chrome)
        self.assertLessEqual({"Terms of service", "Privacy", "Help center"}, chrome)
        self.assertIn({"Terms of service", "Privacy", "Help center"}, [texts & chrome for texts in packed])
        for texts in packed:
            self.assertTrue(texts <= chrome or not texts & chrome, f"chrome mixed with content: {texts}")
        self.assertFalse(set(sample) & chrome)
        self.assertIn("When AI builds itself", sample)


class BootstrapUrlApiTests(unittest.TestCase):
    def _client(self, root: Path, **env_overrides: str):
        from fastapi.testclient import TestClient
        from sqlalchemy.pool import StaticPool

        import book_agent.domain.models  # noqa: F401  (register every table before create_all)
        from book_agent.core.config import get_settings

        engine = build_engine(
            f"sqlite+pysqlite:///{root / 'w.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        env = {
            "BOOK_AGENT_APP_SCOPE": "smoke",
            "BOOK_AGENT_DATABASE_URL": f"sqlite+pysqlite:///{root / 'w.db'}",
            "BOOK_AGENT_AUTH_MODE": "disabled",
            "BOOK_AGENT_RUN_EXECUTOR_ENABLED": "false",
            "BOOK_AGENT_TRANSLATION_BACKEND": "echo",
            "BOOK_AGENT_UPLOAD_ROOT": str(root / "uploads"),
            "BOOK_AGENT_EXPORT_ROOT": str(root / "exports"),
            **env_overrides,
        }
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        from book_agent.app.main import create_app

        app = create_app()
        app.state.session_factory = build_session_factory(engine=engine)
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_a_pasted_link_becomes_a_web_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client = self._client(root)
            real_client = httpx.Client(transport=_transport(_site()))
            self.addCleanup(real_client.close)
            with patch("book_agent.ingestion.web.httpx.Client", return_value=real_client):
                response = client.post("/v1/documents/bootstrap-url", json={"url": "example.com/post"})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["source_type"], "web")
            self.assertEqual(len(list((root / "uploads").rglob("*.html"))), 1)

    def test_unreachable_pages_get_a_reason_and_leave_nothing_behind(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client = self._client(root)
            real_client = httpx.Client(transport=_transport({}))
            self.addCleanup(real_client.close)
            with patch("book_agent.ingestion.web.httpx.Client", return_value=real_client):
                response = client.post("/v1/documents/bootstrap-url", json={"url": "https://example.com/post"})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("HTTP 404", response.json()["detail"])
            self.assertEqual(list((root / "uploads").glob("*")), [])


if __name__ == "__main__":
    unittest.main()
