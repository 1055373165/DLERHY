import tempfile
import unittest
from pathlib import Path
import sys
from unittest import mock
from uuid import uuid4

from book_agent.app.runtime.controller_runner import ControllerRunner
from book_agent.app.runtime.controllers.incident_controller import IncidentController
from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.core.config import Settings
from book_agent.domain.enums import (
    ChapterStatus,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    RuntimeIncidentKind,
    RuntimeIncidentStatus,
    RuntimePatchProposalStatus,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import DocumentRun, RunBudget, RuntimeIncident, RuntimePatchProposal, WorkItem
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_execution import RunExecutionService
from book_agent.tools.runtime_repair_contract_runner import execute_runtime_repair_contract_runner
from book_agent.tools.runtime_repair_runner import execute_runtime_repair_runner


class ControllerRunnerPacketRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.bundle_root = Path(self.tempdir.name) / "runtime-bundles"
        self.database_path = Path(self.tempdir.name) / "controller-runner-packet-repair.sqlite"
        self.database_url = f"sqlite+pysqlite:///{self.database_path}"
        self.engine = build_engine(self.database_url)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed_document_run(
        self,
        *,
        preferred_execution_mode: str = "transport_backed",
        preferred_executor_hint: str = "python_transport_repair_executor",
        preferred_executor_contract_version: int = 1,
        preferred_transport_hint: str | None = "http_repair_transport",
        preferred_transport_contract_version: int | None = 1,
    ) -> tuple[str, str]:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"controller-runner-packet-repair-{uuid4()}",
                source_path="/tmp/controller-runner-packet-repair.epub",
                title="Controller Runner Packet Repair",
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

            packet = TranslationPacket(
                chapter_id=chapter.id,
                block_start_id=None,
                block_end_id=None,
                packet_type="translate",
                book_profile_version=1,
                chapter_brief_version=1,
                termbase_version=1,
                entity_snapshot_version=1,
                style_snapshot_version=1,
                packet_json={"packet_ordinal": 1, "input_version_bundle": {"chapter_id": chapter.id}},
                risk_score=0.1,
                status="built",
            )
            session.add(packet)
            session.flush()

            run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="test",
                priority=100,
                status_detail_json={
                    "runtime_v2": {
                        "allowed_patch_surfaces": ["runtime_bundle"],
                        "preferred_repair_execution_mode": preferred_execution_mode,
                        "preferred_repair_executor_hint": preferred_executor_hint,
                        "preferred_repair_executor_contract_version": preferred_executor_contract_version,
                        **(
                            {
                                "preferred_repair_transport_hint": preferred_transport_hint,
                                "preferred_repair_transport_contract_version": (
                                    preferred_transport_contract_version or 1
                                ),
                            }
                            if preferred_transport_hint is not None
                            else {}
                        ),
                    }
                },
            )
            session.add(run)
            session.commit()
            return run.id, packet.id

    def test_controller_runner_auto_schedules_and_executes_packet_runtime_defect_repair_through_http_transport(
        self,
    ) -> None:
        run_id, packet_id = self._seed_document_run()
        runner = ControllerRunner(self.session_factory)
        runner.reconcile_run(run_id=run_id)

        with self.session_factory() as session:
            session.add(RunBudget(run_id=run_id, max_auto_followup_attempts=4))
            execution = RunExecutionService(RunControlRepository(session))
            work_item_id = execution.seed_work_items(
                run_id=run_id,
                stage=WorkItemStage.TRANSLATE,
                scope_type=WorkItemScopeType.PACKET,
                scope_ids=[packet_id],
                priority=100,
                input_version_bundle_by_scope_id={packet_id: {"packet_id": packet_id}},
            )[0]
            work_item = execution.repository.get_work_item(work_item_id)
            work_item.attempt = 2
            work_item.status = WorkItemStatus.TERMINAL_FAILED
            session.add(work_item)
            session.commit()

        stats = runner.reconcile_run(run_id=run_id)
        self.assertEqual(stats.projected_packet_lane_health, 1)

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
            incident = (
                session.query(RuntimeIncident)
                .filter(RuntimeIncident.run_id == run_id, RuntimeIncident.scope_id == packet_id)
                .one()
            )
            proposal = (
                session.query(RuntimePatchProposal)
                .filter(RuntimePatchProposal.incident_id == incident.id)
                .one()
            )
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

        self.assertEqual(incident.incident_kind, RuntimeIncidentKind.PACKET_RUNTIME_DEFECT)
        self.assertEqual(incident.status, RuntimeIncidentStatus.PUBLISHED)
        self.assertEqual(proposal.status, RuntimePatchProposalStatus.PUBLISHED)
        self.assertIsNotNone(repair_work_item)
        assert repair_work_item is not None
        self.assertEqual(repair_work_item.input_version_bundle_json["transport_hint"], "http_repair_transport")
        self.assertEqual(
            proposal.status_detail_json["repair_dispatch"]["transport_hint"],
            "http_repair_transport",
        )
        self.assertEqual(
            incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_transport_hint"],
            "http_repair_transport",
        )

    def test_controller_runner_auto_schedules_packet_runtime_defect_repair_honors_agent_backed_executor_override(
        self,
    ) -> None:
        run_id, packet_id = self._seed_document_run(
            preferred_execution_mode="agent_backed",
            preferred_executor_hint="python_subprocess_repair_executor",
            preferred_executor_contract_version=1,
            preferred_transport_hint=None,
            preferred_transport_contract_version=None,
        )
        runner = ControllerRunner(self.session_factory)
        runner.reconcile_run(run_id=run_id)

        with self.session_factory() as session:
            session.add(RunBudget(run_id=run_id, max_auto_followup_attempts=4))
            execution = RunExecutionService(RunControlRepository(session))
            work_item_id = execution.seed_work_items(
                run_id=run_id,
                stage=WorkItemStage.TRANSLATE,
                scope_type=WorkItemScopeType.PACKET,
                scope_ids=[packet_id],
                priority=100,
                input_version_bundle_by_scope_id={packet_id: {"packet_id": packet_id}},
            )[0]
            work_item = execution.repository.get_work_item(work_item_id)
            work_item.attempt = 2
            work_item.status = WorkItemStatus.TERMINAL_FAILED
            session.add(work_item)
            session.commit()

        runner.reconcile_run(run_id=run_id)

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
            incident = (
                session.query(RuntimeIncident)
                .filter(RuntimeIncident.run_id == run_id, RuntimeIncident.scope_id == packet_id)
                .one()
            )
            proposal = (
                session.query(RuntimePatchProposal)
                .filter(RuntimePatchProposal.incident_id == incident.id)
                .one()
            )
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

        self.assertIsNotNone(repair_work_item)
        assert repair_work_item is not None
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

    def test_controller_runner_auto_schedules_packet_runtime_defect_repair_honors_configured_command_transport_override(
        self,
    ) -> None:
        run_id, packet_id = self._seed_document_run(
            preferred_execution_mode="transport_backed",
            preferred_executor_hint="python_transport_repair_executor",
            preferred_executor_contract_version=1,
            preferred_transport_hint="configured_command_repair_transport",
            preferred_transport_contract_version=1,
        )
        runner = ControllerRunner(self.session_factory)
        runner.reconcile_run(run_id=run_id)

        with self.session_factory() as session:
            session.add(RunBudget(run_id=run_id, max_auto_followup_attempts=4))
            execution = RunExecutionService(RunControlRepository(session))
            work_item_id = execution.seed_work_items(
                run_id=run_id,
                stage=WorkItemStage.TRANSLATE,
                scope_type=WorkItemScopeType.PACKET,
                scope_ids=[packet_id],
                priority=100,
                input_version_bundle_by_scope_id={packet_id: {"packet_id": packet_id}},
            )[0]
            work_item = execution.repository.get_work_item(work_item_id)
            work_item.attempt = 2
            work_item.status = WorkItemStatus.TERMINAL_FAILED
            session.add(work_item)
            session.commit()

        runner.reconcile_run(run_id=run_id)

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
            incident = (
                session.query(RuntimeIncident)
                .filter(RuntimeIncident.run_id == run_id, RuntimeIncident.scope_id == packet_id)
                .one()
            )
            proposal = (
                session.query(RuntimePatchProposal)
                .filter(RuntimePatchProposal.incident_id == incident.id)
                .one()
            )
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)

        self.assertIsNotNone(repair_work_item)
        assert repair_work_item is not None
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

    def test_controller_runner_auto_packet_repair_can_resume_manual_escalation_with_transport_override(self) -> None:
        settings = Settings(
            database_url=self.database_url,
            runtime_bundle_root=self.bundle_root,
            runtime_repair_transport_http_url="https://repair-agent.example/execute",
            runtime_repair_transport_http_timeout_seconds=30,
            runtime_repair_transport_http_bearer_token="repair-token",
        )
        with mock.patch(
            "book_agent.app.runtime.controllers.packet_controller.get_settings",
            return_value=settings,
        ):
            run_id, packet_id = self._seed_document_run(
                preferred_execution_mode=None,
                preferred_executor_hint=None,
                preferred_transport_hint=None,
                preferred_transport_contract_version=None,
            )
        runner = ControllerRunner(self.session_factory)
        with mock.patch(
            "book_agent.app.runtime.controllers.packet_controller.get_settings",
            return_value=settings,
        ):
            runner.reconcile_run(run_id=run_id)

        with self.session_factory() as session:
            session.add(RunBudget(run_id=run_id, max_auto_followup_attempts=4))
            execution = RunExecutionService(RunControlRepository(session))
            work_item_id = execution.seed_work_items(
                run_id=run_id,
                stage=WorkItemStage.TRANSLATE,
                scope_type=WorkItemScopeType.PACKET,
                scope_ids=[packet_id],
                priority=100,
                input_version_bundle_by_scope_id={packet_id: {"packet_id": packet_id}},
            )[0]
            work_item = execution.repository.get_work_item(work_item_id)
            work_item.attempt = 2
            work_item.status = WorkItemStatus.TERMINAL_FAILED
            session.add(work_item)
            session.commit()

        with mock.patch(
            "book_agent.app.runtime.controllers.packet_controller.get_settings",
            return_value=settings,
        ):
            runner.reconcile_run(run_id=run_id)

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
            result["repair_agent_decision_reason"] = "controller_runner_manual_override"
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
            incident = (
                session.query(RuntimeIncident)
                .filter(RuntimeIncident.run_id == run_id, RuntimeIncident.scope_id == packet_id)
                .one()
            )
            proposal = (
                session.query(RuntimePatchProposal)
                .filter(RuntimePatchProposal.incident_id == incident.id)
                .one()
            )
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)
            self.assertIsNotNone(repair_work_item)
            assert repair_work_item is not None
            self.assertEqual(repair_work_item.status, WorkItemStatus.TERMINAL_FAILED)
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["status"],
                "manual_escalation_required",
            )

        with self.session_factory() as session:
            controller = IncidentController(session=session)
            resumed = controller.resume_repair_dispatch(
                proposal_id=proposal.id,
                resumed_by="ops-user",
                note="switch controller-runner packet repair transport",
                dispatch_overrides={
                    "transport_hint": "configured_command_repair_transport",
                    "transport_contract_version": 1,
                },
            )
            self.assertEqual(resumed["status"], "pending")
            self.assertEqual(resumed["transport_hint"], "configured_command_repair_transport")
            execution = RunExecutionService(RunControlRepository(session))
            self.assertEqual(
                execution.repository.list_claimable_work_item_ids(run_id, stage=WorkItemStage.REPAIR),
                [repair_work_item_id],
            )
            session.commit()


if __name__ == "__main__":
    unittest.main()
