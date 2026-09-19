"""Repair Agent: plan wiring, tools, approval-gated wontfix, and a run it rescues end to end."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from book_agent.app.main import create_app
from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.core.ids import stable_id
from book_agent.domain.enums import AgentItemKind, AgentTurnStatus, ApprovalStatus, DocumentRunType, IssueStatus
from book_agent.domain.models import Sentence
from book_agent.domain.models.review import ReviewIssue
from book_agent.harness.agents.repair import (
    MAX_EXECUTIONS_PER_ISSUE,
    EditSegmentArgs,
    ExecuteActionArgs,
    ListBlockingIssuesArgs,
    RepairAgent,
    edit_segment,
    execute_action,
    list_blocking_issues,
)
from book_agent.harness.approvals.service import ApprovalService
from book_agent.harness.kernel.model import AgentStep, ToolCall
from book_agent.harness.kernel.turn import AgentTurnRunner
from book_agent.harness.tools.registry import ToolContext, ToolError
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.orchestrator.run_plan import plan_for_run
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.review import ReviewService
from book_agent.services.translation import TranslationService
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.translation.contracts import (
    AlignmentSuggestion,
    TranslationTargetSegment,
    TranslationWorkerOutput,
    TranslationWorkerResult,
)
from book_agent.workers.translator import TranslationTask, TranslationWorkerMetadata
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML

FIXES = {
    "The solution to this problem is context engineering.": "这个问题的解决方案是上下文工程。",
    "Context engineering is a discipline.": "上下文工程是一门学科。",
}


class StubbornWorker:
    """Always renders the locked term "context engineering" as 语境设计, whatever the prompt says."""

    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(worker_name="stubborn", model_name="stubborn", prompt_version="v1")

    def translate(self, task: TranslationTask) -> TranslationWorkerResult:
        segments, alignments = [], []
        for sentence in task.current_sentences:
            temp_id = stable_id("temp", sentence.id)
            text = sentence.source_text.replace("context engineering", "语境设计").replace("Context engineering", "语境设计")
            segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id, text_zh=f"译::{text}", segment_type="sentence", source_sentence_ids=[sentence.id], confidence=0.9
                )
            )
            alignments.append(AlignmentSuggestion(source_sentence_ids=[sentence.id], target_temp_ids=[temp_id], relation_type="1:1"))
        return TranslationWorkerResult(
            output=TranslationWorkerOutput(packet_id=task.context_packet.packet_id, target_segments=segments, alignment_suggestions=alignments)
        )


class FixingModel:
    """A repair model: list blockers, edit the known term conflicts, list again, stop."""

    def __init__(self) -> None:
        self.steps = 0

    def step(self, *, model_name, messages, tools):
        self.steps += 1
        if self.steps > 12:
            return AgentStep(text="放弃。")
        last = messages[-1]
        if last["role"] != "tool":
            return AgentStep(text=None, tool_calls=[ToolCall(f"l{self.steps}", "list_blocking_issues", {})])
        output = (json.loads(last["content"]) or {}).get("output") or {}
        if "issues" in output:
            calls = [
                ToolCall(
                    f"e{self.steps}-{index}",
                    "edit_segment",
                    {"issue_id": issue["issue_id"], "new_text": FIXES[issue["source_text"]], "reason": "术语应为上下文工程"},
                )
                for index, issue in enumerate(output["issues"])
                if issue.get("source_text") in FIXES
            ]
            if not calls:
                return AgentStep(text="没有可修复的阻断问题。")
            return AgentStep(text=None, tool_calls=calls)
        return AgentStep(text=None, tool_calls=[ToolCall(f"l{self.steps}", "list_blocking_issues", {})])


def _write_epub(root: Path) -> Path:
    epub_path = root / "sample.epub"
    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", CONTENT_OPF)
        archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
        archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
    return epub_path


class RepairAgentToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'repair.db'}", connect_args={"check_same_thread": False}
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        artifacts = BootstrapOrchestrator().bootstrap_epub(_write_epub(Path(self.tempdir.name)))
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
        self.document_id = artifacts.document.id
        self.chapter_id = artifacts.chapters[0].id
        with self.session_factory() as session:
            GlossaryService(session).lock_term(self.document_id, "context engineering", "上下文工程")
            service = TranslationService(TranslationRepository(session), worker=StubbornWorker(), max_output_repairs=0)
            for packet in artifacts.translation_packets:
                service.execute_packet(packet.id)
            ReviewService(ReviewRepository(session)).review_chapter(self.chapter_id)
            session.commit()

    def _ctx(self, session, turn_id: str, extras=None) -> ToolContext:
        return ToolContext(
            session=session,
            document_id=self.document_id,
            agent_kind="repair",
            turn_id=turn_id,
            actor_id="agent.repair",
            extras=extras or {},
        )

    def _turn(self, session) -> str:
        return RepairAgent(session).start_turn(document_id=self.document_id, model_name="m").turn_id

    def test_plan_includes_the_repair_stage_only_on_request(self) -> None:
        self.assertNotIn("repair", plan_for_run(DocumentRunType.TRANSLATE_FULL, {}).stages)
        plan = plan_for_run(DocumentRunType.TRANSLATE_FULL, {"run_request": {"repair_agent": "on"}})
        self.assertTrue(plan.repair_agent)
        self.assertEqual(plan.stages[plan.stages.index("review") + 1], "repair")
        self.assertIn("repair", plan.agent_stages)

    def test_list_and_edit_clear_term_conflicts(self) -> None:
        with self.session_factory() as session:
            ctx = self._ctx(session, self._turn(session))
            listing = list_blocking_issues(ctx, ListBlockingIssuesArgs())
            conflicts = [issue for issue in listing["issues"] if issue["issue_type"] == "TERM_CONFLICT"]
            self.assertEqual(len(conflicts), 2)
            self.assertTrue(all(issue["planned_actions"] for issue in conflicts))
            self.assertIn("语境设计", conflicts[0]["current_translation"])
            for issue in conflicts:
                result = edit_segment(
                    ctx, EditSegmentArgs(issue_id=issue["issue_id"], new_text=FIXES[issue["source_text"]], reason="术语")
                )
                self.assertEqual(result["issue_status"], "resolved")
            self.assertEqual(result["blocking_issues_left"], 0)
            with self.assertRaisesRegex(ToolError, "locked glossary terms"):
                sentence_issue = session.scalars(select(ReviewIssue).where(ReviewIssue.issue_type == "TERM_CONFLICT")).first()
                edit_segment(ctx, EditSegmentArgs(issue_id=sentence_issue.id, new_text="语境设计是一门学科。", reason="x"))

    def test_execute_action_reruns_with_the_workflow_service_and_is_capped_per_issue(self) -> None:
        with self.session_factory() as session:
            turn_id = self._turn(session)
            ctx = self._ctx(
                session,
                turn_id,
                {"workflow_factory": lambda s: DocumentWorkflowService(s, translation_worker=StubbornWorker())},
            )
            issue = next(item for item in list_blocking_issues(ctx, ListBlockingIssuesArgs())["issues"] if item["planned_actions"])
            action_id = issue["planned_actions"][0]["action_id"]
            result = execute_action(ctx, ExecuteActionArgs(action_id=action_id))
            self.assertEqual(result["issue_id"], issue["issue_id"])
            self.assertIn(result["issue_status"], {"open", "triaged"})  # the stubborn worker cannot fix it
            with self.assertRaisesRegex(ToolError, "no workflow service"):
                execute_action(self._ctx(session, turn_id), ExecuteActionArgs(action_id=action_id))
            ledger = AgentLedgerRepository(session)
            for index in range(MAX_EXECUTIONS_PER_ISSUE):
                ledger.append_item(
                    turn_id,
                    kind=AgentItemKind.TOOL_RESULT,
                    content={"call_id": f"x{index}", "name": "execute_action", "result": {"ok": True, "output": {"issue_id": issue["issue_id"]}}},
                )
            with self.assertRaisesRegex(ToolError, "repair executions"):
                execute_action(ctx, ExecuteActionArgs(action_id=action_id))

    def test_mark_wontfix_waits_for_a_human_and_records_the_approver(self) -> None:
        with self.session_factory() as session:
            turn_id = self._turn(session)
            issue_id = session.scalars(select(ReviewIssue.id).where(ReviewIssue.issue_type == "TERM_CONFLICT")).first()
            session.commit()

        class WontfixModel:
            def __init__(self) -> None:
                self.done = False

            def step(self, *, model_name, messages, tools):
                if messages[-1]["role"] == "tool":
                    return AgentStep(text="已申请。")
                return AgentStep(text=None, tool_calls=[ToolCall("w1", "mark_wontfix", {"issue_id": issue_id, "note": "引文保留原译"})])

        runner = AgentTurnRunner(
            session_factory=self.session_factory, model=WontfixModel(), registry=RepairAgent.registry(), policy=RepairAgent.policy()
        )
        self.assertEqual(runner.run(turn_id).status, AgentTurnStatus.AWAITING_APPROVAL)
        with self.session_factory() as session:
            self.assertEqual(session.get(ReviewIssue, issue_id).status, IssueStatus.OPEN)
            (approval,) = AgentLedgerRepository(session).list_approvals(self.document_id, status=ApprovalStatus.PENDING)
            ApprovalService(session).decide(approval.id, approved=True, decided_by="human:lead")
            session.commit()
        self.assertEqual(runner.run(turn_id).status, AgentTurnStatus.SUCCEEDED)
        with self.session_factory() as session:
            issue = session.get(ReviewIssue, issue_id)
            self.assertEqual(issue.status, IssueStatus.WONTFIX)
            self.assertEqual(issue.decided_by, "human:lead")
            self.assertIn("proposed by agent.repair", issue.resolution_note)


class RepairAgentRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'run.db'}", connect_args={"check_same_thread": False, "timeout": 30}
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.app = create_app()
        self.app.state.session_factory = self.session_factory
        self.app.state.export_root = str(Path(self.tempdir.name) / "exports")
        self.app.state.translation_worker = StubbornWorker()

    def _run(self, repair_agent: str) -> tuple[dict, str]:
        model = FixingModel()
        executor = DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=self.app.state.export_root,
            translation_worker=self.app.state.translation_worker,
            agent_model_resolver=lambda worker: model,
            poll_interval_seconds=0.05,
            translation_max_output_repairs=0,
        )
        self.app.state.document_run_executor = executor
        executor.start()
        self.addCleanup(executor.stop)
        client = TestClient(self.app)
        self.addCleanup(client.close)
        bootstrap = client.post("/v1/documents/bootstrap", json={"source_path": str(_write_epub(Path(self.tempdir.name)))})
        document_id = bootstrap.json()["document_id"]
        with self.session_factory() as session:
            GlossaryService(session).lock_term(document_id, "context engineering", "上下文工程")
            session.commit()
        created = client.post(
            "/v1/runs",
            json={
                "document_id": document_id,
                "run_type": "translate_full",
                "requested_by": "repair-test",
                "status_detail_json": {
                    "run_request": {"terminology": "skip", "model_review": "skip", "repair_agent": repair_agent},
                },
            },
        )
        run_id = created.json()["run_id"]
        client.post(f"/v1/runs/{run_id}/resume", json={"actor_id": "repair-test"})
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            payload = client.get(f"/v1/runs/{run_id}").json()
            if payload["status"] in {"succeeded", "succeeded_with_warnings", "failed", "paused", "cancelled"}:
                return payload, document_id
            time.sleep(0.2)
        self.fail(f"run {run_id} did not finish")

    def test_without_the_agent_the_run_fails_on_the_blockers(self) -> None:
        payload, _ = self._run("off")
        self.assertEqual(payload["status"], "failed")

    def test_the_agent_edits_the_conflicts_and_the_run_exports(self) -> None:
        payload, document_id = self._run("on")
        self.assertEqual(payload["status"], "succeeded", payload.get("status_detail_json", {}).get("pipeline"))
        with self.session_factory() as session:
            texts = session.scalars(select(Sentence.id).where(Sentence.document_id == document_id)).all()
            self.assertTrue(texts)
            open_blockers = session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.blocking.is_(True),
                    ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
                )
            ).all()
            self.assertEqual(open_blockers, [])
            turn = AgentLedgerRepository(session).latest_turn(document_id=document_id, agent_kind="repair")
            self.assertEqual(turn.status, AgentTurnStatus.SUCCEEDED)


if __name__ == "__main__":
    unittest.main()
