"""The API process serves the built frontend when a dist directory is configured."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from book_agent.app.main import create_app
from book_agent.core.config import get_settings


class FrontendHostingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.dist = Path(self.tempdir.name) / "dist"
        (self.dist / "assets").mkdir(parents=True)
        (self.dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
        (self.dist / "assets" / "index-abc123.js").write_text("console.log('app')", encoding="utf-8")
        (self.dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
        (Path(self.tempdir.name) / "secret.txt").write_text("outside", encoding="utf-8")

    def _client(self, dist: Path | None) -> TestClient:
        env = {"BOOK_AGENT_FRONTEND_DIST_DIR": str(dist)} if dist is not None else {}
        with patch.dict(os.environ, env):
            if dist is None:
                os.environ.pop("BOOK_AGENT_FRONTEND_DIST_DIR", None)
            get_settings.cache_clear()
            try:
                app = create_app()
            finally:
                get_settings.cache_clear()
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_spa_routes_assets_and_runtime_config(self) -> None:
        client = self._client(self.dist)

        for path in ("/", "/runs", "/issues/some-id"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("id=root", response.text)
            self.assertEqual(response.headers["cache-control"], "no-cache")

        asset = client.get("/assets/index-abc123.js")
        self.assertEqual(asset.status_code, 200)
        self.assertIn("immutable", asset.headers["cache-control"])
        self.assertEqual(client.get("/favicon.svg").text, "<svg/>")

        config = client.get("/runtime-config.js")
        self.assertIn('"apiBaseUrl": "/v1"', config.text)
        self.assertIn("javascript", config.headers["content-type"])

        # API paths never fall through to the SPA (health is 503 here: no database in this test).
        self.assertNotIn("id=root", client.get("/v1/health").text)
        self.assertEqual(client.get("/v1/no-such-route").status_code, 404)
        self.assertNotIn("outside", client.get("/../secret.txt").text)

    def test_without_a_dist_directory_the_status_homepage_stays(self) -> None:
        client = self._client(None)
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("id=root", response.text)
        self.assertEqual(client.get("/runs").status_code, 404)


if __name__ == "__main__":
    unittest.main()
