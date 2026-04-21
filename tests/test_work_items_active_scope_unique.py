# ruff: noqa: E402
"""Regression pins for the work_items active-scope UNIQUE index (P0.1a).

The index lives in :class:`WorkItem.__table_args__` as
``uq_work_items_active_scope`` — a partial UNIQUE on
``(run_id, stage, scope_type, scope_id)`` scoped to live statuses
(``pending``, ``leased``, ``running``, ``retryable_failed``). The point
is to stop two concurrent seeders from both inserting "the same"
active work_item after racing past the app-layer dedupe check in
:meth:`RunExecutionService.seed_work_items`.

These tests DO NOT exercise the service layer — they pin the DB
contract itself so a future refactor of the service can't silently
drop the safety net.
"""

import os
import sys
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import (
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun, WorkItem
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory


class WorkItemsActiveScopeUniqueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"uniq-{uuid4()}",
                source_path="/tmp/uniq.epub",
                title="Uniq",
                author="tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()
            run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="test",
                priority=100,
            )
            session.add(run)
            session.flush()
            session.commit()
            self.run_id = run.id

    def _add(self, session, *, scope_id: str, status: WorkItemStatus) -> WorkItem:
        work_item = WorkItem(
            run_id=self.run_id,
            stage=WorkItemStage.TRANSLATE,
            scope_type=WorkItemScopeType.PACKET,
            scope_id=scope_id,
            status=status,
            priority=100,
        )
        session.add(work_item)
        return work_item

    # ---- DB-level guarantee --------------------------------------------

    def test_duplicate_active_scope_rejected_by_db(self) -> None:
        # Two live rows for the same (run, stage, scope_type, scope_id)
        # are exactly the double-seed failure mode we're guarding against.
        scope_id = str(uuid4())
        with self.session_factory() as session:
            self._add(session, scope_id=scope_id, status=WorkItemStatus.PENDING)
            self._add(session, scope_id=scope_id, status=WorkItemStatus.PENDING)
            with self.assertRaises(IntegrityError):
                session.flush()

    def test_second_row_allowed_after_first_terminates(self) -> None:
        # Once the previous attempt is succeeded/terminal_failed, the
        # slot frees up — this is the REPAIR stage reseed path and the
        # partial index explicitly allows it.
        scope_id = str(uuid4())
        with self.session_factory() as session:
            first = self._add(session, scope_id=scope_id, status=WorkItemStatus.PENDING)
            session.flush()
            first.status = WorkItemStatus.SUCCEEDED
            session.flush()
            self._add(session, scope_id=scope_id, status=WorkItemStatus.PENDING)
            # Must not raise: partial index excludes the terminal row.
            session.flush()
            session.commit()

    def test_different_stages_are_independent(self) -> None:
        # A chapter can simultaneously have a live TRANSLATE work_item
        # and a live REVIEW work_item on the same scope_id. The index
        # must only dedupe within the (run, stage) pair.
        scope_id = str(uuid4())
        with self.session_factory() as session:
            self._add(session, scope_id=scope_id, status=WorkItemStatus.PENDING)
            other = WorkItem(
                run_id=self.run_id,
                stage=WorkItemStage.REVIEW,
                scope_type=WorkItemScopeType.PACKET,
                scope_id=scope_id,
                status=WorkItemStatus.PENDING,
                priority=100,
            )
            session.add(other)
            session.flush()
            session.commit()


if __name__ == "__main__":
    unittest.main()
