import unittest
from uuid import uuid4

from book_agent.app.runtime.controller_runner import ControllerRunner
from book_agent.domain.enums import (
    ChapterStatus,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    JobScopeType,
    PacketStatus,
    PacketType,
    ReviewSessionStatus,
    RuntimeIncidentKind,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import DocumentRun, ReviewSession, RunBudget, RuntimeIncident, RuntimePatchProposal
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository
from book_agent.services.run_execution import RunExecutionService
from book_agent.infra.repositories.run_control import RunControlRepository


class ControllerRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed_document_run(self) -> tuple[str, str, str]:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"controller-runner-{uuid4()}",
                source_path="/tmp/controller-runner.epub",
                title="Controller Runner",
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
                packet_json={"packet_ordinal": 1, "input_version_bundle": {"chapter_id": chapter.id}},
                risk_score=0.1,
                status=PacketStatus.BUILT,
            )
            session.add(packet)
            session.flush()

            run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="test",
                priority=100,
                status_detail_json={},
            )
            session.add(run)
            session.commit()
            return run.id, chapter.id, packet.id

    def test_reconcile_run_creates_runtime_resources_and_checkpoint(self) -> None:
        run_id, chapter_id, packet_id = self._seed_document_run()

        runner = ControllerRunner(self.session_factory)
        stats = runner.reconcile_run(run_id=run_id)
        self.assertEqual(stats.created_chapter_runs, 1)
        self.assertEqual(stats.created_packet_tasks, 1)
        self.assertEqual(stats.created_review_sessions, 1)
        self.assertEqual(stats.mirrored_packet_tasks, 0)
        self.assertEqual(stats.projected_packet_lane_health, 1)

        with self.session_factory() as session:
            repo = RuntimeResourcesRepository(session)
            chapter_runs = repo.list_chapter_runs_for_run(run_id=run_id)
            self.assertEqual(len(chapter_runs), 1)
            packet_tasks = repo.list_packet_tasks_for_chapter_run(chapter_run_id=chapter_runs[0].id)
            self.assertEqual(len(packet_tasks), 1)
            self.assertEqual(packet_tasks[0].packet_id, packet_id)
            review_sessions = repo.list_review_sessions_for_chapter_run(chapter_run_id=chapter_runs[0].id)
            self.assertEqual(len(review_sessions), 1)
            self.assertEqual(review_sessions[0].status, ReviewSessionStatus.ACTIVE)
            checkpoint = repo.get_checkpoint(
                run_id=run_id,
                scope_type=JobScopeType.DOCUMENT,
                scope_id=chapter_runs[0].document_id,
                checkpoint_key="controller_runner.phase_a",
            )
            self.assertIsNotNone(checkpoint)
            assert checkpoint is not None
            self.assertEqual(checkpoint.checkpoint_json["created_review_sessions"], 1)
            self.assertEqual(checkpoint.checkpoint_json["projected_packet_lane_health"], 1)
            chapter_checkpoint = repo.get_checkpoint(
                run_id=run_id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=chapter_runs[0].chapter_id,
                checkpoint_key="controller_runner.chapter.phase_a",
            )
            self.assertIsNotNone(chapter_checkpoint)
            assert chapter_checkpoint is not None
            self.assertEqual(chapter_checkpoint.checkpoint_json["review_session_id"], review_sessions[0].id)
            review_checkpoint = repo.get_checkpoint(
                run_id=run_id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=chapter_runs[0].chapter_id,
                checkpoint_key="review_controller.lane_health",
            )
            self.assertIsNotNone(review_checkpoint)

    def test_reconcile_run_mirrors_existing_translate_work_item_binding(self) -> None:
        run_id, _chapter_id, packet_id = self._seed_document_run()
        runner = ControllerRunner(self.session_factory)
        runner.reconcile_run(run_id=run_id)

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            execution.seed_work_items(
                run_id=run_id,
                stage=WorkItemStage.TRANSLATE,
                scope_type=WorkItemScopeType.PACKET,
                scope_ids=[packet_id],
                priority=100,
                input_version_bundle_by_scope_id={packet_id: {"packet_id": packet_id}},
            )
            session.commit()

        stats = runner.reconcile_run(run_id=run_id)
        self.assertEqual(stats.created_chapter_runs, 0)
        self.assertEqual(stats.created_packet_tasks, 0)
        self.assertEqual(stats.created_review_sessions, 0)
        self.assertEqual(stats.mirrored_packet_tasks, 1)
        self.assertEqual(stats.projected_packet_lane_health, 1)

        with self.session_factory() as session:
            repo = RuntimeResourcesRepository(session)
            chapter_run = repo.list_chapter_runs_for_run(run_id=run_id)[0]
            packet_task = repo.list_packet_tasks_for_chapter_run(chapter_run_id=chapter_run.id)[0]
            self.assertIsNotNone(packet_task.last_work_item_id)
            self.assertEqual(packet_task.attempt_count, 1)
            review_sessions = session.query(ReviewSession).all()
            self.assertEqual(len(review_sessions), 1)

    def test_reconcile_run_auto_schedules_packet_runtime_defect_repair(self) -> None:
        run_id, _chapter_id, packet_id = self._seed_document_run()
        runner = ControllerRunner(self.session_factory)
        runner.reconcile_run(run_id=run_id)

        with self.session_factory() as session:
            run = session.get(DocumentRun, run_id)
            assert run is not None
            run.status_detail_json = {
                "runtime_v2": {
                    "allowed_patch_surfaces": ["runtime_bundle"],
                    "preferred_repair_execution_mode": "in_process",
                    "preferred_repair_executor_hint": "python_repair_executor",
                    "preferred_repair_executor_contract_version": 1,
                }
            }
            session.add(run)
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

        self.assertEqual(incident.incident_kind, RuntimeIncidentKind.PACKET_RUNTIME_DEFECT)
        self.assertEqual(proposal.status_detail_json["repair_dispatch"]["execution_mode"], "in_process")
