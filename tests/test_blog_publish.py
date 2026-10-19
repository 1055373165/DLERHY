"""Publishing a reading copy to the blog: log in, upload the pictures and the book's cover, save an
article draft with a title, excerpt and SEO made by rule; publishing again updates only the body of
the same article and uploads nothing twice."""

from __future__ import annotations

import json
import os
import re
import unittest
from unittest.mock import patch

import httpx

from book_agent.export.blog_metadata import build_blog_metadata
from book_agent.services.blog_publish import BlogClient, BlogPublishError, BlogTarget
from book_agent.services.book_cover import BookCover
from tests import test_standalone_html as downloads

TARGET_ENV = {
    "BOOK_AGENT_BLOG_API_URL": "http://blog.test",
    "BOOK_AGENT_BLOG_WEB_URL": "http://blog-web.test",
    "BOOK_AGENT_BLOG_EMAIL": "admin@blog.test",
    "BOOK_AGENT_BLOG_PASSWORD": "secret",
}


class FakeBlog:
    """The blog's login, picture and cover upload and article endpoints."""

    def __init__(self, *, fail_upload_at: int | None = None) -> None:
        self.articles: dict[int, dict] = {}
        self.uploads: list[str] = []
        self.covers: list[str] = []
        self.puts: list[dict] = []
        self.logins = 0
        self.fail_upload_at = fail_upload_at

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/login":
            body = json.loads(request.content)
            if body != {"email": "admin@blog.test", "password": "secret"}:
                return httpx.Response(401, json={"success": False, "error": "邮箱或密码错误"})
            self.logins += 1
            return httpx.Response(200, json={"success": True, "data": {"token": "jwt-1"}})
        if request.headers.get("authorization") != "Bearer jwt-1":
            return httpx.Response(401, json={"success": False, "error": "缺少token"})
        if path == "/api/upload/image":
            name = re.search(rb'filename="([^"]+)"', request.content).group(1).decode()
            if self.fail_upload_at is not None and len(self.uploads) + 1 == self.fail_upload_at:
                return httpx.Response(500, json={"success": False, "error": "磁盘满了"})
            self.uploads.append(name)
            return httpx.Response(200, json={"success": True, "data": {"url": f"http://blog.test/api/upload/image/{len(self.uploads)}-{name}"}})
        if path == "/api/cover/upload":
            assert "图书封面".encode() in request.content
            self.covers.append(re.search(rb'filename="([^"]+)"', request.content).group(1).decode())
            return httpx.Response(200, json={"success": True, "data": {"url": f"http://blog.test/api/upload/cover/图书封面/cover_{len(self.covers)}.jpg"}})
        if path == "/api/articles" and request.method == "POST":
            article_id = len(self.articles) + 1
            self.articles[article_id] = json.loads(request.content)
            return httpx.Response(201, json={"success": True, "data": {"id": article_id}})
        match = re.fullmatch(r"/api/articles/(\d+)", path)
        if match and request.method == "GET":
            article_id = int(match.group(1))
            if article_id not in self.articles:
                return httpx.Response(404, json={"success": False, "error": "文章不存在"})
            return httpx.Response(200, json={"success": True, "data": {"cover_image": "", **self.articles[article_id], "id": article_id}})
        if match and request.method == "PUT":
            article_id = int(match.group(1))
            if article_id not in self.articles:
                return httpx.Response(404, json={"success": False, "error": "文章不存在"})
            self.puts.append(json.loads(request.content))
            self.articles[article_id].update(json.loads(request.content))
            return httpx.Response(200, json={"success": True, "data": {"id": article_id}})
        return httpx.Response(404, json={"error": f"unhandled {request.method} {path}"})


class BlogMetadataTest(unittest.TestCase):
    def test_title_excerpt_and_seo_by_rule(self) -> None:
        markdown = (
            "# 从零开始构建AI智能体\n\n## 目录\n\n- 前言\n\n## 前言\n\n"
            "我们刚开始构建智能体时，那些框架简直是天赐之物。它们让我们用寥寥几行代码就能搭起一个能跑的智能体。\n\n"
            "## 第 1 章 什么是 AI 智能体？\n\n智能体会调用工具。智能体会记住对话。\n"
        )
        meta = build_blog_metadata(
            markdown=markdown,
            title="从零开始构建AI智能体",
            source_title="Build an AI Agent (From Scratch)",
            authors="Jungjun Hur; Younghee Song",
            glossary_terms=["智能体", "工具", "没出现的词"],
        )
        # Titled by the book's original name; the Chinese title leads the excerpt and SEO.
        self.assertEqual(meta.title, "Build an AI Agent (From Scratch)")
        self.assertTrue(meta.excerpt.startswith("《从零开始构建AI智能体》（Build an AI Agent (From Scratch)）中文版，Jungjun Hur、Younghee Song 著。我们刚开始构建智能体时"))
        self.assertLessEqual(len(meta.excerpt), 200)
        self.assertEqual(meta.meta_title, "《从零开始构建AI智能体》中文版 | Build an AI Agent (From Scratch)")
        self.assertLessEqual(len(meta.meta_description), 150)
        keywords = meta.meta_keywords.split(",")
        self.assertEqual(keywords[:4], ["从零开始构建AI智能体", "Build an AI Agent (From Scratch)", "Jungjun Hur", "Younghee Song"])
        self.assertIn("智能体", keywords)
        self.assertNotIn("没出现的词", keywords)
        bilingual = build_blog_metadata(markdown=markdown, title="书", source_title=None, authors=None, bilingual=True)
        # Without an original title, the Chinese one.
        self.assertEqual(bilingual.title, "书（中英对照）")
        self.assertIn("中英对照版", bilingual.meta_title)

    def test_without_a_preface_the_chapters_say_what_the_book_is(self) -> None:
        markdown = "# 书\n\n## 第 1 章 引言\n\n正文。\n\n## 第 2 章 记忆\n\n正文。\n"
        meta = build_blog_metadata(markdown=markdown, title="书", source_title="Book", authors="A")
        self.assertIn("全书共 2 章：引言、记忆。", meta.excerpt)


class PublishProgressTest(unittest.TestCase):
    def test_progress_counts_each_picture(self) -> None:
        import tempfile
        from pathlib import Path

        from book_agent.services.blog_publish import publish_markdown

        blog = FakeBlog()
        transport = httpx.MockTransport(blog.handle)
        target = BlogTarget(api_url="http://blog.test", email="admin@blog.test", password="secret")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "assets").mkdir()
            for name in ("a.png", "b.png", "c.png"):
                (Path(tmp) / "assets" / name).write_bytes(name.encode())
            markdown = "# 书\n\n![](assets/a.png)\n\n![](assets/b.png)\n\n![](assets/a.png)\n\n![](assets/c.png)\n"
            seen: list[tuple[str, int, int]] = []
            meta = build_blog_metadata(markdown=markdown, title="书", source_title=None, authors=None)
            result, _state = publish_markdown(
                target=target, state={}, markdown=markdown, base_dir=Path(tmp), metadata=meta, edition="zh",
                client_factory=lambda t: BlogClient(t, transport=transport), progress=lambda *args: seen.append(args),
            )
        self.assertEqual(
            seen,
            [("login", 0, 3), ("images", 0, 3), ("images", 1, 3), ("images", 2, 3), ("images", 3, 3), ("saving", 3, 3)],
        )
        self.assertEqual(result.images_uploaded, 3)


class WebPagePublishTest(unittest.TestCase):
    """A web article's Markdown links the site's pictures; the article gets the blog's own copies,
    and never the model usage the reading copies show under their title."""

    def test_web_pictures_are_uploaded_once_and_the_usage_lines_left_out(self) -> None:
        import tempfile
        from pathlib import Path

        from book_agent.services.blog_publish import RemotePicture, publish_markdown

        target = BlogTarget(api_url="http://blog.test", email="admin@blog.test", password="secret")
        markdown = (
            "# 文章\n\n- 翻译用量：模型调用 128 次\n- Token 519,628（输入 486,800 · 输出 32,828）\n- 花费 US$ 0.04\n\n"
            "正文。\n\n![](https://cdn.example.com/a.png)\n\n![](https://cdn.example.com/gone.png)\n"
        )
        fetched: list[str] = []

        def fetch(url: str):
            fetched.append(url)
            return RemotePicture("a.png", b"png-bytes", "image/png") if url.endswith("/a.png") else None

        meta = build_blog_metadata(markdown=markdown, title="文章", source_title=None, authors=None)
        blog = FakeBlog()
        transport = httpx.MockTransport(blog.handle)
        with tempfile.TemporaryDirectory() as tmp:
            def publish(state: dict):
                return publish_markdown(
                    target=target, state=state, markdown=markdown, base_dir=Path(tmp), metadata=meta, edition="zh",
                    client_factory=lambda t: BlogClient(t, transport=transport), fetch_picture=fetch,
                )

            result, state = publish({})
            content = blog.articles[result.article_id]["content"]
            again, _state = publish(state)
        self.assertEqual(blog.uploads, ["a.png"])
        self.assertIn("![](http://blog.test/api/upload/image/1-a.png)", content)
        # One it could not download stays linked where it was.
        self.assertIn("![](https://cdn.example.com/gone.png)", content)
        self.assertEqual(result.images_skipped, ["https://cdn.example.com/gone.png"])
        self.assertNotIn("翻译用量", content)
        self.assertNotIn("US$", content)
        self.assertTrue(content.startswith("# 文章\n\n正文。"))
        # Publishing again neither downloads nor uploads the picture it already has.
        self.assertEqual(fetched.count("https://cdn.example.com/a.png"), 1)
        self.assertEqual((again.images_uploaded, again.images_reused, again.action), (0, 1, "unchanged"))

    def test_a_picture_is_downloaded_only_from_a_public_address(self) -> None:
        from book_agent.services.blog_publish import download_picture

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith(".png"):
                return httpx.Response(200, headers={"content-type": "image/png"}, content=b"png")
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html>")

        transport = httpx.MockTransport(handler)
        with patch("book_agent.services.url_guard.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            picture = download_picture("https://cdn.example.com/x/chart.png?w=1", transport=transport)
            page = download_picture("https://cdn.example.com/x/page", transport=transport)
        self.assertEqual((picture.name, picture.data, picture.content_type), ("chart.png", b"png", "image/png"))
        self.assertIsNone(page)
        self.assertIsNone(download_picture("http://127.0.0.1/a.png", transport=transport))


class PublishStateTest(unittest.TestCase):
    def test_pictures_uploaded_before_a_failure_are_not_uploaded_again(self) -> None:
        import tempfile
        from pathlib import Path

        from book_agent.services.blog_publish import publish_markdown

        target = BlogTarget(api_url="http://blog.test", email="admin@blog.test", password="secret")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "assets").mkdir()
            for name in ("a.png", "b.png", "c.png"):
                (Path(tmp) / "assets" / name).write_bytes(name.encode())
            markdown = "# 书\n\n![](assets/a.png)\n\n![](assets/b.png)\n\n![](assets/c.png)\n"
            meta = build_blog_metadata(markdown=markdown, title="书", source_title=None, authors=None)

            def publish(blog: FakeBlog, state: dict):
                transport = httpx.MockTransport(blog.handle)
                return publish_markdown(
                    target=target, state=state, markdown=markdown, base_dir=Path(tmp), metadata=meta, edition="zh",
                    client_factory=lambda t: BlogClient(t, transport=transport),
                )

            failing = FakeBlog(fail_upload_at=3)
            with self.assertRaises(BlogPublishError) as caught:
                publish(failing, {})
            self.assertEqual(len(caught.exception.state["http://blog.test"]["images"]), 2)
            retry = FakeBlog()
            result, _state = publish(retry, caught.exception.state)
        self.assertEqual((result.images_uploaded, result.images_reused), (1, 2))
        self.assertEqual(retry.uploads, ["c.png"])


    def test_a_blog_that_does_not_show_the_article_gets_an_update_not_a_second_article(self) -> None:
        import tempfile
        from pathlib import Path

        from book_agent.services.blog_publish import publish_markdown

        class OlderBlog(FakeBlog):
            def handle(self, request: httpx.Request) -> httpx.Response:
                if request.method == "GET" and request.url.path.startswith("/api/articles/"):
                    return httpx.Response(404, json={"success": False, "error": "文章不存在"})
                return super().handle(request)

        blog = OlderBlog()
        transport = httpx.MockTransport(blog.handle)
        target = BlogTarget(api_url="http://blog.test", email="admin@blog.test", password="secret")
        meta = build_blog_metadata(markdown="# 书\n\n正文。\n", title="书", source_title=None, authors=None)
        state: dict = {}
        actions = []
        for _ in range(2):
            result, state = publish_markdown(
                target=target, state=state, markdown="# 书\n\n正文。\n", base_dir=Path(tempfile.gettempdir()), metadata=meta,
                edition="zh", cover=lambda: BookCover(b"c", "cover.jpg", "image/jpeg"),
                client_factory=lambda t: BlogClient(t, transport=transport),
            )
            actions.append((result.action, result.cover))
        self.assertEqual(actions, [("created", "uploaded"), ("updated", "kept")])
        self.assertEqual((len(blog.articles), len(blog.covers), list(blog.puts[-1])), (1, 1, ["content"]))


class BlogPublishApiTest(unittest.TestCase):
    """The API on the download tests' exported book (its Markdown references assets/pdf-images/fig.png)."""

    def setUp(self) -> None:
        downloads.DownloadTests.setUp(self)
        self.blog = FakeBlog()
        env = patch.dict(os.environ, TARGET_ENV)
        env.start()
        self.addCleanup(env.stop)
        from book_agent.core.config import get_settings

        get_settings.cache_clear()
        transport = httpx.MockTransport(self.blog.handle)
        factory = patch("book_agent.app.api.routes.blog.BlogClient", lambda target: BlogClient(target, transport=transport))
        factory.start()
        self.addCleanup(factory.stop)
        self.cover_calls = 0

        def cover(source_path: str | None) -> BookCover:
            self.cover_calls += 1
            return BookCover(b"jpeg-bytes", "cover.jpg", "image/jpeg")

        cover_patch = patch("book_agent.app.api.routes.blog.book_cover", cover)
        cover_patch.start()
        self.addCleanup(cover_patch.stop)

    def _start(self, edition: str = "zh") -> httpx.Response:
        return self.client.post(f"/v1/documents/{self.document_id}/blog-publish", json={"edition": edition})

    def _publish(self, edition: str = "zh") -> dict:
        """Start a publish and follow its job to the end; the finished job."""
        import time

        started = self._start(edition)
        self.assertEqual(started.status_code, 202, started.text)
        job = started.json()
        self.stages = [(job["stage"], job["done"], job["total"])]
        deadline = time.time() + 10
        while job["status"] == "running" and time.time() < deadline:
            time.sleep(0.02)
            job = self.client.get(f"/v1/blog/publish-jobs/{job['job_id']}").json()
            self.stages.append((job["stage"], job["done"], job["total"]))
        return job

    def test_the_book_becomes_a_draft_with_its_pictures_uploaded(self) -> None:
        self.assertEqual(self.client.get("/v1/blog/target").json(), {"configured": True, "api_url": "http://blog.test", "web_url": "http://blog-web.test"})
        job = self._publish()
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual((job["stage"], job["done"], job["total"]), ("done", 1, 1))
        body = job["result"]
        self.assertEqual(body["action"], "created")
        self.assertEqual(body["edit_url"], f"http://blog-web.test/admin/articles/{body['article_id']}/edit")
        self.assertEqual(body["images_uploaded"], 1)
        article = self.blog.articles[body["article_id"]]
        self.assertFalse(article["is_published"])
        self.assertIn("](http://blog.test/api/upload/image/1-fig.png)", article["content"])
        self.assertNotIn("](assets/", article["content"])
        for field in ("title", "excerpt", "meta_title", "meta_description", "meta_keywords"):
            self.assertTrue(article[field], field)

        # The book's cover went to the blog's cover library and is the article's cover.
        self.assertEqual(body["cover"], "uploaded")
        self.assertEqual(self.blog.covers, ["cover.jpg"])
        self.assertEqual(article["cover_image"], "http://blog.test/api/upload/cover/图书封面/cover_1.jpg")

        # Again with the same body: nothing is sent or uploaded, the cover is kept.
        again = self._publish()["result"]
        self.assertEqual((again["action"], again["article_id"]), ("unchanged", body["article_id"]))
        self.assertEqual((again["images_uploaded"], again["images_reused"], again["cover"]), (0, 1, "kept"))
        self.assertEqual((len(self.blog.uploads), len(self.blog.covers), self.blog.puts), (1, 1, []))
        self.assertEqual(len(self.blog.articles), 1)

        # A changed body is the only thing sent: the title, SEO and a cover chosen on the blog stay.
        self.blog.articles[body["article_id"]].update(content="旧正文", title="博客上改过的标题", cover_image="http://blog.test/mine.jpg")
        changed = self._publish()["result"]
        self.assertEqual((changed["action"], changed["cover"]), ("updated", "kept"))
        self.assertEqual(list(self.blog.puts[-1]), ["content"])
        self.assertEqual(self.blog.articles[body["article_id"]]["title"], "博客上改过的标题")
        self.assertEqual(self.blog.articles[body["article_id"]]["cover_image"], "http://blog.test/mine.jpg")

        # An article without a cover (published before covers, or removed on the blog) gets the one uploaded.
        self.blog.articles[body["article_id"]]["cover_image"] = ""
        covered = self._publish()["result"]
        self.assertEqual((covered["action"], covered["cover"]), ("updated", "reused"))
        self.assertEqual(self.blog.puts[-1], {"cover_image": "http://blog.test/api/upload/cover/图书封面/cover_1.jpg"})
        self.assertEqual(len(self.blog.covers), 1)

        # The bilingual edition is its own article; one deleted on the blog is created anew.
        bilingual = self._publish("bilingual")["result"]
        self.assertNotEqual(bilingual["article_id"], body["article_id"])
        self.assertEqual((bilingual["cover"], len(self.blog.covers)), ("reused", 1))
        self.assertTrue(self.blog.articles[bilingual["article_id"]]["title"].endswith("（中英对照）"))
        del self.blog.articles[body["article_id"]]
        self.assertEqual(self._publish()["result"]["action"], "created")

    def test_a_wrong_password_or_missing_configuration_is_reported(self) -> None:
        from book_agent.core.config import get_settings

        with patch.dict(os.environ, {"BOOK_AGENT_BLOG_PASSWORD": "wrong"}):
            get_settings.cache_clear()
            job = self._publish()
        self.assertEqual(job["status"], "failed")
        self.assertIn("博客登录失败", job["error"])
        with patch.dict(os.environ, {"BOOK_AGENT_BLOG_API_URL": ""}):
            get_settings.cache_clear()
            self.assertEqual(self._start().status_code, 422)
            self.assertEqual(self.client.get("/v1/blog/target").json()["configured"], False)
        get_settings.cache_clear()

    def test_a_missing_markdown_export_answers_404_so_the_app_exports_first(self) -> None:
        (self.export_root / self.document_id / "merged-document.bilingual.md").unlink()
        self.assertEqual(self._start("bilingual").status_code, 404)
        self.assertEqual(self.client.get("/v1/blog/publish-jobs/unknown").status_code, 404)


if __name__ == "__main__":
    unittest.main()
