# ruff: noqa: E402
"""Regression pins for uq_document_runs_active_per_document (P0.3a).

The partial UNIQUE index on ``document_runs(document_id)`` — scoped
to active statuses (``queued``, ``running``, ``draining``) — is the
DB-layer backstop for the "one active run per document" invariant
the pipeline relies on. These tests pin the DB contract directly so
a future service-layer refactor can't silently drop it.
"""

import sys
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import (
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    SourceType,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory


class DocumentRunsActiveUniqueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"dr-{uuid4()}",
                source_path="/tmp/dr.epub",
                title="DR",
                author="tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()
            session.commit()
            self.document_id = document.id

    def _run(self, *, status: DocumentRunStatus) -> DocumentRun:
        return DocumentRun(
            document_id=self.document_id,
            run_type=DocumentRunType.TRANSLATE_FULL,
            status=status,
            requested_by="test",
            priority=100,
        )

    # ---- DB-level guarantee --------------------------------------------

    def test_two_running_runs_for_same_document_rejected(self) -> None:
        with self.session_factory() as session:
            session.add(self._run(status=DocumentRunStatus.RUNNING))
            session.add(self._run(status=DocumentRunStatus.RUNNING))
            with self.assertRaises(IntegrityError):
                session.flush()

    def test_queued_plus_running_for_same_document_rejected(self) -> None:
        with self.session_factory() as session:
            session.add(self._run(status=DocumentRunStatus.QUEUED))
            session.add(self._run(status=DocumentRunStatus.RUNNING))
            with self.assertRaises(IntegrityError):
                session.flush()

    def test_paused_does_not_occupy_active_slot(self) -> None:
        # Retry while an older run is PAUSED: the new run must be
        # able to go active. Paused is deliberately excluded from
        # the partial UNIQUE so this lineage flow works.
        with self.session_factory() as session:
            session.add(self._run(status=DocumentRunStatus.PAUSED))
            session.add(self._run(status=DocumentRunStatus.RUNNING))
            session.flush()
            session.commit()

    def test_terminal_runs_do_not_occupy_active_slot(self) -> None:
        # Lineage chains keep terminal runs around forever. They
        # must never block a fresh active run on the same document.
        with self.session_factory() as session:
            session.add(self._run(status=DocumentRunStatus.SUCCEEDED))
            session.add(self._run(status=DocumentRunStatus.FAILED))
            session.add(self._run(status=DocumentRunStatus.CANCELLED))
            session.add(self._run(status=DocumentRunStatus.SUCCEEDED_WITH_WARNINGS))
            session.add(self._run(status=DocumentRunStatus.RUNNING))
            session.flush()
            session.commit()

    def test_different_documents_are_independent(self) -> None:
        with self.session_factory() as session:
            other = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"dr-other-{uuid4()}",
                source_path="/tmp/dr2.epub",
                title="DR2",
                author="tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(other)
            session.flush()
            session.add(self._run(status=DocumentRunStatus.RUNNING))
            session.add(
                DocumentRun(
                    document_id=other.id,
                    run_type=DocumentRunType.TRANSLATE_FULL,
                    status=DocumentRunStatus.RUNNING,
                    requested_by="test",
                    priority=100,
                )
            )
            session.flush()
            session.commit()


if __name__ == "__main__":
    unittest.main()
