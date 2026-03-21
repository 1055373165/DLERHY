# ruff: noqa: E402

import os
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.app.api.routes.documents import ArchiveInput, _build_export_archive, _resolve_artifact_path
from book_agent.app.main import create_app
from book_agent.core.config import get_settings
from book_agent.domain.enums import DocumentStatus, ExportStatus, ExportType, SourceType
from book_agent.domain.models.document import Document
from book_agent.domain.models.review import Export
from book_agent.infra.db.base import Base
from book_agent.infra.db.sqlite_schema_backfill import ensure_sqlite_schema_compat
from book_agent.infra.db.session import build_engine, build_session_factory

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

    def _seed_legacy_database(
        self,
        db_path: Path,
        *,
        document_id: str,
        title: str,
        updated_at: str,
        merged_export_count: int,
        chapter_export_count: int,
        author: str = "Legacy Runtime",
    ) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = build_engine(database_url=f"sqlite+pysqlite:///{db_path}")
        Base.metadata.create_all(engine)
        session_factory = build_session_factory(engine=engine)
        with session_factory() as session:
            document = Document(
                id=document_id,
                source_type=SourceType.EPUB,
                file_fingerprint=f"fingerprint::{document_id}",
                source_path=f"/legacy/{title}.epub",
                title=title,
                author=author,
                status=DocumentStatus.EXPORTED,
                metadata_json={},
            )
            session.add(document)
            for index in range(merged_export_count):
                session.add(
                    Export(
                        id=str(uuid5(NAMESPACE_URL, f"{document_id}::merged::{index}")),
                        document_id=document_id,
                        export_type=ExportType.MERGED_HTML,
                        input_version_bundle_json={},
                        file_path=str(db_path.parent / f"merged-document-{index}.html"),
                        status=ExportStatus.SUCCEEDED,
                    )
                )
            for index in range(chapter_export_count):
                session.add(
                    Export(
                        id=str(uuid5(NAMESPACE_URL, f"{document_id}::chapter::{index}")),
                        document_id=document_id,
                        export_type=ExportType.BILINGUAL_HTML,
                        input_version_bundle_json={"chapter_id": f"chapter-{index}"},
                        file_path=str(db_path.parent / f"bilingual-{index}.html"),
                        status=ExportStatus.SUCCEEDED,
                    )
                )
            session.commit()

        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE documents SET updated_at = ?, created_at = ? WHERE id IN (?, ?)",
                (updated_at, updated_at, document_id, document_id.replace("-", "")),
            )
            connection.commit()

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

    def test_sqlite_schema_backfill_adds_document_title_columns(self) -> None:
        db_path = Path(self.tempdir.name) / "legacy-runtime.sqlite"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY,
                    title TEXT
                )
                """
            )
            connection.execute("INSERT INTO documents (id, title) VALUES (?, ?)", ("doc-1", "Legacy Title"))
            connection.commit()

        added_column_count = ensure_sqlite_schema_compat(f"sqlite+pysqlite:///{db_path}")

        self.assertEqual(added_column_count, 2)
        with sqlite3.connect(db_path) as connection:
            columns = [str(row[1]) for row in connection.execute("PRAGMA table_info('documents')").fetchall()]
            self.assertIn("title_src", columns)
            self.assertIn("title_tgt", columns)
            row = connection.execute(
                "SELECT title, title_src, title_tgt FROM documents WHERE id = ?",
                ("doc-1",),
            ).fetchone()
        self.assertEqual(row[0], "Legacy Title")
        self.assertEqual(row[1], "Legacy Title")
        self.assertIsNone(row[2])

    def test_sqlite_schema_backfill_corrects_auxiliary_pdf_document_titles_from_source_filename(self) -> None:
        db_path = Path(self.tempdir.name) / "legacy-runtime-title-backfill.sqlite"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    source_type TEXT,
                    source_path TEXT,
                    src_lang TEXT,
                    tgt_lang TEXT,
                    metadata_json TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO documents (id, title, source_type, source_path, src_lang, tgt_lang, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "doc-2",
                    "Dedication",
                    SourceType.PDF_TEXT.value,
                    "/tmp/Agentic Design Patterns A Hands-On Guide to Building Intelligent Systems (Antonio Gulli) (z-library.sk, 1lib.sk, z-lib.sk).pdf",
                    "en",
                    "zh",
                    json.dumps({"pdf_profile": {"recovery_lane": "outlined_book"}}),
                ),
            )
            connection.commit()

        added_column_count = ensure_sqlite_schema_compat(f"sqlite+pysqlite:///{db_path}")

        self.assertEqual(added_column_count, 2)
        with sqlite3.connect(db_path) as connection:
            row = connection.execute(
                "SELECT title, title_src, title_tgt, metadata_json FROM documents WHERE id = ?",
                ("doc-2",),
            ).fetchone()
        self.assertEqual(row[0], "Agentic Design Patterns A Hands-On Guide to Building Intelligent Systems")
        self.assertEqual(row[1], "Agentic Design Patterns A Hands-On Guide to Building Intelligent Systems")
        self.assertIsNone(row[2])
        metadata = json.loads(row[3])
        self.assertEqual(metadata["document_title"]["resolution_source"], "source_filename")

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

    def test_create_app_backfills_best_legacy_history_records_into_empty_sqlite_db(self) -> None:
        db_path = Path(self.tempdir.name) / "runtime.sqlite"
        legacy_root = Path(self.tempdir.name) / "real-book-live"
        self._set_env("BOOK_AGENT_DATABASE_URL", f"sqlite+pysqlite:///{db_path}")

        shared_document_id = "11111111-1111-1111-1111-111111111111"
        self._seed_legacy_database(
            legacy_root / "book-one-v1" / "full.sqlite",
            document_id=shared_document_id,
            title="Legacy Book One",
            updated_at="2026-03-14 10:00:00",
            merged_export_count=0,
            chapter_export_count=0,
        )
        self._seed_legacy_database(
            legacy_root / "book-one-v2" / "full.sqlite",
            document_id=shared_document_id,
            title="Legacy Book One",
            updated_at="2026-03-15 10:00:00",
            merged_export_count=1,
            chapter_export_count=3,
        )
        self._seed_legacy_database(
            legacy_root / "book-two" / "full.sqlite",
            document_id="22222222-2222-2222-2222-222222222222",
            title="Legacy Book Two",
            updated_at="2026-03-16 08:00:00",
            merged_export_count=1,
            chapter_export_count=2,
            author="welcome.html",
        )

        app = create_app()
        client = TestClient(app)
        self.addCleanup(client.close)

        history = client.get("/v1/documents/history", params={"limit": 10, "offset": 0})
        self.assertEqual(history.status_code, 200)
        payload = history.json()
        self.assertEqual(payload["total_count"], 2)

        first_entry = next(entry for entry in payload["entries"] if entry["document_id"] == shared_document_id)
        self.assertTrue(first_entry["merged_export_ready"])
        self.assertEqual(first_entry["chapter_bilingual_export_count"], 3)
        second_entry = next(
            entry for entry in payload["entries"] if entry["document_id"] == "22222222-2222-2222-2222-222222222222"
        )
        self.assertIsNone(second_entry["author"])

    def test_history_backfill_endpoint_imports_legacy_records_without_restart(self) -> None:
        db_path = Path(self.tempdir.name) / "runtime.sqlite"
        legacy_root = Path(self.tempdir.name) / "real-book-live"
        self._set_env("BOOK_AGENT_DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
        previous_cwd = Path.cwd()
        os.chdir(self.tempdir.name)
        self.addCleanup(os.chdir, previous_cwd)

        app = create_app()
        client = TestClient(app)
        self.addCleanup(client.close)

        initial_history = client.get("/v1/documents/history", params={"limit": 10, "offset": 0})
        self.assertEqual(initial_history.status_code, 200)
        self.assertEqual(initial_history.json()["total_count"], 0)

        self._seed_legacy_database(
            legacy_root / "late-import" / "full.sqlite",
            document_id="33333333-3333-3333-3333-333333333333",
            title="Late Legacy Import",
            updated_at="2026-03-16 11:00:00",
            merged_export_count=1,
            chapter_export_count=4,
        )

        imported = client.post("/v1/documents/history/backfill")
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json()["imported_document_count"], 1)

        refreshed_history = client.get("/v1/documents/history", params={"limit": 10, "offset": 0})
        self.assertEqual(refreshed_history.status_code, 200)
        payload = refreshed_history.json()
        self.assertEqual(payload["total_count"], 1)
        self.assertEqual(payload["entries"][0]["document_id"], "33333333-3333-3333-3333-333333333333")

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
            include_related_exports=True,
        )
        self.addCleanup(archive_path.unlink, missing_ok=True)

        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()

        self.assertIn("doc-1-analysis-bundle/merged-document.html", names)
        self.assertNotIn("doc-1-analysis-bundle/merged-document-first-epub.html", names)
