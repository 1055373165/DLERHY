"""API shapes for the agent harness: approvals, decisions, BOOK.md, turns."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from book_agent.schemas.common import BaseSchema


class ApprovalResponse(BaseSchema):
    id: str
    document_id: str
    turn_id: str | None = None
    kind: str
    status: str
    payload_json: dict[str, Any] = Field(default_factory=dict)
    policy_id: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    decision_note: str | None = None
    created_at: str | None = None


class ApprovalDecisionRequest(BaseSchema):
    decided_by: str = Field(min_length=1, max_length=200)
    note: str | None = None
    # For budget_extension approvals: how much to add.
    extension: dict[str, int] | None = None


class DecisionResponse(BaseSchema):
    id: str
    scope: str
    scope_id: str | None = None
    key: str
    value_json: dict[str, Any] = Field(default_factory=dict)
    rationale: str | None = None
    decided_by: str
    turn_id: str | None = None
    created_at: str | None = None


class BookGuideResponse(BaseSchema):
    document_id: str
    markdown: str
    prompt_guidance: str
    locked_term_count: int
    preferred_term_count: int
    decision_count: int


class AgentTurnResponse(BaseSchema):
    id: str
    document_id: str
    run_id: str | None = None
    agent_kind: str
    status: str
    model_name: str | None = None
    stop_reason: str | None = None
    usage_json: dict[str, Any] = Field(default_factory=dict)
    result_json: dict[str, Any] = Field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None
