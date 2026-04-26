from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from book_agent.app.api.deps import get_db_session
from book_agent.core.config import get_settings
from book_agent.schemas.provider import (
    ProviderCredentialCreate,
    ProviderCredentialRead,
    ProviderCredentialUpdate,
    ProviderTestResult,
)
from book_agent.services import provider_credentials as svc
from book_agent.services.provider_credentials import api_key_preview

router = APIRouter()


def _to_read(record) -> ProviderCredentialRead:
    return ProviderCredentialRead(
        id=record.id,
        name=record.name,
        provider_kind=record.provider_kind,
        model_name=record.model_name,
        base_url=record.base_url,
        streaming=record.streaming,
        max_output_tokens=record.max_output_tokens,
        timeout_seconds=record.timeout_seconds,
        max_retries=record.max_retries,
        retry_backoff_seconds=record.retry_backoff_seconds_x10 / 10.0,
        is_active=record.is_active,
        api_key_preview=api_key_preview(record),
        last_test_status=record.last_test_status,
        last_test_at=record.last_test_at,
        last_test_message=record.last_test_message,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.get("", response_model=list[ProviderCredentialRead])
def list_providers(
    request: Request,
    session: Session = Depends(get_db_session),
) -> list[ProviderCredentialRead]:
    # First-touch bootstrap from .env if the table is empty.
    svc.resolve_active_credential(session, get_settings())
    rows = svc.list_credentials(session)
    return [_to_read(row) for row in rows]


@router.post("", response_model=ProviderCredentialRead, status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: ProviderCredentialCreate,
    request: Request,
    session: Session = Depends(get_db_session),
) -> ProviderCredentialRead:
    record = svc.create_credential(
        session,
        name=payload.name,
        provider_kind=payload.provider_kind,
        model_name=payload.model_name,
        base_url=payload.base_url,
        api_key=payload.api_key,
        streaming=payload.streaming,
        max_output_tokens=payload.max_output_tokens,
        timeout_seconds=payload.timeout_seconds,
        max_retries=payload.max_retries,
        retry_backoff_seconds=payload.retry_backoff_seconds,
        activate=payload.activate,
    )
    if payload.activate:
        _invalidate_app_worker(request)
    return _to_read(record)


@router.patch("/{credential_id}", response_model=ProviderCredentialRead)
def update_provider(
    credential_id: str,
    payload: ProviderCredentialUpdate,
    request: Request,
    session: Session = Depends(get_db_session),
) -> ProviderCredentialRead:
    try:
        record = svc.update_credential(
            session,
            credential_id,
            name=payload.name,
            provider_kind=payload.provider_kind,
            model_name=payload.model_name,
            base_url=payload.base_url,
            api_key=payload.api_key,
            streaming=payload.streaming,
            max_output_tokens=payload.max_output_tokens,
            timeout_seconds=payload.timeout_seconds,
            max_retries=payload.max_retries,
            retry_backoff_seconds=payload.retry_backoff_seconds,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="provider credential not found")
    if record.is_active:
        _invalidate_app_worker(request)
    return _to_read(record)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(
    credential_id: str,
    session: Session = Depends(get_db_session),
) -> None:
    try:
        svc.delete_credential(session, credential_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="provider credential not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{credential_id}/activate", response_model=ProviderCredentialRead)
def activate_provider(
    credential_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
) -> ProviderCredentialRead:
    try:
        record = svc.activate_credential(session, credential_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="provider credential not found")
    _invalidate_app_worker(request)
    return _to_read(record)


@router.post("/{credential_id}/test", response_model=ProviderTestResult)
def test_provider(
    credential_id: str,
    session: Session = Depends(get_db_session),
) -> ProviderTestResult:
    try:
        record = svc.get_credential(session, credential_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="provider credential not found")
    outcome = svc.test_credential_connection(record)
    svc.record_test_outcome(session, credential_id, outcome)
    return ProviderTestResult(
        status=outcome.status,
        message=outcome.message,
        elapsed_ms=outcome.elapsed_ms,
        sample_output=outcome.sample_output,
    )


@router.get("/active", response_model=ProviderCredentialRead | None)
def get_active_provider(
    session: Session = Depends(get_db_session),
) -> ProviderCredentialRead | None:
    settings = get_settings()
    record = svc.resolve_active_credential(session, settings)
    if record is None:
        return None
    return _to_read(record)


def _invalidate_app_worker(request: Request) -> None:
    """Clear the cached worker so the next packet rebuilds from DB."""
    state = request.app.state
    if hasattr(state, "translation_worker_revision"):
        state.translation_worker_revision = -1
    state.translation_worker = None
