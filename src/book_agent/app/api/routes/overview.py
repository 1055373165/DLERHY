"""A reader's view of a book, and what waits on them across books."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from book_agent.app.api.access import current_principal
from book_agent.app.api.deps import get_db_session
from book_agent.app.capabilities import pdf_renderer_available
from book_agent.application.book_overview import OverviewContext, build_overview, inbox_items
from book_agent.core.config import get_settings

documents_router = APIRouter()
inbox_router = APIRouter()


@documents_router.get("/{document_id}/overview")
def get_overview(document_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Where the book stands: readable, its translation, background activity, what needs you."""
    try:
        return build_overview(session, document_id, OverviewContext(settings=get_settings(), pdf_renderer=pdf_renderer_available()))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@inbox_router.get("")
def get_inbox(request: Request, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Everything waiting on a person across the caller's books."""
    principal = current_principal(request)
    items = inbox_items(session, org_id=principal.org_id if principal.auth_enabled else None)
    return {"count": len(items), "items": items}
