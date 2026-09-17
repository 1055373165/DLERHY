"""Agent harness ledger: turns, their item stream, approvals and decisions.

A *turn* is one bounded agent loop (a work item of stage ``agent``). Its
*items* are the append-only conversation and tool ledger the loop is rebuilt
from after a crash or an approval. *Approvals* are the durable objects a human
(or an auto-approval policy) decides on before an irreversible tool runs.
*Decisions* are the book-level facts agents and humans settle (register,
preservation policy, resolved ambiguities); BOOK.md is rendered from them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, Uuid, event
from sqlalchemy.orm import Mapped, mapped_column

from book_agent.domain.enums import AgentItemKind, AgentTurnStatus, ApprovalStatus, DecisionScope
from book_agent.infra.db.base import Base, CreatedAtMixin, JsonDocument, TimestampMixin, UUIDPrimaryKeyMixin, enum_value_type


class AgentTurn(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_turns"
    __table_args__ = (
        Index("idx_agent_turns_document_kind", "document_id", "agent_kind", "created_at"),
        Index("idx_agent_turns_run", "run_id"),
    )

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("document_runs.id", ondelete="SET NULL")
    )
    work_item_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("work_items.id", ondelete="SET NULL")
    )
    agent_kind: Mapped[str] = mapped_column(Text, nullable=False)
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    status: Mapped[AgentTurnStatus] = mapped_column(
        enum_value_type(AgentTurnStatus, name="agent_turn_status"), nullable=False
    )
    model_name: Mapped[str | None] = mapped_column(Text)
    harness_version: Mapped[str] = mapped_column(Text, nullable=False, default="h1")
    skills_json: Mapped[list[Any]] = mapped_column(JsonDocument, nullable=False, default=list)
    budget_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    usage_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    result_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    error_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentItem(CreatedAtMixin, Base):
    """One entry of a turn's ledger. Append-only; ``ordinal`` orders the stream."""

    __tablename__ = "agent_items"
    __table_args__ = (UniqueConstraint("turn_id", "ordinal", name="uq_agent_items_turn_ordinal"),)

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    turn_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("agent_turns.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[AgentItemKind] = mapped_column(enum_value_type(AgentItemKind, name="agent_item_kind"), nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Approval(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (Index("idx_approvals_document_status", "document_id", "status"),)

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("agent_turns.id", ondelete="CASCADE")
    )
    tool_call_item_id: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer(), "sqlite"))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_value_type(ApprovalStatus, name="approval_status"), nullable=False
    )
    policy_id: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)


class Decision(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A settled book- or chapter-level fact. Append-only: revisit by adding a newer one."""

    __tablename__ = "decisions"
    __table_args__ = (Index("idx_decisions_document_scope_key", "document_id", "scope", "key", "created_at"),)

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[DecisionScope] = mapped_column(enum_value_type(DecisionScope, name="decision_scope"), nullable=False)
    scope_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    turn_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("agent_turns.id", ondelete="SET NULL")
    )


class AppendOnlyAgentLedgerViolation(RuntimeError):
    pass


def _reject_updates(mapper, connection, target) -> None:  # pragma: no cover - listener plumbing
    raise AppendOnlyAgentLedgerViolation(f"{type(target).__name__} rows are append-only")


for _model in (AgentItem, Decision):
    event.listen(_model, "before_update", _reject_updates)
