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
        self.assertIn("publishing-grade cockpit", body)
        self.assertIn("Document Workspace", body)
        self.assertIn("Run Console", body)
        self.assertIn("Chapter Worklist Board", body)
        self.assertIn("Chapter detail drawer", body)
        self.assertIn("Owner workload lane", body)
        self.assertIn("Owner alerts and routing cues", body)
        self.assertIn("Auto-refresh document: on", body)
        self.assertIn("Auto-refresh run: on", body)
        self.assertIn("Assign owner", body)
        self.assertIn("Execute action", body)
        self.assertIn("Document Sync", body)
        self.assertIn("Worklist Sync", body)
        self.assertIn("Apply filters", body)
        self.assertIn("Assigned owner", body)
        self.assertIn("Click an owner card or type owner name", body)
        self.assertIn("Owner drill-down and balancing hints", body)
        self.assertIn("Clear owner focus", body)
        self.assertIn("Show breached queue", body)
        self.assertIn("Focus unassigned immediate", body)
        self.assertIn("Open file picker", body)
        self.assertIn("Choose an EPUB or PDF", body)
        self.assertIn("Export & save", body)
        self.assertIn("Analysis history", body)
        self.assertIn("History detail", body)
        self.assertIn("Search title, author, path, or document id", body)
        self.assertIn("Apply history filters", body)
        self.assertIn("Import legacy history", body)
        self.assertIn("All source types", body)
        self.assertIn("Merged export", body)
        self.assertIn("Retry run", body)
        self.assertIn("Download chapter", body)
        self.assertIn("Create full run", body)
        self.assertIn("/v1/documents/bootstrap-upload", body)
        self.assertIn("/v1/docs", body)
        self.assertIn("/v1/health", body)
        self.assertIn("Operational Workflow", body)
