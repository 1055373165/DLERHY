import tempfile
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from uuid import uuid4

from book_agent.app.runtime.controllers.incident_controller import IncidentController
from book_agent.app.runtime.controllers.review_controller import ReviewController
from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.core.config import Settings
from book_agent.domain.enums import (
    ChapterRunPhase,
    ChapterRunStatus,
    ChapterStatus,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    JobScopeType,
    ReviewSessionStatus,
    ReviewTerminalityState,
    RuntimeIncidentKind,
    RuntimeIncidentStatus,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import (
    ChapterRun,
    DocumentRun,
    RunBudget,
    RuntimeCheckpoint,
    RuntimeIncident,
    RuntimePatchProposal,
    WorkItem,
)
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository
from book_agent.services.runtime_bundle import RuntimeBundleService
from book_agent.services.runtime_patch_validation import RuntimePatchValidationService
from book_agent.tools.runtime_repair_contract_runner import execute_runtime_repair_contract_runner
from book_agent.tools.runtime_repair_runner import execute_runtime_repair_runner


class ReqMx01ReviewDeadlockSelfHealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.bundle_root = Path(self.tempdir.name) / "runtime-bundles"
        self.database_path = Path(self.tempdir.name) / "req-mx-01.sqlite"
        self.database_url = f"sqlite+pysqlite:///{self.database_path}"
        self.engine = build_engine(self.database_url)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _settings(self) -> Settings:
        return Settings(
            database_url=self.database_url,
            runtime_bundle_root=self.bundle_root,
        )

    def _seed_review_deadlock_scope(
        self,
        *,
        preferred_execution_mode: str | None = None,
        preferred_executor_hint: str | None = None,
        preferred_executor_contract_version: int | None = None,
        preferred_transport_hint: str | None = None,
        preferred_transport_contract_version: int | None = None,
    ) -> tuple[str, str]:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"req-mx-01-{uuid4()}",
                source_path="/tmp/req-mx-01.epub",
                title="REQ-MX-01",
                author="Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()

            target_chapter = Chapter(
                document_id=document.id,
                ordinal=1,
                title_src="Target Chapter",
                status=ChapterStatus.PACKET_BUILT,
                metadata_json={},
            )
            session.add(target_chapter)
            session.flush()

            runtime_v2 = {
                "allowed_patch_surfaces": ["runtime_bundle"],
                "enable_review_deadlock_auto_repair": True,
            }
            if preferred_execution_mode is not None:
                runtime_v2["preferred_repair_execution_mode"] = preferred_execution_mode
            if preferred_executor_hint is not None:
                runtime_v2["preferred_repair_executor_hint"] = preferred_executor_hint
            if preferred_execution_mode is not None or preferred_executor_hint is not None:
                runtime_v2["preferred_repair_executor_contract_version"] = (
                    preferred_executor_contract_version or 1
                )
            if preferred_transport_hint is not None:
                runtime_v2["preferred_repair_transport_hint"] = preferred_transport_hint
                runtime_v2["preferred_repair_transport_contract_version"] = (
                    preferred_transport_contract_version or 1
                )

            run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="tester",
                priority=100,
                status_detail_json={"runtime_v2": runtime_v2},
            )
            session.add(run)
            session.flush()

            chapter_run = ChapterRun(
                run_id=run.id,
                document_id=document.id,
                chapter_id=target_chapter.id,
                desired_phase=ChapterRunPhase.REVIEW,
                observed_phase=ChapterRunPhase.REVIEW,
                status=ChapterRunStatus.ACTIVE,
                generation=1,
                observed_generation=1,
                conditions_json={},
                status_detail_json={},
            )
            session.add(chapter_run)
            session.flush()

            review_session = RuntimeResourcesRepository(session).ensure_review_session(
                chapter_run_id=chapter_run.id,
                desired_generation=1,
                observed_generation=1,
                scope_json={
                    "run_id": run.id,
                    "document_id": document.id,
                    "chapter_id": target_chapter.id,
                },
                runtime_bundle_revision_id=None,
            )
            review_session.status = ReviewSessionStatus.ACTIVE
            review_session.terminality_state = ReviewTerminalityState.OPEN
            review_session.last_reconciled_at = datetime.now(timezone.utc) - timedelta(minutes=40)
            session.add(review_session)

            work_item = WorkItem(
                run_id=run.id,
                stage=WorkItemStage.REVIEW,
                scope_type=WorkItemScopeType.CHAPTER,
                scope_id=target_chapter.id,
                attempt=2,
                priority=100,
                status=WorkItemStatus.RETRYABLE_FAILED,
                lease_owner=None,
                lease_expires_at=None,
                last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=40),
                started_at=datetime.now(timezone.utc) - timedelta(minutes=45),
                updated_at=datetime.now(timezone.utc) - timedelta(minutes=40),
                finished_at=datetime.now(timezone.utc) - timedelta(minutes=40),
                input_version_bundle_json={
                    "document_id": document.id,
                    "chapter_id": target_chapter.id,
                    "chapter_run_id": chapter_run.id,
                    "review_session_id": review_session.id,
                },
                output_artifact_refs_json={},
                error_detail_json={},
            )
            session.add(work_item)
            session.add(RunBudget(run_id=run.id, max_auto_followup_attempts=2))
            session.flush()

            controller = ReviewController(session=session)
            controller._incident_controller = IncidentController(
                session=session,
                bundle_service=RuntimeBundleService(session, settings=self._settings()),
                validation_service=RuntimePatchValidationService(session),
            )
            controller.reconcile_review_session(chapter_run_id=chapter_run.id)
            controller.reconcile_review_session(chapter_run_id=chapter_run.id)
            session.commit()
            return run.id, target_chapter.id

    def test_req_mx_01_review_deadlock_self_heal_stays_bounded_to_review_scope(self) -> None:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"req-mx-01-{uuid4()}",
                source_path="/tmp/req-mx-01.epub",
                title="REQ-MX-01",
                author="Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()

            target_chapter = Chapter(
                document_id=document.id,
                ordinal=1,
                title_src="Target Chapter",
                status=ChapterStatus.PACKET_BUILT,
                metadata_json={},
            )
            untouched_chapter = Chapter(
                document_id=document.id,
                ordinal=2,
                title_src="Untouched Chapter",
                status=ChapterStatus.PACKET_BUILT,
                metadata_json={},
            )
            session.add_all([target_chapter, untouched_chapter])
            session.flush()

            run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="tester",
                priority=100,
                status_detail_json={
                    "runtime_v2": {
                        "allowed_patch_surfaces": ["runtime_bundle"],
                        "enable_review_deadlock_auto_repair": True,
                    }
                },
            )
            session.add(run)
            session.flush()

            target_chapter_run = ChapterRun(
                run_id=run.id,
                document_id=document.id,
                chapter_id=target_chapter.id,
                desired_phase=ChapterRunPhase.REVIEW,
                observed_phase=ChapterRunPhase.REVIEW,
                status=ChapterRunStatus.ACTIVE,
                generation=1,
                observed_generation=1,
                conditions_json={},
                status_detail_json={},
            )
            untouched_chapter_run = ChapterRun(
                run_id=run.id,
                document_id=document.id,
                chapter_id=untouched_chapter.id,
                desired_phase=ChapterRunPhase.REVIEW,
                observed_phase=ChapterRunPhase.REVIEW,
                status=ChapterRunStatus.ACTIVE,
                generation=1,
                observed_generation=1,
                conditions_json={},
                status_detail_json={},
            )
            session.add_all([target_chapter_run, untouched_chapter_run])
            session.flush()

            runtime_repo = RuntimeResourcesRepository(session)
            bundle_revision_id = str(uuid4())
            target_review_session = runtime_repo.ensure_review_session(
                chapter_run_id=target_chapter_run.id,
                desired_generation=1,
                observed_generation=1,
                scope_json={
                    "run_id": run.id,
                    "document_id": document.id,
                    "chapter_id": target_chapter.id,
                },
                runtime_bundle_revision_id=bundle_revision_id,
            )
            runtime_repo.ensure_review_session(
                chapter_run_id=untouched_chapter_run.id,
                desired_generation=1,
                observed_generation=1,
                scope_json={
                    "run_id": run.id,
                    "document_id": document.id,
                    "chapter_id": untouched_chapter.id,
                },
            )
            runtime_repo.update_review_session(
                target_review_session.id,
                status=ReviewSessionStatus.ACTIVE,
                terminality_state=ReviewTerminalityState.OPEN,
                last_reconciled_at=datetime.now(timezone.utc) - timedelta(minutes=40),
            )
            target_review_session.updated_at = datetime.now(timezone.utc) - timedelta(minutes=40)
            session.add(target_review_session)

            work_item = WorkItem(
                run_id=run.id,
                stage=WorkItemStage.REVIEW,
                scope_type=WorkItemScopeType.CHAPTER,
                scope_id=target_chapter.id,
                attempt=2,
                priority=100,
                status=WorkItemStatus.RETRYABLE_FAILED,
                runtime_bundle_revision_id=bundle_revision_id,
                lease_owner=None,
                started_at=datetime.now(timezone.utc) - timedelta(minutes=45),
                updated_at=datetime.now(timezone.utc) - timedelta(minutes=40),
                finished_at=datetime.now(timezone.utc) - timedelta(minutes=40),
                input_version_bundle_json={
                    "document_id": document.id,
                    "chapter_id": target_chapter.id,
                    "chapter_run_id": target_chapter_run.id,
                    "review_session_id": target_review_session.id,
                },
                output_artifact_refs_json={},
                error_detail_json={},
            )
            session.add(work_item)
            session.add(
                RunBudget(
                    run_id=run.id,
                    max_auto_followup_attempts=2,
                )
            )
            session.flush()

            controller = ReviewController(session=session)
            controller._incident_controller = IncidentController(
                session=session,
                bundle_service=RuntimeBundleService(session, settings=self._settings()),
                validation_service=RuntimePatchValidationService(session),
            )

            controller.reconcile_review_session(chapter_run_id=target_chapter_run.id)
            controller.reconcile_review_session(chapter_run_id=target_chapter_run.id)
            session.commit()

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=self.bundle_root,
            translation_worker=None,
        )
        with self.session_factory() as session:
            execution = executor._run_execution_service(session)
            claimed = execution.claim_next_work_item(
                run_id=run.id,
                stage=WorkItemStage.REPAIR,
                worker_name="test.repair",
                worker_instance_id="repair-worker-1",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            session.commit()

        executor._execute_repair_work_item(run.id, claimed)

        with self.session_factory() as session:
            runtime_repo = RuntimeResourcesRepository(session)
            incident = session.query(RuntimeIncident).filter(RuntimeIncident.run_id == run.id).one()
            proposal = session.query(RuntimePatchProposal).filter(RuntimePatchProposal.incident_id == incident.id).one()
            checkpoint = session.query(RuntimeCheckpoint).filter(
                RuntimeCheckpoint.run_id == run.id,
                RuntimeCheckpoint.scope_type == JobScopeType.CHAPTER,
                RuntimeCheckpoint.scope_id == target_chapter.id,
                RuntimeCheckpoint.checkpoint_key == "review_controller.deadlock_recovery",
            ).one()
            persisted_review_session = runtime_repo.get_review_session(target_review_session.id)
            persisted_work_items = session.query(WorkItem).filter(WorkItem.run_id == run.id).all()
            persisted_run = session.get(DocumentRun, run.id)
            persisted_target_chapter_run = session.get(ChapterRun, target_chapter_run.id)
            persisted_untouched_chapter_run = session.get(ChapterRun, untouched_chapter_run.id)

        self.assertEqual(incident.incident_kind, RuntimeIncidentKind.REVIEW_DEADLOCK)
        self.assertEqual(incident.status, RuntimeIncidentStatus.PUBLISHED)
        self.assertEqual(incident.scope_type, JobScopeType.CHAPTER)
        self.assertEqual(incident.scope_id, target_chapter.id)
        self.assertEqual(proposal.status.value, "published")
        self.assertIsNotNone(persisted_run)
        self.assertIsNotNone(persisted_target_chapter_run)
        self.assertIsNotNone(persisted_untouched_chapter_run)
        assert persisted_run is not None
        assert persisted_target_chapter_run is not None
        assert persisted_untouched_chapter_run is not None

        self.assertEqual(persisted_run.runtime_bundle_revision_id, proposal.published_bundle_revision_id)
        self.assertEqual(checkpoint.checkpoint_json["recovery"]["replay_scope_id"], target_chapter.id)
        self.assertEqual(checkpoint.checkpoint_json["recovery"]["replay_work_item_ids"], [work_item.id])
        self.assertEqual(
            persisted_review_session.status_detail_json["runtime_v2"]["last_deadlock_recovery"]["replay_work_item_ids"],
            [work_item.id],
        )
        self.assertEqual(
            persisted_review_session.status_detail_json["runtime_v2"]["last_deadlock_recovery"]["bundle_revision_id"],
            proposal.published_bundle_revision_id,
        )
        self.assertEqual(
            persisted_target_chapter_run.conditions_json["recovered_lineage"][0]["replay_scope_id"],
            target_chapter.id,
        )
        self.assertEqual(persisted_untouched_chapter_run.conditions_json, {})
        review_items = [item for item in persisted_work_items if item.stage == WorkItemStage.REVIEW]
        repair_items = [item for item in persisted_work_items if item.stage == WorkItemStage.REPAIR]
        self.assertEqual(len(review_items), 1)
        self.assertEqual(review_items[0].scope_type, WorkItemScopeType.CHAPTER)
        self.assertEqual(review_items[0].scope_id, target_chapter.id)
        self.assertEqual(len(repair_items), 1)
        self.assertEqual(repair_items[0].scope_type, WorkItemScopeType.ISSUE_ACTION)
        self.assertEqual(repair_items[0].scope_id, proposal.id)
        self.assertEqual(repair_items[0].status, WorkItemStatus.SUCCEEDED)
        self.assertEqual(repair_items[0].input_version_bundle_json["target_scope_id"], target_chapter.id)
        self.assertEqual(repair_items[0].input_version_bundle_json["target_scope_type"], "chapter")
        self.assertEqual(repair_items[0].input_version_bundle_json["execution_mode"], "transport_backed")
        self.assertEqual(
            repair_items[0].input_version_bundle_json["executor_hint"],
            "python_transport_repair_executor",
        )
        self.assertEqual(
            repair_items[0].input_version_bundle_json["transport_hint"],
            "python_subprocess_repair_transport",
        )
        self.assertEqual(proposal.status_detail_json["repair_dispatch"]["execution_mode"], "transport_backed")
        self.assertEqual(
            proposal.status_detail_json["repair_dispatch"]["transport_hint"],
            "python_subprocess_repair_transport",
        )
        self.assertEqual(
            incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_executor_execution_mode"],
            "transport_backed",
        )
        self.assertEqual(
            incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_transport_hint"],
            "python_subprocess_repair_transport",
        )

    def test_req_mx_01_review_deadlock_self_heal_can_execute_through_http_transport_override(self) -> None:
        run_id, chapter_id = self._seed_review_deadlock_scope(
            preferred_transport_hint="http_repair_transport",
            preferred_transport_contract_version=1,
        )

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=self.bundle_root,
            translation_worker=None,
        )
        with self.session_factory() as session:
            execution = executor._run_execution_service(session)
            claimed = execution.claim_next_work_item(
                run_id=run_id,
                stage=WorkItemStage.REPAIR,
                worker_name="test.repair",
                worker_instance_id="repair-worker-1",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            session.commit()

        class _HttpResponse:
            def __init__(self, payload: dict[str, object]) -> None:
                self.status_code = 200
                self._payload = payload
                self.text = ""

            def json(self) -> dict[str, object]:
                return dict(self._payload)

        def _http_side_effect(url: str, *, json: dict[str, object], headers: dict[str, str], timeout: int):
            self.assertEqual(url, "https://repair-agent.example/execute")
            self.assertEqual(timeout, 30)
            self.assertEqual(headers["authorization"], "Bearer repair-token")
            return _HttpResponse(execute_runtime_repair_runner(json))

        with (
            mock.patch(
                "book_agent.services.runtime_repair_transport.get_settings",
                return_value=Settings(
                    database_url=self.database_url,
                    runtime_bundle_root=self.bundle_root,
                    runtime_repair_transport_http_url="https://repair-agent.example/execute",
                    runtime_repair_transport_http_timeout_seconds=30,
                    runtime_repair_transport_http_bearer_token="repair-token",
                ),
            ),
            mock.patch(
                "book_agent.services.runtime_repair_transport.httpx.post",
                side_effect=_http_side_effect,
            ),
        ):
            executor._execute_repair_work_item(run_id, claimed)

        with self.session_factory() as session:
            incident = session.query(RuntimeIncident).filter(RuntimeIncident.run_id == run_id).one()
            proposal = session.query(RuntimePatchProposal).filter(RuntimePatchProposal.incident_id == incident.id).one()
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

            self.assertIsNotNone(repair_work_item)
            assert repair_work_item is not None
            self.assertEqual(incident.incident_kind, RuntimeIncidentKind.REVIEW_DEADLOCK)
            self.assertEqual(incident.scope_id, chapter_id)
            self.assertEqual(proposal.status.value, "published")
            self.assertEqual(repair_work_item.input_version_bundle_json["transport_hint"], "http_repair_transport")
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["transport_hint"],
                "http_repair_transport",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_transport_hint"],
                "http_repair_transport",
            )

    def test_req_mx_01_review_deadlock_self_heal_can_execute_through_http_contract_transport_override(self) -> None:
        run_id, chapter_id = self._seed_review_deadlock_scope(
            preferred_execution_mode="transport_backed",
            preferred_executor_hint="python_contract_transport_repair_executor",
            preferred_executor_contract_version=1,
            preferred_transport_hint="http_contract_repair_transport",
            preferred_transport_contract_version=1,
        )

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=self.bundle_root,
            translation_worker=None,
        )
        with self.session_factory() as session:
            execution = executor._run_execution_service(session)
            claimed = execution.claim_next_work_item(
                run_id=run_id,
                stage=WorkItemStage.REPAIR,
                worker_name="test.repair",
                worker_instance_id="repair-worker-1",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            session.commit()

        class _HttpResponse:
            def __init__(self, payload: dict[str, object]) -> None:
                self.status_code = 200
                self._payload = payload
                self.text = ""

            def json(self) -> dict[str, object]:
                return dict(self._payload)

        def _http_side_effect(url: str, *, json: dict[str, object], headers: dict[str, str], timeout: int):
            self.assertEqual(url, "https://repair-agent.example/execute")
            self.assertEqual(timeout, 30)
            self.assertEqual(headers["authorization"], "Bearer repair-token")
            self.assertNotIn("database_url", json)
            self.assertNotIn("claimed", json)
            return _HttpResponse(execute_runtime_repair_contract_runner(json))

        with (
            mock.patch(
                "book_agent.services.runtime_repair_transport.get_settings",
                return_value=Settings(
                    database_url=self.database_url,
                    runtime_bundle_root=self.bundle_root,
                    runtime_repair_transport_http_url="https://repair-agent.example/execute",
                    runtime_repair_transport_http_timeout_seconds=30,
                    runtime_repair_transport_http_bearer_token="repair-token",
                ),
            ),
            mock.patch(
                "book_agent.services.runtime_repair_transport.httpx.post",
                side_effect=_http_side_effect,
            ),
        ):
            executor._execute_repair_work_item(run_id, claimed)

        with self.session_factory() as session:
            incident = session.query(RuntimeIncident).filter(RuntimeIncident.run_id == run_id).one()
            proposal = session.query(RuntimePatchProposal).filter(RuntimePatchProposal.incident_id == incident.id).one()
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

            self.assertIsNotNone(repair_work_item)
            assert repair_work_item is not None
            self.assertEqual(incident.incident_kind, RuntimeIncidentKind.REVIEW_DEADLOCK)
            self.assertEqual(incident.scope_id, chapter_id)
            self.assertEqual(proposal.status.value, "published")
            self.assertEqual(repair_work_item.input_version_bundle_json["transport_hint"], "http_contract_repair_transport")
            self.assertEqual(
                repair_work_item.input_version_bundle_json["executor_hint"],
                "python_contract_transport_repair_executor",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["transport_hint"],
                "http_contract_repair_transport",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_transport_hint"],
                "http_contract_repair_transport",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_executor_hint"],
                "python_contract_transport_repair_executor",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_adapter_name"],
                "review_deadlock_remote_contract_repair_agent",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_transport_endpoint"],
                "https://repair-agent.example/execute",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_execution_status"],
                "succeeded",
            )
            self.assertTrue(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_execution_id"]
            )

    def test_req_mx_01_review_deadlock_self_heal_defaults_to_http_contract_transport_when_remote_endpoint_is_configured(self) -> None:
        settings = Settings(
            database_url=self.database_url,
            runtime_bundle_root=self.bundle_root,
            runtime_repair_transport_http_url="https://repair-agent.example/execute",
            runtime_repair_transport_http_timeout_seconds=30,
            runtime_repair_transport_http_bearer_token="repair-token",
        )
        with mock.patch(
            "book_agent.app.runtime.controllers.review_controller.get_settings",
            return_value=settings,
        ):
            run_id, chapter_id = self._seed_review_deadlock_scope(
                preferred_execution_mode=None,
                preferred_executor_hint=None,
            )

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=self.bundle_root,
            translation_worker=None,
        )
        with self.session_factory() as session:
            execution = executor._run_execution_service(session)
            claimed = execution.claim_next_work_item(
                run_id=run_id,
                stage=WorkItemStage.REPAIR,
                worker_name="test.repair",
                worker_instance_id="repair-worker-1",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            session.commit()

        class _HttpResponse:
            def __init__(self, payload: dict[str, object]) -> None:
                self.status_code = 200
                self._payload = payload
                self.text = ""

            def json(self) -> dict[str, object]:
                return dict(self._payload)

        def _http_side_effect(url: str, *, json: dict[str, object], headers: dict[str, str], timeout: int):
            self.assertEqual(url, "https://repair-agent.example/execute")
            self.assertEqual(timeout, 30)
            self.assertEqual(headers["authorization"], "Bearer repair-token")
            return _HttpResponse(execute_runtime_repair_contract_runner(json))

        with (
            mock.patch(
                "book_agent.services.runtime_repair_transport.get_settings",
                return_value=settings,
            ),
            mock.patch(
                "book_agent.services.runtime_repair_transport.httpx.post",
                side_effect=_http_side_effect,
            ),
        ):
            executor._execute_repair_work_item(run_id, claimed)

        with self.session_factory() as session:
            incident = session.query(RuntimeIncident).filter(RuntimeIncident.run_id == run_id).one()
            proposal = session.query(RuntimePatchProposal).filter(RuntimePatchProposal.incident_id == incident.id).one()
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

            self.assertIsNotNone(repair_work_item)
            assert repair_work_item is not None
            self.assertEqual(incident.incident_kind, RuntimeIncidentKind.REVIEW_DEADLOCK)
            self.assertEqual(incident.scope_id, chapter_id)
            self.assertEqual(proposal.status.value, "published")
            self.assertEqual(
                repair_work_item.input_version_bundle_json["executor_hint"],
                "python_contract_transport_repair_executor",
            )
            self.assertEqual(
                repair_work_item.input_version_bundle_json["transport_hint"],
                "http_contract_repair_transport",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["executor_hint"],
                "python_contract_transport_repair_executor",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["transport_hint"],
                "http_contract_repair_transport",
            )

    def test_req_mx_01_review_deadlock_self_heal_records_retry_later_remote_decision(self) -> None:
        run_id, chapter_id = self._seed_review_deadlock_scope(
            preferred_execution_mode="transport_backed",
            preferred_executor_hint="python_contract_transport_repair_executor",
            preferred_executor_contract_version=1,
            preferred_transport_hint="http_contract_repair_transport",
            preferred_transport_contract_version=1,
        )

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=self.bundle_root,
            translation_worker=None,
        )
        with self.session_factory() as session:
            execution = executor._run_execution_service(session)
            claimed = execution.claim_next_work_item(
                run_id=run_id,
                stage=WorkItemStage.REPAIR,
                worker_name="test.repair",
                worker_instance_id="repair-worker-1",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            session.commit()

        settings = Settings(
            database_url=self.database_url,
            runtime_bundle_root=self.bundle_root,
            runtime_repair_transport_http_url="https://repair-agent.example/execute",
            runtime_repair_transport_http_timeout_seconds=30,
            runtime_repair_transport_http_bearer_token="repair-token",
        )

        class _HttpResponse:
            def __init__(self, payload: dict[str, object]) -> None:
                self.status_code = 200
                self._payload = payload
                self.text = ""

            def json(self) -> dict[str, object]:
                return dict(self._payload)

        def _http_side_effect(url: str, *, json: dict[str, object], headers: dict[str, str], timeout: int):
            self.assertEqual(url, "https://repair-agent.example/execute")
            self.assertEqual(timeout, 30)
            self.assertEqual(headers["authorization"], "Bearer repair-token")
            result = execute_runtime_repair_contract_runner(json)
            result["repair_agent_decision"] = "retry_later"
            result["repair_agent_decision_reason"] = "awaiting_remote_dependency"
            result["repair_agent_retry_after_seconds"] = 900
            return _HttpResponse(result)

        with (
            mock.patch(
                "book_agent.services.runtime_repair_transport.get_settings",
                return_value=settings,
            ),
            mock.patch(
                "book_agent.services.runtime_repair_transport.httpx.post",
                side_effect=_http_side_effect,
            ),
        ):
            executor._execute_repair_work_item(run_id, claimed)

        with self.session_factory() as session:
            incident = session.query(RuntimeIncident).filter(RuntimeIncident.run_id == run_id).one()
            proposal = session.query(RuntimePatchProposal).filter(RuntimePatchProposal.incident_id == incident.id).one()
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

            self.assertIsNotNone(repair_work_item)
            assert repair_work_item is not None
            self.assertEqual(incident.incident_kind, RuntimeIncidentKind.REVIEW_DEADLOCK)
            self.assertEqual(incident.scope_id, chapter_id)
            self.assertEqual(proposal.status.value, "proposed")
            self.assertEqual(repair_work_item.status, WorkItemStatus.RETRYABLE_FAILED)
            self.assertEqual(repair_work_item.error_class, "RuntimeRepairRetryLater")
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["status"],
                "retry_later",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["next_action"],
                "retry_repair_lane",
            )
            self.assertTrue(proposal.status_detail_json["repair_dispatch"]["retryable"])
            self.assertEqual(proposal.status_detail_json["repair_dispatch"]["retry_after_seconds"], 900)
            self.assertIn("T", proposal.status_detail_json["repair_dispatch"]["next_retry_after"])
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_decision"],
                "retry_later",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_decision_reason"],
                "awaiting_remote_dependency",
            )
            self.assertEqual(
                (repair_work_item.error_detail_json or {}).get("repair_agent_decision"),
                "retry_later",
            )
        with self.session_factory() as session:
            chapter_run = session.query(ChapterRun).filter(
                ChapterRun.run_id == run_id,
                ChapterRun.chapter_id == chapter_id,
            ).one()
            review_session = RuntimeResourcesRepository(session).get_review_session_by_identity(
                chapter_run_id=chapter_run.id,
                desired_generation=int(chapter_run.generation or 1),
            )
            checkpoint = (
                session.query(RuntimeCheckpoint)
                .filter(
                    RuntimeCheckpoint.run_id == run_id,
                    RuntimeCheckpoint.scope_type == JobScopeType.CHAPTER,
                    RuntimeCheckpoint.scope_id == chapter_id,
                    RuntimeCheckpoint.checkpoint_key == "review_controller.deadlock_recovery",
                )
                .one()
            )

            self.assertEqual(
                review_session.status_detail_json["runtime_v2"]["last_deadlock_recovery"]["repair_blockage"]["state"],
                "backoff_blocked",
            )
            self.assertEqual(
                checkpoint.checkpoint_json["recovery"]["repair_blockage"]["state"],
                "backoff_blocked",
            )

    def test_req_mx_01_review_deadlock_self_heal_honors_run_level_executor_override(self) -> None:
        run_id, chapter_id = self._seed_review_deadlock_scope(
            preferred_execution_mode="agent_backed",
            preferred_executor_hint="python_subprocess_repair_executor",
            preferred_executor_contract_version=1,
        )

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=self.bundle_root,
            translation_worker=None,
        )
        with self.session_factory() as session:
            execution = executor._run_execution_service(session)
            claimed = execution.claim_next_work_item(
                run_id=run_id,
                stage=WorkItemStage.REPAIR,
                worker_name="test.repair",
                worker_instance_id="repair-worker-1",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            session.commit()

        executor._execute_repair_work_item(run_id, claimed)

        with self.session_factory() as session:
            incident = session.query(RuntimeIncident).filter(RuntimeIncident.run_id == run_id).one()
            proposal = session.query(RuntimePatchProposal).filter(RuntimePatchProposal.incident_id == incident.id).one()
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

            self.assertIsNotNone(repair_work_item)
            assert repair_work_item is not None
            self.assertEqual(incident.incident_kind, RuntimeIncidentKind.REVIEW_DEADLOCK)
            self.assertEqual(incident.scope_id, chapter_id)
            self.assertEqual(proposal.status.value, "published")
            self.assertEqual(repair_work_item.input_version_bundle_json["execution_mode"], "agent_backed")
            self.assertEqual(
                repair_work_item.input_version_bundle_json["executor_hint"],
                "python_subprocess_repair_executor",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["execution_mode"],
                "agent_backed",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_executor_execution_mode"],
                "agent_backed",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_executor_hint"],
                "python_subprocess_repair_executor",
            )

    def test_req_mx_01_review_deadlock_self_heal_can_execute_through_contract_agent_executor_override(self) -> None:
        run_id, chapter_id = self._seed_review_deadlock_scope(
            preferred_execution_mode="agent_backed",
            preferred_executor_hint="python_contract_agent_repair_executor",
            preferred_executor_contract_version=1,
        )

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=self.bundle_root,
            translation_worker=None,
        )
        with self.session_factory() as session:
            execution = executor._run_execution_service(session)
            claimed = execution.claim_next_work_item(
                run_id=run_id,
                stage=WorkItemStage.REPAIR,
                worker_name="test.repair",
                worker_instance_id="repair-worker-1",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            session.commit()

        executor._execute_repair_work_item(run_id, claimed)

        with self.session_factory() as session:
            incident = session.query(RuntimeIncident).filter(RuntimeIncident.run_id == run_id).one()
            proposal = session.query(RuntimePatchProposal).filter(RuntimePatchProposal.incident_id == incident.id).one()
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

            self.assertIsNotNone(repair_work_item)
            assert repair_work_item is not None
            self.assertEqual(incident.incident_kind, RuntimeIncidentKind.REVIEW_DEADLOCK)
            self.assertEqual(incident.scope_id, chapter_id)
            self.assertEqual(proposal.status.value, "published")
            self.assertEqual(repair_work_item.input_version_bundle_json["execution_mode"], "agent_backed")
            self.assertEqual(
                repair_work_item.input_version_bundle_json["executor_hint"],
                "python_contract_agent_repair_executor",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["execution_mode"],
                "agent_backed",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_executor_execution_mode"],
                "agent_backed",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_executor_hint"],
                "python_contract_agent_repair_executor",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_adapter_name"],
                "review_deadlock_remote_contract_repair_agent",
            )

    def test_req_mx_01_review_deadlock_self_heal_can_execute_through_configured_command_transport_override(self) -> None:
        run_id, chapter_id = self._seed_review_deadlock_scope(
            preferred_transport_hint="configured_command_repair_transport",
            preferred_transport_contract_version=1,
        )

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=self.bundle_root,
            translation_worker=None,
        )
        with self.session_factory() as session:
            execution = executor._run_execution_service(session)
            claimed = execution.claim_next_work_item(
                run_id=run_id,
                stage=WorkItemStage.REPAIR,
                worker_name="test.repair",
                worker_instance_id="repair-worker-1",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            session.commit()

        with mock.patch(
            "book_agent.services.runtime_repair_transport.get_settings",
            return_value=Settings(
                database_url=self.database_url,
                runtime_bundle_root=self.bundle_root,
                runtime_repair_transport_command=(
                    f"{sys.executable} -m book_agent.tools.runtime_repair_runner"
                ),
            ),
        ):
            executor._execute_repair_work_item(run_id, claimed)

        with self.session_factory() as session:
            incident = session.query(RuntimeIncident).filter(RuntimeIncident.run_id == run_id).one()
            proposal = session.query(RuntimePatchProposal).filter(RuntimePatchProposal.incident_id == incident.id).one()
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

            self.assertIsNotNone(repair_work_item)
            assert repair_work_item is not None
            self.assertEqual(incident.incident_kind, RuntimeIncidentKind.REVIEW_DEADLOCK)
            self.assertEqual(incident.scope_id, chapter_id)
            self.assertEqual(proposal.status.value, "published")
            self.assertEqual(
                repair_work_item.input_version_bundle_json["transport_hint"],
                "configured_command_repair_transport",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["transport_hint"],
                "configured_command_repair_transport",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_transport_hint"],
                "configured_command_repair_transport",
            )


if __name__ == "__main__":
    unittest.main()
