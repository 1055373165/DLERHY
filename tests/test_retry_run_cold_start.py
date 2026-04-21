# ruff: noqa: E402
"""Regression pin for retry_run cold-start semantics (P0.3b).

``RunControlService.retry_run`` must always spawn a fresh DocumentRun
row rather than resetting the predecessor's status back to RUNNING.
The cold-start contract is what lets the run-lineage chain survive
reviews / reconciliation / debugging — an in-place hot-restart would
erase the predecessor's terminal state and make "why did this retry
happen?" unanswerable after the fact.

Integration coverage exists in ``tests/test_api_workflow.py``
(test_retry_run_restarts_pipeline_with_previous_lineage) but that
harness currently fails its bootstrap step on SQLite for reasons
unrelated to run-control. This file pins the contract at the service
layer directly so the cold-start invariant can't regress without
breaking a fast deterministic test.
"""

import sys
import unittest
from pathlib import Path
from uuid import uuid4

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
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_control import RunControlService


class RetryRunColdStartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed_failed_run(self) -> tuple[str, str]:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"retry-{uuid4()}",
                source_path="/tmp/retry.epub",
                title="Retry",
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
                status=DocumentRunStatus.FAILED,
                requested_by="original-operator",
                priority=100,
                stop_reason="provider.timeout",
                status_detail_json={"note": "some prior detail"},
            )
            session.add(run)
            session.flush()
            session.commit()
            return document.id, run.id

    def _service(self, session) -> RunControlService:
        return RunControlService(RunControlRepository(session))

    # ---- cold-start contract -------------------------------------------

    def test_retry_creates_new_run_with_lineage_edge(self) -> None:
        document_id, original_run_id = self._seed_failed_run()
        with self.session_factory() as session:
            summary = self._service(session).retry_run(
                original_run_id, actor_id="operator-42"
            )
            session.commit()

        self.assertNotEqual(summary.run_id, original_run_id)
        self.assertEqual(summary.document_id, document_id)
        self.assertEqual(summary.resume_from_run_id, original_run_id)
        self.assertEqual(summary.status, DocumentRunStatus.RUNNING.value)

    def test_retry_does_not_mutate_predecessor_status(self) -> None:
        document_id, original_run_id = self._seed_failed_run()
        with self.session_factory() as session:
            self._service(session).retry_run(
                original_run_id, actor_id="operator-42"
            )
            session.commit()
        with self.session_factory() as session:
            previous = session.get(DocumentRun, original_run_id)
            self.assertIsNotNone(previous)
            assert previous is not None
            self.assertEqual(previous.status, DocumentRunStatus.FAILED)
            self.assertEqual(previous.stop_reason, "provider.timeout")

    def test_retry_records_retry_of_run_id_in_status_detail(self) -> None:
        # The new run carries a ``retry_of_run_id`` breadcrumb in its
        # status_detail_json so operators reading the API can identify
        # this run as a retry without having to walk the lineage
        # chain themselves.
        document_id, original_run_id = self._seed_failed_run()
        with self.session_factory() as session:
            summary = self._service(session).retry_run(
                original_run_id, actor_id="operator-42"
            )
            session.commit()
        last_control = summary.status_detail_json.get("last_control") or {}
        last_control_detail = last_control.get("detail_json") or {}
        self.assertEqual(
            last_control_detail.get("retry_of_run_id"), original_run_id
        )

    def test_retry_rejects_running_non_stale_predecessor(self) -> None:
        # Retrying an actively running (not stale) run must fail —
        # otherwise two active runs would briefly coexist, which is
        # exactly the violation P0.3a's DB constraint catches. Surface
        # the error at the service layer so operators see a clean
        # 409-style message instead of an IntegrityError.
        from book_agent.services.run_control import RunControlTransitionError

        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"retry-running-{uuid4()}",
                source_path="/tmp/rr.epub",
                title="RR",
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
                requested_by="op",
                priority=100,
            )
            session.add(run)
            session.flush()
            session.commit()
            run_id = run.id

        with self.session_factory() as session:
            with self.assertRaises(RunControlTransitionError):
                self._service(session).retry_run(run_id, actor_id="op")


if __name__ == "__main__":
    unittest.main()
