import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.app.main import create_app
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
from book_agent.domain.models.ops import RunAuditEvent, WorkItem, WorkerLease
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository


class _NoopDocumentRunExecutor:
    def __init__(self) -> None:
        self.wake_calls: list[str | None] = []

    def wake(self, _run_id: str | None = None) -> None:
        self.wake_calls.append(_run_id)
        return None

    def stop(self) -> None:
        return None


class RunControlApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

        sqlite_path = Path(self.tempdir.name) / "book-agent-run-control.db"
        self.engine = build_engine(
            f"sqlite+pysqlite:///{sqlite_path}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.app = create_app()
        self.app.state.session_factory = self.session_factory
        self.executor = _NoopDocumentRunExecutor()
        self.app.state.document_run_executor = self.executor
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def _create_document(self) -> str:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"run-control-{datetime.now(timezone.utc).timestamp()}",
                source_path="/tmp/run-control.epub",
                title="Run Control Document",
                author="Run Control Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.commit()
            return document.id

    def test_run_control_api_create_and_transition_flow(self) -> None:
        document_id = self._create_document()

        create_response = self.client.post(
            "/v1/runs",
            json={
                "document_id": document_id,
                "run_type": DocumentRunType.TRANSLATE_FULL.value,
                "requested_by": "ops-user",
                "backend": "openai_compatible",
                "model_name": "deepseek-chat",
                "priority": 40,
                "budget": {
                    "max_wall_clock_seconds": 3600,
                    "max_parallel_workers": 4,
                    "max_total_cost_usd": 12.5,
                },
            },
        )
        self.assertEqual(create_response.status_code, 201)
        run_id = create_response.json()["run_id"]
        self.assertEqual(create_response.json()["status"], "queued")
        self.assertEqual(create_response.json()["events"]["event_count"], 1)
        self.assertEqual(create_response.json()["budget"]["max_parallel_workers"], 4)

        pause_response = self.client.post(
            f"/v1/runs/{run_id}/pause",
            json={"actor_id": "ops-user", "note": "hold for budget review"},
        )
        self.assertEqual(pause_response.status_code, 200)
        self.assertEqual(pause_response.json()["status"], "paused")

        resume_response = self.client.post(
            f"/v1/runs/{run_id}/resume",
            json={"actor_id": "ops-user", "note": "resume after approval"},
        )
        self.assertEqual(resume_response.status_code, 200)
        self.assertEqual(resume_response.json()["status"], "running")
        self.assertIsNotNone(resume_response.json()["started_at"])

        drain_response = self.client.post(
            f"/v1/runs/{run_id}/drain",
            json={"actor_id": "ops-user", "note": "finish current leases only"},
        )
        self.assertEqual(drain_response.status_code, 200)
        self.assertEqual(drain_response.json()["status"], "draining")

        cancel_response = self.client.post(
            f"/v1/runs/{run_id}/cancel",
            json={"actor_id": "ops-user", "note": "stop overnight run"},
        )
        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.json()["status"], "cancelled")
        self.assertEqual(cancel_response.json()["stop_reason"], "stop overnight run")
        self.assertIsNotNone(cancel_response.json()["finished_at"])

        events_response = self.client.get(f"/v1/runs/{run_id}/events")
        self.assertEqual(events_response.status_code, 200)
        event_types = [entry["event_type"] for entry in events_response.json()["entries"]]
        self.assertGreaterEqual(events_response.json()["event_count"], 5)
        self.assertEqual(event_types[0], "run.cancelled")
        self.assertEqual(event_types[-1], "run.created")
        self.assertIn("run.paused", event_types)
        self.assertIn("run.resumed", event_types)
        self.assertIn("run.draining", event_types)
        self.assertIn("run.cancelled", event_types)

    def test_invalid_run_transition_returns_conflict(self) -> None:
        document_id = self._create_document()
        create_response = self.client.post(
            "/v1/runs",
            json={
                "document_id": document_id,
                "run_type": DocumentRunType.TRANSLATE_FULL.value,
                "requested_by": "ops-user",
            },
        )
        run_id = create_response.json()["run_id"]

        cancel_response = self.client.post(
            f"/v1/runs/{run_id}/cancel",
            json={"actor_id": "ops-user", "note": "stop now"},
        )
        self.assertEqual(cancel_response.status_code, 200)

        invalid_pause = self.client.post(
            f"/v1/runs/{run_id}/pause",
            json={"actor_id": "ops-user", "note": "too late"},
        )
        self.assertEqual(invalid_pause.status_code, 409)
        self.assertIn("cannot transition", invalid_pause.json()["detail"])

    def test_run_summary_aggregates_work_items_leases_and_events(self) -> None:
        document_id = self._create_document()
        create_response = self.client.post(
            "/v1/runs",
            json={
                "document_id": document_id,
                "run_type": DocumentRunType.TRANSLATE_FULL.value,
                "requested_by": "ops-user",
            },
        )
        run_id = create_response.json()["run_id"]

        with self.session_factory() as session:
            work_item_pending = WorkItem(
                run_id=run_id,
                stage=WorkItemStage.TRANSLATE,
                scope_type=WorkItemScopeType.PACKET,
                scope_id=document_id,
                priority=50,
                status=WorkItemStatus.PENDING,
                input_version_bundle_json={"packet_id": "pkt-a"},
            )
            work_item_running = WorkItem(
                run_id=run_id,
                stage=WorkItemStage.REVIEW,
                scope_type=WorkItemScopeType.CHAPTER,
                scope_id=document_id,
                priority=40,
                status=WorkItemStatus.RUNNING,
                lease_owner="worker-a",
                input_version_bundle_json={"chapter_id": "ch-a"},
                started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
            session.add_all([work_item_pending, work_item_running])
            session.flush()
            session.add(
                WorkerLease(
                    run_id=run_id,
                    work_item_id=work_item_running.id,
                    worker_name="translate-worker",
                    worker_instance_id="worker-a",
                    lease_token="lease-a",
                    status=WorkerLeaseStatus.ACTIVE,
                    lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
                    last_heartbeat_at=datetime.now(timezone.utc),
                )
            )
            session.add(
                RunAuditEvent(
                    run_id=run_id,
                    work_item_id=work_item_running.id,
                    event_type="work_item.started",
                    actor_type=ActorType.SYSTEM,
                    actor_id="worker-a",
                    payload_json={"stage": "review"},
                )
            )
            session.commit()

        summary_response = self.client.get(f"/v1/runs/{run_id}")
        self.assertEqual(summary_response.status_code, 200)
        summary_data = summary_response.json()
        self.assertEqual(summary_data["work_items"]["total_count"], 2)
        self.assertEqual(summary_data["work_items"]["status_counts"]["pending"], 1)
        self.assertEqual(summary_data["work_items"]["status_counts"]["running"], 1)
        self.assertEqual(summary_data["work_items"]["stage_counts"]["translate"], 1)
        self.assertEqual(summary_data["work_items"]["stage_counts"]["review"], 1)
        self.assertEqual(summary_data["worker_leases"]["total_count"], 1)
        self.assertEqual(summary_data["worker_leases"]["status_counts"]["active"], 1)
        self.assertIsNotNone(summary_data["worker_leases"]["latest_heartbeat_at"])
        self.assertEqual(summary_data["events"]["event_count"], 2)

    def test_run_summary_wakes_executor_for_active_run(self) -> None:
        document_id = self._create_document()
        create_response = self.client.post(
            "/v1/runs",
            json={
                "document_id": document_id,
                "run_type": DocumentRunType.TRANSLATE_FULL.value,
                "requested_by": "ops-user",
            },
        )
        run_id = create_response.json()["run_id"]

        with self.session_factory() as session:
            repository = RunControlRepository(session)
            run = repository.get_run(run_id)
            run.status = DocumentRunStatus.RUNNING
            session.commit()

        self.executor.wake_calls.clear()

        summary_response = self.client.get(f"/v1/runs/{run_id}")

        self.assertEqual(summary_response.status_code, 200)
        self.assertEqual(self.executor.wake_calls, [run_id])

    def test_run_summary_surfaces_active_bundle_revision_and_recovered_lineage(self) -> None:
        document_id = self._create_document()
        create_response = self.client.post(
            "/v1/runs",
            json={
                "document_id": document_id,
                "run_type": DocumentRunType.TRANSLATE_FULL.value,
                "requested_by": "ops-user",
            },
        )
        run_id = create_response.json()["run_id"]
        stable_revision_id = str(uuid4())
        bad_revision_id = str(uuid4())

        with self.session_factory() as session:
            repository = RunControlRepository(session)
            run = repository.get_run(run_id)
            run.runtime_bundle_revision_id = stable_revision_id
            run.status_detail_json = {
                "runtime_v2": {
                    "pending_export_route_repair": {
                        "incident_id": "incident-1",
                        "proposal_id": "proposal-1",
                        "repair_work_item_id": "repair-work-item-1",
                        "repair_blockage": {
                            "state": "manual_escalation_waiting",
                            "blocked": True,
                            "reason": "manual_escalation_required",
                        },
                    },
                    "last_export_route_recovery": {
                        "incident_id": "incident-1",
                        "proposal_id": "proposal-1",
                        "bundle_revision_id": bad_revision_id,
                        "bound_work_item_ids": ["work-item-1"],
                        "repair_blockage": {
                            "state": "ready_to_continue",
                            "blocked": False,
                            "reason": "repair_completed",
                        },
                    },
                    "recovered_lineage": [
                        {
                            "incident_id": "incident-1",
                            "proposal_id": "proposal-1",
                            "published_bundle_revision_id": bad_revision_id,
                            "active_bundle_revision_id": stable_revision_id,
                        }
                    ],
                }
            }
            repository.save_run(run)
            session.commit()

        summary_response = self.client.get(f"/v1/runs/{run_id}")
        self.assertEqual(summary_response.status_code, 200)
        runtime_v2 = summary_response.json()["status_detail_json"]["runtime_v2"]
        self.assertEqual(runtime_v2["runtime_bundle_revision_id"], stable_revision_id)
        self.assertEqual(runtime_v2["active_runtime_bundle_revision_id"], stable_revision_id)
        self.assertEqual(runtime_v2["last_export_route_recovery"]["bundle_revision_id"], bad_revision_id)
        self.assertEqual(
            runtime_v2["last_export_route_recovery"]["active_bundle_revision_id"],
            stable_revision_id,
        )
        self.assertEqual(runtime_v2["repair_blockage_state"], "ready_to_continue")
        self.assertFalse(runtime_v2["repair_blocked"])
        self.assertEqual(runtime_v2["repair_blockage_source"], "last_export_route_recovery")
        self.assertEqual(len(runtime_v2["recovered_lineage"]), 1)
        self.assertEqual(runtime_v2["recovered_lineage"][0]["proposal_id"], "proposal-1")


if __name__ == "__main__":
    unittest.main()
