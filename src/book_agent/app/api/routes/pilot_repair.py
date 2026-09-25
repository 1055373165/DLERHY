"""The pilot repair loop over the API: what it did for a book, and the defect tickets it filed.

GET /documents/{id}/pilot-repair is the latest repair of the book's pilot: its summary, every
attempt (classification, tool, parameters, before/after metrics, kept or reverted, cost), the defect
tickets and the questions left for the reader. The page images attached to a question are served
from the book's export folder. GET /pilot-defects lists the systemic defects the repairs filed, for
the dev side (admin only, never shown to readers); PATCH marks one fixed or won't fix.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.app.api.access import current_principal
from book_agent.app.api.deps import get_db_session
from book_agent.domain.models import Document
from book_agent.domain.models.review import PilotDefectTicket

documents_router = APIRouter()
defects_router = APIRouter()


def _document_or_404(session: Session, document_id: str) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return document


@documents_router.get("/{document_id}/pilot-repair")
def get_pilot_repair(document_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """The latest repair of the pilot: summary, attempts, defect tickets and open questions."""
    from book_agent.services.pilot_repair import latest_repair, repair_payload

    _document_or_404(session, document_id)
    repair = latest_repair(session, document_id)
    if repair is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no pilot repair yet")
    return repair_payload(session, repair)


@documents_router.get("/{document_id}/pilot-repair/evidence/{name}")
def get_pilot_repair_evidence(document_id: str, name: str, request: Request, session: Session = Depends(get_db_session)) -> FileResponse:
    """A page image attached to a question (the problem outlined on the original page)."""
    from book_agent.services.pilot_repair import EVIDENCE_DIR

    _document_or_404(session, document_id)
    if "/" in name or "\\" in name or not name.endswith(".png"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    root = Path(getattr(request.app.state, "export_root", None) or "artifacts/exports") / document_id / EVIDENCE_DIR
    path = root / name
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return FileResponse(path, media_type="image/png")


@defects_router.get("")
def list_pilot_defects(
    request: Request,
    status_filter: str | None = Query(default="open", alias="status", description="open | fixed | wontfix | all"),
    check_id: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Systemic defects the pilot repairs filed, newest first, with their evidence."""
    from book_agent.services.pilot_repair import ticket_payload

    principal = current_principal(request)
    statement = select(PilotDefectTicket).order_by(PilotDefectTicket.created_at.desc())
    if status_filter and status_filter != "all":
        statement = statement.where(PilotDefectTicket.status == status_filter)
    if check_id:
        statement = statement.where(PilotDefectTicket.check_id == check_id)
    if principal.auth_enabled:
        statement = statement.join(Document, Document.id == PilotDefectTicket.document_id).where(Document.org_id == principal.org_id)
    tickets = session.scalars(statement.limit(200)).all()
    return {"count": len(tickets), "tickets": [ticket_payload(ticket) for ticket in tickets]}


class DefectUpdate(BaseModel):
    status: str
    note: str | None = None


@defects_router.patch("/{ticket_id}")
def update_pilot_defect(ticket_id: str, update: DefectUpdate, request: Request, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """The dev side closes a ticket: fixed (with the commit in the note) or won't fix."""
    from book_agent.services.pilot_repair import ticket_payload

    if update.status not in {"open", "fixed", "wontfix"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="status must be open, fixed or wontfix")
    ticket = session.get(PilotDefectTicket, ticket_id)
    principal = current_principal(request)
    document = session.get(Document, ticket.document_id) if ticket is not None else None
    if ticket is None or (principal.auth_enabled and (document is None or document.org_id != principal.org_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket not found")
    ticket.status = update.status
    if update.note:
        ticket.workaround_json = {**(ticket.workaround_json or {}), "resolution_note": update.note}
    session.commit()
    return ticket_payload(ticket)
