"""Publishing a reading copy to the blog: log in, upload the pictures, save an article draft with a
title, excerpt and SEO made by rule; publishing again updates the same article."""

from __future__ import annotations

import json
import os
import re
import unittest
from unittest.mock import patch

import httpx

from book_agent.export.blog_metadata import build_blog_metadata
from book_agent.services.blog_publish import BlogClient, BlogTarget
from tests import test_standalone_html as downloads

TARGET_ENV = {
    "BOOK_AGENT_BLOG_API_URL": "http://blog.test",
    "BOOK_AGENT_BLOG_WEB_URL": "http://blog-web.test",
    "BOOK_AGENT_BLOG_EMAIL": "admin@blog.test",
    "BOOK_AGENT_BLOG_PASSWORD": "secret",
}


class FakeBlog:
    """The blog's login, picture upload and article endpoints."""

    def __init__(self) -> None:
        self.articles: dict[int, dict] = {}
        self.uploads: list[str] = []
        self.logins = 0

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
            self.uploads.append(name)
            return httpx.Response(200, json={"success": True, "data": {"url": f"http://blog.test/api/upload/image/{len(self.uploads)}-{name}"}})
        if path == "/api/articles" and request.method == "POST":
            article_id = len(self.articles) + 1
            self.articles[article_id] = json.loads(request.content)
            return httpx.Response(201, json={"success": True, "data": {"id": article_id}})
        match = re.fullmatch(r"/api/articles/(\d+)", path)
        if match and request.method == "PUT":
            article_id = int(match.group(1))
            if article_id not in self.articles:
                return httpx.Response(404, json={"success": False, "error": "文章不存在"})
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
        self.assertEqual(meta.title, "从零开始构建AI智能体")
        self.assertTrue(meta.excerpt.startswith("《从零开始构建AI智能体》（Build an AI Agent (From Scratch)）中文版，Jungjun Hur、Younghee Song 著。我们刚开始构建智能体时"))
        self.assertLessEqual(len(meta.excerpt), 200)
        self.assertEqual(meta.meta_title, "《从零开始构建AI智能体》中文版 | Build an AI Agent (From Scratch)")
        self.assertLessEqual(len(meta.meta_description), 150)
        keywords = meta.meta_keywords.split(",")
        self.assertEqual(keywords[:4], ["从零开始构建AI智能体", "Build an AI Agent (From Scratch)", "Jungjun Hur", "Younghee Song"])
        self.assertIn("智能体", keywords)
        self.assertNotIn("没出现的词", keywords)
        bilingual = build_blog_metadata(markdown=markdown, title="书", source_title=None, authors=None, bilingual=True)
        self.assertEqual(bilingual.title, "书（中英对照）")
        self.assertIn("中英对照版", bilingual.meta_title)

    def test_without_a_preface_the_chapters_say_what_the_book_is(self) -> None:
        markdown = "# 书\n\n## 第 1 章 引言\n\n正文。\n\n## 第 2 章 记忆\n\n正文。\n"
        meta = build_blog_metadata(markdown=markdown, title="书", source_title="Book", authors="A")
        self.assertIn("全书共 2 章：引言、记忆。", meta.excerpt)


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

    def _publish(self, edition: str = "zh") -> httpx.Response:
        return self.client.post(f"/v1/documents/{self.document_id}/blog-publish", json={"edition": edition})

    def test_the_book_becomes_a_draft_with_its_pictures_uploaded(self) -> None:
        self.assertEqual(self.client.get("/v1/blog/target").json(), {"configured": True, "api_url": "http://blog.test", "web_url": "http://blog-web.test"})
        response = self._publish()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["action"], "created")
        self.assertEqual(body["edit_url"], f"http://blog-web.test/admin/articles/{body['article_id']}/edit")
        self.assertEqual(body["images_uploaded"], 1)
        article = self.blog.articles[body["article_id"]]
        self.assertFalse(article["is_published"])
        self.assertIn("](http://blog.test/api/upload/image/1-fig.png)", article["content"])
        self.assertNotIn("](assets/", article["content"])
        for field in ("title", "excerpt", "meta_title", "meta_description", "meta_keywords"):
            self.assertTrue(article[field], field)

        # Again: the same article is updated, its picture not uploaded twice, its state left alone.
        again = self._publish().json()
        self.assertEqual((again["action"], again["article_id"]), ("updated", body["article_id"]))
        self.assertEqual((again["images_uploaded"], again["images_reused"]), (0, 1))
        self.assertEqual(len(self.blog.uploads), 1)
        self.assertEqual(len(self.blog.articles), 1)

        # The bilingual edition is its own article; one deleted on the blog is created anew.
        bilingual = self._publish("bilingual").json()
        self.assertNotEqual(bilingual["article_id"], body["article_id"])
        self.assertTrue(self.blog.articles[bilingual["article_id"]]["title"].endswith("（中英对照）"))
        del self.blog.articles[body["article_id"]]
        self.assertEqual(self._publish().json()["action"], "created")

    def test_a_wrong_password_or_missing_configuration_is_reported(self) -> None:
        from book_agent.core.config import get_settings

        with patch.dict(os.environ, {"BOOK_AGENT_BLOG_PASSWORD": "wrong"}):
            get_settings.cache_clear()
            response = self._publish()
        self.assertEqual(response.status_code, 502)
        self.assertIn("博客登录失败", response.json()["detail"])
        with patch.dict(os.environ, {"BOOK_AGENT_BLOG_API_URL": ""}):
            get_settings.cache_clear()
            self.assertEqual(self._publish().status_code, 422)
            self.assertEqual(self.client.get("/v1/blog/target").json()["configured"], False)
        get_settings.cache_clear()

    def test_a_missing_markdown_export_answers_404_so_the_app_exports_first(self) -> None:
        (self.export_root / self.document_id / "merged-document.bilingual.md").unlink()
        self.assertEqual(self._publish("bilingual").status_code, 404)


if __name__ == "__main__":
    unittest.main()
