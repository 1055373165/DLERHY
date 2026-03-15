from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from book_agent.app.api.deps import get_db_session
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.schemas.run_control import (
    CreateDocumentRunRequest,
    DocumentRunSummaryResponse,
    RunAuditEventPageResponse,
    RunControlRequest,
)
from book_agent.services.run_control import (
    DocumentRunSummary,
    RunAuditEventPage,
    RunBudgetSummary,
    RunControlService,
    RunControlTransitionError,
)

router = APIRouter()


def _wake_executor(request: Request, run_id: str) -> None:
    executor = getattr(request.app.state, "document_run_executor", None)
    if executor is not None:
        executor.wake(run_id)


def _service(session: Session) -> RunControlService:
    return RunControlService(RunControlRepository(session))


def _to_run_summary_response(summary: DocumentRunSummary) -> DocumentRunSummaryResponse:
    return DocumentRunSummaryResponse(
        run_id=summary.run_id,
        document_id=summary.document_id,
        run_type=summary.run_type,
        status=summary.status,
        backend=summary.backend,
        model_name=summary.model_name,
        requested_by=summary.requested_by,
        priority=summary.priority,
        resume_from_run_id=summary.resume_from_run_id,
        stop_reason=summary.stop_reason,
        status_detail_json=summary.status_detail_json,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
        started_at=summary.started_at,
        finished_at=summary.finished_at,
        budget=(
            {
                "max_wall_clock_seconds": summary.budget.max_wall_clock_seconds,
                "max_total_cost_usd": summary.budget.max_total_cost_usd,
                "max_total_token_in": summary.budget.max_total_token_in,
                "max_total_token_out": summary.budget.max_total_token_out,
                "max_retry_count_per_work_item": summary.budget.max_retry_count_per_work_item,
                "max_consecutive_failures": summary.budget.max_consecutive_failures,
                "max_parallel_workers": summary.budget.max_parallel_workers,
                "max_parallel_requests_per_provider": summary.budget.max_parallel_requests_per_provider,
                "max_auto_followup_attempts": summary.budget.max_auto_followup_attempts,
            }
            if summary.budget is not None
            else None
        ),
        work_items={
            "total_count": summary.work_items.total_count,
            "status_counts": summary.work_items.status_counts,
            "stage_counts": summary.work_items.stage_counts,
        },
        worker_leases={
            "total_count": summary.worker_leases.total_count,
            "status_counts": summary.worker_leases.status_counts,
            "latest_heartbeat_at": summary.worker_leases.latest_heartbeat_at,
        },
        events={
            "event_count": summary.events.event_count,
            "latest_event_at": summary.events.latest_event_at,
        },
    )


def _to_run_events_response(page: RunAuditEventPage) -> RunAuditEventPageResponse:
    return RunAuditEventPageResponse(
        run_id=page.run_id,
        event_count=page.event_count,
        record_count=page.record_count,
        offset=page.offset,
        limit=page.limit,
        has_more=page.has_more,
        entries=[
            {
                "event_id": entry.event_id,
                "run_id": entry.run_id,
                "work_item_id": entry.work_item_id,
                "event_type": entry.event_type,
                "actor_type": entry.actor_type,
                "actor_id": entry.actor_id,
                "payload_json": entry.payload_json,
                "created_at": entry.created_at,
            }
            for entry in page.entries
        ],
    )


@router.post("", response_model=DocumentRunSummaryResponse, status_code=status.HTTP_201_CREATED)
def create_run(
    request: Request,
    payload: CreateDocumentRunRequest,
    session: Session = Depends(get_db_session),
) -> DocumentRunSummaryResponse:
    service = _service(session)
    try:
        summary = service.create_run(
            document_id=payload.document_id,
            run_type=payload.run_type,
            requested_by=payload.requested_by,
            backend=payload.backend,
            model_name=payload.model_name,
            priority=payload.priority,
            resume_from_run_id=payload.resume_from_run_id,
            status_detail_json=payload.status_detail_json,
            budget=(
                RunBudgetSummary(**payload.budget.model_dump())
                if payload.budget is not None
                else None
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    _wake_executor(request, summary.run_id)
    return _to_run_summary_response(summary)


@router.get("/{run_id}", response_model=DocumentRunSummaryResponse)
def get_run_summary(
    run_id: str,
    session: Session = Depends(get_db_session),
) -> DocumentRunSummaryResponse:
    try:
        summary = _service(session).get_run_summary(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_run_summary_response(summary)


@router.get("/{run_id}/events", response_model=RunAuditEventPageResponse)
def get_run_events(
    run_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
) -> RunAuditEventPageResponse:
    try:
        page = _service(session).get_run_events(run_id, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _to_run_events_response(page)


@router.post("/{run_id}/pause", response_model=DocumentRunSummaryResponse)
def pause_run(
    run_id: str,
    request: Request,
    payload: RunControlRequest,
    session: Session = Depends(get_db_session),
) -> DocumentRunSummaryResponse:
    try:
        summary = _service(session).pause_run(
            run_id,
            actor_id=payload.actor_id,
            note=payload.note,
            detail_json=payload.detail_json,
        )
    except RunControlTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    _wake_executor(request, summary.run_id)
    return _to_run_summary_response(summary)


@router.post("/{run_id}/resume", response_model=DocumentRunSummaryResponse)
def resume_run(
    run_id: str,
    request: Request,
    payload: RunControlRequest,
    session: Session = Depends(get_db_session),
) -> DocumentRunSummaryResponse:
    try:
        summary = _service(session).resume_run(
            run_id,
            actor_id=payload.actor_id,
            note=payload.note,
            detail_json=payload.detail_json,
        )
    except RunControlTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    _wake_executor(request, summary.run_id)
    return _to_run_summary_response(summary)


@router.post("/{run_id}/drain", response_model=DocumentRunSummaryResponse)
def drain_run(
    run_id: str,
    request: Request,
    payload: RunControlRequest,
    session: Session = Depends(get_db_session),
) -> DocumentRunSummaryResponse:
    try:
        summary = _service(session).drain_run(
            run_id,
            actor_id=payload.actor_id,
            note=payload.note,
            detail_json=payload.detail_json,
        )
    except RunControlTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    _wake_executor(request, summary.run_id)
    return _to_run_summary_response(summary)


@router.post("/{run_id}/cancel", response_model=DocumentRunSummaryResponse)
def cancel_run(
    run_id: str,
    request: Request,
    payload: RunControlRequest,
    session: Session = Depends(get_db_session),
) -> DocumentRunSummaryResponse:
    try:
        summary = _service(session).cancel_run(
            run_id,
            actor_id=payload.actor_id,
            note=payload.note,
            detail_json=payload.detail_json,
        )
    except RunControlTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    _wake_executor(request, summary.run_id)
    return _to_run_summary_response(summary)
