import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from book_agent.app.runtime.controllers.export_controller import ExportController
from book_agent.core.config import Settings
from book_agent.domain.enums import (
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    ExportType,
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
    RunBudget,
    RuntimeIncident,
    RuntimePatchProposal,
    WorkItem,
)
from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.export_routing import ExportRoutingError, ExportRoutingService
from book_agent.services.runtime_bundle import RuntimeBundleService


class ExportControllerTests(unittest.TestCase):
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

    def _seed_misrouted_export_scope(self) -> tuple[str, str, str, str]:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"export-controller-{uuid4()}",
                source_path="/tmp/export-controller.epub",
                title="Export Controller",
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
                runtime_bundle_revision_id=None,
                status_detail_json={
                    "runtime_v2": {
                        "allowed_patch_surfaces": ["runtime_bundle"],
                        "auto_patch_attempt_count": 0,
                    }
                },
            )
            session.add(run)
            session.flush()

            scope_id = str(uuid4())
            work_item = WorkItem(
                run_id=run.id,
                stage=WorkItemStage.EXPORT,
                scope_type=WorkItemScopeType.EXPORT,
                scope_id=scope_id,
                priority=100,
                status=WorkItemStatus.RETRYABLE_FAILED,
                input_version_bundle_json={
                    "document_id": document.id,
                    "export_type": ExportType.REBUILT_PDF.value,
                },
                output_artifact_refs_json={},
                error_detail_json={},
            )
            session.add(work_item)
            session.flush()

            budget = RunBudget(
                run_id=run.id,
                max_auto_followup_attempts=2,
            )
            session.add(budget)

            bundle_service = RuntimeBundleService(session, settings=self._settings())
            record = bundle_service.publish_bundle(
                revision_name="bundle-misrouted",
                manifest_json={
                    "code": {"entrypoint": "book_agent"},
                    "config": {"mode": "dev"},
                    "routing_policy": {
                        "export_routes": {
                            "rebuilt_pdf": {
                                "selected_route": "pdf.direct",
                                "allowed_routes": ["pdf.direct"],
                                "route_candidates": ["pdf.direct"],
                                "source_types": ["epub"],
                            }
                        }
                    },
                },
                rollout_scope_json={"mode": "dev"},
            )
            bundle_service.activate_bundle(record.revision.id)
            session.commit()
            return run.id, document.id, work_item.id, scope_id

    def test_recover_export_misrouting_schedules_repair_then_executor_publishes_bundle_and_rebinds_scope(self) -> None:
        run_id, document_id, work_item_id, scope_id = self._seed_misrouted_export_scope()

        with self.session_factory() as session:
            bundle_service = RuntimeBundleService(session, settings=self._settings())
            routing_service = ExportRoutingService(runtime_bundle_service=bundle_service)

            document = session.get(Document, document_id)
            self.assertIsNotNone(document)
            assert document is not None

            with self.assertRaises(ExportRoutingError) as exc_info:
                routing_service.resolve_document_route(
                    document=document,
                    export_type=ExportType.REBUILT_PDF,
                )

            controller = ExportController(session=session)
            recovery = controller.recover_export_misrouting(
                run_id=run_id,
                work_item_id=work_item_id,
                scope_id=scope_id,
                source_type=document.source_type.value,
                selected_route=exc_info.exception.selected_route,
                runtime_bundle_revision_id=exc_info.exception.runtime_bundle_revision_id,
                route_candidates=exc_info.exception.expected_route_candidates,
                route_evidence_json=exc_info.exception.route_evidence_json,
                error_message=str(exc_info.exception),
                export_type=ExportType.REBUILT_PDF.value,
            )
            session.commit()

        self.assertEqual(recovery.corrected_route, "epub.rebuilt_pdf_via_html")
        self.assertTrue(recovery.repair_work_item_id)

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
            incident = session.get(RuntimeIncident, recovery.incident_id)
            proposal = session.get(RuntimePatchProposal, recovery.proposal_id)
            run = session.get(DocumentRun, run_id)
            work_item = session.get(WorkItem, work_item_id)

            self.assertIsNotNone(incident)
            self.assertIsNotNone(proposal)
            self.assertIsNotNone(run)
            self.assertIsNotNone(work_item)
            assert incident is not None and proposal is not None and run is not None and work_item is not None

            self.assertEqual(incident.incident_kind, RuntimeIncidentKind.EXPORT_MISROUTING)
            self.assertEqual(incident.status, RuntimeIncidentStatus.PUBLISHED)
            self.assertEqual(proposal.status, RuntimePatchProposalStatus.PUBLISHED)
            self.assertEqual(proposal.published_bundle_revision_id, run.runtime_bundle_revision_id)
            self.assertEqual(work_item.runtime_bundle_revision_id, run.runtime_bundle_revision_id)
            self.assertEqual(
                run.status_detail_json["runtime_v2"]["last_export_route_recovery"]["corrected_route"],
                "epub.rebuilt_pdf_via_html",
            )
            self.assertEqual(
                run.status_detail_json["runtime_v2"]["last_export_route_evidence"]["selected_route"],
                "pdf.direct",
            )
            self.assertIn(work_item_id, proposal.status_detail_json["bound_work_item_ids"])
            self.assertEqual(
                proposal.status_detail_json["repair_plan"]["incident_kind"],
                "export_misrouting",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_plan"]["replay"]["scope_id"],
                scope_id,
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["status"],
                "executed",
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
                repair_work_item.input_version_bundle_json["worker_hint"],
                "export_routing_repair_agent",
            )
            self.assertEqual(
                repair_work_item.input_version_bundle_json["worker_contract_version"],
                1,
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["validation"]["status"],
                "passed",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["bundle_publication"]["published_revision_id"],
                proposal.published_bundle_revision_id,
            )
            self.assertEqual(
                incident.status_detail_json["latest_patch_proposal"]["repair_plan"]["bundle"]["revision_name"],
                f"export-routing-fix-{recovery.route_evidence_json.get('route_fingerprint', scope_id)[:12]}",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["status"],
                "succeeded",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_adapter_name"],
                "export_routing_in_process_repair_agent",
            )
