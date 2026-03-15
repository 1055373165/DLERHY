from typing import Any

from pydantic import Field

from book_agent.domain.enums import DocumentRunType
from book_agent.schemas.common import BaseSchema


class RunBudgetRequest(BaseSchema):
    max_wall_clock_seconds: int | None = Field(default=None, ge=1)
    max_total_cost_usd: float | None = Field(default=None, ge=0)
    max_total_token_in: int | None = Field(default=None, ge=1)
    max_total_token_out: int | None = Field(default=None, ge=1)
    max_retry_count_per_work_item: int | None = Field(default=None, ge=0)
    max_consecutive_failures: int | None = Field(default=None, ge=0)
    max_parallel_workers: int | None = Field(default=None, ge=1)
    max_parallel_requests_per_provider: int | None = Field(default=None, ge=1)
    max_auto_followup_attempts: int | None = Field(default=None, ge=1)


class CreateDocumentRunRequest(BaseSchema):
    document_id: str
    run_type: DocumentRunType
    requested_by: str
    backend: str | None = None
    model_name: str | None = None
    priority: int = Field(default=100, ge=0)
    resume_from_run_id: str | None = None
    status_detail_json: dict[str, Any] = Field(default_factory=dict)
    budget: RunBudgetRequest | None = None


class RunControlRequest(BaseSchema):
    actor_id: str
    note: str | None = None
    detail_json: dict[str, Any] = Field(default_factory=dict)


class RunBudgetResponse(BaseSchema):
    max_wall_clock_seconds: int | None = None
    max_total_cost_usd: float | None = None
    max_total_token_in: int | None = None
    max_total_token_out: int | None = None
    max_retry_count_per_work_item: int | None = None
    max_consecutive_failures: int | None = None
    max_parallel_workers: int | None = None
    max_parallel_requests_per_provider: int | None = None
    max_auto_followup_attempts: int | None = None


class RunWorkItemSummaryResponse(BaseSchema):
    total_count: int
    status_counts: dict[str, int] = Field(default_factory=dict)
    stage_counts: dict[str, int] = Field(default_factory=dict)


class RunLeaseSummaryResponse(BaseSchema):
    total_count: int
    status_counts: dict[str, int] = Field(default_factory=dict)
    latest_heartbeat_at: str | None = None


class RunEventSummaryResponse(BaseSchema):
    event_count: int
    latest_event_at: str | None = None


class DocumentRunSummaryResponse(BaseSchema):
    run_id: str
    document_id: str
    run_type: str
    status: str
    backend: str | None = None
    model_name: str | None = None
    requested_by: str | None = None
    priority: int
    resume_from_run_id: str | None = None
    stop_reason: str | None = None
    status_detail_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    budget: RunBudgetResponse | None = None
    work_items: RunWorkItemSummaryResponse
    worker_leases: RunLeaseSummaryResponse
    events: RunEventSummaryResponse


class RunAuditEventResponse(BaseSchema):
    event_id: str
    run_id: str
    work_item_id: str | None = None
    event_type: str
    actor_type: str
    actor_id: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class RunAuditEventPageResponse(BaseSchema):
    run_id: str
    event_count: int
    record_count: int
    offset: int
    limit: int | None = None
    has_more: bool = False
    entries: list[RunAuditEventResponse] = Field(default_factory=list)
