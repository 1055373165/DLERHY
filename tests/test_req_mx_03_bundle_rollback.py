import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from book_agent.app.runtime.controllers.incident_controller import IncidentController
from book_agent.core.config import Settings
from book_agent.domain.enums import (
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    JobScopeType,
    RuntimeBundleRevisionStatus,
    RuntimeIncidentKind,
    RuntimeIncidentStatus,
    RuntimePatchProposalStatus,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import (
    DocumentRun,
    RuntimeBundleRevision,
    RuntimeIncident,
    RuntimePatchProposal,
    WorkItem,
)
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_control import RunControlService
from book_agent.services.runtime_bundle import RuntimeBundleService
from book_agent.services.runtime_patch_validation import RuntimePatchValidationService


class ReqMx03BundleRollbackTests(unittest.TestCase):
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

    def test_req_mx_03_bad_bundle_rollout_auto_rolls_back_to_stable_revision(self) -> None:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"req-mx-03-{uuid4()}",
                source_path="/tmp/req-mx-03.epub",
                title="REQ-MX-03",
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
                requested_by="req-mx-03-test",
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
                input_version_bundle_json={"packet_id": packet_scope_id},
                output_artifact_refs_json={},
                error_detail_json={},
            )
            session.add(work_item)
            session.flush()

            bundle_service = RuntimeBundleService(session, settings=self._settings())
            stable_bundle = bundle_service.publish_bundle(
                revision_name="bundle-stable",
                manifest_json={"code": {"entrypoint": "book_agent"}, "config": {"mode": "dev"}},
                rollout_scope_json={"mode": "dev"},
            )
            bundle_service.record_canary_verdict(
                stable_bundle.revision.id,
                verdict="passed",
                report_json={"passed": True, "lane": "canary"},
            )
            bundle_service.activate_bundle(stable_bundle.revision.id)
            run.runtime_bundle_revision_id = stable_bundle.revision.id
            session.add(run)

            incident = RuntimeIncident(
                run_id=run.id,
                scope_type=JobScopeType.PACKET,
                scope_id=packet_scope_id,
                incident_kind=RuntimeIncidentKind.RUNTIME_DEFECT,
                fingerprint=f"req-mx-03-runtime-defect:{uuid4()}",
                source_type="epub",
                selected_route="runtime",
                runtime_bundle_revision_id=stable_bundle.revision.id,
                status=RuntimeIncidentStatus.OPEN,
                failure_count=1,
                route_evidence_json={"scope_id": packet_scope_id},
                latest_error_json={"error_code": "runtime_defect"},
                bundle_json={},
                status_detail_json={},
            )
            session.add(incident)
            session.flush()

            controller = IncidentController(
                session=session,
                bundle_service=bundle_service,
                validation_service=RuntimePatchValidationService(session),
            )
            proposal = controller.open_patch_proposal(
                incident_id=incident.id,
                patch_surface="runtime_bundle",
                diff_manifest_json={"files": ["src/book_agent/services/bundle_guard.py"]},
            )
            controller.validate_patch_proposal(
                proposal_id=proposal.id,
                passed=True,
                report_json={"command": "uv run pytest tests/test_req_mx_03_bundle_rollback.py"},
            )
            published_bundle = controller.publish_validated_patch(
                proposal_id=proposal.id,
                revision_name="bundle-bad-canary",
                manifest_json={"code": {"entrypoint": "book_agent"}, "config": {"mode": "dev", "candidate": True}},
                rollout_scope_json={"mode": "dev", "lane": "canary"},
                canary_report_json={"canary_verdict": "failed", "signal": "canary_regression"},
            )
            session.commit()

            proposal_id = proposal.id
            incident_id = incident.id
            run_id = run.id
            work_item_id = work_item.id

        with self.session_factory() as session:
            bundle_service = RuntimeBundleService(session, settings=self._settings())
            proposal = session.get(RuntimePatchProposal, proposal_id)
            incident = session.get(RuntimeIncident, incident_id)
            run = session.get(DocumentRun, run_id)
            work_item = session.get(WorkItem, work_item_id)
            bad_bundle = session.get(RuntimeBundleRevision, proposal.published_bundle_revision_id)
            active_bundle = bundle_service.lookup_active_bundle()
            summary = RunControlService(RunControlRepository(session)).get_run_summary(run_id)

            self.assertIsNotNone(proposal)
            self.assertIsNotNone(incident)
            self.assertIsNotNone(run)
            self.assertIsNotNone(work_item)
            self.assertIsNotNone(bad_bundle)
            assert proposal is not None
            assert incident is not None
            assert run is not None
            assert work_item is not None
            assert bad_bundle is not None

            self.assertEqual(published_bundle.revision.id, proposal.published_bundle_revision_id)
            self.assertEqual(published_bundle.revision.id, bad_bundle.id)
            self.assertEqual(proposal.status, RuntimePatchProposalStatus.ROLLED_BACK)
            self.assertTrue(proposal.status_detail_json["bundle_guard"]["rollback_performed"])
            self.assertEqual(
                proposal.status_detail_json["bundle_guard"]["effective_revision_id"],
                stable_bundle.revision.id,
            )
            self.assertEqual(incident.status, RuntimeIncidentStatus.FROZEN)
            self.assertEqual(incident.runtime_bundle_revision_id, stable_bundle.revision.id)
            self.assertEqual(incident.bundle_json["published_bundle_revision_id"], bad_bundle.id)
            self.assertEqual(incident.bundle_json["active_bundle_revision_id"], stable_bundle.revision.id)
            self.assertEqual(run.runtime_bundle_revision_id, stable_bundle.revision.id)
            self.assertEqual(work_item.runtime_bundle_revision_id, stable_bundle.revision.id)
            self.assertEqual(bad_bundle.status, RuntimeBundleRevisionStatus.ROLLED_BACK)
            self.assertEqual(bad_bundle.rollback_target_revision_id, stable_bundle.revision.id)
            self.assertEqual(bad_bundle.freeze_reason, "canary_regression")
            self.assertEqual(active_bundle.revision.id, stable_bundle.revision.id)
            runtime_v2 = summary.status_detail_json["runtime_v2"]
            self.assertEqual(runtime_v2["active_runtime_bundle_revision_id"], stable_bundle.revision.id)
            self.assertEqual(runtime_v2["runtime_bundle_revision_id"], stable_bundle.revision.id)
