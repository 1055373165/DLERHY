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


class RepairRequest(BaseModel):
    # The most the repair may spend; default twice what the pilot cost.
    budget_usd: float | None = None


@documents_router.post("/{document_id}/pilot-repair", status_code=status.HTTP_202_ACCEPTED)
def start_pilot_repair(
    document_id: str, request: Request, body: RepairRequest | None = None, session: Session = Depends(get_db_session)
) -> dict[str, Any]:
    """Run the repair loop again now, in the background (after a code fix, or for a pilot translated
    another way); GET the same path to follow it."""
    from book_agent.app.runtime.document_run_executor import ensure_document_run_executor
    from book_agent.services.pilot_repair import latest_repair

    _document_or_404(session, document_id)
    running = latest_repair(session, document_id)
    if running is not None and running.status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="这本书的试译正在自动修复，等它完成后再试。")
    executor = ensure_document_run_executor(request.app)
    start = getattr(executor, "start_pilot_repair", None)
    if start is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="the run executor is not running")
    repair_id = start(document_id, budget_usd=body.budget_usd if body else None)
    return {"repair_id": repair_id, "status": "running"}


@documents_router.get("/{document_id}/book-quality")
def get_book_quality(document_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """The latest layered QA of the whole book (after the whole-book run), in the pilot report's shape."""
    from book_agent.services.book_quality import latest_book_report
    from book_agent.services.pilot_quality import report_payload

    _document_or_404(session, document_id)
    report = latest_book_report(session, document_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no whole-book quality report yet")
    return report_payload(session, report)


@documents_router.post("/{document_id}/book-quality")
def run_book_quality(document_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """Run the layered QA on every translated chapter now (rules only, no model call)."""
    from book_agent.services.book_quality import BOOK_QA_TRIGGER, SCOPE_BOOK, book_chapter_ids
    from book_agent.services.pilot import latest_book_run
    from book_agent.services.pilot_quality import PilotQualityService, report_payload

    _document_or_404(session, document_id)
    chapter_ids = book_chapter_ids(session, document_id)
    if not chapter_ids:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="这本书没有可以质检的章节。")
    report = PilotQualityService(session).run(
        document_id, run=latest_book_run(session, document_id), trigger=BOOK_QA_TRIGGER, chapter_ids=chapter_ids, scope=SCOPE_BOOK
    )
    return report_payload(session, report)


@documents_router.get("/{document_id}/book-repair")
def get_book_repair(document_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    """The latest repair of the whole book: summary, attempts, defect tickets and open questions."""
    from book_agent.services.book_quality import latest_book_repair
    from book_agent.services.pilot_repair import repair_payload

    _document_or_404(session, document_id)
    repair = latest_book_repair(session, document_id)
    if repair is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no whole-book repair yet")
    return repair_payload(session, repair)


@documents_router.post("/{document_id}/book-repair", status_code=status.HTTP_202_ACCEPTED)
def start_book_repair(
    document_id: str, request: Request, body: RepairRequest | None = None, session: Session = Depends(get_db_session)
) -> dict[str, Any]:
    """Run the whole book's repair loop now, in the background; GET the same path to follow it."""
    from book_agent.app.runtime.document_run_executor import ensure_document_run_executor
    from book_agent.services.book_quality import latest_book_repair

    _document_or_404(session, document_id)
    running = latest_book_repair(session, document_id)
    if running is not None and running.status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="这本书正在自动修复，等它完成后再试。")
    executor = ensure_document_run_executor(request.app)
    start = getattr(executor, "start_pilot_repair", None)
    if start is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="the run executor is not running")
    repair_id = start(document_id, budget_usd=body.budget_usd if body else None, scope="book")
    return {"repair_id": repair_id, "status": "running"}


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
