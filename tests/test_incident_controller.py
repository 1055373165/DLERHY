import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from book_agent.app.runtime.controllers.budget_controller import BudgetController
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
    RuntimeBundleRevision,
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
from book_agent.services.runtime_repair_planner import RuntimeRepairPlannerService


class IncidentControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.bundle_root = Path(self.tempdir.name) / "runtime-bundles"
        self.database_path = Path(self.tempdir.name) / "incident-controller.sqlite"
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

    def _seed_run_incident_and_work_item(self) -> tuple[str, str, str, str]:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"incident-controller-{uuid4()}",
                source_path="/tmp/incident-controller.epub",
                title="Incident Controller",
                author="Tester",
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
                requested_by="tester",
                priority=100,
                status_detail_json={},
            )
            session.add(run)
            session.flush()

            packet_scope_id = str(uuid4())
            work_item = WorkItem(
                run_id=run.id,
                stage=WorkItemStage.REPAIR,
                scope_type=WorkItemScopeType.PACKET,
                scope_id=packet_scope_id,
                priority=100,
                status=WorkItemStatus.RETRYABLE_FAILED,
                input_version_bundle_json={},
                output_artifact_refs_json={},
                error_detail_json={},
            )
            session.add(work_item)
            session.flush()

            incident = RuntimeIncident(
                run_id=run.id,
                scope_type=JobScopeType.PACKET,
                scope_id=packet_scope_id,
                incident_kind=RuntimeIncidentKind.RUNTIME_DEFECT,
                fingerprint=f"incident-controller:{uuid4()}",
                source_type="epub",
                selected_route="runtime",
                status=RuntimeIncidentStatus.OPEN,
                failure_count=1,
                route_evidence_json={"scope_id": packet_scope_id},
                latest_error_json={"error_code": "runtime_defect"},
                bundle_json={},
                status_detail_json={},
            )
            session.add(incident)
            session.commit()
            return run.id, incident.id, work_item.id, packet_scope_id

    def test_open_validate_publish_and_bind_revision(self) -> None:
        run_id, incident_id, work_item_id, _packet_scope_id = self._seed_run_incident_and_work_item()

        with self.session_factory() as session:
            bundle_service = RuntimeBundleService(session, settings=self._settings())
            validation_service = RuntimePatchValidationService(session)
            controller = IncidentController(
                session=session,
                bundle_service=bundle_service,
                validation_service=validation_service,
            )

            proposal = controller.open_patch_proposal(
                incident_id=incident_id,
                patch_surface="runtime_bundle",
                diff_manifest_json={"files": ["src/book_agent/services/runtime_bundle.py"]},
            )
            self.assertEqual(proposal.patch_surface, "runtime_bundle")

            validation_result = controller.validate_patch_proposal(
                proposal_id=proposal.id,
                passed=True,
                report_json={"command": "uv run pytest tests/test_incident_controller.py"},
            )
            self.assertTrue(validation_result.passed)

            bundle_record = controller.publish_validated_patch(
                proposal_id=proposal.id,
                revision_name="bundle-v3",
                manifest_json={"code": {"entrypoint": "book_agent"}, "config": {"mode": "dev"}},
            )
            session.commit()

        active_pointer = self.bundle_root / "active.json"
        self.assertTrue(active_pointer.exists())

        with self.session_factory() as session:
            proposal = session.get(RuntimePatchProposal, proposal.id)
            incident = session.get(RuntimeIncident, incident_id)
            run = session.get(DocumentRun, run_id)
            work_item = session.get(WorkItem, work_item_id)

            self.assertIsNotNone(proposal)
            self.assertIsNotNone(incident)
            self.assertIsNotNone(run)
            self.assertIsNotNone(work_item)
            assert proposal is not None and incident is not None and run is not None and work_item is not None

            self.assertEqual(proposal.status.value, "published")
            self.assertEqual(proposal.published_bundle_revision_id, bundle_record.revision.id)
            self.assertEqual(incident.status.value, "published")
            self.assertEqual(run.runtime_bundle_revision_id, bundle_record.revision.id)
            self.assertEqual(work_item.runtime_bundle_revision_id, bundle_record.revision.id)

    def test_repair_dispatch_lineage_can_be_claimed_executed_and_published(self) -> None:
        run_id, incident_id, work_item_id, packet_scope_id = self._seed_run_incident_and_work_item()

        with self.session_factory() as session:
            bundle_service = RuntimeBundleService(session, settings=self._settings())
            validation_service = RuntimePatchValidationService(session)
            controller = IncidentController(
                session=session,
                bundle_service=bundle_service,
                validation_service=validation_service,
            )
            repair_plan = RuntimeRepairPlannerService().plan_export_misrouting_repair(
                scope_id=packet_scope_id,
                export_type="rebuilt_pdf",
                corrected_route="epub.rebuilt_pdf_via_html",
                route_candidates=["epub.rebuilt_pdf_via_html"],
                route_evidence_json={"route_fingerprint": f"dispatch-{packet_scope_id}", "source_type": "epub"},
            )
            proposal = controller.open_patch_proposal(
                incident_id=incident_id,
                patch_surface=repair_plan.patch_surface,
                diff_manifest_json=repair_plan.diff_manifest_json,
                status_detail_json={"repair_plan": repair_plan.handoff_json},
            )

            claimed = controller.claim_repair_dispatch(
                proposal_id=proposal.id,
                worker_name="runtime.repair-agent",
                worker_instance_id="repair-worker-1",
            )
            self.assertEqual(claimed["status"], "claimed")
            self.assertEqual(claimed["attempt_count"], 1)

            executed = controller.record_repair_dispatch_execution(
                proposal_id=proposal.id,
                succeeded=True,
                result_json={"changed_files": repair_plan.handoff_json["owned_files"]},
            )
            self.assertEqual(executed["status"], "executed")
            self.assertEqual(executed["execution_history"][0]["status"], "succeeded")

            validation_result = controller.validate_patch_proposal(
                proposal_id=proposal.id,
                passed=True,
                report_json=repair_plan.validation_report_json,
            )
            self.assertTrue(validation_result.passed)

            bundle_record = controller.publish_validated_patch(
                proposal_id=proposal.id,
                revision_name=repair_plan.revision_name,
                manifest_json=repair_plan.manifest_json,
                rollout_scope_json=repair_plan.rollout_scope_json,
            )
            session.commit()

        with self.session_factory() as session:
            proposal = session.get(RuntimePatchProposal, proposal.id)
            incident = session.get(RuntimeIncident, incident_id)
            run = session.get(DocumentRun, run_id)
            work_item = session.get(WorkItem, work_item_id)

            self.assertIsNotNone(proposal)
            self.assertIsNotNone(incident)
            self.assertIsNotNone(run)
            self.assertIsNotNone(work_item)
            assert proposal is not None and incident is not None and run is not None and work_item is not None

            self.assertEqual(proposal.status.value, "published")
            self.assertEqual(proposal.status_detail_json["repair_dispatch"]["status"], "executed")
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)
            self.assertIsNotNone(repair_work_item)
            assert repair_work_item is not None
            self.assertEqual(repair_work_item.stage, WorkItemStage.REPAIR)
            self.assertEqual(repair_work_item.scope_type, WorkItemScopeType.ISSUE_ACTION)
            self.assertEqual(repair_work_item.scope_id, proposal.id)
            self.assertEqual(repair_work_item.status, WorkItemStatus.SUCCEEDED)
            self.assertEqual(repair_work_item.input_version_bundle_json["proposal_id"], proposal.id)
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["validation"]["status"],
                "passed",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["bundle_publication"]["published_revision_id"],
                bundle_record.revision.id,
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["status"],
                "succeeded",
            )
            self.assertEqual(
                incident.status_detail_json["latest_patch_proposal"]["repair_dispatch"]["proposal_id"],
                proposal.id,
            )
            self.assertEqual(run.runtime_bundle_revision_id, bundle_record.revision.id)
            self.assertEqual(work_item.runtime_bundle_revision_id, bundle_record.revision.id)

    def test_resume_repair_dispatch_requeues_manual_escalation_with_resume_lineage(self) -> None:
        _run_id, incident_id, _work_item_id, packet_scope_id = self._seed_run_incident_and_work_item()

        with self.session_factory() as session:
            controller = IncidentController(session=session)
            repair_plan = RuntimeRepairPlannerService().plan_export_misrouting_repair(
                scope_id=packet_scope_id,
                export_type="rebuilt_pdf",
                corrected_route="epub.rebuilt_pdf_via_html",
                route_candidates=["epub.rebuilt_pdf_via_html"],
                route_evidence_json={"route_fingerprint": f"resume-{packet_scope_id}", "source_type": "epub"},
            )
            proposal = controller.open_patch_proposal(
                incident_id=incident_id,
                patch_surface=repair_plan.patch_surface,
                diff_manifest_json=repair_plan.diff_manifest_json,
                status_detail_json={"repair_plan": repair_plan.handoff_json},
            )
            controller.claim_repair_dispatch(
                proposal_id=proposal.id,
                worker_name="runtime.repair-agent",
                worker_instance_id="repair-worker-1",
            )
            executed = controller.record_repair_dispatch_execution(
                proposal_id=proposal.id,
                succeeded=False,
                result_json={
                    "repair_agent_decision": "manual_escalation_required",
                    "repair_agent_decision_reason": "requires_operator_review",
                },
            )
            self.assertEqual(executed["status"], "manual_escalation_required")

            resumed = controller.resume_repair_dispatch(
                proposal_id=proposal.id,
                resumed_by="ops-user",
                note="approved override",
            )
            self.assertEqual(resumed["status"], "pending")
            self.assertEqual(resumed["next_action"], "claim_repair_lane")
            self.assertEqual(resumed["resume_count"], 1)
            self.assertEqual(resumed["resume_history"][0]["previous_status"], "manual_escalation_required")
            self.assertEqual(resumed["resume_history"][0]["resumed_by"], "ops-user")
            session.commit()

        with self.session_factory() as session:
            proposal = session.get(RuntimePatchProposal, proposal.id)
            self.assertIsNotNone(proposal)
            assert proposal is not None
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)
            self.assertIsNotNone(repair_work_item)
            assert repair_work_item is not None
            self.assertEqual(repair_work_item.status, WorkItemStatus.PENDING)
            self.assertEqual(repair_work_item.attempt, 2)

        with self.session_factory() as session:
            controller = IncidentController(session=session)
            reclaimed = controller.claim_repair_dispatch(
                proposal_id=proposal.id,
                worker_name="runtime.repair-agent",
                worker_instance_id="repair-worker-2",
            )
            self.assertEqual(reclaimed["status"], "claimed")
            self.assertEqual(reclaimed["attempt_count"], 2)

    def test_resume_repair_dispatch_can_override_executor_routing(self) -> None:
        _run_id, incident_id, _work_item_id, packet_scope_id = self._seed_run_incident_and_work_item()

        with self.session_factory() as session:
            controller = IncidentController(session=session)
            repair_plan = RuntimeRepairPlannerService().plan_review_deadlock_repair(
                chapter_id=packet_scope_id,
                chapter_run_id=str(uuid4()),
                review_session_id=str(uuid4()),
                reason_code="review_deadlock",
            )
            proposal = controller.open_patch_proposal(
                incident_id=incident_id,
                patch_surface=repair_plan.patch_surface,
                diff_manifest_json=repair_plan.diff_manifest_json,
                status_detail_json={"repair_plan": repair_plan.handoff_json},
            )
            controller.claim_repair_dispatch(
                proposal_id=proposal.id,
                worker_name="runtime.repair-agent",
                worker_instance_id="repair-worker-1",
            )
            controller.record_repair_dispatch_execution(
                proposal_id=proposal.id,
                succeeded=False,
                result_json={
                    "repair_agent_decision": "manual_escalation_required",
                    "repair_agent_decision_reason": "switch_executor_route",
                },
            )
            resumed = controller.resume_repair_dispatch(
                proposal_id=proposal.id,
                resumed_by="ops-user",
                note="switch to agent-backed executor",
                dispatch_overrides={
                    "execution_mode": "agent_backed",
                    "executor_hint": "python_subprocess_repair_executor",
                    "executor_contract_version": 1,
                    "validation_command": "uv run pytest tests/test_req_mx_01_review_deadlock_self_heal.py",
                },
            )
            self.assertEqual(resumed["status"], "pending")
            self.assertEqual(resumed["execution_mode"], "agent_backed")
            self.assertEqual(resumed["executor_hint"], "python_subprocess_repair_executor")
            self.assertEqual(
                resumed["last_resume_overrides"]["executor_hint"],
                "python_subprocess_repair_executor",
            )
            session.commit()

        with self.session_factory() as session:
            proposal = session.get(RuntimePatchProposal, proposal.id)
            self.assertIsNotNone(proposal)
            assert proposal is not None
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)
            self.assertIsNotNone(repair_work_item)
            assert repair_work_item is not None
            self.assertEqual(repair_work_item.status, WorkItemStatus.PENDING)
            self.assertEqual(repair_work_item.input_version_bundle_json["execution_mode"], "agent_backed")
            self.assertEqual(
                repair_work_item.input_version_bundle_json["executor_hint"],
                "python_subprocess_repair_executor",
            )
            self.assertEqual(
                repair_work_item.input_version_bundle_json["validation_command"],
                "uv run pytest tests/test_req_mx_01_review_deadlock_self_heal.py",
            )

    def test_publish_validated_patch_rolls_back_failed_dev_canary_and_rebinds_scope(self) -> None:
        run_id, incident_id, work_item_id, _packet_scope_id = self._seed_run_incident_and_work_item()

        with self.session_factory() as session:
            bundle_service = RuntimeBundleService(session, settings=self._settings())
            stable = bundle_service.publish_bundle(
                revision_name="bundle-stable",
                manifest_json={"code": {"entrypoint": "book_agent"}, "config": {"mode": "dev"}},
                rollout_scope_json={"mode": "dev"},
            )
            bundle_service.record_canary_verdict(stable.revision.id, verdict="passed", report_json={"passed": True})
            bundle_service.activate_bundle(stable.revision.id)

            incident = session.get(RuntimeIncident, incident_id)
            assert incident is not None
            incident.runtime_bundle_revision_id = stable.revision.id
            session.add(incident)
            session.commit()

        with self.session_factory() as session:
            bundle_service = RuntimeBundleService(session, settings=self._settings())
            validation_service = RuntimePatchValidationService(session)
            controller = IncidentController(
                session=session,
                bundle_service=bundle_service,
                validation_service=validation_service,
            )

            proposal = controller.open_patch_proposal(
                incident_id=incident_id,
                patch_surface="runtime_bundle",
                diff_manifest_json={"files": ["src/book_agent/services/bundle_guard.py"]},
            )
            controller.validate_patch_proposal(
                proposal_id=proposal.id,
                passed=True,
                report_json={"command": "uv run pytest tests/test_incident_controller.py"},
            )
            published_bundle = controller.publish_validated_patch(
                proposal_id=proposal.id,
                revision_name="bundle-bad-canary",
                manifest_json={"code": {"entrypoint": "book_agent"}, "config": {"mode": "dev", "candidate": True}},
                rollout_scope_json={"mode": "dev", "lane": "canary"},
                canary_report_json={"canary_verdict": "failed", "signal": "export_misrouting"},
            )
            session.commit()

        with self.session_factory() as session:
            proposal = session.get(RuntimePatchProposal, proposal.id)
            incident = session.get(RuntimeIncident, incident_id)
            run = session.get(DocumentRun, run_id)
            work_item = session.get(WorkItem, work_item_id)
            rolled_back_revision = session.get(RuntimeBundleRevision, published_bundle.revision.id)

            self.assertIsNotNone(proposal)
            self.assertIsNotNone(incident)
            self.assertIsNotNone(run)
            self.assertIsNotNone(work_item)
            self.assertIsNotNone(rolled_back_revision)
            assert proposal is not None
            assert incident is not None
            assert run is not None
            assert work_item is not None
            assert rolled_back_revision is not None

            self.assertEqual(proposal.status.value, "rolled_back")
            self.assertEqual(proposal.published_bundle_revision_id, published_bundle.revision.id)
            self.assertTrue(proposal.status_detail_json["bundle_guard"]["rollback_performed"])
            self.assertEqual(proposal.status_detail_json["bundle_guard"]["effective_revision_id"], stable.revision.id)
            self.assertEqual(incident.status.value, "frozen")
            self.assertEqual(incident.runtime_bundle_revision_id, stable.revision.id)
            self.assertEqual(run.runtime_bundle_revision_id, stable.revision.id)
            self.assertEqual(work_item.runtime_bundle_revision_id, stable.revision.id)
            self.assertEqual(rolled_back_revision.status.value, "rolled_back")
            self.assertEqual(rolled_back_revision.rollback_target_revision_id, stable.revision.id)
            self.assertEqual(rolled_back_revision.freeze_reason, "export_misrouting")

    def test_review_controller_recovers_repeated_deadlock_with_minimal_chapter_replay(self) -> None:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"review-deadlock-{uuid4()}",
                source_path="/tmp/review-deadlock.epub",
                title="Review Deadlock",
                author="Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()

            chapter = Chapter(
                document_id=document.id,
                ordinal=1,
                title_src="Chapter 1",
                status=ChapterStatus.PACKET_BUILT,
                metadata_json={},
            )
            session.add(chapter)
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

            chapter_run = ChapterRun(
                run_id=run.id,
                document_id=document.id,
                chapter_id=chapter.id,
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
                    "chapter_id": chapter.id,
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
                scope_id=chapter.id,
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
                    "chapter_id": chapter.id,
                    "chapter_run_id": chapter_run.id,
                    "review_session_id": review_session.id,
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

            controller.reconcile_review_session(chapter_run_id=chapter_run.id)
            controller.reconcile_review_session(chapter_run_id=chapter_run.id)
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
            incident = session.query(RuntimeIncident).filter(RuntimeIncident.run_id == run.id).one()
            proposal = session.query(RuntimePatchProposal).filter(RuntimePatchProposal.incident_id == incident.id).one()
            checkpoint = session.query(RuntimeCheckpoint).filter(
                RuntimeCheckpoint.run_id == run.id,
                RuntimeCheckpoint.scope_type == JobScopeType.CHAPTER,
                RuntimeCheckpoint.scope_id == chapter.id,
                RuntimeCheckpoint.checkpoint_key == "review_controller.deadlock_recovery",
            ).one()
            persisted_review_session = RuntimeResourcesRepository(session).get_review_session(review_session.id)
            persisted_work_item = session.get(WorkItem, work_item.id)
            persisted_run = session.get(DocumentRun, run.id)
            persisted_chapter_run = session.get(ChapterRun, chapter_run.id)

        self.assertEqual(incident.incident_kind, RuntimeIncidentKind.REVIEW_DEADLOCK)
        self.assertEqual(incident.status, RuntimeIncidentStatus.PUBLISHED)
        self.assertEqual(incident.failure_count, 1)
        self.assertEqual(proposal.status.value, "published")
        self.assertIsNotNone(persisted_run)
        self.assertIsNotNone(persisted_chapter_run)
        assert persisted_run is not None and persisted_chapter_run is not None
        self.assertEqual(persisted_run.runtime_bundle_revision_id, proposal.published_bundle_revision_id)
        self.assertEqual(persisted_work_item.runtime_bundle_revision_id, proposal.published_bundle_revision_id)
        self.assertIn(work_item.id, proposal.status_detail_json["bound_work_item_ids"])
        self.assertEqual(
            persisted_review_session.status_detail_json["runtime_v2"]["recovery_decision"]["fingerprint_occurrences"],
            2,
        )
        self.assertEqual(
            persisted_review_session.status_detail_json["runtime_v2"]["last_deadlock_recovery"]["replay_work_item_ids"],
            [work_item.id],
        )
        self.assertEqual(
            checkpoint.checkpoint_json["recovery"]["replay_work_item_ids"],
            [work_item.id],
        )
        self.assertEqual(
            persisted_review_session.status_detail_json["runtime_v2"]["last_deadlock_recovery"]["bundle_revision_id"],
            proposal.published_bundle_revision_id,
        )
        self.assertEqual(
            persisted_chapter_run.conditions_json["recovered_lineage"][0]["incident_id"],
            incident.id,
        )
        self.assertEqual(proposal.status_detail_json["repair_plan"]["incident_kind"], "review_deadlock")
        self.assertEqual(
            proposal.status_detail_json["repair_plan"]["replay"]["scope_id"],
            chapter.id,
        )
        self.assertEqual(
            proposal.status_detail_json["repair_dispatch"]["status"],
            "executed",
        )
        self.assertEqual(
            proposal.status_detail_json["repair_dispatch"]["execution_mode"],
            "transport_backed",
        )
        self.assertEqual(
            proposal.status_detail_json["repair_dispatch"]["transport_hint"],
            "python_subprocess_repair_transport",
        )
        repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
        repair_work_item = session.get(WorkItem, repair_work_item_id)
        self.assertIsNotNone(repair_work_item)
        assert repair_work_item is not None
        self.assertEqual(repair_work_item.stage, WorkItemStage.REPAIR)
        self.assertEqual(repair_work_item.scope_type, WorkItemScopeType.ISSUE_ACTION)
        self.assertEqual(repair_work_item.scope_id, proposal.id)
        self.assertEqual(repair_work_item.status, WorkItemStatus.SUCCEEDED)
        self.assertEqual(
            repair_work_item.input_version_bundle_json["execution_mode"],
            "transport_backed",
        )
        self.assertEqual(
            repair_work_item.input_version_bundle_json["executor_hint"],
            "python_transport_repair_executor",
        )
        self.assertEqual(
            repair_work_item.input_version_bundle_json["transport_hint"],
            "python_subprocess_repair_transport",
        )
        self.assertEqual(
            proposal.status_detail_json["repair_dispatch"]["validation"]["status"],
            "passed",
        )
        self.assertEqual(
            incident.status_detail_json["latest_patch_proposal"]["repair_plan"]["replay"]["boundary"],
            "review_session",
        )
        self.assertEqual(
            incident.status_detail_json["repair_dispatch"]["last_result"]["status"],
            "succeeded",
        )
        self.assertEqual(
            incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_executor_execution_mode"],
            "transport_backed",
        )
        self.assertEqual(
            incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_transport_hint"],
            "python_subprocess_repair_transport",
        )


class BudgetControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed_run_with_budget(self) -> str:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"budget-controller-{uuid4()}",
                source_path="/tmp/budget-controller.epub",
                title="Budget Controller",
                author="Tester",
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
                requested_by="tester",
                priority=100,
                status_detail_json={
                    "runtime_v2": {
                        "allowed_patch_surfaces": ["runtime_bundle", "incident_triage"],
                    }
                },
            )
            session.add(run)
            session.flush()

            budget = RunBudget(
                run_id=run.id,
                max_auto_followup_attempts=2,
            )
            session.add(budget)
            session.commit()
            return run.id

    def test_allowlist_and_attempt_cap(self) -> None:
        run_id = self._seed_run_with_budget()

        with self.session_factory() as session:
            controller = BudgetController(session=session)

            first = controller.evaluate_auto_patch(run_id=run_id, patch_surface="runtime_bundle")
            self.assertTrue(first.allowed)

            recorded_first = controller.record_auto_patch_attempt(run_id=run_id, patch_surface="runtime_bundle")
            self.assertEqual(recorded_first.current_auto_patch_attempt_count, 1)

            recorded_second = controller.record_auto_patch_attempt(run_id=run_id, patch_surface="runtime_bundle")
            self.assertEqual(recorded_second.current_auto_patch_attempt_count, 2)

            exhausted = controller.evaluate_auto_patch(run_id=run_id, patch_surface="runtime_bundle")
            self.assertFalse(exhausted.allowed)
            self.assertEqual(exhausted.reason, "max_auto_patch_attempts_exhausted")

            blocked_surface = controller.evaluate_auto_patch(run_id=run_id, patch_surface="export_routing")
            self.assertFalse(blocked_surface.allowed)
            self.assertEqual(blocked_surface.reason, "patch_surface_not_allowlisted")
