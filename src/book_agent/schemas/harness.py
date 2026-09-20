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


class TermResponse(BaseSchema):
    source_term: str
    target_term: str
    term_type: str
    lock_level: str
    # Who locked it last, from the approval record: e.g. auto.fast_reading_mode, or human:<name>.
    locked_by: str | None = None


class TermUpdateRequest(BaseSchema):
    source_term: str = Field(min_length=1, max_length=500)
    # New rendering; required when lock is true.
    target_term: str | None = Field(default=None, max_length=500)
    # false unlocks the term: review stops blocking on other renderings.
    lock: bool = True
    decided_by: str = Field(min_length=1, max_length=200)

