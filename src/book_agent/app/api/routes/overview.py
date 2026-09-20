"""A reader's view of a book, and what waits on them across books."""

from __future__ import annotations

from typing import Any

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from book_agent.app.api.access import current_principal
from book_agent.app.api.deps import get_db_session
from book_agent.app.capabilities import pdf_renderer_available
from book_agent.application.book_overview import OverviewContext, build_overview, inbox_items, inbox_updates
from book_agent.core.config import get_settings

documents_router = APIRouter()
inbox_router = APIRouter()
overviews_router = APIRouter()

MAX_OVERVIEWS = 50


@documents_router.get("/{document_id}/overview")
def get_overview(document_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Where the book stands: readable, its translation, background activity, what needs you."""
    try:
        return build_overview(session, document_id, OverviewContext(settings=get_settings(), pdf_renderer=pdf_renderer_available()))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@inbox_router.get("")
def get_inbox(
    request: Request,
    since: datetime | None = Query(default=None, description="Also list full translations that ended since then (ISO time)."),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Everything waiting on a person across the caller's books, and translations that just ended."""
    principal = current_principal(request)
    org_id = principal.org_id if principal.auth_enabled else None
    items = inbox_items(session, org_id=org_id)
    cutoff = since or datetime.now(timezone.utc) - timedelta(minutes=10)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    return {"count": len(items), "items": items, "updates": inbox_updates(session, org_id=org_id, since=cutoff)}


@overviews_router.get("")
def get_overviews(
    request: Request,
    ids: str = Query(description="Comma-separated document ids, at most 50."),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Overviews of several books at once (without cost), for lists such as the home page's cards."""
    from book_agent.domain.models import Document

    principal = current_principal(request)
    wanted = [value for value in dict.fromkeys(part.strip() for part in ids.split(",")) if value][:MAX_OVERVIEWS]
    context = OverviewContext(settings=get_settings(), pdf_renderer=pdf_renderer_available())
    overviews = []
    for document_id in wanted:
        document = session.get(Document, document_id)
        # Other organisations' books are simply not there.
        if document is None or (principal.auth_enabled and document.org_id != principal.org_id):
            continue
        overviews.append(build_overview(session, document_id, context, include_cost=False))
    return {"overviews": overviews}
