"""Persistence for the agent harness ledger (turns, items, approvals, decisions)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from book_agent.domain.enums import AgentItemKind, AgentTurnStatus, ApprovalStatus, DecisionScope
from book_agent.domain.models.agent import AgentItem, AgentTurn, Approval, Decision


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentLedgerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- turns ---------------------------------------------------------------

    def create_turn(
        self,
        *,
        document_id: str,
        agent_kind: str,
        scope_type: str,
        scope_id: str | None,
        run_id: str | None = None,
        work_item_id: str | None = None,
        model_name: str | None = None,
        budget: dict[str, Any] | None = None,
        skills: list[str] | None = None,
        harness_version: str = "h1",
    ) -> AgentTurn:
        turn = AgentTurn(
            document_id=document_id,
            run_id=run_id,
            work_item_id=work_item_id,
            agent_kind=agent_kind,
            scope_type=scope_type,
            scope_id=scope_id,
            status=AgentTurnStatus.RUNNING,
            model_name=model_name,
            harness_version=harness_version,
            skills_json=list(skills or []),
            budget_json=dict(budget or {}),
            usage_json={"steps": 0, "tool_calls": 0, "token_in": 0, "token_out": 0, "cost_usd": 0.0},
            started_at=_utcnow(),
        )
        self.session.add(turn)
        self.session.flush()
        return turn

    def get_turn(self, turn_id: str) -> AgentTurn:
        turn = self.session.get(AgentTurn, turn_id)
        if turn is None:
            raise ValueError(f"Agent turn not found: {turn_id}")
        return turn

    def get_turn_for_update(self, turn_id: str) -> AgentTurn:
        # Same trick as document_runs: a no-op UPDATE takes the row (PG) or
        # database (SQLite) write lock before the SELECT ... FOR UPDATE.
        self.session.flush()
        self.session.execute(update(AgentTurn).where(AgentTurn.id == turn_id).values(updated_at=AgentTurn.updated_at))
        turn = self.session.execute(
            select(AgentTurn).where(AgentTurn.id == turn_id).with_for_update().execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if turn is None:
            raise ValueError(f"Agent turn not found: {turn_id}")
        return turn

    def latest_turn(self, *, document_id: str, agent_kind: str, run_id: str | None = None) -> AgentTurn | None:
        stmt = select(AgentTurn).where(AgentTurn.document_id == document_id, AgentTurn.agent_kind == agent_kind)
        if run_id is not None:
            stmt = stmt.where(AgentTurn.run_id == run_id)
        return self.session.scalars(stmt.order_by(AgentTurn.created_at.desc(), AgentTurn.id.desc()).limit(1)).first()

    def list_turns(self, document_id: str, *, agent_kind: str | None = None) -> list[AgentTurn]:
        stmt = select(AgentTurn).where(AgentTurn.document_id == document_id)
        if agent_kind is not None:
            stmt = stmt.where(AgentTurn.agent_kind == agent_kind)
        return list(self.session.scalars(stmt.order_by(AgentTurn.created_at.asc(), AgentTurn.id.asc())).all())

    # --- items ----------------------------------------------------------------

    def list_items(self, turn_id: str) -> list[AgentItem]:
        return list(
            self.session.scalars(
                select(AgentItem).where(AgentItem.turn_id == turn_id).order_by(AgentItem.ordinal.asc())
            ).all()
        )

    def append_item(self, turn_id: str, *, kind: AgentItemKind, content: dict[str, Any], token_count: int = 0) -> AgentItem:
        next_ordinal = (
            self.session.scalar(select(func.max(AgentItem.ordinal)).where(AgentItem.turn_id == turn_id)) or 0
        ) + 1
        item = AgentItem(turn_id=turn_id, ordinal=next_ordinal, kind=kind, content_json=dict(content), token_count=token_count)
        self.session.add(item)
        self.session.flush()
        return item

    # --- approvals ------------------------------------------------------------

    def create_approval(
        self,
        *,
        document_id: str,
        turn_id: str | None,
        kind: str,
        payload: dict[str, Any],
        status: ApprovalStatus = ApprovalStatus.PENDING,
        tool_call_item_id: int | None = None,
        policy_id: str | None = None,
        decided_by: str | None = None,
    ) -> Approval:
        approval = Approval(
            document_id=document_id,
            turn_id=turn_id,
            tool_call_item_id=tool_call_item_id,
            kind=kind,
            payload_json=dict(payload),
            status=status,
            policy_id=policy_id,
            decided_by=decided_by,
            decided_at=_utcnow() if status != ApprovalStatus.PENDING else None,
        )
        self.session.add(approval)
        self.session.flush()
        return approval

    def get_approval(self, approval_id: str) -> Approval:
        approval = self.session.get(Approval, approval_id)
        if approval is None:
            raise ValueError(f"Approval not found: {approval_id}")
        return approval

    def list_approvals(self, document_id: str, *, status: ApprovalStatus | None = None) -> list[Approval]:
        stmt = select(Approval).where(Approval.document_id == document_id)
        if status is not None:
            stmt = stmt.where(Approval.status == status)
        return list(self.session.scalars(stmt.order_by(Approval.created_at.asc(), Approval.id.asc())).all())

    def approvals_for_turn(self, turn_id: str) -> list[Approval]:
        return list(
            self.session.scalars(
                select(Approval).where(Approval.turn_id == turn_id).order_by(Approval.created_at.asc(), Approval.id.asc())
            ).all()
        )

    # --- decisions ------------------------------------------------------------

    def record_decision(
        self,
        *,
        document_id: str,
        scope: DecisionScope,
        key: str,
        value: dict[str, Any],
        decided_by: str,
        rationale: str | None = None,
        scope_id: str | None = None,
        turn_id: str | None = None,
    ) -> Decision:
        decision = Decision(
            document_id=document_id,
            scope=scope,
            scope_id=scope_id,
            key=key,
            value_json=dict(value),
            rationale=rationale,
            decided_by=decided_by,
            turn_id=turn_id,
        )
        self.session.add(decision)
        self.session.flush()
        return decision

    def list_decisions(self, document_id: str, *, scope: DecisionScope | None = None, scope_id: str | None = None) -> list[Decision]:
        stmt = select(Decision).where(Decision.document_id == document_id)
        if scope is not None:
            stmt = stmt.where(Decision.scope == scope)
        if scope_id is not None:
            stmt = stmt.where(Decision.scope_id == scope_id)
        return list(self.session.scalars(stmt.order_by(Decision.created_at.asc(), Decision.id.asc())).all())

    def latest_decisions(self, document_id: str, *, scope: DecisionScope, scope_id: str | None = None) -> dict[str, Decision]:
        """The most recent decision per key (later rows supersede earlier ones)."""
        latest: dict[str, Decision] = {}
        for decision in self.list_decisions(document_id, scope=scope, scope_id=scope_id):
            latest[decision.key] = decision
        return latest
