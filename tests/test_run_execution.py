import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from book_agent.app.runtime.document_run_executor import (
    DocumentRunExecutor,
    _is_retryable_exception,
    _pause_reason_for_exception,
)
from book_agent.domain.enums import (
    ArtifactStatus,
    BlockType,
    ChapterStatus,
    DocumentStatus,
    DocumentRunType,
    JobScopeType,
    PacketStatus,
    PacketType,
    ProtectedPolicy,
    RuntimeBundleRevisionStatus,
    RuntimeIncidentKind,
    RuntimeIncidentStatus,
    RuntimePatchProposalStatus,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Block, Chapter, Document
from book_agent.domain.models.ops import RuntimeBundleRevision, RuntimeIncident, RuntimePatchProposal, WorkItem
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_control import RunBudgetSummary, RunControlService
from book_agent.services.run_execution import RunExecutionService


class RunExecutionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _create_document(self) -> str:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"run-execution-{uuid4()}",
                source_path="/tmp/run-execution.epub",
                title="Run Execution Document",
                author="Run Execution Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.commit()
            return document.id

    def _create_running_run(self, *, budget: RunBudgetSummary | None = None) -> str:
        document_id = self._create_document()
        return self._create_running_run_for_document(document_id, budget=budget)

    def _create_running_run_for_document(
        self,
        document_id: str,
        *,
        budget: RunBudgetSummary | None = None,
    ) -> str:
        with self.session_factory() as session:
            repository = RunControlRepository(session)
            control = RunControlService(repository)
            run = control.create_run(
                document_id=document_id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                requested_by="test-runner",
                budget=budget,
            )
            resumed = control.resume_run(run.run_id, actor_id="test-runner", note="start")
            session.commit()
            return resumed.run_id

    def _create_document_with_chapter_packets(
        self,
        chapter_packet_ordinals: list[list[int]],
    ) -> tuple[str, list[list[str]]]:
        document_id = self._create_document()
        with self.session_factory() as session:
            packet_ids_by_chapter: list[list[str]] = []
            for chapter_index, packet_ordinals in enumerate(chapter_packet_ordinals, start=1):
                chapter = Chapter(
                    document_id=document_id,
                    ordinal=chapter_index,
                    title_src=f"Chapter {chapter_index}",
                    status=ChapterStatus.PACKET_BUILT,
                    metadata_json={},
                )
                session.add(chapter)
                session.flush()

                chapter_packet_ids: list[str] = []
                for packet_ordinal in packet_ordinals:
                    block = Block(
                        chapter_id=chapter.id,
                        ordinal=packet_ordinal,
                        block_type=BlockType.PARAGRAPH,
                        source_text=f"Chapter {chapter_index} packet {packet_ordinal}",
                        normalized_text=f"Chapter {chapter_index} packet {packet_ordinal}",
                        source_span_json={},
                        protected_policy=ProtectedPolicy.TRANSLATE,
                        status=ArtifactStatus.ACTIVE,
                    )
                    session.add(block)
                    session.flush()

                    packet = TranslationPacket(
                        chapter_id=chapter.id,
                        block_start_id=block.id,
                        block_end_id=block.id,
                        packet_type=PacketType.TRANSLATE,
                        book_profile_version=1,
                        chapter_brief_version=1,
                        termbase_version=1,
                        entity_snapshot_version=1,
                        style_snapshot_version=1,
                        packet_json={
                            "packet_id": str(uuid4()),
                            "chapter_id": chapter.id,
                            "current_blocks": [{"block_id": block.id, "sentence_ids": []}],
                            "packet_ordinal": packet_ordinal,
                            "input_version_bundle": {
                                "chapter_id": chapter.id,
                                "packet_ordinal": packet_ordinal,
                            },
                            "runtime_state": {
                                "stage": "translate",
                                "substate": "ready",
                                "packet_ordinal": packet_ordinal,
                            },
                        },
                        risk_score=0.1,
                        status=PacketStatus.BUILT,
                    )
                    session.add(packet)
                    session.flush()
                    chapter_packet_ids.append(packet.id)

                packet_ids_by_chapter.append(chapter_packet_ids)

            session.commit()
        return document_id, packet_ids_by_chapter

    def test_run_execution_success_lifecycle_updates_usage_and_terminal_state(self) -> None:
        run_id = self._create_running_run()
        packet_id = str(uuid4())

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            execution.seed_translate_work_items(run_id=run_id, packet_ids=[packet_id])
            claimed = execution.claim_next_translate_work_item(
                run_id=run_id,
                worker_name="test.translate",
                worker_instance_id="worker-1",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=60)
            self.assertTrue(execution.heartbeat_work_item(lease_token=claimed.lease_token, lease_seconds=60))
            execution.complete_translate_success(
                lease_token=claimed.lease_token,
                packet_id=packet_id,
                translation_run_id=str(uuid4()),
                token_in=120,
                token_out=45,
                cost_usd=0.0035,
                latency_ms=750,
            )
            summary = execution.reconcile_run_terminal_state(run_id=run_id)

        self.assertEqual(summary.status, "succeeded")
        self.assertEqual(summary.work_items.status_counts["succeeded"], 1)
        self.assertEqual(summary.status_detail_json["usage_summary"]["token_in"], 120)
        self.assertEqual(summary.status_detail_json["usage_summary"]["token_out"], 45)
        self.assertEqual(summary.status_detail_json["usage_summary"]["latency_ms"], 750)
        self.assertAlmostEqual(summary.status_detail_json["usage_summary"]["cost_usd"], 0.0035, places=8)

    def test_reclaim_expired_lease_requeues_work_item_and_increments_attempt_on_reclaim(self) -> None:
        run_id = self._create_running_run(
            budget=RunBudgetSummary(
                max_wall_clock_seconds=None,
                max_total_cost_usd=None,
                max_total_token_in=None,
                max_total_token_out=None,
                max_retry_count_per_work_item=2,
                max_consecutive_failures=None,
                max_parallel_workers=1,
                max_parallel_requests_per_provider=1,
                max_auto_followup_attempts=1,
            )
        )
        packet_id = str(uuid4())

        with self.session_factory() as session:
            repository = RunControlRepository(session)
            execution = RunExecutionService(repository)
            execution.seed_translate_work_items(run_id=run_id, packet_ids=[packet_id])
            claimed = execution.claim_next_translate_work_item(
                run_id=run_id,
                worker_name="test.translate",
                worker_instance_id="worker-2",
                lease_seconds=30,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=30)

            lease = repository.get_active_lease_by_token(claimed.lease_token)
            work_item = repository.get_work_item(claimed.work_item_id)
            expired_at = datetime.now(timezone.utc) - timedelta(minutes=2)
            lease.lease_expires_at = expired_at
            work_item.lease_expires_at = expired_at
            session.commit()

            reclaimed = execution.reclaim_expired_leases(run_id=run_id)
            self.assertEqual(reclaimed.expired_lease_count, 1)
            self.assertEqual(reclaimed.reclaimed_work_item_ids, [claimed.work_item_id])

            reclaimed_claim = execution.claim_next_translate_work_item(
                run_id=run_id,
                worker_name="test.translate",
                worker_instance_id="worker-3",
                lease_seconds=30,
            )

        self.assertIsNotNone(reclaimed_claim)
        assert reclaimed_claim is not None
        self.assertEqual(reclaimed_claim.attempt, 2)

    def test_ensure_scope_replay_work_items_seeds_missing_chapter_review_once(self) -> None:
        document_id = self._create_document()
        run_id = self._create_running_run_for_document(document_id)
        chapter_scope_id = str(uuid4())

        with self.session_factory() as session:
            repository = RunControlRepository(session)
            execution = RunExecutionService(repository)

            first_ids = execution.ensure_scope_replay_work_items(
                run_id=run_id,
                stage=WorkItemStage.REVIEW,
                scope_type=WorkItemScopeType.CHAPTER,
                scope_ids=[chapter_scope_id],
                input_version_bundle_by_scope_id={
                    chapter_scope_id: {
                        "document_id": document_id,
                        "chapter_id": chapter_scope_id,
                    }
                },
            )
            second_ids = execution.ensure_scope_replay_work_items(
                run_id=run_id,
                stage=WorkItemStage.REVIEW,
                scope_type=WorkItemScopeType.CHAPTER,
                scope_ids=[chapter_scope_id],
                input_version_bundle_by_scope_id={
                    chapter_scope_id: {
                        "document_id": document_id,
                        "chapter_id": chapter_scope_id,
                    }
                },
            )

            persisted_items = session.query(WorkItem).filter(
                WorkItem.run_id == run_id,
                WorkItem.stage == WorkItemStage.REVIEW,
                WorkItem.scope_type == WorkItemScopeType.CHAPTER,
                WorkItem.scope_id == chapter_scope_id,
            ).all()

        self.assertEqual(len(first_ids), 1)
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(len(persisted_items), 1)
        self.assertEqual(persisted_items[0].status, WorkItemStatus.PENDING)
        self.assertEqual(persisted_items[0].input_version_bundle_json["document_id"], document_id)

    def test_ensure_repair_dispatch_work_item_seeds_claimable_repair_lane_once(self) -> None:
        run_id = self._create_running_run()
        proposal_id = str(uuid4())

        with self.session_factory() as session:
            repository = RunControlRepository(session)
            execution = RunExecutionService(repository)
            first_id = execution.ensure_repair_dispatch_work_item(
                run_id=run_id,
                proposal_id=proposal_id,
                incident_id=str(uuid4()),
                repair_dispatch_json={
                    "dispatch_id": str(uuid4()),
                    "patch_surface": "runtime_bundle",
                    "claim_mode": "runtime_owned",
                    "claim_target": "runtime_patch_proposal",
                    "lane": "runtime.repair",
                    "worker_hint": "review_deadlock_repair_agent",
                    "worker_contract_version": 1,
                    "execution_mode": "transport_backed",
                    "executor_hint": "python_transport_repair_executor",
                    "executor_contract_version": 1,
                    "transport_hint": "python_subprocess_repair_transport",
                    "transport_contract_version": 1,
                    "validation_command": "uv run pytest tests/test_incident_controller.py",
                    "bundle_revision_name": "bundle-repair-1",
                    "rollout_scope_json": {"mode": "dev"},
                    "replay": {
                        "scope_type": "chapter",
                        "scope_id": "chapter-1",
                        "boundary": "review_session",
                    },
                },
                repair_plan_json={
                    "goal": "Repair repeated review deadlock and replay the minimal chapter review scope.",
                    "owned_files": ["src/book_agent/app/runtime/controllers/review_controller.py"],
                    "validation": {
                        "command": "uv run pytest tests/test_incident_controller.py",
                        "scope": "review_deadlock",
                    },
                    "bundle": {
                        "revision_name": "bundle-repair-1",
                        "manifest_json": {"code": {"surface": "review_deadlock"}},
                        "rollout_scope_json": {"mode": "dev"},
                    },
                    "replay": {
                        "scope_type": "chapter",
                        "scope_id": "chapter-1",
                        "boundary": "review_session",
                    },
                },
            )
            second_id = execution.ensure_repair_dispatch_work_item(
                run_id=run_id,
                proposal_id=proposal_id,
                incident_id=str(uuid4()),
                repair_dispatch_json={
                    "dispatch_id": str(uuid4()),
                    "patch_surface": "runtime_bundle",
                    "claim_mode": "runtime_owned",
                    "claim_target": "runtime_patch_proposal",
                    "lane": "runtime.repair",
                    "worker_hint": "review_deadlock_repair_agent",
                    "worker_contract_version": 1,
                    "execution_mode": "transport_backed",
                    "executor_hint": "python_transport_repair_executor",
                    "executor_contract_version": 1,
                    "transport_hint": "python_subprocess_repair_transport",
                    "transport_contract_version": 1,
                    "validation_command": "uv run pytest tests/test_incident_controller.py",
                    "bundle_revision_name": "bundle-repair-1",
                    "rollout_scope_json": {"mode": "dev"},
                    "replay": {
                        "scope_type": "chapter",
                        "scope_id": "chapter-1",
                        "boundary": "review_session",
                    },
                },
            )
            work_item = repository.get_work_item(first_id)

        self.assertEqual(first_id, second_id)
        self.assertEqual(work_item.stage, WorkItemStage.REPAIR)
        self.assertEqual(work_item.scope_type, WorkItemScopeType.ISSUE_ACTION)
        self.assertEqual(work_item.scope_id, proposal_id)
        self.assertEqual(work_item.status, WorkItemStatus.PENDING)
        self.assertEqual(work_item.input_version_bundle_json["proposal_id"], proposal_id)
        self.assertEqual(work_item.input_version_bundle_json["target_scope_type"], "chapter")
        self.assertEqual(work_item.input_version_bundle_json["claim_mode"], "runtime_owned")
        self.assertEqual(work_item.input_version_bundle_json["claim_target"], "runtime_patch_proposal")
        self.assertEqual(work_item.input_version_bundle_json["dispatch_lane"], "runtime.repair")
        self.assertEqual(work_item.input_version_bundle_json["worker_hint"], "review_deadlock_repair_agent")
        self.assertEqual(work_item.input_version_bundle_json["worker_contract_version"], 1)
        self.assertEqual(work_item.input_version_bundle_json["execution_mode"], "transport_backed")
        self.assertEqual(work_item.input_version_bundle_json["executor_hint"], "python_transport_repair_executor")
        self.assertEqual(work_item.input_version_bundle_json["executor_contract_version"], 1)
        self.assertEqual(work_item.input_version_bundle_json["transport_hint"], "python_subprocess_repair_transport")
        self.assertEqual(work_item.input_version_bundle_json["transport_contract_version"], 1)
        self.assertEqual(work_item.input_version_bundle_json["repair_request_contract_version"], 1)
        self.assertEqual(
            work_item.input_version_bundle_json["repair_goal"],
            "Repair repeated review deadlock and replay the minimal chapter review scope.",
        )
        self.assertEqual(
            work_item.input_version_bundle_json["owned_files"],
            ["src/book_agent/app/runtime/controllers/review_controller.py"],
        )
        self.assertEqual(
            work_item.input_version_bundle_json["validation_json"]["scope"],
            "review_deadlock",
        )
        self.assertEqual(
            work_item.input_version_bundle_json["bundle_json"]["manifest_json"]["code"]["surface"],
            "review_deadlock",
        )
        self.assertEqual(
            work_item.input_version_bundle_json["repair_plan_json"]["replay"]["scope_id"],
            "chapter-1",
        )

    def test_executor_fails_repair_work_item_for_unknown_worker_hint(self) -> None:
        run_id = self._create_running_run()
        proposal_id = str(uuid4())
        incident_id = str(uuid4())

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            work_item_id = execution.ensure_repair_dispatch_work_item(
                run_id=run_id,
                proposal_id=proposal_id,
                incident_id=incident_id,
                repair_dispatch_json={
                    "dispatch_id": str(uuid4()),
                    "patch_surface": "runtime_bundle",
                    "claim_mode": "runtime_owned",
                    "claim_target": "runtime_patch_proposal",
                    "lane": "runtime.repair",
                    "worker_hint": "unknown_runtime_repair_agent",
                    "worker_contract_version": 1,
                    "execution_mode": "in_process",
                    "executor_hint": "python_repair_executor",
                    "executor_contract_version": 1,
                    "validation_command": "uv run pytest tests/test_incident_controller.py",
                    "bundle_revision_name": "bundle-repair-unknown",
                    "rollout_scope_json": {"mode": "dev"},
                    "replay": {
                        "scope_type": "chapter",
                        "scope_id": "chapter-unknown",
                        "boundary": "review_session",
                    },
                },
            )
            claimed = execution.claim_repair_dispatch_work_item(
                work_item_id=work_item_id,
                worker_name="app.run.repair",
                worker_instance_id="app.repair:test",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            session.commit()

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root="/tmp",
            translation_worker=None,
        )
        executor._execute_repair_work_item(run_id, claimed)

        with self.session_factory() as session:
            work_item = session.get(WorkItem, work_item_id)
            self.assertIsNotNone(work_item)
            assert work_item is not None
            self.assertEqual(work_item.status, WorkItemStatus.TERMINAL_FAILED)
            self.assertEqual(work_item.error_class, "UnsupportedRuntimeRepairWorkerError")
            self.assertIn(
                "unknown_runtime_repair_agent",
                str((work_item.error_detail_json or {}).get("message") or ""),
            )

    def test_executor_fails_repair_work_item_for_unknown_executor_hint(self) -> None:
        run_id = self._create_running_run()
        proposal_id = str(uuid4())
        incident_id = str(uuid4())

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            work_item_id = execution.ensure_repair_dispatch_work_item(
                run_id=run_id,
                proposal_id=proposal_id,
                incident_id=incident_id,
                repair_dispatch_json={
                    "dispatch_id": str(uuid4()),
                    "patch_surface": "runtime_bundle",
                    "claim_mode": "runtime_owned",
                    "claim_target": "runtime_patch_proposal",
                    "lane": "runtime.repair",
                    "worker_hint": "review_deadlock_repair_agent",
                    "worker_contract_version": 1,
                    "execution_mode": "in_process",
                    "executor_hint": "unknown_repair_executor",
                    "executor_contract_version": 1,
                    "validation_command": "uv run pytest tests/test_incident_controller.py",
                    "bundle_revision_name": "bundle-repair-unknown-executor",
                    "rollout_scope_json": {"mode": "dev"},
                    "replay": {
                        "scope_type": "chapter",
                        "scope_id": "chapter-unknown-executor",
                        "boundary": "review_session",
                    },
                },
            )
            claimed = execution.claim_repair_dispatch_work_item(
                work_item_id=work_item_id,
                worker_name="app.run.repair",
                worker_instance_id="app.repair:test",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            session.commit()

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root="/tmp",
            translation_worker=None,
        )
        executor._execute_repair_work_item(run_id, claimed)

        with self.session_factory() as session:
            work_item = session.get(WorkItem, work_item_id)
            self.assertIsNotNone(work_item)
            assert work_item is not None
            self.assertEqual(work_item.status, WorkItemStatus.TERMINAL_FAILED)
            self.assertEqual(work_item.error_class, "UnsupportedRuntimeRepairExecutorError")
            self.assertIn(
                "unknown_repair_executor",
                str((work_item.error_detail_json or {}).get("message") or ""),
            )

    def test_executor_fails_repair_work_item_for_unknown_transport_hint(self) -> None:
        run_id = self._create_running_run()
        proposal_id = str(uuid4())
        incident_id = str(uuid4())

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            work_item_id = execution.ensure_repair_dispatch_work_item(
                run_id=run_id,
                proposal_id=proposal_id,
                incident_id=incident_id,
                repair_dispatch_json={
                    "dispatch_id": str(uuid4()),
                    "patch_surface": "runtime_bundle",
                    "claim_mode": "runtime_owned",
                    "claim_target": "runtime_patch_proposal",
                    "lane": "runtime.repair",
                    "worker_hint": "review_deadlock_repair_agent",
                    "worker_contract_version": 1,
                    "execution_mode": "transport_backed",
                    "executor_hint": "python_transport_repair_executor",
                    "executor_contract_version": 1,
                    "transport_hint": "unknown_repair_transport",
                    "transport_contract_version": 1,
                    "validation_command": "uv run pytest tests/test_incident_controller.py",
                    "bundle_revision_name": "bundle-repair-unknown-transport",
                    "rollout_scope_json": {"mode": "dev"},
                    "replay": {
                        "scope_type": "chapter",
                        "scope_id": "chapter-unknown-transport",
                        "boundary": "review_session",
                    },
                },
            )
            claimed = execution.claim_repair_dispatch_work_item(
                work_item_id=work_item_id,
                worker_name="app.run.repair",
                worker_instance_id="app.repair:test",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            session.commit()

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root="/tmp",
            translation_worker=None,
        )
        executor._execute_repair_work_item(run_id, claimed)

        with self.session_factory() as session:
            work_item = session.get(WorkItem, work_item_id)
            self.assertIsNotNone(work_item)
            assert work_item is not None
            self.assertEqual(work_item.status, WorkItemStatus.TERMINAL_FAILED)
            self.assertEqual(work_item.error_class, "UnsupportedRuntimeRepairTransportError")
            self.assertIn(
                "unknown_repair_transport",
                str((work_item.error_detail_json or {}).get("message") or ""),
            )

    def test_retry_later_repair_work_item_respects_retry_after_before_reclaim(self) -> None:
        run_id = self._create_running_run()
        proposal_id = str(uuid4())
        incident_id = str(uuid4())

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            work_item_id = execution.ensure_repair_dispatch_work_item(
                run_id=run_id,
                proposal_id=proposal_id,
                incident_id=incident_id,
                repair_dispatch_json={
                    "dispatch_id": str(uuid4()),
                    "patch_surface": "runtime_bundle",
                    "claim_mode": "runtime_owned",
                    "claim_target": "runtime_patch_proposal",
                    "lane": "runtime.repair",
                    "worker_hint": "review_deadlock_repair_agent",
                    "worker_contract_version": 1,
                    "execution_mode": "transport_backed",
                    "executor_hint": "python_contract_transport_repair_executor",
                    "executor_contract_version": 1,
                    "transport_hint": "http_contract_repair_transport",
                    "transport_contract_version": 1,
                    "validation_command": "uv run pytest tests/test_incident_controller.py",
                    "bundle_revision_name": "bundle-repair-retry-later",
                    "rollout_scope_json": {"mode": "dev"},
                    "replay": {
                        "scope_type": "chapter",
                        "scope_id": "chapter-retry-later",
                        "boundary": "review_session",
                    },
                },
            )
            claimed = execution.claim_repair_dispatch_work_item(
                work_item_id=work_item_id,
                worker_name="app.run.repair",
                worker_instance_id="app.repair:test",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=60)
            execution.complete_work_item_failure(
                lease_token=claimed.lease_token,
                error_class="RuntimeRepairRetryLater",
                error_detail_json={
                    "repair_agent_decision": "retry_later",
                    "repair_result_json": {
                        "repair_agent_decision": "retry_later",
                        "repair_agent_retry_after_seconds": 300,
                    },
                },
                retryable=True,
            )
            session.commit()

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            self.assertEqual(
                execution.repository.list_claimable_work_item_ids(run_id, stage=WorkItemStage.REPAIR),
                [],
            )
            self.assertIsNone(
                execution.claim_next_work_item(
                    run_id=run_id,
                    stage=WorkItemStage.REPAIR,
                    worker_name="app.run.repair",
                    worker_instance_id="app.repair:test-2",
                    lease_seconds=60,
                )
            )
            work_item = session.get(WorkItem, work_item_id)
            self.assertIsNotNone(work_item)
            assert work_item is not None
            work_item.finished_at = datetime.now(timezone.utc) - timedelta(seconds=301)
            session.add(work_item)
            session.commit()

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            self.assertEqual(
                execution.repository.list_claimable_work_item_ids(run_id, stage=WorkItemStage.REPAIR),
                [work_item_id],
            )
            reclaimed = execution.claim_next_work_item(
                run_id=run_id,
                stage=WorkItemStage.REPAIR,
                worker_name="app.run.repair",
                worker_instance_id="app.repair:test-3",
                lease_seconds=60,
            )
            self.assertIsNotNone(reclaimed)
            assert reclaimed is not None
            self.assertEqual(reclaimed.work_item_id, work_item_id)

    def test_manual_escalation_repair_work_item_requires_explicit_resume_before_reclaim(self) -> None:
        run_id = self._create_running_run()
        proposal_id = str(uuid4())
        incident_id = str(uuid4())

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            work_item_id = execution.ensure_repair_dispatch_work_item(
                run_id=run_id,
                proposal_id=proposal_id,
                incident_id=incident_id,
                repair_dispatch_json={
                    "dispatch_id": str(uuid4()),
                    "patch_surface": "runtime_bundle",
                    "claim_mode": "runtime_owned",
                    "claim_target": "runtime_patch_proposal",
                    "lane": "runtime.repair",
                    "worker_hint": "export_routing_repair_agent",
                    "worker_contract_version": 1,
                    "execution_mode": "transport_backed",
                    "executor_hint": "python_contract_transport_repair_executor",
                    "executor_contract_version": 1,
                    "transport_hint": "http_contract_repair_transport",
                    "transport_contract_version": 1,
                    "validation_command": "uv run pytest tests/test_export_controller.py",
                    "bundle_revision_name": "bundle-repair-manual-escalation",
                    "rollout_scope_json": {"mode": "dev"},
                    "replay": {
                        "scope_type": "packet",
                        "scope_id": "packet-manual-escalation",
                        "boundary": "packet_task",
                    },
                },
            )
            claimed = execution.claim_repair_dispatch_work_item(
                work_item_id=work_item_id,
                worker_name="app.run.repair",
                worker_instance_id="app.repair:test",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=60)
            execution.complete_work_item_failure(
                lease_token=claimed.lease_token,
                error_class="RuntimeRepairManualEscalationRequired",
                error_detail_json={
                    "repair_agent_decision": "manual_escalation_required",
                    "repair_agent_decision_reason": "requires_operator_review",
                    "repair_result_json": {
                        "repair_agent_decision": "manual_escalation_required",
                        "repair_agent_decision_reason": "requires_operator_review",
                    },
                },
                retryable=False,
            )
            session.commit()

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            self.assertEqual(
                execution.repository.list_claimable_work_item_ids(run_id, stage=WorkItemStage.REPAIR),
                [],
            )
            self.assertIsNone(
                execution.claim_next_work_item(
                    run_id=run_id,
                    stage=WorkItemStage.REPAIR,
                    worker_name="app.run.repair",
                    worker_instance_id="app.repair:block",
                    lease_seconds=60,
                )
            )
            resumed = execution.resume_repair_dispatch_work_item(
                work_item_id=work_item_id,
                actor_id="ops-user",
                note="manual approval granted",
            )
            self.assertEqual(resumed.status, WorkItemStatus.PENDING)
            self.assertEqual(resumed.attempt, 2)
            session.commit()

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            self.assertEqual(
                execution.repository.list_claimable_work_item_ids(run_id, stage=WorkItemStage.REPAIR),
                [work_item_id],
            )
            reclaimed = execution.claim_next_work_item(
                run_id=run_id,
                stage=WorkItemStage.REPAIR,
                worker_name="app.run.repair",
                worker_instance_id="app.repair:resumed",
                lease_seconds=60,
            )
            self.assertIsNotNone(reclaimed)
            assert reclaimed is not None
            self.assertEqual(reclaimed.work_item_id, work_item_id)
            self.assertEqual(reclaimed.attempt, 2)

    def test_manual_escalation_repair_item_blocks_reseed_until_resumed(self) -> None:
        run_id = self._create_running_run()
        proposal_id = str(uuid4())
        incident_id = str(uuid4())

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            work_item_id = execution.ensure_repair_dispatch_work_item(
                run_id=run_id,
                proposal_id=proposal_id,
                incident_id=incident_id,
                repair_dispatch_json={
                    "dispatch_id": str(uuid4()),
                    "patch_surface": "runtime_bundle",
                    "claim_mode": "runtime_owned",
                    "claim_target": "runtime_patch_proposal",
                    "lane": "runtime.repair",
                    "worker_hint": "review_deadlock_repair_agent",
                    "worker_contract_version": 1,
                    "execution_mode": "transport_backed",
                    "executor_hint": "python_contract_transport_repair_executor",
                    "executor_contract_version": 1,
                    "transport_hint": "http_contract_repair_transport",
                    "transport_contract_version": 1,
                    "validation_command": "uv run pytest tests/test_incident_controller.py",
                    "bundle_revision_name": "bundle-repair-manual-reseed",
                    "rollout_scope_json": {"mode": "dev"},
                    "replay": {
                        "scope_type": "chapter",
                        "scope_id": "chapter-manual-escalation",
                        "boundary": "review_session",
                    },
                },
            )
            claimed = execution.claim_repair_dispatch_work_item(
                work_item_id=work_item_id,
                worker_name="app.run.repair",
                worker_instance_id="app.repair:test",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=60)
            execution.complete_work_item_failure(
                lease_token=claimed.lease_token,
                error_class="RuntimeRepairManualEscalationRequired",
                error_detail_json={
                    "repair_agent_decision": "manual_escalation_required",
                    "repair_result_json": {
                        "repair_agent_decision": "manual_escalation_required",
                    },
                },
                retryable=False,
            )
            session.commit()

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            ensured_ids = execution.ensure_scope_replay_work_items(
                run_id=run_id,
                stage=WorkItemStage.REPAIR,
                scope_type=WorkItemScopeType.ISSUE_ACTION,
                scope_ids=[proposal_id],
                input_version_bundle_by_scope_id={
                    proposal_id: {
                        "proposal_id": proposal_id,
                        "incident_id": incident_id,
                    }
                },
            )
            self.assertEqual(ensured_ids, [work_item_id])
            work_items = session.query(WorkItem).filter(WorkItem.run_id == run_id).all()
            self.assertEqual(len(work_items), 1)

    def test_executor_reclaims_expired_leases_before_stage_progression(self) -> None:
        run_id = self._create_running_run(
            budget=RunBudgetSummary(
                max_wall_clock_seconds=None,
                max_total_cost_usd=None,
                max_total_token_in=None,
                max_total_token_out=None,
                max_retry_count_per_work_item=2,
                max_consecutive_failures=None,
                max_parallel_workers=1,
                max_parallel_requests_per_provider=1,
                max_auto_followup_attempts=1,
            )
        )
        packet_id = str(uuid4())

        with self.session_factory() as session:
            repository = RunControlRepository(session)
            execution = RunExecutionService(repository)
            execution.seed_translate_work_items(run_id=run_id, packet_ids=[packet_id])
            claimed = execution.claim_next_translate_work_item(
                run_id=run_id,
                worker_name="test.translate",
                worker_instance_id="worker-expired",
                lease_seconds=30,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=30)

            lease = repository.get_active_lease_by_token(claimed.lease_token)
            work_item = repository.get_work_item(claimed.work_item_id)
            expired_at = datetime.now(timezone.utc) - timedelta(minutes=2)
            lease.lease_expires_at = expired_at
            work_item.lease_expires_at = expired_at
            session.commit()

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root="/tmp",
            translation_worker=None,
        )

        self.assertTrue(executor._reclaim_expired_leases(run_id))

        with self.session_factory() as session:
            repository = RunControlRepository(session)
            execution = RunExecutionService(repository)
            reclaimed_claim = execution.claim_next_translate_work_item(
                run_id=run_id,
                worker_name="test.translate",
                worker_instance_id="worker-after-reclaim",
                lease_seconds=30,
            )

        self.assertIsNotNone(reclaimed_claim)
        assert reclaimed_claim is not None
        self.assertEqual(reclaimed_claim.attempt, 2)

    def test_document_run_executor_controller_runner_reconcile_is_best_effort_and_throttled(self) -> None:
        run_id = self._create_running_run()
        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root="/tmp",
            translation_worker=None,
            controller_reconcile_interval_seconds=999.0,
        )

        calls: list[str] = []

        class _ExplodingRunner:
            def reconcile_run(self, *, run_id: str) -> None:
                calls.append(run_id)
                raise RuntimeError("controller runner exploded")

        executor._controller_runner = _ExplodingRunner()  # type: ignore[assignment]

        executor._maybe_reconcile_controllers(run_id)
        self.assertEqual(calls, [run_id])

        # Second call should be throttled even if the first attempt errored.
        executor._maybe_reconcile_controllers(run_id)
        self.assertEqual(calls, [run_id])

    def test_claim_translate_work_items_prefers_front_packet_even_when_work_item_order_is_reversed(self) -> None:
        document_id, packet_ids_by_chapter = self._create_document_with_chapter_packets([[1, 2]])
        run_id = self._create_running_run_for_document(document_id)
        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root="/tmp",
            translation_worker=None,
        )

        first_packet_id, second_packet_id = packet_ids_by_chapter[0]
        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            versions = executor._translate_input_versions(session, [second_packet_id, first_packet_id])
            self.assertEqual(versions[first_packet_id]["packet_ordinal"], 1)
            self.assertEqual(versions[second_packet_id]["packet_ordinal"], 2)

            execution.seed_translate_work_items(
                run_id=run_id,
                packet_ids=[second_packet_id, first_packet_id],
                input_version_bundle_by_packet_id=versions,
            )
            items = executor._list_stage_items(session, run_id, WorkItemStage.TRANSLATE)
            item_by_scope_id = {str(item.scope_id): item for item in items}
            item_by_scope_id[second_packet_id].created_at = datetime(2026, 3, 22, 0, 0, 0, tzinfo=timezone.utc)
            item_by_scope_id[first_packet_id].created_at = datetime(2026, 3, 22, 0, 1, 0, tzinfo=timezone.utc)
            session.commit()

            translate_items = executor._list_stage_items(session, run_id, WorkItemStage.TRANSLATE)
            claimed = executor._claim_translate_work_items(
                session=session,
                execution=execution,
                run_id=run_id,
                translate_items=translate_items,
            )
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0].scope_id, first_packet_id)

            leased_packet = session.get(TranslationPacket, first_packet_id)
            self.assertIsNotNone(leased_packet)
            assert leased_packet is not None
            self.assertEqual(leased_packet.packet_json["runtime_state"]["substate"], "leased")
            self.assertEqual(leased_packet.packet_json["runtime_state"]["packet_ordinal"], 1)
            self.assertEqual(leased_packet.packet_json["runtime_state"]["work_item_id"], claimed[0].work_item_id)

            waiting_packet = session.get(TranslationPacket, second_packet_id)
            self.assertIsNotNone(waiting_packet)
            assert waiting_packet is not None
            self.assertEqual(waiting_packet.packet_json["runtime_state"]["substate"], "ready")

    def test_seedable_translate_packet_ids_only_advance_frontier_for_unblocked_chapter(self) -> None:
        document_id, packet_ids_by_chapter = self._create_document_with_chapter_packets([[1, 2], [1, 2]])
        run_id = self._create_running_run_for_document(document_id)
        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root="/tmp",
            translation_worker=None,
        )

        chapter_one_first, chapter_one_second = packet_ids_by_chapter[0]
        chapter_two_first, chapter_two_second = packet_ids_by_chapter[1]

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            seedable = executor._list_seedable_translate_packet_ids(
                session=session,
                run_id=run_id,
                document_id=document_id,
                translate_items=[],
            )
            self.assertEqual(set(seedable), {chapter_one_first, chapter_two_first})

            execution.seed_translate_work_items(
                run_id=run_id,
                packet_ids=seedable,
                input_version_bundle_by_packet_id=executor._translate_input_versions(session, seedable),
            )
            translate_items = executor._list_stage_items(session, run_id, WorkItemStage.TRANSLATE)
            self.assertEqual(len(translate_items), 2)

            blocked_seedable = executor._list_seedable_translate_packet_ids(
                session=session,
                run_id=run_id,
                document_id=document_id,
                translate_items=translate_items,
            )
            self.assertEqual(blocked_seedable, [])

            completed_item = next(item for item in translate_items if str(item.scope_id) == chapter_one_first)
            completed_item.status = WorkItemStatus.SUCCEEDED
            completed_packet = session.get(TranslationPacket, chapter_one_first)
            self.assertIsNotNone(completed_packet)
            assert completed_packet is not None
            completed_packet.status = PacketStatus.TRANSLATED
            completed_packet.packet_json = {
                **dict(completed_packet.packet_json or {}),
                "runtime_state": {
                    "stage": "translate",
                    "substate": "translated",
                    "packet_ordinal": 1,
                },
            }
            session.merge(completed_item)
            session.merge(completed_packet)
            session.commit()

            refreshed_items = executor._list_stage_items(session, run_id, WorkItemStage.TRANSLATE)
            next_seedable = executor._list_seedable_translate_packet_ids(
                session=session,
                run_id=run_id,
                document_id=document_id,
                translate_items=refreshed_items,
            )
            self.assertEqual(next_seedable, [chapter_one_second])
            self.assertNotIn(chapter_two_second, next_seedable)

    def test_retryable_front_packet_blocks_same_chapter_followup_until_reclaimed_packet_is_retried(self) -> None:
        document_id, packet_ids_by_chapter = self._create_document_with_chapter_packets([[1, 2]])
        run_id = self._create_running_run_for_document(
            document_id,
            budget=RunBudgetSummary(
                max_wall_clock_seconds=None,
                max_total_cost_usd=None,
                max_total_token_in=None,
                max_total_token_out=None,
                max_retry_count_per_work_item=2,
                max_consecutive_failures=None,
                max_parallel_workers=1,
                max_parallel_requests_per_provider=1,
                max_auto_followup_attempts=1,
            ),
        )
        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root="/tmp",
            translation_worker=None,
        )
        first_packet_id, second_packet_id = packet_ids_by_chapter[0]

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            seeded_packet_ids = executor._seed_translate_frontier_work_items(
                session=session,
                execution=execution,
                run_id=run_id,
                document_id=document_id,
                translate_items=[],
            )
            self.assertEqual(seeded_packet_ids, [first_packet_id])

            translate_items = executor._list_stage_items(session, run_id, WorkItemStage.TRANSLATE)
            claimed = executor._claim_translate_work_items(
                session=session,
                execution=execution,
                run_id=run_id,
                translate_items=translate_items,
            )
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0].scope_id, first_packet_id)

            execution.start_work_item(lease_token=claimed[0].lease_token, lease_seconds=30)
            lease = RunControlRepository(session).get_active_lease_by_token(claimed[0].lease_token)
            work_item = RunControlRepository(session).get_work_item(claimed[0].work_item_id)
            expired_at = datetime.now(timezone.utc) - timedelta(minutes=2)
            lease.lease_expires_at = expired_at
            work_item.lease_expires_at = expired_at
            session.commit()

        self.assertTrue(executor._reclaim_expired_leases(run_id))

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            refreshed_items = executor._list_stage_items(session, run_id, WorkItemStage.TRANSLATE)
            self.assertEqual(len(refreshed_items), 1)
            self.assertEqual(refreshed_items[0].status, WorkItemStatus.RETRYABLE_FAILED)
            self.assertEqual(str(refreshed_items[0].scope_id), first_packet_id)

            blocked_seedable = executor._list_seedable_translate_packet_ids(
                session=session,
                run_id=run_id,
                document_id=document_id,
                translate_items=refreshed_items,
            )
            self.assertEqual(blocked_seedable, [])

            reclaimed_claim = executor._claim_translate_work_items(
                session=session,
                execution=execution,
                run_id=run_id,
                translate_items=refreshed_items,
            )
            self.assertEqual(len(reclaimed_claim), 1)
            self.assertEqual(reclaimed_claim[0].scope_id, first_packet_id)
            self.assertEqual(reclaimed_claim[0].attempt, 2)

            waiting_packet = session.get(TranslationPacket, second_packet_id)
            self.assertIsNotNone(waiting_packet)
            assert waiting_packet is not None
            self.assertEqual(waiting_packet.packet_json["runtime_state"]["substate"], "ready")

    def test_budget_guardrail_pauses_run_when_cost_limit_is_exceeded(self) -> None:
        run_id = self._create_running_run(
            budget=RunBudgetSummary(
                max_wall_clock_seconds=None,
                max_total_cost_usd=0.001,
                max_total_token_in=None,
                max_total_token_out=None,
                max_retry_count_per_work_item=1,
                max_consecutive_failures=None,
                max_parallel_workers=1,
                max_parallel_requests_per_provider=1,
                max_auto_followup_attempts=1,
            )
        )
        packet_id = str(uuid4())

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            execution.seed_translate_work_items(run_id=run_id, packet_ids=[packet_id])
            claimed = execution.claim_next_translate_work_item(
                run_id=run_id,
                worker_name="test.translate",
                worker_instance_id="worker-4",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=60)
            execution.complete_translate_success(
                lease_token=claimed.lease_token,
                packet_id=packet_id,
                translation_run_id=str(uuid4()),
                token_in=10,
                token_out=5,
                cost_usd=0.005,
                latency_ms=100,
            )
            guardrail = execution.enforce_budget_guardrails(run_id=run_id)

        self.assertTrue(guardrail.budget_exceeded)
        self.assertEqual(guardrail.stop_reason, "budget.cost_exceeded")
        self.assertEqual(guardrail.run_summary.status, "paused")

    def test_consecutive_failure_budget_fails_run(self) -> None:
        run_id = self._create_running_run(
            budget=RunBudgetSummary(
                max_wall_clock_seconds=None,
                max_total_cost_usd=None,
                max_total_token_in=None,
                max_total_token_out=None,
                max_retry_count_per_work_item=0,
                max_consecutive_failures=1,
                max_parallel_workers=1,
                max_parallel_requests_per_provider=1,
                max_auto_followup_attempts=1,
            )
        )
        packet_id = str(uuid4())

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            execution.seed_translate_work_items(run_id=run_id, packet_ids=[packet_id])
            claimed = execution.claim_next_translate_work_item(
                run_id=run_id,
                worker_name="test.translate",
                worker_instance_id="worker-5",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=60)
            execution.complete_work_item_failure(
                lease_token=claimed.lease_token,
                error_class="RuntimeError",
                error_detail_json={"message": "boom"},
                retryable=False,
            )
            guardrail = execution.enforce_budget_guardrails(run_id=run_id)

        self.assertTrue(guardrail.budget_exceeded)
        self.assertEqual(guardrail.stop_reason, "budget.consecutive_failures_exceeded")
        self.assertEqual(guardrail.run_summary.status, "failed")

    def test_retryable_exception_helper_treats_http_429_as_retryable(self) -> None:
        exc = RuntimeError("Provider returned HTTP 429: rate limit exceeded")
        self.assertTrue(_is_retryable_exception(exc))

    def test_retryable_exception_helper_treats_http_402_insufficient_balance_as_non_retryable(self) -> None:
        exc = RuntimeError(
            'Provider returned HTTP 402: {"error":{"message":"Insufficient Balance","type":"unknown_error"}}'
        )
        self.assertFalse(_is_retryable_exception(exc))
        self.assertEqual(_pause_reason_for_exception(exc), "provider.insufficient_balance")

    def test_provider_insufficient_balance_pauses_run_immediately(self) -> None:
        run_id = self._create_running_run(
            budget=RunBudgetSummary(
                max_wall_clock_seconds=None,
                max_total_cost_usd=None,
                max_total_token_in=None,
                max_total_token_out=None,
                max_retry_count_per_work_item=2,
                max_consecutive_failures=24,
                max_parallel_workers=1,
                max_parallel_requests_per_provider=1,
                max_auto_followup_attempts=1,
            )
        )
        packet_id = str(uuid4())

        with self.session_factory() as session:
            execution = RunExecutionService(RunControlRepository(session))
            execution.seed_translate_work_items(run_id=run_id, packet_ids=[packet_id])
            claimed = execution.claim_next_translate_work_item(
                run_id=run_id,
                worker_name="test.translate",
                worker_instance_id="worker-6",
                lease_seconds=60,
            )
            self.assertIsNotNone(claimed)
            assert claimed is not None
            execution.start_work_item(lease_token=claimed.lease_token, lease_seconds=60)
            session.commit()

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root="/tmp",
            translation_worker=None,
        )
        exc = RuntimeError(
            'Provider returned HTTP 402: {"error":{"message":"Insufficient Balance","type":"unknown_error"}}'
        )
        executor._complete_failure(
            run_id=run_id,
            claimed=claimed,
            exc=exc,
            stage_key="translate",
        )

        with self.session_factory() as session:
            summary = RunControlService(RunControlRepository(session)).get_run_summary(run_id)

        self.assertEqual(summary.status, "paused")
        self.assertEqual(summary.stop_reason, "provider.insufficient_balance")
        self.assertEqual(summary.work_items.status_counts["terminal_failed"], 1)

    def test_recover_export_misrouting_rebinds_run_to_effective_bundle_revision(self) -> None:
        document_id = self._create_document()
        run_id = self._create_running_run_for_document(document_id)
        export_scope_id = str(uuid4())
        proposal_id = str(uuid4())
        stable_revision_id = str(uuid4())
        bad_revision_id = str(uuid4())

        with self.session_factory() as session:
            work_item = WorkItem(
                run_id=run_id,
                stage=WorkItemStage.EXPORT,
                scope_type=WorkItemScopeType.EXPORT,
                scope_id=export_scope_id,
                priority=100,
                status=WorkItemStatus.RETRYABLE_FAILED,
                input_version_bundle_json={"document_id": document_id, "export_type": "rebuilt_pdf"},
                output_artifact_refs_json={},
                error_detail_json={},
            )
            session.add(work_item)
            stable_revision = RuntimeBundleRevision(
                id=stable_revision_id,
                bundle_type="runtime",
                revision_name="bundle-stable",
                status=RuntimeBundleRevisionStatus.PUBLISHED,
                manifest_json={},
                rollout_scope_json={"mode": "dev"},
            )
            bad_revision = RuntimeBundleRevision(
                id=bad_revision_id,
                bundle_type="runtime",
                revision_name="bundle-bad",
                status=RuntimeBundleRevisionStatus.ROLLED_BACK,
                parent_bundle_revision_id=stable_revision_id,
                rollback_target_revision_id=stable_revision_id,
                manifest_json={},
                rollout_scope_json={"mode": "dev"},
            )
            session.add(stable_revision)
            session.add(bad_revision)
            incident = RuntimeIncident(
                id=str(uuid4()),
                run_id=run_id,
                scope_type=JobScopeType.DOCUMENT,
                scope_id=export_scope_id,
                incident_kind=RuntimeIncidentKind.EXPORT_MISROUTING,
                fingerprint=f"export-misrouting:{uuid4()}",
                source_type="epub",
                selected_route="pdf.direct",
                runtime_bundle_revision_id=stable_revision_id,
                status=RuntimeIncidentStatus.FROZEN,
                failure_count=1,
                route_evidence_json={},
                latest_error_json={},
                bundle_json={},
                status_detail_json={},
            )
            proposal = RuntimePatchProposal(
                id=proposal_id,
                incident_id=incident.id,
                status=RuntimePatchProposalStatus.ROLLED_BACK,
                published_bundle_revision_id=bad_revision_id,
                diff_manifest_json={},
                validation_report_json={},
                status_detail_json={
                    "bundle_guard": {
                        "rollback_performed": True,
                        "effective_revision_id": stable_revision_id,
                        "rollback_target_revision_id": stable_revision_id,
                    }
                },
            )
            session.add(incident)
            session.add(proposal)
            session.commit()
            work_item_id = work_item.id

        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root="/tmp",
            translation_worker=None,
        )
        claimed = SimpleNamespace(
            run_id=run_id,
            work_item_id=work_item_id,
            stage=WorkItemStage.EXPORT.value,
            scope_type=WorkItemScopeType.EXPORT.value,
            scope_id=export_scope_id,
            attempt=1,
            priority=100,
            lease_token="lease-test",
            worker_name="executor-test",
            worker_instance_id="executor-test-worker",
            lease_expires_at=datetime.now(timezone.utc).isoformat(),
        )

        with self.session_factory() as session:
            with patch(
                "book_agent.app.runtime.document_run_executor.ExportController.recover_export_misrouting",
                return_value=SimpleNamespace(
                    incident_id=incident.id,
                    proposal_id=proposal_id,
                    bundle_revision_id=None,
                    repair_work_item_id="repair-work-item-1",
                    corrected_route="epub.rebuilt_pdf_via_html",
                    bound_work_item_ids=[],
                ),
            ):
                executor._recover_export_misrouting(
                    session=session,
                    run_id=run_id,
                    claimed=claimed,
                    exc=RuntimeError("export misrouting"),
                )
                session.commit()

        with self.session_factory() as session:
            repository = RunControlRepository(session)
            run = repository.get_run(run_id)
            runtime_v2 = dict((run.status_detail_json or {}).get("runtime_v2") or {})
            pending = dict(runtime_v2.get("pending_export_route_repair") or {})

        self.assertEqual(run.runtime_bundle_revision_id, stable_revision_id)
        self.assertEqual(pending["incident_id"], incident.id)
        self.assertEqual(pending["proposal_id"], proposal_id)
        self.assertEqual(pending["repair_work_item_id"], "repair-work-item-1")
        self.assertEqual(pending["replay_scope_id"], export_scope_id)
        self.assertEqual(pending["corrected_route"], "epub.rebuilt_pdf_via_html")

    def test_run_control_isoformat_treats_naive_sqlite_datetimes_as_utc(self) -> None:
        with self.session_factory() as session:
            control = RunControlService(RunControlRepository(session))
            naive = datetime(2026, 3, 18, 13, 30, 19, 791297)

            self.assertEqual(
                control._isoformat(naive),
                "2026-03-18T13:30:19.791297+00:00",
            )


if __name__ == "__main__":
    unittest.main()
