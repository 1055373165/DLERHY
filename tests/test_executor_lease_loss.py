"""A worker whose lease is reclaimed mid-execution must not commit its results.

The LLM call can outlive the lease. Previously the late worker still wrote its
translation artifacts and then crashed on release ("Active worker lease not
found"), duplicating work the new lease owner redoes.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.domain.enums import ActorType, DocumentRunType, DocumentStatus, SourceType, WorkItemStatus
from book_agent.domain.models import Document
from book_agent.domain.models.ops import RunAuditEvent, WorkerLease, WorkItem
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_control import RunControlService
from book_agent.services.run_execution import RunExecutionService


class ExecutorLeaseLossTests(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(tempdir.name) / 'lease.db'}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=str(Path(tempdir.name) / "exports"),
            translation_worker=None,
            heartbeat_interval_seconds=3600,
        )

    def _claim_translate_item(self):
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"lease-loss-{uuid4()}",
                source_path="/tmp/lease-loss.epub",
                title="Lease Loss",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()
            control = RunControlService(RunControlRepository(session))
            run = control.create_run(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                requested_by="lease-test",
            )
            control.resume_run(run.run_id, actor_id="lease-test", note="start")
            execution = RunExecutionService(RunControlRepository(session))
            execution.seed_translate_work_items(run_id=run.run_id, packet_ids=[str(uuid4())])
            claimed = execution.claim_next_translate_work_item(
                run_id=run.run_id,
                worker_name="lease-test",
                worker_instance_id="worker-late",
                lease_seconds=60,
            )
            session.commit()
        assert claimed is not None
        return run.run_id, claimed

    def _reclaim_lease(self, run_id: str, lease_token: str) -> None:
        with session_scope(self.session_factory) as session:
            lease = session.scalar(select(WorkerLease).where(WorkerLease.lease_token == lease_token))
            expired_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            lease.lease_expires_at = expired_at
            session.get(WorkItem, lease.work_item_id).lease_expires_at = expired_at
            session.flush()
            RunExecutionService(RunControlRepository(session)).reclaim_expired_leases(run_id=run_id)

    def test_results_are_discarded_when_the_lease_is_reclaimed_mid_execution(self) -> None:
        run_id, claimed = self._claim_translate_item()
        success_calls: list[str] = []

        def _worker() -> dict:
            with session_scope(self.session_factory) as session:
                session.add(
                    RunAuditEvent(
                        run_id=run_id,
                        event_type="test.late_worker_result",
                        actor_type=ActorType.SYSTEM,
                        actor_id="worker-late",
                        payload_json={},
                    )
                )
                # The lease expires and is reclaimed while the LLM call runs.
                self._reclaim_lease(run_id, claimed.lease_token)
                RunExecutionService(RunControlRepository(session)).assert_lease_held(
                    lease_token=claimed.lease_token
                )
            return {}

        with self.assertLogs("book_agent.app.runtime.document_run_executor", level="WARNING"):
            self.executor._execute_claimed_work_item(
                run_id=run_id,
                claimed=claimed,
                worker_fn=_worker,
                on_success=lambda payload, token: success_calls.append(token),
                stage_key="translate",
            )

        self.assertEqual(success_calls, [])
        with self.session_factory() as session:
            late_results = session.scalars(
                select(RunAuditEvent).where(RunAuditEvent.event_type == "test.late_worker_result")
            ).all()
            work_item = session.get(WorkItem, claimed.work_item_id)
        self.assertEqual(late_results, [])
        # Left as the reclaim set it, not overwritten by a failure record.
        self.assertEqual(work_item.status, WorkItemStatus.RETRYABLE_FAILED)
        self.assertEqual(work_item.error_class, "lease_expired")


if __name__ == "__main__":
    unittest.main()
