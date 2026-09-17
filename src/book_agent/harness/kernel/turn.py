"""The agent loop, persisted step by step.

``AgentTurnRunner.run`` drives one turn: rebuild messages from the ledger,
check the budget, sample the model with the registered tools, record the
assistant item, then execute each requested tool under permissions and
hooks, recording a tool_result item per call. It returns when the model
stops calling tools (turn succeeded), when a tool needs approval (turn
awaiting_approval), when the budget is exhausted (turn paused) or on error.

Like packet translation, the model call happens with no database session
open; everything before and after it is a short transaction.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from book_agent.domain.enums import AgentItemKind, AgentTurnStatus, ApprovalStatus
from book_agent.domain.models.agent import AgentItem, AgentTurn
from book_agent.harness.kernel import trace
from book_agent.harness.kernel.budget import TurnBudget
from book_agent.harness.kernel.messages import (
    IMAGE_OUTPUT_KEY,
    IMAGE_TOKEN_ESTIMATE,
    assemble_messages,
    conversation_size,
    estimate_tokens,
)
from book_agent.harness.kernel.model import AgentModelClient, AgentStep, ToolCall
from book_agent.harness.tools.hooks import DEFAULT_HOOKS, ToolHook
from book_agent.harness.tools.permissions import PermissionKind, PermissionPolicy
from book_agent.harness.tools.registry import ToolContext, ToolError, ToolOutcome, ToolRegistry, ToolRejected, ToolSpec
from book_agent.infra.db.session import session_scope
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.workers.llm_calls import observed_llm_call

logger = logging.getLogger(__name__)


class LeaseLost(RuntimeError):
    """The runner's caller may raise this from ``lease_check`` to abandon the turn."""


@dataclass(slots=True)
class TurnOutcome:
    turn_id: str
    status: AgentTurnStatus
    stop_reason: str | None = None
    final_text: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentTurnRunner:
    session_factory: sessionmaker
    model: AgentModelClient
    registry: ToolRegistry
    policy: PermissionPolicy = field(default_factory=PermissionPolicy)
    hooks: tuple[ToolHook, ...] = DEFAULT_HOOKS
    # Compaction: summarise the conversation once it exceeds this many
    # estimated tokens. None disables it.
    compaction_threshold_tokens: int | None = 24_000
    # Called before each transaction; lets the executor abandon the turn
    # when its lease is gone.
    lease_check: Callable[[Session], None] | None = None
    # Extra context handed to tools (e.g. services the agent may use).
    tool_extras: dict[str, Any] = field(default_factory=dict)

    # --- public -------------------------------------------------------------

    def run(self, turn_id: str) -> TurnOutcome:
        while True:
            prepared = self._prepare_step(turn_id)
            if prepared.outcome is not None:
                return prepared.outcome
            step = self._sample(prepared)
            outcome = self._apply_step(turn_id, prepared, step)
            if outcome is not None:
                return outcome

    # --- step 1: read the ledger, decide whether to sample -----------------

    def _prepare_step(self, turn_id: str) -> "_PreparedStep":
        with session_scope(self.session_factory) as session:
            self._check_lease(session)
            ledger = AgentLedgerRepository(session)
            turn = ledger.get_turn_for_update(turn_id)
            if turn.status != AgentTurnStatus.RUNNING:
                return _PreparedStep(outcome=self._outcome(turn))
            items = ledger.list_items(turn_id)
            if not any(item.kind == AgentItemKind.ASSISTANT for item in items):
                trace.turn_started(
                    session,
                    turn_id=turn.id,
                    agent_kind=turn.agent_kind,
                    run_id=turn.run_id,
                    chapter_id=None,
                    payload={"scope_type": turn.scope_type, "scope_id": turn.scope_id, "model": turn.model_name},
                )
            # Tool calls that were waiting for an approval decision run first.
            pending_outcome = self._settle_pending_tool_calls(session, ledger, turn, items)
            if pending_outcome is not None:
                return _PreparedStep(outcome=pending_outcome)
            items = ledger.list_items(turn_id)
            budget = TurnBudget.from_json(turn.budget_json)
            exhausted = budget.exceeded(turn.usage_json or {})
            if exhausted is not None:
                self._pause_for_budget(session, ledger, turn, exhausted)
                return _PreparedStep(outcome=self._outcome(turn))
            if self.compaction_threshold_tokens is not None and conversation_size(items) > self.compaction_threshold_tokens:
                self._compact(session, ledger, turn, items)
                items = ledger.list_items(turn_id)
            return _PreparedStep(
                turn_id=turn.id,
                agent_kind=turn.agent_kind,
                run_id=turn.run_id,
                document_id=turn.document_id,
                model_name=turn.model_name or "",
                messages=assemble_messages(items),
                tools=self.registry.provider_schemas(),
            )

    # --- step 2: the model, with no session open ---------------------------

    def _sample(self, prepared: "_PreparedStep") -> AgentStep:
        from book_agent.infra import tracing

        with tracing.span(
            "agent.step",
            **{"gen_ai.request.model": prepared.model_name, "book_agent.messages": len(prepared.messages)},
        ) as current:
            step = self.model.step(model_name=prepared.model_name, messages=prepared.messages, tools=prepared.tools)
            tracing.set_attributes(
                current, **{"book_agent.tool_calls": len(step.tool_calls), "gen_ai.response.finish_reason": step.finish_reason}
            )
            return step

    # --- step 3: record and act ------------------------------------------

    def _apply_step(self, turn_id: str, prepared: "_PreparedStep", step: AgentStep) -> TurnOutcome | None:
        with session_scope(self.session_factory) as session:
            self._check_lease(session)
            ledger = AgentLedgerRepository(session)
            turn = ledger.get_turn_for_update(turn_id)
            with observed_llm_call(
                session,
                call_kind=f"agent.{turn.agent_kind}",
                model=prepared.model_name,
                run_id=turn.run_id,
                document_id=turn.document_id,
                payload={"turn_id": turn.id, "step": int((turn.usage_json or {}).get("steps", 0)) + 1},
            ) as call:
                call.complete(step.usage)
            self._bump_usage(turn, step, tool_calls=len(step.tool_calls))
            ledger.append_item(
                turn.id,
                kind=AgentItemKind.ASSISTANT,
                content={
                    "text": step.text,
                    "tool_calls": [
                        {"call_id": call.call_id, "name": call.name, "arguments": call.arguments} for call in step.tool_calls
                    ],
                    "finish_reason": step.finish_reason,
                },
                token_count=estimate_tokens(step.text or "") + sum(estimate_tokens(json.dumps(c.arguments)) for c in step.tool_calls),
            )
            if not step.tool_calls:
                self._finish(session, ledger, turn, status=AgentTurnStatus.SUCCEEDED, final_text=step.text)
                return self._outcome(turn)
            for call in step.tool_calls:
                tool_item = ledger.append_item(
                    turn.id,
                    kind=AgentItemKind.TOOL_CALL,
                    content={"call_id": call.call_id, "name": call.name, "arguments": call.arguments},
                )
                halted = self._execute_call(session, ledger, turn, call, tool_item)
                if halted is not None:
                    return halted
            return None

    # --- tool execution -----------------------------------------------------

    def _execute_call(
        self,
        session: Session,
        ledger: AgentLedgerRepository,
        turn: AgentTurn,
        call: ToolCall,
        tool_item: AgentItem,
        *,
        approved_by: str | None = None,
    ) -> TurnOutcome | None:
        """Run one tool call; returns an outcome when the turn must stop (approval)."""
        ctx = self._tool_context(session, turn)
        if approved_by is not None:
            # Handlers that record a decision (e.g. mark_wontfix) attribute it to the approver.
            ctx.extras["approved_by"] = approved_by
        tool = self.registry.get(call.name)
        if tool is None:
            self._record_result(session, ledger, turn, call, ToolOutcome(ok=False, error=f"unknown tool: {call.name}"))
            return None
        try:
            arguments = tool.parse_arguments(call.arguments)
        except ToolError as exc:
            self._record_result(session, ledger, turn, call, ToolOutcome(ok=False, error=str(exc)))
            return None
        if approved_by is None:
            decision = self.policy.decide(tool, ctx, arguments)
            if decision.kind == PermissionKind.DENY:
                self._record_result(session, ledger, turn, call, ToolOutcome(ok=False, error=f"denied: {decision.reason}"))
                return None
            if decision.kind == PermissionKind.REQUIRE_APPROVAL:
                approval = ledger.create_approval(
                    document_id=turn.document_id,
                    turn_id=turn.id,
                    tool_call_item_id=tool_item.id,
                    kind=tool.approval_kind or tool.name,
                    payload={"tool": tool.name, "arguments": arguments.model_dump(mode="json"), "reason": decision.reason},
                    policy_id=decision.policy_id,
                )
                ledger.append_item(
                    turn.id,
                    kind=AgentItemKind.APPROVAL_REQUEST,
                    content={"call_id": call.call_id, "approval_id": approval.id, "tool": tool.name},
                )
                turn.status = AgentTurnStatus.AWAITING_APPROVAL
                turn.stop_reason = f"approval:{tool.name}"
                session.flush()
                trace.approval_requested(
                    session,
                    turn_id=turn.id,
                    agent_kind=turn.agent_kind,
                    run_id=turn.run_id,
                    chapter_id=ctx.chapter_id,
                    payload={"approval_id": approval.id, "tool": tool.name, "policy_id": decision.policy_id},
                )
                return self._outcome(turn)
            if decision.auto_approved:
                ledger.create_approval(
                    document_id=turn.document_id,
                    turn_id=turn.id,
                    tool_call_item_id=tool_item.id,
                    kind=tool.approval_kind or tool.name,
                    payload={"tool": tool.name, "arguments": arguments.model_dump(mode="json"), "reason": decision.reason},
                    status=ApprovalStatus.AUTO_APPROVED,
                    policy_id=decision.policy_id,
                    decided_by=decision.policy_id,
                )
        outcome = self._run_tool(ctx, tool, arguments)
        usage = dict(turn.usage_json or {})
        usage["tool_call_counts"] = dict(ctx.extras.get("tool_call_counts") or {})
        turn.usage_json = usage
        self._record_result(session, ledger, turn, call, outcome, approved_by=approved_by)
        return None

    def _run_tool(self, ctx: ToolContext, tool: ToolSpec, arguments: BaseModel) -> ToolOutcome:
        from book_agent.infra import tracing

        with tracing.span(
            f"agent.tool {tool.name}",
            **{
                "book_agent.turn_id": ctx.turn_id,
                "book_agent.agent_kind": ctx.agent_kind,
                "book_agent.run_id": ctx.run_id,
                "book_agent.tool_permission": tool.permission.value,
            },
        ) as current:
            outcome = self._run_tool_untraced(ctx, tool, arguments)
            tracing.set_attributes(current, **{"book_agent.tool_ok": outcome.ok, "book_agent.tool_error": outcome.error})
            return outcome

    def _run_tool_untraced(self, ctx: ToolContext, tool: ToolSpec, arguments: BaseModel) -> ToolOutcome:
        trace.tool_called(
            ctx.session,
            turn_id=ctx.turn_id,
            agent_kind=ctx.agent_kind,
            run_id=ctx.run_id,
            chapter_id=ctx.chapter_id,
            payload={"tool": tool.name, "permission": tool.permission.value},
        )
        try:
            for hook in self.hooks:
                hook.before_tool(ctx, tool, arguments)
            output = tool.handler(ctx, arguments)
            outcome = ToolOutcome(ok=True, output=output)
        except ToolRejected as exc:
            outcome = ToolOutcome(ok=False, error=f"rejected: {exc}")
        except ToolError as exc:
            outcome = ToolOutcome(ok=False, error=str(exc))
        except Exception as exc:  # the model gets a chance to recover; the failure is on record
            logger.exception("Tool %s failed inside turn %s", tool.name, ctx.turn_id)
            outcome = ToolOutcome(ok=False, error=f"{type(exc).__name__}: {str(exc)[:300]}")
        for hook in self.hooks:
            try:
                hook.after_tool(ctx, tool, arguments, outcome)
            except Exception:
                logger.exception("after_tool hook %s failed", type(hook).__name__)
        trace.tool_returned(
            ctx.session,
            turn_id=ctx.turn_id,
            agent_kind=ctx.agent_kind,
            run_id=ctx.run_id,
            chapter_id=ctx.chapter_id,
            payload={"tool": tool.name, "ok": outcome.ok, **({"error": outcome.error[:200]} if outcome.error else {})},
        )
        return outcome

    def _record_result(
        self,
        session: Session,
        ledger: AgentLedgerRepository,
        turn: AgentTurn,
        call: ToolCall,
        outcome: ToolOutcome,
        *,
        approved_by: str | None = None,
    ) -> None:
        images: list[dict[str, Any]] = []
        if outcome.ok and isinstance(outcome.output, dict) and IMAGE_OUTPUT_KEY in outcome.output:
            # Images travel next to the JSON result; the model receives them as an image message.
            images = [dict(image) for image in outcome.output.get(IMAGE_OUTPUT_KEY) or []]
            outcome = ToolOutcome(ok=True, output={k: v for k, v in outcome.output.items() if k != IMAGE_OUTPUT_KEY})
        content = {"call_id": call.call_id, "name": call.name, "result": outcome.to_content()}
        if approved_by is not None:
            content["approved_by"] = approved_by
        token_count = estimate_tokens(json.dumps(content, ensure_ascii=False)) + IMAGE_TOKEN_ESTIMATE * len(images)
        if images:
            content["images"] = images
        ledger.append_item(
            turn.id,
            kind=AgentItemKind.TOOL_RESULT,
            content=content,
            token_count=token_count,
        )

    # --- approvals: resume ----------------------------------------------------

    def _settle_pending_tool_calls(
        self, session: Session, ledger: AgentLedgerRepository, turn: AgentTurn, items: list[AgentItem]
    ) -> TurnOutcome | None:
        """Execute or reject tool calls whose approval was decided while the turn was paused."""
        results = {str(item.content_json.get("call_id")) for item in items if item.kind == AgentItemKind.TOOL_RESULT}
        approvals_by_call = {
            str(item.content_json.get("call_id")): item.content_json for item in items if item.kind == AgentItemKind.APPROVAL_RESULT
        }
        for item in items:
            if item.kind != AgentItemKind.TOOL_CALL:
                continue
            call_id = str(item.content_json.get("call_id"))
            if call_id in results:
                continue
            decision = approvals_by_call.get(call_id)
            if decision is None:
                # Still waiting; the runner is not supposed to be here.
                turn.status = AgentTurnStatus.AWAITING_APPROVAL
                session.flush()
                return self._outcome(turn)
            call = ToolCall(call_id=call_id, name=str(item.content_json.get("name")), arguments=dict(item.content_json.get("arguments") or {}))
            if decision.get("approved"):
                self._execute_call(session, ledger, turn, call, item, approved_by=str(decision.get("decided_by") or "human"))
            else:
                self._record_result(
                    session,
                    ledger,
                    turn,
                    call,
                    ToolOutcome(ok=False, error=f"rejected by {decision.get('decided_by') or 'reviewer'}: {decision.get('note') or 'no reason given'}"),
                )
        # The step that stopped for approval may have asked for more calls after the one that
        # waited; they never started. Run them now, in order, so every call the model made gets a
        # result (providers reject a conversation with unanswered tool calls).
        last_assistant = next((item for item in reversed(items) if item.kind == AgentItemKind.ASSISTANT), None)
        if last_assistant is not None:
            started = {str(item.content_json.get("call_id")) for item in items if item.kind == AgentItemKind.TOOL_CALL}
            for raw in (last_assistant.content_json or {}).get("tool_calls") or []:
                call_id = str(raw.get("call_id"))
                if call_id in started:
                    continue
                call = ToolCall(call_id=call_id, name=str(raw.get("name")), arguments=dict(raw.get("arguments") or {}))
                tool_item = ledger.append_item(
                    turn.id,
                    kind=AgentItemKind.TOOL_CALL,
                    content={"call_id": call.call_id, "name": call.name, "arguments": call.arguments},
                )
                halted = self._execute_call(session, ledger, turn, call, tool_item)
                if halted is not None:
                    return halted
        return None

    # --- budget / compaction / finish ----------------------------------------

    def _pause_for_budget(self, session: Session, ledger: AgentLedgerRepository, turn: AgentTurn, reason: str) -> None:
        approval = ledger.create_approval(
            document_id=turn.document_id,
            turn_id=turn.id,
            kind="budget_extension",
            payload={"reason": reason, "usage": dict(turn.usage_json or {}), "budget": dict(turn.budget_json or {})},
        )
        ledger.append_item(turn.id, kind=AgentItemKind.APPROVAL_REQUEST, content={"approval_id": approval.id, "kind": "budget_extension", "reason": reason})
        turn.status = AgentTurnStatus.PAUSED
        turn.stop_reason = reason
        session.flush()
        trace.approval_requested(
            session,
            turn_id=turn.id,
            agent_kind=turn.agent_kind,
            run_id=turn.run_id,
            chapter_id=None,
            payload={"approval_id": approval.id, "kind": "budget_extension", "reason": reason},
        )

    def _compact(self, session: Session, ledger: AgentLedgerRepository, turn: AgentTurn, items: list[AgentItem]) -> None:
        messages = assemble_messages(items)
        summary_request = messages + [
            {
                "role": "user",
                "content": (
                    "Summarize the work so far for your own continuation: decisions taken, tool results that still matter, "
                    "and what remains to be done. Plain text, no tool calls."
                ),
            }
        ]
        step = self.model.step(model_name=turn.model_name or "", messages=summary_request, tools=[])
        self._bump_usage(turn, step, tool_calls=0, count_step=False)
        ledger.append_item(
            turn.id,
            kind=AgentItemKind.COMPACTION,
            content={"summary": step.text or "", "compacted_items": len(items)},
            token_count=estimate_tokens(step.text or ""),
        )

    def _finish(self, session: Session, ledger: AgentLedgerRepository, turn: AgentTurn, *, status: AgentTurnStatus, final_text: str | None) -> None:
        turn.status = status
        turn.finished_at = datetime.now(timezone.utc)
        turn.result_json = {**(turn.result_json or {}), "final_text": final_text}
        session.flush()
        trace.turn_finished(
            session,
            turn_id=turn.id,
            agent_kind=turn.agent_kind,
            run_id=turn.run_id,
            chapter_id=None,
            payload={"status": status.value, "usage": dict(turn.usage_json or {})},
        )

    def fail(self, turn_id: str, *, error: BaseException) -> None:
        with session_scope(self.session_factory) as session:
            ledger = AgentLedgerRepository(session)
            turn = ledger.get_turn_for_update(turn_id)
            turn.status = AgentTurnStatus.FAILED
            turn.stop_reason = "error"
            turn.error_json = {"error_class": type(error).__name__, "message": str(error)[:1000]}
            turn.finished_at = datetime.now(timezone.utc)
            session.flush()
            trace.turn_finished(
                session,
                turn_id=turn.id,
                agent_kind=turn.agent_kind,
                run_id=turn.run_id,
                chapter_id=None,
                payload={"status": "failed", "error_class": type(error).__name__},
            )

    # --- helpers ---------------------------------------------------------------

    def _bump_usage(self, turn: AgentTurn, step: AgentStep, *, tool_calls: int, count_step: bool = True) -> None:
        usage = dict(turn.usage_json or {})
        if count_step:
            usage["steps"] = int(usage.get("steps", 0)) + 1
        usage["tool_calls"] = int(usage.get("tool_calls", 0)) + tool_calls
        if step.usage is not None:
            usage["token_in"] = int(usage.get("token_in", 0)) + int(step.usage.token_in or 0)
            usage["token_out"] = int(usage.get("token_out", 0)) + int(step.usage.token_out or 0)
            usage["cost_usd"] = round(float(usage.get("cost_usd", 0.0)) + float(step.usage.cost_usd or 0.0), 8)
        turn.usage_json = usage

    def _tool_context(self, session: Session, turn: AgentTurn) -> ToolContext:
        return ToolContext(
            session=session,
            document_id=turn.document_id,
            agent_kind=turn.agent_kind,
            turn_id=turn.id,
            run_id=turn.run_id,
            chapter_id=turn.scope_id if turn.scope_type == "chapter" else None,
            actor_id=f"agent.{turn.agent_kind}",
            extras={**self.tool_extras, "tool_call_counts": dict((turn.usage_json or {}).get("tool_call_counts") or {})},
        )

    def _check_lease(self, session: Session) -> None:
        if self.lease_check is not None:
            self.lease_check(session)

    @staticmethod
    def _outcome(turn: AgentTurn) -> TurnOutcome:
        return TurnOutcome(
            turn_id=turn.id,
            status=turn.status,
            stop_reason=turn.stop_reason,
            final_text=(turn.result_json or {}).get("final_text"),
            usage=dict(turn.usage_json or {}),
        )


@dataclass(slots=True)
class _PreparedStep:
    outcome: TurnOutcome | None = None
    turn_id: str = ""
    agent_kind: str = ""
    run_id: str | None = None
    document_id: str = ""
    model_name: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
