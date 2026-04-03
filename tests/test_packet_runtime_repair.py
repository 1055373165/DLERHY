import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from unittest import mock
from uuid import uuid4

from book_agent.app.runtime.controllers.incident_controller import IncidentController
from book_agent.app.runtime.controllers.packet_controller import PacketController
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
    PacketTaskAction,
    PacketTaskStatus,
    PacketType,
    PacketStatus,
    RuntimeIncidentKind,
    RuntimeIncidentStatus,
    RuntimePatchProposalStatus,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import (
    ChapterRun,
    DocumentRun,
    PacketTask,
    RunBudget,
    RuntimeCheckpoint,
    RuntimeIncident,
    RuntimePatchProposal,
    WorkItem,
)
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository
from book_agent.tools.runtime_repair_contract_runner import execute_runtime_repair_contract_runner
from book_agent.tools.runtime_repair_runner import execute_runtime_repair_runner


class PacketRuntimeRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.bundle_root = Path(self.tempdir.name) / "runtime-bundles"
        self.database_path = Path(self.tempdir.name) / "packet-runtime-repair.sqlite"
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

    def _seed_packet_runtime_defect_scope(
        self,
        *,
        preferred_execution_mode: str | None = "in_process",
        preferred_executor_hint: str | None = "python_repair_executor",
        preferred_executor_contract_version: int | None = 1,
        preferred_transport_hint: str | None = None,
        preferred_transport_contract_version: int | None = None,
    ) -> tuple[str, str, str, str]:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"packet-runtime-defect-{uuid4()}",
                source_path="/tmp/packet-runtime-defect.epub",
                title="Packet Runtime Defect",
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
                packet_type=PacketType.TRANSLATE,
                book_profile_version=1,
                chapter_brief_version=1,
                termbase_version=1,
                entity_snapshot_version=1,
                style_snapshot_version=1,
                packet_json={"packet_ordinal": 1},
                risk_score=0.1,
                status=PacketStatus.BUILT,
            )
            session.add(packet)
            session.flush()

            runtime_v2 = {"allowed_patch_surfaces": ["runtime_bundle"]}
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
                chapter_id=chapter.id,
                desired_phase=ChapterRunPhase.TRANSLATE,
                observed_phase=ChapterRunPhase.TRANSLATE,
                status=ChapterRunStatus.ACTIVE,
                generation=1,
                observed_generation=1,
                conditions_json={},
                status_detail_json={},
            )
            session.add(chapter_run)
            session.flush()

            packet_task = PacketTask(
                chapter_run_id=chapter_run.id,
                packet_id=packet.id,
                packet_generation=1,
                desired_action=PacketTaskAction.TRANSLATE,
                status=PacketTaskStatus.RUNNING,
                input_version_bundle_json={},
                attempt_count=2,
                conditions_json={},
                status_detail_json={},
                created_at=now - timedelta(minutes=40),
                updated_at=now - timedelta(minutes=40),
            )
            session.add(packet_task)
            session.flush()

            work_item = WorkItem(
                run_id=run.id,
                stage=WorkItemStage.TRANSLATE,
                scope_type=WorkItemScopeType.PACKET,
                scope_id=packet.id,
                attempt=2,
                priority=100,
                status=WorkItemStatus.TERMINAL_FAILED,
                last_heartbeat_at=now - timedelta(minutes=20),
                started_at=now - timedelta(minutes=30),
                updated_at=now - timedelta(minutes=20),
                finished_at=now - timedelta(minutes=20),
                input_version_bundle_json={
                    "packet_id": packet.id,
                    "chapter_id": chapter.id,
                    "packet_ordinal": 1,
                },
                output_artifact_refs_json={},
                error_detail_json={"message": "packet runtime defect"},
            )
            session.add(work_item)
            session.add(RunBudget(run_id=run.id, max_auto_followup_attempts=4))
            session.commit()
            return run.id, chapter_run.id, packet_task.id, packet.id

    def test_packet_controller_schedules_packet_runtime_defect_repair(self) -> None:
        run_id, chapter_run_id, packet_task_id, packet_id = self._seed_packet_runtime_defect_scope()

        with self.session_factory() as session:
            projected = PacketController(session=session).project_lane_health(run_id=run_id)
            session.commit()

            packet_task = RuntimeResourcesRepository(session).get_packet_task(packet_task_id)
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
            checkpoint = (
                session.query(RuntimeCheckpoint)
                .filter(
                    RuntimeCheckpoint.run_id == run_id,
                    RuntimeCheckpoint.scope_type == JobScopeType.PACKET,
                    RuntimeCheckpoint.scope_id == packet_id,
                    RuntimeCheckpoint.checkpoint_key == "packet_controller.runtime_defect_recovery",
                )
                .one()
            )

        self.assertEqual(projected, 1)
        self.assertEqual(incident.incident_kind, RuntimeIncidentKind.PACKET_RUNTIME_DEFECT)
        self.assertEqual(incident.status, RuntimeIncidentStatus.PATCH_PROPOSED)
        self.assertEqual(proposal.status, RuntimePatchProposalStatus.PROPOSED)
        self.assertEqual(
            proposal.status_detail_json["repair_dispatch"]["worker_hint"],
            "packet_runtime_defect_repair_agent",
        )
        self.assertEqual(
            packet_task.status_detail_json["runtime_v2"]["last_runtime_defect_recovery"]["status"],
            "scheduled",
        )
        self.assertEqual(
            checkpoint.checkpoint_json["recovery"]["replay_scope_id"],
            packet_id,
        )
        self.assertEqual(
            checkpoint.checkpoint_json["recovery"]["repair_work_item_id"],
            proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"],
        )

    def test_packet_runtime_defect_repair_executes_and_replays_only_packet_scope(self) -> None:
        run_id, chapter_run_id, packet_task_id, packet_id = self._seed_packet_runtime_defect_scope()
        settings = self._settings()

        with self.session_factory() as session, mock.patch(
            "book_agent.app.runtime.controllers.packet_controller.get_settings",
            return_value=settings,
        ):
            PacketController(session=session).project_lane_health(run_id=run_id)
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
            "book_agent.services.runtime_bundle.get_settings",
            return_value=self._settings(),
        ):
            executor._execute_repair_work_item(run_id, claimed)

        with self.session_factory() as session:
            packet_task = RuntimeResourcesRepository(session).get_packet_task(packet_task_id)
            chapter_run = RuntimeResourcesRepository(session).get_chapter_run(chapter_run_id)
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
            checkpoint = (
                session.query(RuntimeCheckpoint)
                .filter(
                    RuntimeCheckpoint.run_id == run_id,
                    RuntimeCheckpoint.scope_type == JobScopeType.PACKET,
                    RuntimeCheckpoint.scope_id == packet_id,
                    RuntimeCheckpoint.checkpoint_key == "packet_controller.runtime_defect_recovery",
                )
                .one()
            )
            translate_items = (
                session.query(WorkItem)
                .filter(
                    WorkItem.run_id == run_id,
                    WorkItem.stage == WorkItemStage.TRANSLATE,
                    WorkItem.scope_type == WorkItemScopeType.PACKET,
                    WorkItem.scope_id == packet_id,
                )
                .all()
            )
            chapter_hold_checkpoint = (
                session.query(RuntimeCheckpoint)
                .filter(
                    RuntimeCheckpoint.run_id == run_id,
                    RuntimeCheckpoint.scope_type == JobScopeType.CHAPTER,
                    RuntimeCheckpoint.checkpoint_key == "chapter_controller.chapter_hold",
                )
                .first()
            )

        self.assertEqual(incident.status, RuntimeIncidentStatus.PUBLISHED)
        self.assertEqual(proposal.status, RuntimePatchProposalStatus.PUBLISHED)
        self.assertEqual(
            packet_task.status_detail_json["runtime_v2"]["last_runtime_defect_recovery"]["status"],
            "published",
        )
        self.assertEqual(
            checkpoint.checkpoint_json["recovery"]["status"],
            "published",
        )
        self.assertEqual(
            incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_result_contract_version"],
            1,
        )
        self.assertTrue(
            packet_task.status_detail_json["runtime_v2"]["last_runtime_defect_recovery"]["replay_work_item_ids"]
        )
        self.assertTrue(any(item.status == WorkItemStatus.PENDING for item in translate_items))
        self.assertIsNone(chapter_hold_checkpoint)
        self.assertTrue(
            any(
                entry.get("source") == "runtime.packet_runtime_defect"
                for entry in list(chapter_run.conditions_json.get("recovered_lineage") or [])
            )
        )

    def test_packet_runtime_defect_repair_honors_run_level_executor_override(self) -> None:
        run_id, _chapter_run_id, packet_task_id, packet_id = self._seed_packet_runtime_defect_scope(
            preferred_execution_mode="agent_backed",
            preferred_executor_hint="python_subprocess_repair_executor",
            preferred_executor_contract_version=1,
        )
        settings = self._settings()

        with self.session_factory() as session, mock.patch(
            "book_agent.app.runtime.controllers.packet_controller.get_settings",
            return_value=settings,
        ):
            PacketController(session=session).project_lane_health(run_id=run_id)
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
            packet_task = RuntimeResourcesRepository(session).get_packet_task(packet_task_id)
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
        self.assertEqual(
            packet_task.status_detail_json["runtime_v2"]["last_runtime_defect_recovery"]["status"],
            "published",
        )

    def test_packet_runtime_defect_repair_can_execute_through_contract_agent_executor_override(self) -> None:
        run_id, _chapter_run_id, packet_task_id, packet_id = self._seed_packet_runtime_defect_scope(
            preferred_execution_mode="agent_backed",
            preferred_executor_hint="python_contract_agent_repair_executor",
            preferred_executor_contract_version=1,
        )
        settings = self._settings()

        with self.session_factory() as session, mock.patch(
            "book_agent.app.runtime.controllers.packet_controller.get_settings",
            return_value=settings,
        ):
            PacketController(session=session).project_lane_health(run_id=run_id)
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
            packet_task = RuntimeResourcesRepository(session).get_packet_task(packet_task_id)
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
        self.assertEqual(
            incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_adapter_name"],
            "packet_runtime_defect_remote_contract_repair_agent",
        )
        self.assertEqual(
            packet_task.status_detail_json["runtime_v2"]["last_runtime_defect_recovery"]["status"],
            "published",
        )

    def test_packet_runtime_defect_repair_honors_run_level_configured_command_transport_override(self) -> None:
        run_id, _chapter_run_id, _packet_task_id, packet_id = self._seed_packet_runtime_defect_scope(
            preferred_execution_mode="transport_backed",
            preferred_executor_hint="python_transport_repair_executor",
            preferred_executor_contract_version=1,
            preferred_transport_hint="configured_command_repair_transport",
            preferred_transport_contract_version=1,
        )
        settings = self._settings()

        with self.session_factory() as session, mock.patch(
            "book_agent.app.runtime.controllers.packet_controller.get_settings",
            return_value=settings,
        ):
            PacketController(session=session).project_lane_health(run_id=run_id)
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

    def test_packet_runtime_defect_repair_can_execute_through_http_transport_override(self) -> None:
        run_id, _chapter_run_id, _packet_task_id, packet_id = self._seed_packet_runtime_defect_scope(
            preferred_execution_mode="transport_backed",
            preferred_executor_hint="python_transport_repair_executor",
            preferred_executor_contract_version=1,
            preferred_transport_hint="http_repair_transport",
            preferred_transport_contract_version=1,
        )

        with self.session_factory() as session:
            PacketController(session=session).project_lane_health(run_id=run_id)
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

    def test_packet_runtime_defect_repair_can_execute_through_http_contract_transport_override(self) -> None:
        run_id, _chapter_run_id, _packet_task_id, packet_id = self._seed_packet_runtime_defect_scope(
            preferred_execution_mode="transport_backed",
            preferred_executor_hint="python_contract_transport_repair_executor",
            preferred_executor_contract_version=1,
            preferred_transport_hint="http_contract_repair_transport",
            preferred_transport_contract_version=1,
        )

        with self.session_factory() as session:
            PacketController(session=session).project_lane_health(run_id=run_id)
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
            incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_transport_hint"],
            "http_contract_repair_transport",
        )
        self.assertEqual(
            incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_executor_hint"],
            "python_contract_transport_repair_executor",
        )
        self.assertEqual(
            incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_adapter_name"],
            "packet_runtime_defect_remote_contract_repair_agent",
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

    def test_packet_runtime_defect_repair_defaults_to_http_contract_transport_when_remote_endpoint_is_configured(self) -> None:
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
            run_id, _chapter_run_id, packet_task_id, packet_id = self._seed_packet_runtime_defect_scope(
                preferred_execution_mode=None,
                preferred_executor_hint=None,
            )

        with self.session_factory() as session, mock.patch(
            "book_agent.app.runtime.controllers.packet_controller.get_settings",
            return_value=settings,
        ):
            PacketController(session=session).project_lane_health(run_id=run_id)
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
            incident = (
                session.query(RuntimeIncident)
                .filter(RuntimeIncident.run_id == run_id, RuntimeIncident.scope_id == packet_id)
                .one()
            )
            packet_task = RuntimeResourcesRepository(session).get_packet_task(packet_task_id)
            checkpoint = (
                session.query(RuntimeCheckpoint)
                .filter(
                    RuntimeCheckpoint.run_id == run_id,
                    RuntimeCheckpoint.scope_type == JobScopeType.PACKET,
                    RuntimeCheckpoint.scope_id == packet_id,
                    RuntimeCheckpoint.checkpoint_key == "packet_controller.runtime_defect_recovery",
                )
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

    def test_packet_runtime_defect_repair_records_retry_later_remote_decision(self) -> None:
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
            run_id, _chapter_run_id, packet_task_id, packet_id = self._seed_packet_runtime_defect_scope(
                preferred_execution_mode=None,
                preferred_executor_hint=None,
            )

        with self.session_factory() as session, mock.patch(
            "book_agent.app.runtime.controllers.packet_controller.get_settings",
            return_value=settings,
        ):
            PacketController(session=session).project_lane_health(run_id=run_id)
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
            result["repair_agent_decision"] = "retry_later"
            result["repair_agent_decision_reason"] = "packet_scope_backoff_required"
            result["repair_agent_retry_after_seconds"] = 300
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
        self.assertEqual(proposal.status_detail_json["repair_dispatch"]["retry_after_seconds"], 300)
        self.assertIn("T", proposal.status_detail_json["repair_dispatch"]["next_retry_after"])
        self.assertEqual(
            incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_decision"],
            "retry_later",
        )
        self.assertEqual(
            incident.status_detail_json["repair_dispatch"]["last_result"]["result_json"]["repair_agent_decision_reason"],
            "packet_scope_backoff_required",
        )
        self.assertEqual(
            proposal.status_detail_json["repair_dispatch"]["repair_blockage"]["state"],
            "backoff_blocked",
        )
        with self.session_factory() as session:
            packet_task = RuntimeResourcesRepository(session).get_packet_task(packet_task_id)
            checkpoint = (
                session.query(RuntimeCheckpoint)
                .filter(
                    RuntimeCheckpoint.run_id == run_id,
                    RuntimeCheckpoint.scope_type == JobScopeType.PACKET,
                    RuntimeCheckpoint.scope_id == packet_id,
                    RuntimeCheckpoint.checkpoint_key == "packet_controller.runtime_defect_recovery",
                )
                .one()
            )

        self.assertEqual(
            packet_task.status_detail_json["runtime_v2"]["last_runtime_defect_recovery"]["repair_blockage"]["state"],
            "backoff_blocked",
        )
        self.assertTrue(
            packet_task.status_detail_json["runtime_v2"]["last_runtime_defect_recovery"]["repair_blockage"]["blocked"]
        )
        self.assertEqual(
            checkpoint.checkpoint_json["recovery"]["repair_blockage"]["state"],
            "backoff_blocked",
        )

        next_retry_after = datetime.fromisoformat(proposal.status_detail_json["repair_dispatch"]["next_retry_after"])
        with self.session_factory() as session, mock.patch(
            "book_agent.app.runtime.controllers.packet_controller.get_settings",
            return_value=settings,
        ), mock.patch(
            "book_agent.app.runtime.controllers.packet_controller._utcnow",
            return_value=next_retry_after + timedelta(seconds=1),
        ):
            PacketController(session=session).project_lane_health(run_id=run_id)
            session.commit()

        with self.session_factory() as session:
            packet_task = RuntimeResourcesRepository(session).get_packet_task(packet_task_id)
            checkpoint = (
                session.query(RuntimeCheckpoint)
                .filter(
                    RuntimeCheckpoint.run_id == run_id,
                    RuntimeCheckpoint.scope_type == JobScopeType.PACKET,
                    RuntimeCheckpoint.scope_id == packet_id,
                    RuntimeCheckpoint.checkpoint_key == "packet_controller.runtime_defect_recovery",
                )
                .one()
            )

        self.assertEqual(
            packet_task.status_detail_json["runtime_v2"]["last_runtime_defect_recovery"]["repair_blockage"]["state"],
            "ready_to_continue",
        )
        self.assertFalse(
            packet_task.status_detail_json["runtime_v2"]["last_runtime_defect_recovery"]["repair_blockage"]["blocked"]
        )
        self.assertEqual(
            checkpoint.checkpoint_json["recovery"]["repair_blockage"]["state"],
            "ready_to_continue",
        )

    def test_packet_runtime_defect_repair_can_resume_manual_escalation_with_transport_override(self) -> None:
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
            run_id, _chapter_run_id, packet_task_id, packet_id = self._seed_packet_runtime_defect_scope(
                preferred_execution_mode=None,
                preferred_executor_hint=None,
            )

        with self.session_factory() as session, mock.patch(
            "book_agent.app.runtime.controllers.packet_controller.get_settings",
            return_value=settings,
        ):
            PacketController(session=session).project_lane_health(run_id=run_id)
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
            result["repair_agent_decision_reason"] = "packet_scope_manual_override"
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
            packet_task = RuntimeResourcesRepository(session).get_packet_task(packet_task_id)
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
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["repair_blockage"]["state"],
                "manual_escalation_waiting",
            )
            self.assertEqual(
                packet_task.status_detail_json["runtime_v2"]["last_runtime_defect_recovery"]["repair_blockage"]["state"],
                "manual_escalation_waiting",
            )

        with self.session_factory() as session:
            controller = IncidentController(session=session)
            resumed = controller.resume_repair_dispatch(
                proposal_id=proposal.id,
                resumed_by="ops-user",
                note="switch packet repair transport",
                dispatch_overrides={
                    "transport_hint": "configured_command_repair_transport",
                    "transport_contract_version": 1,
                    "validation_command": "uv run pytest tests/test_packet_runtime_repair.py",
                },
            )
            self.assertEqual(resumed["status"], "pending")
            self.assertEqual(resumed["transport_hint"], "configured_command_repair_transport")
            session.commit()

        with self.session_factory() as session:
            proposal = session.get(RuntimePatchProposal, proposal.id)
            self.assertIsNotNone(proposal)
            assert proposal is not None
            packet_task = RuntimeResourcesRepository(session).get_packet_task(packet_task_id)
            repair_work_item_id = proposal.status_detail_json["repair_dispatch"]["repair_work_item_id"]
            repair_work_item = session.get(WorkItem, repair_work_item_id)
            self.assertIsNotNone(repair_work_item)
            assert repair_work_item is not None
            self.assertEqual(repair_work_item.status, WorkItemStatus.PENDING)
            self.assertEqual(
                repair_work_item.input_version_bundle_json["transport_hint"],
                "configured_command_repair_transport",
            )
            self.assertEqual(
                proposal.status_detail_json["repair_dispatch"]["repair_blockage"]["state"],
                "ready_to_continue",
            )
            self.assertEqual(
                packet_task.status_detail_json["runtime_v2"]["last_runtime_defect_recovery"]["repair_blockage"]["state"],
                "ready_to_continue",
            )
            execution = executor._run_execution_service(session)
            self.assertEqual(
                execution.repository.list_claimable_work_item_ids(run_id, stage=WorkItemStage.REPAIR),
                [repair_work_item_id],
            )


if __name__ == "__main__":
    unittest.main()
