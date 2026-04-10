"""Minimal end-to-end pipeline smoke tests running against PostgreSQL.

These tests use the ``echo`` translation backend (no real LLM calls) to verify
the full 5-stage pipeline: parse → translate → review → bilingual export → merged HTML.
"""

import os
import sys
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import ExportType, SourceType
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.services.workflows import DocumentWorkflowService

# Use a dedicated test database to avoid polluting the development one.
_TEST_DB_NAME = f"book_agent_smoke_{uuid4().hex[:8]}"
_PG_ADMIN_URL = os.environ.get(
    "BOOK_AGENT_TEST_PG_ADMIN_URL",
    "postgresql+psycopg://postgres:postgres@localhost:55432/postgres",
)
_PG_BASE = _PG_ADMIN_URL.rsplit("/", 1)[0]
_PG_TEST_URL = f"{_PG_BASE}/{_TEST_DB_NAME}"


def _create_test_database() -> None:
    """Create an isolated test database using the admin connection."""
    from sqlalchemy import text
    admin_engine = build_engine(_PG_ADMIN_URL)
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_TEST_DB_NAME}"'))
        conn.execute(text(f'CREATE DATABASE "{_TEST_DB_NAME}"'))
    admin_engine.dispose()


def _drop_test_database() -> None:
    """Drop the isolated test database."""
    from sqlalchemy import text
    admin_engine = build_engine(_PG_ADMIN_URL)
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{_TEST_DB_NAME}' AND pid <> pg_backend_pid()"
        ))
        conn.execute(text(f'DROP DATABASE IF EXISTS "{_TEST_DB_NAME}"'))
    admin_engine.dispose()


# ── Pre-built sample paths ──────────────────────────────────────────────
EPUB_SAMPLE = ROOT / "artifacts" / "smoke_samples" / "minimal_pipeline.epub"
PDF_SAMPLE = ROOT / "artifacts" / "smoke_samples" / "minimal_pipeline.pdf"


class MinimalPipelineSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _create_test_database()
        cls._engine = build_engine(_PG_TEST_URL)
        Base.metadata.create_all(cls._engine)
        cls._session_factory = build_session_factory(engine=cls._engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._engine.dispose()
        _drop_test_database()

    def test_minimal_epub_full_pipeline_smoke(self) -> None:
        """EPUB: parse → translate → review → bilingual → merged HTML."""
        self.assertTrue(EPUB_SAMPLE.exists(), f"Sample not found: {EPUB_SAMPLE}")
        export_root = ROOT / "artifacts" / "minimal_pipeline_smoke" / "exports-epub"
        export_root.mkdir(parents=True, exist_ok=True)

        with session_scope(self._session_factory) as session:
            workflow = DocumentWorkflowService(session, export_root=export_root)

            # Stage 1: Parse
            summary = workflow.bootstrap_document(EPUB_SAMPLE)
            self.assertEqual(summary.source_type, SourceType.EPUB.value)
            self.assertGreaterEqual(summary.chapter_count, 1)
            self.assertGreaterEqual(summary.sentence_count, 1)
            self.assertGreaterEqual(summary.packet_count, 1)

            # Stage 2: Translate
            translate = workflow.translate_document(summary.document_id)
            self.assertGreaterEqual(translate.translated_packet_count, 1)

            # Stage 3: Review
            review = workflow.review_document(summary.document_id)
            # Echo backend should produce clean translations with no issues
            self.assertEqual(review.total_issue_count, 0)

            # Stage 4: Bilingual Export
            export = workflow.export_document(summary.document_id, ExportType.BILINGUAL_HTML)
            self.assertEqual(export.document_status, "exported")
            self.assertGreaterEqual(len(export.chapter_results), 1)
            self.assertTrue(Path(export.chapter_results[0].file_path).exists())

            # Stage 5: Merged HTML (中文阅读稿)
            merged = workflow.export_document(summary.document_id, ExportType.MERGED_HTML)
            self.assertEqual(merged.document_status, "exported")

    def test_minimal_pdf_full_pipeline_smoke(self) -> None:
        """PDF: parse → translate → review → bilingual → merged HTML."""
        self.assertTrue(PDF_SAMPLE.exists(), f"Sample not found: {PDF_SAMPLE}")
        export_root = ROOT / "artifacts" / "minimal_pipeline_smoke" / "exports-pdf"
        export_root.mkdir(parents=True, exist_ok=True)

        with session_scope(self._session_factory) as session:
            workflow = DocumentWorkflowService(session, export_root=export_root)

            # Stage 1: Parse
            summary = workflow.bootstrap_document(PDF_SAMPLE)
            self.assertEqual(summary.source_type, SourceType.PDF_TEXT.value)
            self.assertGreaterEqual(summary.chapter_count, 1)
            self.assertGreaterEqual(summary.sentence_count, 1)
            self.assertGreaterEqual(summary.packet_count, 1)

            # Stage 2: Translate
            translate = workflow.translate_document(summary.document_id)
            self.assertGreaterEqual(translate.translated_packet_count, 1)

            # Stage 3: Review
            review = workflow.review_document(summary.document_id)
            self.assertEqual(review.total_issue_count, 0)

            # Stage 4: Bilingual Export
            export = workflow.export_document(summary.document_id, ExportType.BILINGUAL_HTML)
            self.assertEqual(export.document_status, "exported")
            self.assertGreaterEqual(len(export.chapter_results), 1)
            self.assertTrue(Path(export.chapter_results[0].file_path).exists())

            # Stage 5: Merged HTML (中文阅读稿)
            merged = workflow.export_document(summary.document_id, ExportType.MERGED_HTML)
            self.assertEqual(merged.document_status, "exported")


if __name__ == "__main__":
    unittest.main()
