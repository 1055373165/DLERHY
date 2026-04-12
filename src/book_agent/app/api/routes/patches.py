"""Reviewer-facing REST surface for ``RuntimePatchProposal`` objects.

Endpoints:

- ``GET  /v1/patches`` — list proposals, filterable by status / run_id /
  ``requires_human_review``. Used by reviewer UIs to pull the queue.
- ``GET  /v1/patches/{id}`` — full detail including ``diff_manifest_json``
  and ``validation_report_json`` so the reviewer sees what's about to
  land.
- ``POST /v1/patches/{id}/approve`` — reviewer green-lights the patch.
  The incident controller's ``publish_validated_patch`` call will then
  proceed past the human-review gate.
- ``POST /v1/patches/{id}/reject`` — terminal rejection.

Both transitions emit onto the events bus (``patch.approved`` /
``patch.rejected``) so the SSE stream surfaces them live.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from book_agent.app.api.deps import get_db_session
from book_agent.services.patch_review import (
    PatchProposalDetail,
    PatchProposalSummary,
    PatchReviewError,
    PatchReviewService,
)


router = APIRouter()


class PatchProposalSummaryResponse(BaseModel):
    proposal_id: str
    incident_id: str
    run_id: str | None
    status: str
    patch_surface: str | None
    requires_human_review: bool
    proposed_by: str | None
    approved_by: str | None
    approved_at: datetime | None
    rejected_by: str | None
    rejected_at: datetime | None
    review_notes: str | None
    created_at: datetime
    updated_at: datetime


class PatchProposalDetailResponse(PatchProposalSummaryResponse):
    diff_manifest_json: dict[str, Any]
    validation_report_json: dict[str, Any]
    status_detail_json: dict[str, Any]


class PatchProposalListResponse(BaseModel):
    items: list[PatchProposalSummaryResponse]
    count: int


class ApprovePatchRequest(BaseModel):
    reviewer_id: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class RejectPatchRequest(BaseModel):
    reviewer_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


def _summary_to_response(summary: PatchProposalSummary) -> PatchProposalSummaryResponse:
    return PatchProposalSummaryResponse(**asdict(summary))


def _detail_to_response(detail: PatchProposalDetail) -> PatchProposalDetailResponse:
    return PatchProposalDetailResponse(**asdict(detail))


@router.get("", response_model=PatchProposalListResponse)
def list_patch_proposals(
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Comma-separated status filter (e.g. 'validated,proposed')",
    ),
    run_id: str | None = Query(default=None),
    requires_human_review: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> PatchProposalListResponse:
    statuses: list[str] | None = None
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",") if s.strip()] or None

    service = PatchReviewService(session)
    summaries = service.list_proposals(
        statuses=statuses,
        run_id=run_id,
        requires_human_review=requires_human_review,
        limit=limit,
        offset=offset,
    )
    items = [_summary_to_response(s) for s in summaries]
    return PatchProposalListResponse(items=items, count=len(items))


@router.get("/{proposal_id}", response_model=PatchProposalDetailResponse)
def get_patch_proposal(
    proposal_id: str,
    session: Session = Depends(get_db_session),
) -> PatchProposalDetailResponse:
    service = PatchReviewService(session)
    try:
        detail = service.get_proposal(proposal_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _detail_to_response(detail)


@router.post("/{proposal_id}/approve", response_model=PatchProposalDetailResponse)
def approve_patch_proposal(
    proposal_id: str,
    payload: ApprovePatchRequest,
    session: Session = Depends(get_db_session),
) -> PatchProposalDetailResponse:
    service = PatchReviewService(session)
    try:
        detail = service.approve_proposal(
            proposal_id, reviewer_id=payload.reviewer_id, notes=payload.notes
        )
    except PatchReviewError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _detail_to_response(detail)


@router.post("/{proposal_id}/reject", response_model=PatchProposalDetailResponse)
def reject_patch_proposal(
    proposal_id: str,
    payload: RejectPatchRequest,
    session: Session = Depends(get_db_session),
) -> PatchProposalDetailResponse:
    service = PatchReviewService(session)
    try:
        detail = service.reject_proposal(
            proposal_id, reviewer_id=payload.reviewer_id, reason=payload.reason
        )
    except PatchReviewError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _detail_to_response(detail)
