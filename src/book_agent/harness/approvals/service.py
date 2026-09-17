"""Deciding approvals and letting the paused turn continue."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from book_agent.domain.enums import AgentItemKind, AgentTurnStatus, ApprovalStatus
from book_agent.domain.models.agent import Approval
from book_agent.harness.kernel import trace
from book_agent.infra.repositories.agent import AgentLedgerRepository


class ApprovalService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.ledger = AgentLedgerRepository(session)

    def decide(self, approval_id: str, *, approved: bool, decided_by: str, note: str | None = None) -> Approval:
        """Record the decision and put the owning turn back to RUNNING.

        The turn does not continue here; the executor picks it up (the run
        loop seeds a new agent work item for a RUNNING turn without one) and
        the runner executes or rejects the waiting tool call first.
        """
        approval = self.ledger.get_approval(approval_id)
        if approval.status != ApprovalStatus.PENDING:
            raise ValueError(f"approval {approval_id} is already {approval.status.value}")
        approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        approval.decided_by = decided_by
        approval.decided_at = datetime.now(timezone.utc)
        approval.decision_note = note
        self.session.flush()
        turn = None
        if approval.turn_id is not None:
            turn = self.ledger.get_turn_for_update(approval.turn_id)
            call_id = None
            for item in self.ledger.list_items(turn.id):
                if item.kind == AgentItemKind.APPROVAL_REQUEST and item.content_json.get("approval_id") == approval.id:
                    call_id = item.content_json.get("call_id")
                    break
            self.ledger.append_item(
                turn.id,
                kind=AgentItemKind.APPROVAL_RESULT,
                content={
                    "approval_id": approval.id,
                    "call_id": call_id,
                    "kind": approval.kind,
                    "approved": approved,
                    "decided_by": decided_by,
                    "note": note,
                },
            )
            if approval.kind == "budget_extension":
                if approved:
                    extension = dict(approval.payload_json.get("extension") or {})
                    budget = dict(turn.budget_json or {})
                    for key in ("max_steps", "max_tool_calls"):
                        budget[key] = int(budget.get(key, 0) or 0) + int(extension.get(key, budget.get(key, 0) or 0))
                    turn.budget_json = budget
                    turn.status = AgentTurnStatus.RUNNING
                    turn.stop_reason = None
                else:
                    turn.status = AgentTurnStatus.CANCELLED
                    turn.stop_reason = "budget_extension_rejected"
                    turn.finished_at = datetime.now(timezone.utc)
            elif turn.status == AgentTurnStatus.AWAITING_APPROVAL:
                turn.status = AgentTurnStatus.RUNNING
                turn.stop_reason = None
            self.session.flush()
            trace.approval_decided(
                self.session,
                turn_id=turn.id,
                agent_kind=turn.agent_kind,
                run_id=turn.run_id,
                chapter_id=None,
                payload={"approval_id": approval.id, "approved": approved, "decided_by": decided_by},
            )
        return approval
