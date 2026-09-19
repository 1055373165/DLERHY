"""The PDF tab says "unavailable" unless both Playwright and its Chromium are installed."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from book_agent.app.api.routes.documents import _pdf_renderer_installed


class _FakePlaywright:
    def __init__(self, executable: str) -> None:
        self.chromium = SimpleNamespace(executable_path=executable)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


class PdfRendererCheckTests(unittest.TestCase):
    def test_missing_package_is_unavailable(self) -> None:
        with patch("importlib.util.find_spec", return_value=None):
            self.assertFalse(_pdf_renderer_installed())

    def test_package_without_browser_is_unavailable_and_with_it_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            browser = Path(tmpdir) / "chrome"
            with patch("importlib.util.find_spec", return_value=object()), patch(
                "playwright.sync_api.sync_playwright", lambda: _FakePlaywright(str(browser))
            ):
                self.assertFalse(_pdf_renderer_installed())
                browser.write_bytes(b"")
                self.assertTrue(_pdf_renderer_installed())


class MetaCapabilitiesTests(unittest.TestCase):
    def test_meta_reports_whether_pdf_can_be_rendered(self) -> None:
        from fastapi.testclient import TestClient

        from book_agent.app import capabilities
        from book_agent.app.main import create_app

        capabilities.reset_cache()
        self.addCleanup(capabilities.reset_cache)
        client = TestClient(create_app())
        self.addCleanup(client.close)
        for installed in (False, True):
            capabilities.reset_cache()
            with patch.object(capabilities, "pdf_renderer_installed", return_value=installed):
                response = client.get("/v1/meta")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["capabilities"], {"pdf_renderer": installed})


if __name__ == "__main__":
    unittest.main()
