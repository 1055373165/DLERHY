"""Deleting a book removes its files too: the upload, exports, images and unshared blobs."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from book_agent.core.config import get_settings
from book_agent.domain.enums import ExportStatus, ExportType
from book_agent.domain.models import Document
from book_agent.domain.models.review import Export
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.storage.blobs import blob_target
from tests.export_golden_scenario import write_epub

ROOT = Path(__file__).resolve().parents[1]


class DocumentDeleteFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(tempdir.cleanup)
        self.root = Path(tempdir.name)
        engine = build_engine(
            f"sqlite+pysqlite:///{self.root / 'd.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.session_factory = build_session_factory(engine=engine)
        self.export_root = self.root / "artifacts" / "exports"
        self.upload_root = self.root / "uploads"
        patcher = patch.dict(
            os.environ,
            {
                "BOOK_AGENT_AUTH_MODE": "disabled",
                "BOOK_AGENT_RUN_EXECUTOR_ENABLED": "false",
                "BOOK_AGENT_TRANSLATION_BACKEND": "echo",
                "BOOK_AGENT_EXPORT_ROOT": str(self.export_root),
                "BOOK_AGENT_UPLOAD_ROOT": str(self.upload_root),
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        from book_agent.app.main import create_app

        app = create_app()
        app.state.session_factory = self.session_factory
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def _upload(self) -> str:
        epub = write_epub(self.root)
        with epub.open("rb") as handle:
            response = self.client.post(
                "/v1/documents/bootstrap-upload", files={"source_file": (epub.name, handle, "application/epub+zip")}
            )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["document_id"]

    def _export(self, document_id: str, sha: str) -> Path:
        path = self.export_root / document_id / "merged-document.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html>译文</html>", encoding="utf-8")
        blob = blob_target(self.export_root.resolve().parent / "blobs", sha)
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(b"x")
        with self.session_factory() as session:
            session.add(
                Export(
                    document_id=document_id,
                    export_type=ExportType.MERGED_HTML,
                    input_version_bundle_json={},
                    file_path=str(path),
                    status=ExportStatus.SUCCEEDED,
                    content_sha256=sha,
                )
            )
            session.commit()
        return blob

    def test_delete_removes_the_books_files_and_keeps_shared_blobs(self) -> None:
        document_id = self._upload()
        with self.session_factory() as session:
            source = Path(session.get(Document, document_id).source_path)
        self.assertTrue(source.is_file())
        images = self.export_root.parent / "document-images" / document_id
        images.mkdir(parents=True)
        (images / "figure.png").write_bytes(b"png")
        own_blob = self._export(document_id, "a" * 64)
        shared_blob = self._export(document_id, "b" * 64)

        # Another book holds an export with the same bytes.
        other = Document(**{column: getattr(self._document_row(document_id), column) for column in ("source_type", "status")})
        other.file_fingerprint = "other-book"
        other.source_path = "/srv/elsewhere/other.epub"
        with self.session_factory() as session:
            session.add(other)
            session.flush()
            other_id = other.id
            session.add(
                Export(
                    document_id=other_id,
                    export_type=ExportType.MERGED_HTML,
                    input_version_bundle_json={},
                    file_path=str(self.export_root / str(other_id) / "merged-document.html"),
                    status=ExportStatus.SUCCEEDED,
                    content_sha256="b" * 64,
                )
            )
            session.commit()

        self.assertEqual(self.client.delete(f"/v1/documents/{document_id}").status_code, 204)

        self.assertFalse((self.export_root / document_id).exists())
        self.assertFalse(images.exists())
        self.assertFalse(source.exists())
        self.assertFalse(source.parent.exists())
        self.assertTrue(self.upload_root.is_dir())
        self.assertFalse(own_blob.exists())
        self.assertTrue(shared_blob.exists())
        self.assertEqual(self.client.get(f"/v1/documents/{document_id}").status_code, 404)

    def _document_row(self, document_id: str) -> Document:
        with self.session_factory() as session:
            return session.get(Document, document_id)


if __name__ == "__main__":
    unittest.main()
