"""Approvals inbox, decisions and BOOK.md for the agent harness."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from book_agent.app.api.deps import get_db_session
from book_agent.app.runtime.document_run_executor import ensure_document_run_executor
from book_agent.domain.enums import ApprovalStatus, DecisionScope
from book_agent.domain.models import Document
from book_agent.harness.approvals.service import ApprovalService
from book_agent.harness.context.book_md import load_book_guide
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.schemas.harness import (
    AgentTurnResponse,
    ApprovalDecisionRequest,
    ApprovalResponse,
    BookGuideResponse,
    DecisionResponse,
)

documents_router = APIRouter()
approvals_router = APIRouter()


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _approval(approval) -> ApprovalResponse:
    return ApprovalResponse(
        id=approval.id,
        document_id=approval.document_id,
        turn_id=approval.turn_id,
        kind=approval.kind,
        status=approval.status.value,
        payload_json=dict(approval.payload_json or {}),
        policy_id=approval.policy_id,
        decided_by=approval.decided_by,
        decided_at=_iso(approval.decided_at),
        decision_note=approval.decision_note,
        created_at=_iso(approval.created_at),
    )


def _require_document(session: Session, document_id: str) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return document


@documents_router.get("/{document_id}/approvals", response_model=list[ApprovalResponse])
def list_document_approvals(
    document_id: str,
    status: str | None = Query(default="pending"),
    session: Session = Depends(get_db_session),
) -> list[ApprovalResponse]:
    _require_document(session, document_id)
    status_filter = None
    if status and status != "all":
        try:
            status_filter = ApprovalStatus(status)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"unknown approval status {status!r}")
    ledger = AgentLedgerRepository(session)
    return [_approval(item) for item in ledger.list_approvals(document_id, status=status_filter)]


@documents_router.get("/{document_id}/decisions", response_model=list[DecisionResponse])
def list_document_decisions(document_id: str, session: Session = Depends(get_db_session)) -> list[DecisionResponse]:
    _require_document(session, document_id)
    decisions = AgentLedgerRepository(session).list_decisions(document_id, scope=DecisionScope.BOOK)
    return [
        DecisionResponse(
            id=item.id,
            scope=item.scope.value,
            scope_id=item.scope_id,
            key=item.key,
            value_json=dict(item.value_json or {}),
            rationale=item.rationale,
            decided_by=item.decided_by,
            turn_id=item.turn_id,
            created_at=_iso(item.created_at),
        )
        for item in decisions
    ]


@documents_router.get("/{document_id}/book-guide", response_model=BookGuideResponse)
def get_book_guide(document_id: str, session: Session = Depends(get_db_session)) -> BookGuideResponse:
    document = _require_document(session, document_id)
    guide = load_book_guide(session, document_id)
    return BookGuideResponse(
        document_id=document_id,
        markdown=guide.render_markdown(title=document.title_src or document.title),
        prompt_guidance=guide.render_prompt_guidance(),
        locked_term_count=len(guide.locked_terms),
        preferred_term_count=len(guide.preferred_terms),
        decision_count=len(guide.decisions),
    )


@documents_router.get("/{document_id}/agent-turns", response_model=list[AgentTurnResponse])
def list_agent_turns(document_id: str, session: Session = Depends(get_db_session)) -> list[AgentTurnResponse]:
    _require_document(session, document_id)
    return [
        AgentTurnResponse(
            id=turn.id,
            document_id=turn.document_id,
            run_id=turn.run_id,
            agent_kind=turn.agent_kind,
            status=turn.status.value,
            model_name=turn.model_name,
            stop_reason=turn.stop_reason,
            usage_json=dict(turn.usage_json or {}),
            result_json=dict(turn.result_json or {}),
            started_at=_iso(turn.started_at),
            finished_at=_iso(turn.finished_at),
        )
        for turn in AgentLedgerRepository(session).list_turns(document_id)
    ]


def _decide(request: Request, session: Session, approval_id: str, payload: ApprovalDecisionRequest, *, approved: bool) -> ApprovalResponse:
    ledger = AgentLedgerRepository(session)
    try:
        approval = ledger.get_approval(approval_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="approval not found")
    if payload.extension and approval.kind == "budget_extension":
        approval.payload_json = {**(approval.payload_json or {}), "extension": dict(payload.extension)}
    try:
        decided = ApprovalService(session).decide(approval_id, approved=approved, decided_by=payload.decided_by, note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    session.commit()
    if decided.turn_id is not None:
        turn = ledger.get_turn(decided.turn_id)
        if turn.run_id is not None:
            ensure_document_run_executor(request.app).wake(turn.run_id)
    return _approval(decided)


@approvals_router.post("/{approval_id}/approve", response_model=ApprovalResponse)
def approve(approval_id: str, payload: ApprovalDecisionRequest, request: Request, session: Session = Depends(get_db_session)) -> ApprovalResponse:
    return _decide(request, session, approval_id, payload, approved=True)


@approvals_router.post("/{approval_id}/reject", response_model=ApprovalResponse)
def reject(approval_id: str, payload: ApprovalDecisionRequest, request: Request, session: Session = Depends(get_db_session)) -> ApprovalResponse:
    return _decide(request, session, approval_id, payload, approved=False)
