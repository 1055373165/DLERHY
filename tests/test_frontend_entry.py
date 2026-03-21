# ruff: noqa: E402

import os
import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.app.main import create_app


class FrontendEntryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_root_returns_product_frontend(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        body = response.text
        self.assertIn("Book Agent Pressroom", body)
        self.assertIn("整书译制工作台", body)
        self.assertIn("把一本英文书，从原稿推进到中文交付。", body)
        self.assertIn("拖入书籍文件，或点击选择", body)
        self.assertIn("当前书籍", body)
        self.assertIn("运行总览", body)
        self.assertIn("交付资产", body)
        self.assertIn("复核与阻塞", body)
        self.assertIn("Review Package", body)
        self.assertIn("书库历史", body)
        self.assertIn("搜索标题、作者、路径或 document id", body)
        self.assertIn("重试上次转换", body)
        self.assertIn("刷新并准备重试", body)
        self.assertIn("translation-studio", body)
        self.assertIn("bootstrap-upload", body)
        self.assertIn("/documents/history", body)
        self.assertIn("/runs/", body)
        self.assertIn("/v1/docs", body)
        self.assertIn("/v1/health", body)
