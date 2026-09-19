"""Issue workbench: list, detail and human transitions for review issues."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from book_agent.app.api.deps import get_db_session
from book_agent.domain.enums import IssueStatus
from book_agent.domain.models import Document
from book_agent.schemas.issues import (
    IssueActionResponse,
    IssueDecisionRequest,
    IssueDetailResponse,
    IssueEventResponse,
    IssueListResponse,
    IssueResponse,
)
from book_agent.services.issues import IssueService, IssueTransitionError

documents_router = APIRouter()
issues_router = APIRouter()


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _issue(issue) -> IssueResponse:
    return IssueResponse(
        id=issue.id,
        document_id=issue.document_id,
        chapter_id=issue.chapter_id,
        block_id=issue.block_id,
        sentence_id=issue.sentence_id,
        packet_id=issue.packet_id,
        issue_type=issue.issue_type,
        root_cause_layer=issue.root_cause_layer.value,
        severity=issue.severity.value,
        blocking=bool(issue.blocking),
        detector=issue.detector.value,
        confidence=float(issue.confidence) if issue.confidence is not None else None,
        status=issue.status.value,
        version=int(issue.version or 1),
        reopen_count=int(issue.reopen_count or 0),
        suggested_action=issue.suggested_action,
        resolution_note=issue.resolution_note,
        decided_by=issue.decided_by,
        decided_at=_iso(issue.decided_at),
        evidence_json=dict(issue.evidence_json or {}),
        created_at=_iso(issue.created_at),
        updated_at=_iso(issue.updated_at),
        last_seen_at=_iso(issue.last_seen_at),
    )


def _event(event) -> IssueEventResponse:
    return IssueEventResponse(
        id=event.id,
        issue_id=event.issue_id,
        version=event.version,
        kind=event.kind.value,
        from_status=event.from_status.value if event.from_status is not None else None,
        to_status=event.to_status.value,
        actor_kind=event.actor_kind,
        actor_id=event.actor_id,
        note=event.note,
        evidence_json=dict(event.evidence_json or {}),
        created_at=_iso(event.created_at),
    )


def _action(action) -> IssueActionResponse:
    return IssueActionResponse(
        id=action.id,
        issue_id=action.issue_id,
        action_type=action.action_type.value,
        scope_type=action.scope_type.value,
        scope_id=action.scope_id,
        status=action.status.value,
        reason_json=dict(action.reason_json or {}),
        created_by=action.created_by.value,
        updated_at=_iso(action.updated_at),
    )


@documents_router.get("/{document_id}/issues", response_model=IssueListResponse)
def list_document_issues(
    document_id: str,
    status: str | None = Query(default="active", description="active (open+triaged), all, or a status value"),
    chapter_id: str | None = None,
    issue_type: str | None = None,
    blocking: bool | None = None,
    detector: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_db_session),
) -> IssueListResponse:
    if session.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="document not found")
    if status not in (None, "active", "all") and status not in {item.value for item in IssueStatus}:
        raise HTTPException(status_code=422, detail=f"unknown status filter: {status}")
    page = IssueService(session).list_issues(
        document_id,
        status=status,
        chapter_id=chapter_id,
        issue_type=issue_type,
        blocking=blocking,
        detector=detector,
        offset=offset,
        limit=limit,
    )
    return IssueListResponse(
        document_id=document_id,
        total_count=page.total_count,
        offset=offset,
        limit=limit,
        has_more=offset + len(page.entries) < page.total_count,
        entries=[_issue(issue) for issue in page.entries],
    )


@issues_router.get("/{issue_id}", response_model=IssueDetailResponse)
def get_issue_detail(issue_id: str, session: Session = Depends(get_db_session)) -> IssueDetailResponse:
    try:
        detail = IssueService(session).detail(issue_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return IssueDetailResponse(
        issue=_issue(detail.issue),
        events=[_event(event) for event in detail.events],
        actions=[_action(action) for action in detail.actions],
        source_text=detail.source_text,
        target_text=detail.target_text,
        chapter_title=detail.chapter_title,
    )


def _transition(issue_id: str, payload: IssueDecisionRequest, session: Session, to_status: IssueStatus) -> IssueResponse:
    try:
        issue = IssueService(session).transition(
            issue_id, to_status=to_status, actor_id=f"human:{payload.actor_id}", note=payload.note
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IssueTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _issue(issue)


@issues_router.post("/{issue_id}/triage", response_model=IssueResponse)
def triage_issue(issue_id: str, payload: IssueDecisionRequest, session: Session = Depends(get_db_session)) -> IssueResponse:
    return _transition(issue_id, payload, session, IssueStatus.TRIAGED)


@issues_router.post("/{issue_id}/wontfix", response_model=IssueResponse)
def wontfix_issue(issue_id: str, payload: IssueDecisionRequest, session: Session = Depends(get_db_session)) -> IssueResponse:
    return _transition(issue_id, payload, session, IssueStatus.WONTFIX)


@issues_router.post("/{issue_id}/resolve", response_model=IssueResponse)
def resolve_issue(issue_id: str, payload: IssueDecisionRequest, session: Session = Depends(get_db_session)) -> IssueResponse:
    return _transition(issue_id, payload, session, IssueStatus.RESOLVED)


@issues_router.post("/{issue_id}/reopen", response_model=IssueResponse)
def reopen_issue(issue_id: str, payload: IssueDecisionRequest, session: Session = Depends(get_db_session)) -> IssueResponse:
    return _transition(issue_id, payload, session, IssueStatus.OPEN)
