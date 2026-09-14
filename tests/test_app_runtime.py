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

from book_agent.app.api.routes.documents import ArchiveInput, _build_export_archive, _resolve_artifact_path
from book_agent.app.main import create_app
from book_agent.core.config import AppScopeViolation, get_settings
from book_agent.domain.enums import ExportType

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

    def test_create_app_refuses_sqlite_database_url(self) -> None:
        self._set_env("BOOK_AGENT_DATABASE_URL", f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'runtime.sqlite'}")

        with self.assertRaises(AppScopeViolation):
            create_app()

    def test_smoke_scope_still_requires_sqlite(self) -> None:
        self._set_env("BOOK_AGENT_APP_SCOPE", "smoke")
        self._set_env("BOOK_AGENT_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:9/book_agent")

        with self.assertRaises(AppScopeViolation):
            create_app()

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

    def test_health_returns_503_when_database_is_unavailable(self) -> None:
        self._set_env(
            "BOOK_AGENT_DATABASE_URL",
            "postgresql+psycopg://postgres:postgres@localhost:9/book_agent",
        )

        app = create_app()
        client = TestClient(app)
        self.addCleanup(client.close)

        response = client.get("/v1/health")

        self.assertEqual(response.status_code, 503)
        self.assertIn("Database unavailable", response.json()["detail"])

    def test_resolve_artifact_path_falls_back_to_legacy_merged_document_name(self) -> None:
        artifact_root = Path(self.tempdir.name) / "artifacts"
        legacy_dir = artifact_root / "real-book-live" / "legacy-book" / "exports" / "doc-1"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        actual_path = legacy_dir / "merged-document-first-epub.html"
        actual_path.write_text("<html>legacy</html>", encoding="utf-8")

        resolved = _resolve_artifact_path(
            legacy_dir / "merged-document.html",
            roots=((artifact_root / "exports").resolve(), artifact_root.resolve()),
        )

        self.assertEqual(resolved, actual_path.resolve())

    def test_build_export_archive_uses_canonical_name_for_legacy_merged_html(self) -> None:
        export_dir = Path(self.tempdir.name) / "exports" / "doc-1"
        export_dir.mkdir(parents=True, exist_ok=True)
        actual_path = export_dir / "merged-document-first-epub.html"
        actual_path.write_text("<html>legacy merged</html>", encoding="utf-8")

        archive_path = _build_export_archive(
            "doc-1",
            ExportType.MERGED_HTML,
            [
                ArchiveInput(path=actual_path, archive_name="merged-document.html"),
            ],
        )
        self.addCleanup(archive_path.unlink, missing_ok=True)

        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()

        self.assertIn("doc-1-merged_html/merged-document.html", names)
        self.assertNotIn("doc-1-merged_html/merged-document-first-epub.html", names)
