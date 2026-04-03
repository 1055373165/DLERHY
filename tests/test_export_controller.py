import tempfile
import unittest
from unittest import mock
from pathlib import Path
import sys
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
from book_agent.tools.runtime_repair_contract_runner import execute_runtime_repair_contract_runner
from book_agent.tools.runtime_repair_runner import execute_runtime_repair_runner


class ExportControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.bundle_root = Path(self.tempdir.name) / "runtime-bundles"
        self.database_path = Path(self.tempdir.name) / "export-controller.sqlite"
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

    def _seed_misrouted_export_scope(
        self,
        *,
        preferred_execution_mode: str | None = None,
        preferred_executor_hint: str | None = None,
        preferred_executor_contract_version: int | None = None,
        preferred_transport_hint: str | None = None,
        preferred_transport_contract_version: int | None = None,
    ) -> tuple[str, str, str, str]:
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
                        **(
                            {
                                "preferred_repair_execution_mode": preferred_execution_mode,
                                "preferred_repair_executor_hint": preferred_executor_hint,
                                "preferred_repair_executor_contract_version": (
                                    preferred_executor_contract_version or 1
                                ),
                            }
                            if preferred_execution_mode or preferred_executor_hint
                            else {}
                        ),
                        **(
                            {
                                "preferred_repair_transport_hint": preferred_transport_hint,
                                "preferred_repair_transport_contract_version": (
                                    preferred_transport_contract_version or 1
                                ),
                            }
                            if preferred_transport_hint
                            else {}
                        ),
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
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_executor_execution_mode"],
                "transport_backed",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_executor_hint"],
                "python_transport_repair_executor",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_transport_hint"],
                "python_subprocess_repair_transport",
            )

    def test_recover_export_misrouting_honors_run_level_executor_override(self) -> None:
        run_id, document_id, work_item_id, scope_id = self._seed_misrouted_export_scope(
            preferred_execution_mode="agent_backed",
            preferred_executor_hint="python_subprocess_repair_executor",
            preferred_executor_contract_version=1,
        )

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
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

            self.assertIsNotNone(incident)
            self.assertIsNotNone(proposal)
            self.assertIsNotNone(repair_work_item)
            assert incident is not None and proposal is not None and repair_work_item is not None

            self.assertEqual(
                repair_work_item.input_version_bundle_json["execution_mode"],
                "agent_backed",
            )
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
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_transport_hint"],
                "python_subprocess_repair_transport",
            )

    def test_recover_export_misrouting_can_execute_through_contract_agent_executor_override(self) -> None:
        run_id, document_id, work_item_id, scope_id = self._seed_misrouted_export_scope(
            preferred_execution_mode="agent_backed",
            preferred_executor_hint="python_contract_agent_repair_executor",
            preferred_executor_contract_version=1,
        )

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
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

            self.assertIsNotNone(incident)
            self.assertIsNotNone(proposal)
            self.assertIsNotNone(repair_work_item)
            assert incident is not None and proposal is not None and repair_work_item is not None

            self.assertEqual(
                repair_work_item.input_version_bundle_json["execution_mode"],
                "agent_backed",
            )
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
            self.assertIsNone(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_transport_hint"]
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_adapter_name"],
                "export_routing_remote_contract_repair_agent",
            )

    def test_recover_export_misrouting_honors_run_level_transport_override(self) -> None:
        run_id, document_id, work_item_id, scope_id = self._seed_misrouted_export_scope(
            preferred_transport_hint="configured_command_repair_transport",
            preferred_transport_contract_version=1,
        )

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
            incident = session.get(RuntimeIncident, recovery.incident_id)
            proposal = session.get(RuntimePatchProposal, recovery.proposal_id)
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

            self.assertIsNotNone(incident)
            self.assertIsNotNone(proposal)
            self.assertIsNotNone(repair_work_item)
            assert incident is not None and proposal is not None and repair_work_item is not None

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

    def test_recover_export_misrouting_can_execute_through_http_transport_override(self) -> None:
        run_id, document_id, work_item_id, scope_id = self._seed_misrouted_export_scope(
            preferred_transport_hint="http_repair_transport",
            preferred_transport_contract_version=1,
        )

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
            incident = session.get(RuntimeIncident, recovery.incident_id)
            proposal = session.get(RuntimePatchProposal, recovery.proposal_id)
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

            self.assertIsNotNone(incident)
            self.assertIsNotNone(proposal)
            self.assertIsNotNone(repair_work_item)
            assert incident is not None and proposal is not None and repair_work_item is not None

            self.assertEqual(
                repair_work_item.input_version_bundle_json["transport_hint"],
                "http_repair_transport",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["transport_hint"],
                "http_repair_transport",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_transport_hint"],
                "http_repair_transport",
            )

    def test_recover_export_misrouting_can_execute_through_http_contract_transport_override(self) -> None:
        run_id, document_id, work_item_id, scope_id = self._seed_misrouted_export_scope(
            preferred_execution_mode="transport_backed",
            preferred_executor_hint="python_contract_transport_repair_executor",
            preferred_executor_contract_version=1,
            preferred_transport_hint="http_contract_repair_transport",
            preferred_transport_contract_version=1,
        )

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
            incident = session.get(RuntimeIncident, recovery.incident_id)
            proposal = session.get(RuntimePatchProposal, recovery.proposal_id)
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

            self.assertIsNotNone(incident)
            self.assertIsNotNone(proposal)
            self.assertIsNotNone(repair_work_item)
            assert incident is not None and proposal is not None and repair_work_item is not None

            self.assertEqual(
                repair_work_item.input_version_bundle_json["transport_hint"],
                "http_contract_repair_transport",
            )
            self.assertEqual(
                repair_work_item.input_version_bundle_json["executor_hint"],
                "python_contract_transport_repair_executor",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["transport_hint"],
                "http_contract_repair_transport",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["executor_hint"],
                "python_contract_transport_repair_executor",
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
                "export_routing_remote_contract_repair_agent",
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
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_decision"],
                "publish_bundle_and_replay",
            )

    def test_recover_export_misrouting_defaults_to_http_contract_transport_when_remote_endpoint_is_configured(self) -> None:
        run_id, document_id, work_item_id, scope_id = self._seed_misrouted_export_scope()
        settings = Settings(
            database_url=self.database_url,
            runtime_bundle_root=self.bundle_root,
            runtime_repair_transport_http_url="https://repair-agent.example/execute",
            runtime_repair_transport_http_timeout_seconds=30,
            runtime_repair_transport_http_bearer_token="repair-token",
        )

        with self.session_factory() as session, mock.patch(
            "book_agent.app.runtime.controllers.export_controller.get_settings",
            return_value=settings,
        ):
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
            incident = session.get(RuntimeIncident, recovery.incident_id)
            proposal = session.get(RuntimePatchProposal, recovery.proposal_id)
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

            self.assertIsNotNone(incident)
            self.assertIsNotNone(proposal)
            self.assertIsNotNone(repair_work_item)
            assert incident is not None and proposal is not None and repair_work_item is not None

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

    def test_recover_export_misrouting_records_manual_escalation_remote_decision(self) -> None:
        run_id, document_id, work_item_id, scope_id = self._seed_misrouted_export_scope(
            preferred_execution_mode="transport_backed",
            preferred_executor_hint="python_contract_transport_repair_executor",
            preferred_executor_contract_version=1,
            preferred_transport_hint="http_contract_repair_transport",
            preferred_transport_contract_version=1,
        )
        settings = Settings(
            database_url=self.database_url,
            runtime_bundle_root=self.bundle_root,
            runtime_repair_transport_http_url="https://repair-agent.example/execute",
            runtime_repair_transport_http_timeout_seconds=30,
            runtime_repair_transport_http_bearer_token="repair-token",
        )

        with self.session_factory() as session, mock.patch(
            "book_agent.app.runtime.controllers.export_controller.get_settings",
            return_value=settings,
        ):
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
            controller.recover_export_misrouting(
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
            result = execute_runtime_repair_contract_runner(json)
            result["repair_agent_decision"] = "manual_escalation_required"
            result["repair_agent_decision_reason"] = "requires_operator_review"
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
            run = session.get(DocumentRun, run_id)

            self.assertIsNotNone(repair_work_item)
            self.assertIsNotNone(run)
            assert repair_work_item is not None
            assert run is not None
            self.assertEqual(proposal.status.value, "proposed")
            self.assertEqual(repair_work_item.status, WorkItemStatus.TERMINAL_FAILED)
            self.assertEqual(repair_work_item.error_class, "RuntimeRepairManualEscalationRequired")
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["status"],
                "manual_escalation_required",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["next_action"],
                "manual_escalation",
            )
            self.assertFalse(proposal.status_detail_json["repair_dispatch"]["retryable"])
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_decision"],
                "manual_escalation_required",
            )
            self.assertEqual(
                incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_decision_reason"],
                "requires_operator_review",
            )
            self.assertEqual(
                (repair_work_item.error_detail_json or {}).get("repair_agent_decision"),
                "manual_escalation_required",
            )
            self.assertEqual(
                run.status_detail_json["runtime_v2"]["pending_export_route_repair"]["repair_blockage"]["state"],
                "manual_escalation_waiting",
            )
