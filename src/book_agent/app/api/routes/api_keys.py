"""API key management for the caller's organisation (admin role)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import Field
from sqlalchemy.orm import Session

from book_agent.app.api.access import current_principal
from book_agent.app.api.deps import get_db_session
from book_agent.schemas.common import BaseSchema
from book_agent.services.api_keys import ApiKeyService

router = APIRouter()


class ApiKeyResponse(BaseSchema):
    id: str
    name: str
    role: str
    key_prefix: str
    created_at: str | None = None
    last_used_at: str | None = None
    revoked_at: str | None = None


class ApiKeyCreateRequest(BaseSchema):
    name: str = Field(min_length=1, max_length=200)
    role: str = Field(pattern="^(viewer|editor|admin)$")


class ApiKeyCreatedResponse(ApiKeyResponse):
    key: str = Field(description="The API key. Shown only in this response; store it now.")


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _response(key) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=key.id,
        name=key.name,
        role=key.role,
        key_prefix=key.key_prefix,
        created_at=_iso(key.created_at),
        last_used_at=_iso(key.last_used_at),
        revoked_at=_iso(key.revoked_at),
    )


@router.get("", response_model=list[ApiKeyResponse])
def list_api_keys(request: Request, session: Session = Depends(get_db_session)) -> list[ApiKeyResponse]:
    return [_response(key) for key in ApiKeyService(session).list(current_principal(request).org_id)]


@router.post("", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_api_key(
    payload: ApiKeyCreateRequest, request: Request, session: Session = Depends(get_db_session)
) -> ApiKeyCreatedResponse:
    created = ApiKeyService(session).create(org_id=current_principal(request).org_id, name=payload.name, role=payload.role)
    return ApiKeyCreatedResponse(**_response(created.key).model_dump(), key=created.plaintext)


@router.post("/{key_id}/revoke", response_model=ApiKeyResponse)
def revoke_api_key(key_id: str, request: Request, session: Session = Depends(get_db_session)) -> ApiKeyResponse:
    try:
        key = ApiKeyService(session).revoke(org_id=current_principal(request).org_id, key_id=key_id)
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="api key not found") from exc
    return _response(key)
