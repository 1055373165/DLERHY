"""The terminology agent stage inside a translate_full run: seeding, execution, approval blocking, resume."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from uuid import uuid4

from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.domain.enums import (
    AgentTurnStatus,
    ApprovalStatus,
    ArtifactStatus,
    BlockType,
    ChapterStatus,
    DocumentRunType,
    DocumentStatus,
    PacketStatus,
    PacketType,
    ProtectedPolicy,
    SourceType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Block, Chapter, Document, Sentence
from book_agent.domain.models.ops import WorkItem
from book_agent.domain.models.translation import TranslationPacket
from book_agent.harness.approvals.service import ApprovalService
from book_agent.harness.kernel.model import AgentStep, ToolCall
from book_agent.harness.kernel.openai_model import EchoAgentModel
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.orchestrator.run_plan import plan_for_run
from book_agent.orchestrator.stage_gate import StageGateKeeper
from book_agent.orchestrator.stage_status import StageStatus, StageStatusCalculator
from book_agent.services.run_control import RunControlService
from book_agent.translation.contracts import TranslationUsage
from sqlalchemy import select


class _ScriptedModel:
    def __init__(self, steps):
        self.steps = list(steps)

    def step(self, *, model_name, messages, tools):
        return self.steps.pop(0)


class AgentStageExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.engine = build_engine(f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'agent-stage.db'}", connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        with self.session_factory() as session:
            document = Document(source_type=SourceType.EPUB, file_fingerprint=f"stage-{uuid4()}", title="Stage", status=DocumentStatus.ACTIVE)
            session.add(document)
            session.flush()
            self.document_id = document.id
            chapter = Chapter(document_id=document.id, ordinal=1, title_src="One", status=ChapterStatus.PACKET_BUILT)
            session.add(chapter)
            session.flush()
            block = Block(chapter_id=chapter.id, ordinal=1, block_type=BlockType.PARAGRAPH, source_text="Momentum matters.", protected_policy=ProtectedPolicy.TRANSLATE, status=ArtifactStatus.ACTIVE)
            session.add(block)
            session.flush()
            session.add(Sentence(block_id=block.id, chapter_id=chapter.id, document_id=document.id, ordinal_in_block=1, source_text="Momentum matters."))
            session.add(
                TranslationPacket(
                    chapter_id=chapter.id, block_start_id=block.id, block_end_id=block.id, packet_type=PacketType.TRANSLATE,
                    book_profile_version=1, status=PacketStatus.BUILT,
                    packet_json={"packet_id": str(uuid4()), "chapter_id": chapter.id, "current_blocks": [{"block_id": block.id, "sentence_ids": []}], "packet_ordinal": 1, "input_version_bundle": {"chapter_id": chapter.id, "packet_ordinal": 1}, "runtime_state": {"stage": "translate", "substate": "ready", "packet_ordinal": 1}},
                )
            )
            control = RunControlService(RunControlRepository(session))
            created = control.create_run(document_id=document.id, run_type=DocumentRunType.TRANSLATE_FULL, requested_by="test")
            control.resume_run(created.run_id, actor_id="test")
            session.commit()
            self.run_id = created.run_id

    def _executor(self, model) -> DocumentRunExecutor:
        return DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=str(Path(self.tempdir.name) / "exports"),
            translation_worker=None,
            agent_model_resolver=lambda worker: model,
            heartbeat_interval_seconds=3600,
        )

    def _join_work_threads(self, executor: DocumentRunExecutor) -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            threads = [t for m in executor._active_work_threads.values() for t in m.values() if t.is_alive()]
            if not threads:
                return
            for thread in threads:
                thread.join(timeout=1)
        self.fail("agent work thread did not finish")

    def _stage(self, stage: str) -> StageStatus:
        with self.session_factory() as session:
            return StageStatusCalculator(session).stage_status(self.run_id, self.document_id, stage)

    def _plan(self):
        with self.session_factory() as session:
            run = RunControlRepository(session).get_run(self.run_id)
            return plan_for_run(run.run_type, run.status_detail_json)

    def test_terminology_stage_runs_before_translate_and_succeeds_with_the_echo_model(self) -> None:
        plan = self._plan()
        self.assertEqual(plan.stages[0], "terminology")
        executor = self._executor(EchoAgentModel())
        # translate is gated until the agent stage succeeds
        self.assertFalse(executor._process_translate_stage(self.run_id, plan))
        self.assertTrue(executor._process_agent_stage(self.run_id, "terminology", plan))  # seeded
        self.assertEqual(self._stage("terminology"), StageStatus.RUNNING)
        self.assertTrue(executor._process_agent_stage(self.run_id, "terminology", plan))  # claimed + thread
        self._join_work_threads(executor)
        self.assertEqual(self._stage("terminology"), StageStatus.SUCCEEDED)
        with self.session_factory() as session:
            turn = AgentLedgerRepository(session).latest_turn(document_id=self.document_id, agent_kind="terminology", run_id=self.run_id)
            self.assertEqual(turn.status, AgentTurnStatus.SUCCEEDED)
            items = session.scalars(select(WorkItem).where(WorkItem.run_id == self.run_id, WorkItem.stage == WorkItemStage.AGENT)).all()
            self.assertEqual([item.status for item in items], [WorkItemStatus.SUCCEEDED])
        self.assertFalse(executor._process_agent_stage(self.run_id, "terminology", plan))
        # The gate in front of translate is open now (starting translation here
        # would spawn worker threads that outlive the test database).
        with self.session_factory() as session:
            self.assertTrue(StageGateKeeper(session).can_start(self.run_id, self.document_id, "translate", plan_stages=plan.stages))

    def test_awaiting_approval_blocks_the_stage_until_a_decision_then_resumes(self) -> None:
        model = _ScriptedModel(
            [
                AgentStep(text=None, tool_calls=[ToolCall("c1", "lock_term", {"source_term": "Momentum", "target_term": "动量"})], usage=TranslationUsage(token_in=1, token_out=1)),
                AgentStep(text="done", usage=TranslationUsage(token_in=1, token_out=1)),
            ]
        )
        plan = self._plan()
        executor = self._executor(model)
        executor._process_agent_stage(self.run_id, "terminology", plan)
        executor._process_agent_stage(self.run_id, "terminology", plan)
        self._join_work_threads(executor)
        self.assertEqual(self._stage("terminology"), StageStatus.RUNNING)
        self.assertFalse(executor._process_agent_stage(self.run_id, "terminology", plan))  # blocked, nothing seeded
        self.assertFalse(executor._process_translate_stage(self.run_id, plan))
        with self.session_factory() as session:
            ledger = AgentLedgerRepository(session)
            (approval,) = ledger.list_approvals(self.document_id, status=ApprovalStatus.PENDING)
            self.assertEqual(approval.payload_json["arguments"]["target_term"], "动量")
            ApprovalService(session).decide(approval.id, approved=True, decided_by="reviewer")
            session.commit()
        self.assertTrue(executor._process_agent_stage(self.run_id, "terminology", plan))  # resume item seeded
        self.assertTrue(executor._process_agent_stage(self.run_id, "terminology", plan))  # claimed
        self._join_work_threads(executor)
        self.assertEqual(self._stage("terminology"), StageStatus.SUCCEEDED)
        with self.session_factory() as session:
            turn = AgentLedgerRepository(session).latest_turn(document_id=self.document_id, agent_kind="terminology", run_id=self.run_id)
            self.assertEqual(turn.status, AgentTurnStatus.SUCCEEDED)
            items = session.scalars(select(WorkItem).where(WorkItem.run_id == self.run_id, WorkItem.stage == WorkItemStage.AGENT)).all()
            self.assertEqual(len(items), 2)
            self.assertTrue(all(item.status == WorkItemStatus.SUCCEEDED for item in items))
            from book_agent.services.glossary_service import GlossaryService
            self.assertEqual(GlossaryService(session).get_locked_terms(self.document_id), {"Momentum": "动量"})


if __name__ == "__main__":
    unittest.main()
