# ruff: noqa: E402

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.app.main import create_app
from book_agent.core.config import get_settings

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>
"""

CONTENT_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Runtime Smoke Book</dc:title>
    <dc:creator>Runtime Test</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="chap1" />
  </spine>
</package>
"""

NAV_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="chapter1.xhtml">Chapter One</a></li>
      </ol>
    </nav>
  </body>
</html>
"""

CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p>Runtime smoke content.</p>
  </body>
</html>
"""


class AppRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self._set_env("BOOK_AGENT_UPLOAD_ROOT", str(Path(self.tempdir.name) / "uploads"))
        self._set_env("BOOK_AGENT_EXPORT_ROOT", str(Path(self.tempdir.name) / "exports"))

    def tearDown(self) -> None:
        get_settings.cache_clear()

    def _set_env(self, key: str, value: str | None) -> None:
        previous = os.environ.get(key)

        def _restore() -> None:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
            get_settings.cache_clear()

        self.addCleanup(_restore)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
        get_settings.cache_clear()

    def _write_epub(self, filename: str = "runtime.epub") -> Path:
        epub_path = Path(self.tempdir.name) / filename
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        return epub_path

    def test_create_app_bootstrap_upload_works_with_sqlite_default(self) -> None:
        db_path = Path(self.tempdir.name) / "runtime.sqlite"
        self._set_env("BOOK_AGENT_DATABASE_URL", f"sqlite+pysqlite:///{db_path}")

        app = create_app()
        client = TestClient(app)
        self.addCleanup(client.close)

        epub_path = self._write_epub()
        with epub_path.open("rb") as handle:
            response = client.post(
                "/v1/documents/bootstrap-upload",
                files={"source_file": (epub_path.name, handle, "application/epub+zip")},
            )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["title"], "Runtime Smoke Book")
        self.assertTrue(db_path.exists())

    def test_create_app_returns_503_when_database_is_unavailable(self) -> None:
        self._set_env(
            "BOOK_AGENT_DATABASE_URL",
            "postgresql+psycopg://postgres:postgres@localhost:9/book_agent",
        )

        app = create_app()
        client = TestClient(app)
        self.addCleanup(client.close)

        epub_path = self._write_epub("unavailable-db.epub")
        with epub_path.open("rb") as handle:
            response = client.post(
                "/v1/documents/bootstrap-upload",
                files={"source_file": (epub_path.name, handle, "application/epub+zip")},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Database unavailable", response.json()["detail"])
