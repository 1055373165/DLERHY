import unittest
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from book_agent.domain.enums import (
    ActorType,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
    WorkerLeaseStatus,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun, RunAuditEvent, RunBudget, WorkItem, WorkerLease
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory


class RunControlModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def test_run_control_models_persist_end_to_end(self) -> None:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint="run-control-fingerprint",
                source_path="/tmp/sample.epub",
                title="Run Control Sample",
                author="Test Author",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()

            document_run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                backend="openai_compatible",
                model_name="deepseek-chat",
                requested_by="tester",
                priority=50,
                status_detail_json={"note": "smoke"},
            )
            session.add(document_run)
            session.flush()

            run_budget = RunBudget(
                run_id=document_run.id,
                max_wall_clock_seconds=7200,
                max_total_cost_usd=25.0,
                max_total_token_in=1_000_000,
                max_total_token_out=500_000,
                max_retry_count_per_work_item=3,
                max_consecutive_failures=10,
                max_parallel_workers=4,
                max_parallel_requests_per_provider=4,
                max_auto_followup_attempts=2,
            )
            work_item = WorkItem(
                run_id=document_run.id,
                stage=WorkItemStage.TRANSLATE,
                scope_type=WorkItemScopeType.PACKET,
                scope_id=document.id,
                attempt=1,
                priority=50,
                status=WorkItemStatus.LEASED,
                lease_owner="worker-1",
                input_version_bundle_json={"packet_id": "pkt_1"},
            )
            session.add_all([run_budget, work_item])
            session.flush()

            lease = WorkerLease(
                run_id=document_run.id,
                work_item_id=work_item.id,
                worker_name="translate-worker",
                worker_instance_id="worker-1",
                lease_token="lease-token-1",
                status=WorkerLeaseStatus.ACTIVE,
                lease_expires_at=datetime.now(timezone.utc),
                last_heartbeat_at=datetime.now(timezone.utc),
            )
            audit_event = RunAuditEvent(
                run_id=document_run.id,
                work_item_id=work_item.id,
                event_type="work_item.leased",
                actor_type=ActorType.SYSTEM,
                actor_id="worker-1",
                payload_json={"stage": "translate"},
            )
            session.add_all([lease, audit_event])
            session.commit()

        with self.session_factory() as session:
            persisted_run = session.get(DocumentRun, document_run.id)
            persisted_budget = session.get(RunBudget, run_budget.id)
            persisted_item = session.get(WorkItem, work_item.id)
            persisted_lease = session.get(WorkerLease, lease.id)
            persisted_audit = session.get(RunAuditEvent, audit_event.id)

            self.assertIsNotNone(persisted_run)
            self.assertEqual(persisted_run.status, DocumentRunStatus.RUNNING)
            self.assertEqual(persisted_run.run_type, DocumentRunType.TRANSLATE_FULL)
            self.assertEqual(persisted_budget.max_parallel_workers, 4)
            self.assertEqual(persisted_item.status, WorkItemStatus.LEASED)
            self.assertEqual(persisted_item.scope_type, WorkItemScopeType.PACKET)
            self.assertEqual(persisted_lease.status, WorkerLeaseStatus.ACTIVE)
            self.assertEqual(persisted_audit.event_type, "work_item.leased")

    def test_run_budget_is_unique_per_run(self) -> None:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint="run-control-budget-unique",
                source_path="/tmp/sample.epub",
                title="Run Budget Unique",
                author="Test Author",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()

            document_run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.QUEUED,
            )
            session.add(document_run)
            session.flush()

            session.add(RunBudget(run_id=document_run.id, max_parallel_workers=2))
            session.flush()

            session.add(RunBudget(run_id=document_run.id, max_parallel_workers=4))
            with self.assertRaises(IntegrityError):
                session.flush()


if __name__ == "__main__":
    unittest.main()
