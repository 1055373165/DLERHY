import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from book_agent.app.runtime.controllers.budget_controller import BudgetController
from book_agent.app.runtime.controllers.incident_controller import IncidentController
from book_agent.core.config import Settings
from book_agent.domain.enums import (
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    JobScopeType,
    RuntimeIncidentKind,
    RuntimeIncidentStatus,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun, RunBudget, RuntimeIncident, RuntimePatchProposal, WorkItem
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.runtime_bundle import RuntimeBundleService
from book_agent.services.runtime_patch_validation import RuntimePatchValidationService


class IncidentControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.bundle_root = Path(self.tempdir.name) / "runtime-bundles"
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _settings(self) -> Settings:
        return Settings(
            database_url="sqlite+pysqlite:///:memory:",
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


class BudgetControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
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
