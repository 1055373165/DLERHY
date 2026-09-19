"""A scanned PDF on a deployment without OCR gets a clear answer, not a 500."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

import book_agent.domain.models  # noqa: F401  (register every table before create_all)
from book_agent.core.config import get_settings
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory

ROOT = Path(__file__).resolve().parents[1]


def _scanned_pdf(path: Path) -> Path:
    import fitz  # PyMuPDF

    document = fitz.open()
    for _ in range(3):
        page = document.new_page(width=595, height=842)
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 600), False)
        pixmap.set_rect(pixmap.irect, (240, 240, 240))
        page.insert_image(page.rect, pixmap=pixmap)
    document.save(path)
    document.close()
    return path


class OcrUnavailableUploadTests(unittest.TestCase):
    def test_scanned_pdf_without_ocr_runtime_is_refused_with_a_reason(self) -> None:
        tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        engine = build_engine(
            f"sqlite+pysqlite:///{root / 'o.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        env = {
            "BOOK_AGENT_APP_SCOPE": "smoke",
            "BOOK_AGENT_DATABASE_URL": f"sqlite+pysqlite:///{root / 'o.db'}",
            "BOOK_AGENT_AUTH_MODE": "disabled",
            "BOOK_AGENT_RUN_EXECUTOR_ENABLED": "false",
            "BOOK_AGENT_TRANSLATION_BACKEND": "echo",
            "BOOK_AGENT_UPLOAD_ROOT": str(root / "uploads"),
            "BOOK_AGENT_EXPORT_ROOT": str(root / "exports"),
        }
        with patch.dict(os.environ, env):
            get_settings.cache_clear()
            self.addCleanup(get_settings.cache_clear)
            from book_agent.app.main import create_app

            app = create_app()
            app.state.session_factory = build_session_factory(engine=engine)
            client = TestClient(app)
            self.addCleanup(client.close)
            pdf = _scanned_pdf(root / "scan.pdf")
            with patch("book_agent.ingestion.pdf.ocr.shutil.which", return_value=None), pdf.open("rb") as handle:
                response = client.post(
                    "/v1/documents/bootstrap-upload", files={"source_file": (pdf.name, handle, "application/pdf")}
                )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("扫描版 PDF", response.json()["detail"])
        self.assertEqual(list((root / "uploads").rglob("*.pdf")), [])


if __name__ == "__main__":
    unittest.main()
