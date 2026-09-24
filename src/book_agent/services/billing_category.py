"""Who pays for a run's model calls.

Every ``llm.call.completed`` event made on behalf of a run carries the run's id
(``core/run_context.py``). A run is either ``customer`` (the default: charged to
the organisation's prepaid credit and counted against its monthly budget) or
``pilot``: the free two-chapter trial translation (services/pilot.py), which we
pay for. Pilot calls stay in the ledger, so what pilots cost us can be read
(``usage_statement.pilot_cost_statement``); they are only left out of what the
customer is charged.

The pilot run ids are read first and compared as text: ``events.run_id`` is a
text column while ``document_runs.id`` is a UUID, which the two databases store
differently (PostgreSQL ``uuid``, SQLite 32 hex characters).
"""

from __future__ import annotations

from sqlalchemy import or_, select, true
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun, Event

CUSTOMER = "customer"
PILOT = "pilot"
BILLING_CATEGORIES: tuple[str, ...] = (CUSTOMER, PILOT)


def pilot_run_ids(session: Session, org_id: str | None = None) -> list[str]:
    """Ids of pilot runs, of one organisation's books when ``org_id`` is given."""
    statement = select(DocumentRun.id).where(DocumentRun.billing_category == PILOT)
    if org_id is not None:
        statement = statement.join(Document, Document.id == DocumentRun.document_id).where(Document.org_id == org_id)
    return [str(run_id) for run_id in session.scalars(statement).all()]


def charged_to_customer(pilot_ids: list[str]) -> ColumnElement[bool]:
    """Events the organisation pays for: calls outside any run, or made by a customer run."""
    if not pilot_ids:
        return true()
    return or_(Event.run_id.is_(None), Event.run_id.not_in(pilot_ids))


def paid_by_us(pilot_ids: list[str]) -> ColumnElement[bool]:
    """Events of pilot runs: our cost, never the customer's."""
    return Event.run_id.in_(pilot_ids)
