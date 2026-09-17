"""The agent harness kernel: durable turns, typed tools, permissions, approvals, budgets."""

from __future__ import annotations

import unittest
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from book_agent.domain.enums import (
    AgentItemKind,
    AgentTurnStatus,
    ApprovalStatus,
    DecisionScope,
    DocumentStatus,
    SourceType,
)
from book_agent.domain.event_kinds import AGENT_TOOL_CALLED, AGENT_TURN_FINISHED, AGENT_TURN_STARTED, LLM_CALL_COMPLETED
from book_agent.domain.models import AuditEvent, Document, Event
from book_agent.harness.approvals.service import ApprovalService
from book_agent.harness.kernel.budget import TurnBudget
from book_agent.harness.kernel.messages import assemble_messages
from book_agent.harness.kernel.model import AgentStep, ToolCall
from book_agent.harness.kernel.turn import AgentTurnRunner
from book_agent.harness.tools.permissions import AutoApproveRule, PermissionPolicy
from book_agent.harness.tools.registry import ToolContext, ToolError, ToolPermission, ToolRegistry, ToolSpec
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.translation.contracts import TranslationUsage


class _FakeModel:
    """Returns scripted steps in order and records what it was shown."""

    def __init__(self, steps: list[AgentStep]) -> None:
        self.steps = list(steps)
        self.calls: list[dict[str, Any]] = []

    def step(self, *, model_name: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> AgentStep:
        self.calls.append({"model_name": model_name, "messages": messages, "tools": tools})
        if not self.steps:
            raise AssertionError("model asked for more steps than scripted")
        return self.steps.pop(0)


class LookupArgs(BaseModel):
    term: str = Field(description="term to look up")


class LockArgs(BaseModel):
    source_term: str
    target_term: str
    term_type: str = "concept"


def _usage(n: int = 10) -> TranslationUsage:
    return TranslationUsage(token_in=n, token_out=n // 2, total_tokens=n + n // 2, cost_usd=0.001 * n, latency_ms=5)


class HarnessKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        from book_agent.infra.db.base import Base
        from book_agent.infra.db.session import build_engine, build_session_factory

        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.session_factory() as session:
            document = Document(source_type=SourceType.EPUB, file_fingerprint="harness-fp", title="Harness", status=DocumentStatus.ACTIVE)
            session.add(document)
            session.commit()
            self.document_id = document.id
        self.lookups: list[str] = []
        self.locks: list[tuple[str, str]] = []

    # --- fixtures ---------------------------------------------------------------

    def _registry(self, *, lookup_limit: int | None = None) -> ToolRegistry:
        def lookup(ctx: ToolContext, args: LookupArgs) -> dict[str, Any]:
            self.lookups.append(args.term)
            if args.term == "boom":
                raise ToolError("no such term")
            return {"term": args.term, "occurrences": 3}

        def lock(ctx: ToolContext, args: LockArgs) -> dict[str, Any]:
            self.locks.append((args.source_term, args.target_term))
            AgentLedgerRepository(ctx.session).record_decision(
                document_id=ctx.document_id,
                scope=DecisionScope.BOOK,
                key=f"term:{args.source_term}",
                value={"target_term": args.target_term},
                decided_by=ctx.actor_id,
                turn_id=ctx.turn_id,
            )
            return {"locked": True}

        return ToolRegistry(
            [
                ToolSpec("lookup_term", "Count occurrences of a term.", LookupArgs, ToolPermission.READ, lookup, max_calls_per_turn=lookup_limit),
                ToolSpec("lock_term", "Lock a glossary term.", LockArgs, ToolPermission.WRITE_IRREVERSIBLE, lock),
            ]
        )

    def _new_turn(self, *, budget: TurnBudget | None = None) -> str:
        with self.session_factory() as session:
            ledger = AgentLedgerRepository(session)
            turn = ledger.create_turn(
                document_id=self.document_id,
                agent_kind="terminology",
                scope_type="document",
                scope_id=self.document_id,
                model_name="fake-model",
                budget=(budget or TurnBudget()).to_json(),
            )
            ledger.append_item(turn.id, kind=AgentItemKind.SYSTEM, content={"text": "You are the terminology agent."})
            ledger.append_item(turn.id, kind=AgentItemKind.USER, content={"text": "Decide the glossary for this book."})
            session.commit()
            return turn.id

    def _runner(self, model: _FakeModel, *, registry: ToolRegistry | None = None, policy: PermissionPolicy | None = None) -> AgentTurnRunner:
        return AgentTurnRunner(
            session_factory=self.session_factory,
            model=model,
            registry=registry or self._registry(),
            policy=policy or PermissionPolicy(),
            compaction_threshold_tokens=None,
        )

    def _items(self, turn_id: str) -> list[tuple[str, dict[str, Any]]]:
        with self.session_factory() as session:
            return [(item.kind.value, dict(item.content_json)) for item in AgentLedgerRepository(session).list_items(turn_id)]

    def _turn(self, turn_id: str):
        with self.session_factory() as session:
            turn = AgentLedgerRepository(session).get_turn(turn_id)
            session.expunge(turn)
            return turn

    # --- tests ------------------------------------------------------------------

    def test_turn_runs_tools_and_finishes_with_a_durable_ledger(self) -> None:
        model = _FakeModel(
            [
                AgentStep(text=None, tool_calls=[ToolCall("c1", "lookup_term", {"term": "agent"})], usage=_usage(20)),
                AgentStep(text="Glossary decided.", usage=_usage(10)),
            ]
        )
        turn_id = self._new_turn()
        outcome = self._runner(model).run(turn_id)

        self.assertEqual(outcome.status, AgentTurnStatus.SUCCEEDED)
        self.assertEqual(outcome.final_text, "Glossary decided.")
        self.assertEqual(self.lookups, ["agent"])
        kinds = [kind for kind, _ in self._items(turn_id)]
        self.assertEqual(kinds, ["system", "user", "assistant", "tool_call", "tool_result", "assistant"])
        tool_result = [content for kind, content in self._items(turn_id) if kind == "tool_result"][0]
        self.assertEqual(tool_result["result"], {"ok": True, "output": {"term": "agent", "occurrences": 3}})
        # The second model call saw the tool result in OpenAI chat form.
        second_messages = model.calls[1]["messages"]
        self.assertEqual(second_messages[0]["role"], "system")
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertEqual(second_messages[-1]["tool_call_id"], "c1")
        self.assertEqual([tool["function"]["name"] for tool in model.calls[0]["tools"]], ["lock_term", "lookup_term"])
        turn = self._turn(turn_id)
        self.assertEqual(turn.usage_json["steps"], 2)
        self.assertEqual(turn.usage_json["token_in"], 30)
        self.assertAlmostEqual(turn.usage_json["cost_usd"], 0.03, places=6)
        with self.session_factory() as session:
            kinds_seen = [event.kind for event in session.scalars(select(Event).order_by(Event.id))]
            self.assertIn(AGENT_TURN_STARTED, kinds_seen)
            self.assertIn(AGENT_TOOL_CALLED, kinds_seen)
            self.assertIn(AGENT_TURN_FINISHED, kinds_seen)
            llm_events = [event for event in session.scalars(select(Event)) if event.kind == LLM_CALL_COMPLETED]
            self.assertEqual(len(llm_events), 2)
            self.assertEqual(llm_events[0].payload["call_kind"], "agent.terminology")
            audits = list(session.scalars(select(AuditEvent).where(AuditEvent.object_type == "agent_turn")))
            self.assertEqual([audit.action for audit in audits], ["tool.lookup_term"])

    def test_irreversible_tool_waits_for_approval_and_runs_after_it(self) -> None:
        model = _FakeModel(
            [
                AgentStep(text=None, tool_calls=[ToolCall("c1", "lock_term", {"source_term": "agent", "target_term": "智能体"})], usage=_usage()),
                AgentStep(text="Done.", usage=_usage()),
            ]
        )
        turn_id = self._new_turn()
        runner = self._runner(model)
        outcome = runner.run(turn_id)

        self.assertEqual(outcome.status, AgentTurnStatus.AWAITING_APPROVAL)
        self.assertEqual(self.locks, [])
        with self.session_factory() as session:
            ledger = AgentLedgerRepository(session)
            pending = ledger.list_approvals(self.document_id, status=ApprovalStatus.PENDING)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].kind, "lock_term")
            self.assertEqual(pending[0].payload_json["arguments"]["target_term"], "智能体")
            approval_id = pending[0].id
            ApprovalService(session).decide(approval_id, approved=True, decided_by="reviewer:alice", note="ok")
            session.commit()
            self.assertEqual(ledger.get_turn(turn_id).status, AgentTurnStatus.RUNNING)

        outcome = runner.run(turn_id)
        self.assertEqual(outcome.status, AgentTurnStatus.SUCCEEDED)
        self.assertEqual(self.locks, [("agent", "智能体")])
        kinds = [kind for kind, _ in self._items(turn_id)]
        self.assertEqual(kinds, ["system", "user", "assistant", "tool_call", "approval_request", "approval_result", "tool_result", "assistant"])
        result = [content for kind, content in self._items(turn_id) if kind == "tool_result"][0]
        self.assertEqual(result["approved_by"], "reviewer:alice")
        with self.session_factory() as session:
            decisions = AgentLedgerRepository(session).latest_decisions(self.document_id, scope=DecisionScope.BOOK)
            self.assertEqual(decisions["term:agent"].value_json, {"target_term": "智能体"})
            self.assertEqual(decisions["term:agent"].turn_id, turn_id)

    def test_a_step_files_its_approvals_together_and_resumes_once_all_are_decided(self) -> None:
        model = _FakeModel(
            [
                AgentStep(
                    text=None,
                    tool_calls=[
                        ToolCall("c1", "lookup_term", {"term": "agent"}),
                        ToolCall("c2", "lock_term", {"source_term": "agent", "target_term": "智能体"}),
                        ToolCall("c3", "lookup_term", {"term": "harness"}),
                        ToolCall("c4", "lock_term", {"source_term": "harness", "target_term": "框架"}),
                    ],
                    usage=_usage(),
                ),
                AgentStep(text="Done.", usage=_usage()),
            ]
        )
        turn_id = self._new_turn()
        runner = self._runner(model)
        self.assertEqual(runner.run(turn_id).status, AgentTurnStatus.AWAITING_APPROVAL)
        # Calls that need no approval ran; both approvals were filed together.
        self.assertEqual(self.lookups, ["agent", "harness"])
        with self.session_factory() as session:
            pending = AgentLedgerRepository(session).list_approvals(self.document_id, status=ApprovalStatus.PENDING)
            self.assertEqual({p.payload_json["arguments"]["target_term"] for p in pending}, {"框架", "智能体"})
            ApprovalService(session).decide(pending[0].id, approved=True, decided_by="reviewer:alice")
            session.commit()
            self.assertEqual(AgentLedgerRepository(session).get_turn(turn_id).status, AgentTurnStatus.AWAITING_APPROVAL)
            ApprovalService(session).decide(pending[1].id, approved=True, decided_by="reviewer:alice")
            session.commit()
        outcome = runner.run(turn_id)
        self.assertEqual(outcome.status, AgentTurnStatus.SUCCEEDED)
        self.assertEqual(self.lookups, ["agent", "harness"])
        self.assertEqual(self.locks, [("agent", "智能体"), ("harness", "框架")])
        second_messages = model.calls[1]["messages"]
        answered = [message["tool_call_id"] for message in second_messages if message["role"] == "tool"]
        self.assertEqual(answered, ["c1", "c2", "c3", "c4"])

    def test_rejected_approval_is_reported_to_the_model_as_a_failed_tool_result(self) -> None:
        model = _FakeModel(
            [
                AgentStep(text=None, tool_calls=[ToolCall("c1", "lock_term", {"source_term": "agent", "target_term": "代理"})], usage=_usage()),
                AgentStep(text="Understood, leaving it unlocked.", usage=_usage()),
            ]
        )
        turn_id = self._new_turn()
        runner = self._runner(model)
        runner.run(turn_id)
        with self.session_factory() as session:
            approval = AgentLedgerRepository(session).list_approvals(self.document_id, status=ApprovalStatus.PENDING)[0]
            ApprovalService(session).decide(approval.id, approved=False, decided_by="reviewer:bob", note="prefer 智能体")
            session.commit()
        outcome = runner.run(turn_id)
        self.assertEqual(outcome.status, AgentTurnStatus.SUCCEEDED)
        self.assertEqual(self.locks, [])
        result = [content for kind, content in self._items(turn_id) if kind == "tool_result"][0]
        self.assertFalse(result["result"]["ok"])
        self.assertIn("prefer 智能体", result["result"]["error"])
        self.assertIn("prefer 智能体", model.calls[1]["messages"][-1]["content"])

    def test_auto_approval_rule_runs_the_tool_and_records_the_decision(self) -> None:
        policy = PermissionPolicy(
            auto_approve=[
                AutoApproveRule(
                    policy_id="auto.lock_person_names",
                    tool_name="lock_term",
                    predicate=lambda ctx, args: args.term_type == "person",
                    description="person names are locked automatically",
                )
            ]
        )
        model = _FakeModel(
            [
                AgentStep(text=None, tool_calls=[ToolCall("c1", "lock_term", {"source_term": "Wilder", "target_term": "怀尔德", "term_type": "person"})], usage=_usage()),
                AgentStep(text="Done.", usage=_usage()),
            ]
        )
        turn_id = self._new_turn()
        outcome = self._runner(model, policy=policy).run(turn_id)
        self.assertEqual(outcome.status, AgentTurnStatus.SUCCEEDED)
        self.assertEqual(self.locks, [("Wilder", "怀尔德")])
        with self.session_factory() as session:
            approvals = AgentLedgerRepository(session).approvals_for_turn(turn_id)
            self.assertEqual([a.status for a in approvals], [ApprovalStatus.AUTO_APPROVED])
            self.assertEqual(approvals[0].policy_id, "auto.lock_person_names")

    def test_denied_tool_and_tool_errors_are_reported_not_raised(self) -> None:
        model = _FakeModel(
            [
                AgentStep(
                    text=None,
                    tool_calls=[
                        ToolCall("c1", "lock_term", {"source_term": "x", "target_term": "y"}),
                        ToolCall("c2", "lookup_term", {"term": "boom"}),
                        ToolCall("c3", "nonexistent", {}),
                        ToolCall("c4", "lookup_term", {"wrong": 1}),
                    ],
                    usage=_usage(),
                ),
                AgentStep(text="Recovered.", usage=_usage()),
            ]
        )
        turn_id = self._new_turn()
        outcome = self._runner(model, policy=PermissionPolicy(denied_tools=frozenset({"lock_term"}))).run(turn_id)
        self.assertEqual(outcome.status, AgentTurnStatus.SUCCEEDED)
        results = [content["result"] for kind, content in self._items(turn_id) if kind == "tool_result"]
        self.assertEqual([r["ok"] for r in results], [False, False, False, False])
        self.assertIn("denied", results[0]["error"])
        self.assertIn("no such term", results[1]["error"])
        self.assertIn("unknown tool", results[2]["error"])
        self.assertIn("invalid arguments", results[3]["error"])

    def test_tool_call_limit_hook_rejects_extra_calls_across_steps(self) -> None:
        model = _FakeModel(
            [
                AgentStep(text=None, tool_calls=[ToolCall("c1", "lookup_term", {"term": "a"})], usage=_usage()),
                AgentStep(text=None, tool_calls=[ToolCall("c2", "lookup_term", {"term": "b"})], usage=_usage()),
                AgentStep(text="Done.", usage=_usage()),
            ]
        )
        turn_id = self._new_turn()
        outcome = self._runner(model, registry=self._registry(lookup_limit=1)).run(turn_id)
        self.assertEqual(outcome.status, AgentTurnStatus.SUCCEEDED)
        self.assertEqual(self.lookups, ["a"])
        results = [content["result"] for kind, content in self._items(turn_id) if kind == "tool_result"]
        self.assertTrue(results[0]["ok"])
        self.assertIn("at most 1 times", results[1]["error"])

    def test_budget_exhaustion_pauses_the_turn_until_an_extension_is_approved(self) -> None:
        model = _FakeModel(
            [
                AgentStep(text=None, tool_calls=[ToolCall("c1", "lookup_term", {"term": "a"})], usage=_usage()),
                AgentStep(text="Done.", usage=_usage()),
            ]
        )
        turn_id = self._new_turn(budget=TurnBudget(max_steps=1))
        runner = self._runner(model)
        outcome = runner.run(turn_id)
        self.assertEqual(outcome.status, AgentTurnStatus.PAUSED)
        self.assertEqual(outcome.stop_reason, "budget.max_steps")
        with self.session_factory() as session:
            ledger = AgentLedgerRepository(session)
            approval = ledger.list_approvals(self.document_id, status=ApprovalStatus.PENDING)[0]
            self.assertEqual(approval.kind, "budget_extension")
            approval.payload_json = {**approval.payload_json, "extension": {"max_steps": 2}}
            ApprovalService(session).decide(approval.id, approved=True, decided_by="reviewer:alice")
            session.commit()
            self.assertEqual(ledger.get_turn(turn_id).budget_json["max_steps"], 3)
        outcome = runner.run(turn_id)
        self.assertEqual(outcome.status, AgentTurnStatus.SUCCEEDED)

    def test_crash_between_steps_resumes_from_the_ledger(self) -> None:
        # First runner dies after the tool ran (simulated by scripting only one step);
        # a fresh runner with the remaining script continues from the persisted items.
        turn_id = self._new_turn()
        first = _FakeModel([AgentStep(text=None, tool_calls=[ToolCall("c1", "lookup_term", {"term": "a"})], usage=_usage())])
        with self.assertRaises(AssertionError):
            self._runner(first).run(turn_id)
        self.assertEqual(self._turn(turn_id).status, AgentTurnStatus.RUNNING)
        second = _FakeModel([AgentStep(text="Finished after restart.", usage=_usage())])
        outcome = self._runner(second).run(turn_id)
        self.assertEqual(outcome.status, AgentTurnStatus.SUCCEEDED)
        self.assertEqual(self.lookups, ["a"])
        self.assertEqual(second.calls[0]["messages"][-1]["role"], "tool")

    def test_compaction_replaces_earlier_conversation_in_the_messages(self) -> None:
        from book_agent.domain.models.agent import AgentItem

        items = [
            AgentItem(turn_id="t", ordinal=1, kind=AgentItemKind.SYSTEM, content_json={"text": "sys"}),
            AgentItem(turn_id="t", ordinal=2, kind=AgentItemKind.USER, content_json={"text": "old"}),
            AgentItem(turn_id="t", ordinal=3, kind=AgentItemKind.COMPACTION, content_json={"summary": "did x"}),
            AgentItem(turn_id="t", ordinal=4, kind=AgentItemKind.ASSISTANT, content_json={"text": "next", "tool_calls": []}),
        ]
        messages = assemble_messages(items)
        self.assertEqual([m["role"] for m in messages], ["system", "system", "assistant"])
        self.assertIn("did x", messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
