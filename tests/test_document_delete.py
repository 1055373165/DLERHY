# ruff: noqa: E402
"""Tests for DELETE /v1/documents/{id} — library row deletion."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.app.main import create_app
from book_agent.domain.enums import (
    ChapterStatus,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    SourceType,
)
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import DocumentRun
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory


def _enable_sqlite_fk(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class DocumentDeleteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        sqlite_path = Path(self.tempdir.name) / "book-agent.db"
        self.engine = build_engine(
            f"sqlite+pysqlite:///{sqlite_path}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        event.listen(self.engine, "connect", _enable_sqlite_fk)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.app = create_app()
        self.app.state.session_factory = self.session_factory
        self.app.state.export_root = str(Path(self.tempdir.name) / "exports")
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self._stop_executor)

    def _stop_executor(self) -> None:
        executor = getattr(self.app.state, "document_run_executor", None)
        if executor is not None:
            executor.stop()
            self.app.state.document_run_executor = None

    def _seed_document(self, *, run_status: DocumentRunStatus | None = None) -> str:
        with self.session_factory() as session:
            doc = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"del-{uuid4()}",
                source_path="/tmp/del.epub",
                title="t", author="a", src_lang="en", tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1, segmentation_version=1,
            )
            session.add(doc)
            session.flush()
            session.add(Chapter(
                document_id=doc.id, ordinal=1,
                status=ChapterStatus.PACKET_BUILT,
            ))
            if run_status is not None:
                session.add(DocumentRun(
                    document_id=doc.id,
                    status=run_status,
                    run_type=DocumentRunType.TRANSLATE_FULL,
                ))
            session.commit()
            return doc.id

    def test_delete_unknown_returns_404(self) -> None:
        response = self.client.delete(f"/v1/documents/{uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_delete_document_without_run_returns_204(self) -> None:
        doc_id = self._seed_document()
        response = self.client.delete(f"/v1/documents/{doc_id}")
        self.assertEqual(response.status_code, 204)
        with self.session_factory() as session:
            remaining = session.scalar(select(Document).where(Document.id == doc_id))
            self.assertIsNone(remaining)
            orphan_chapters = session.scalars(
                select(Chapter).where(Chapter.document_id == doc_id)
            ).all()
            self.assertEqual(list(orphan_chapters), [])

    def test_delete_document_with_terminal_run_succeeds(self) -> None:
        doc_id = self._seed_document(run_status=DocumentRunStatus.SUCCEEDED)
        response = self.client.delete(f"/v1/documents/{doc_id}")
        self.assertEqual(response.status_code, 204)

    def test_delete_refused_while_run_active(self) -> None:
        doc_id = self._seed_document(run_status=DocumentRunStatus.RUNNING)
        response = self.client.delete(f"/v1/documents/{doc_id}")
        self.assertEqual(response.status_code, 409)
        with self.session_factory() as session:
            doc = session.scalar(select(Document).where(Document.id == doc_id))
            self.assertIsNotNone(doc)

    def test_delete_refused_while_draining(self) -> None:
        doc_id = self._seed_document(run_status=DocumentRunStatus.DRAINING)
        response = self.client.delete(f"/v1/documents/{doc_id}")
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
