"""API shapes for the review issue workbench."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from book_agent.schemas.common import BaseSchema


class IssueResponse(BaseSchema):
    id: str
    document_id: str
    chapter_id: str | None = None
    block_id: str | None = None
    sentence_id: str | None = None
    packet_id: str | None = None
    issue_type: str
    root_cause_layer: str
    severity: str
    blocking: bool
    detector: str
    confidence: float | None = None
    status: str
    version: int
    reopen_count: int
    suggested_action: str | None = None
    resolution_note: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None
    last_seen_at: str | None = None


class IssueEventResponse(BaseSchema):
    id: str
    issue_id: str
    version: int
    kind: str
    from_status: str | None = None
    to_status: str
    actor_kind: str
    actor_id: str | None = None
    note: str | None = None
    evidence_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class IssueActionResponse(BaseSchema):
    id: str
    issue_id: str
    action_type: str
    scope_type: str
    scope_id: str | None = None
    status: str
    reason_json: dict[str, Any] = Field(default_factory=dict)
    created_by: str
    updated_at: str | None = None


class IssueDetailResponse(BaseSchema):
    issue: IssueResponse
    events: list[IssueEventResponse] = Field(default_factory=list)
    actions: list[IssueActionResponse] = Field(default_factory=list)
    source_text: str | None = None
    target_text: str | None = None
    chapter_title: str | None = None


class IssueListResponse(BaseSchema):
    document_id: str
    total_count: int
    offset: int
    limit: int
    has_more: bool
    entries: list[IssueResponse] = Field(default_factory=list)


class IssueDecisionRequest(BaseSchema):
    actor_id: str = Field(min_length=1, max_length=200)
    note: str | None = None
