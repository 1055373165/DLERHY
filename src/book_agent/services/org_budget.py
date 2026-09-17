"""Monthly model-spend budgets per organisation.

Spend is the cost of ``llm.call.completed`` events attributed to the
organisation's runs since the start of the calendar month (UTC), the same
ledger run budgets and the cost endpoint read. Model calls made outside a
run (synchronous API actions, provider smoke tests) are not attributed to an
organisation and do not count.

With a budget set and exhausted, runs of the organisation cannot be started,
resumed or retried, and running ones are paused by the executor
(``budget.org_monthly_exhausted``) until the budget is raised or the month
turns over.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from book_agent.domain.event_kinds import LLM_CALL_COMPLETED
from book_agent.domain.models import Document
from book_agent.domain.models.auth import Org
from book_agent.domain.models.ops import DocumentRun, Event

STOP_REASON = "budget.org_monthly_exhausted"


class OrgBudgetExhausted(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OrgBudgetStatus:
    org_id: str
    org_name: str
    monthly_budget_usd: float | None
    spent_usd: float
    period_start: datetime

    @property
    def remaining_usd(self) -> float | None:
        if self.monthly_budget_usd is None:
            return None
        return round(max(0.0, self.monthly_budget_usd - self.spent_usd), 6)

    @property
    def exhausted(self) -> bool:
        return self.monthly_budget_usd is not None and self.spent_usd >= self.monthly_budget_usd

    def to_json(self) -> dict:
        return {
            "org_id": self.org_id,
            "org_name": self.org_name,
            "monthly_budget_usd": self.monthly_budget_usd,
            "spent_usd": round(self.spent_usd, 6),
            "remaining_usd": self.remaining_usd,
            "exhausted": self.exhausted,
            "period_start": self.period_start.isoformat(),
        }


def month_start(now: datetime | None = None) -> datetime:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def budget_status(session: Session, org_id: str, *, now: datetime | None = None) -> OrgBudgetStatus:
    org = session.get(Org, org_id)
    if org is None:
        raise LookupError("organisation not found")
    start = month_start(now)
    run_ids = [
        str(run_id)
        for run_id in session.scalars(
            select(DocumentRun.id)
            .join(Document, Document.id == DocumentRun.document_id)
            .where(
                Document.org_id == org_id,
                # Runs that finished before the month cannot have spent in it.
                or_(DocumentRun.finished_at.is_(None), DocumentRun.finished_at >= start),
            )
        )
    ]
    spent = 0.0
    if run_ids:
        spent = float(
            session.scalar(
                select(func.coalesce(func.sum(Event.payload["cost_usd"].as_float()), 0.0)).where(
                    Event.kind == LLM_CALL_COMPLETED,
                    Event.run_id.in_(run_ids),
                    Event.occurred_at >= start,
                )
            )
            or 0.0
        )
    budget = org.monthly_budget_usd
    return OrgBudgetStatus(
        org_id=str(org.id),
        org_name=org.name,
        monthly_budget_usd=float(budget) if budget is not None else None,
        spent_usd=spent,
        period_start=start,
    )


def org_id_for_run(session: Session, run_id: str) -> str | None:
    org_id = session.scalar(
        select(Document.org_id).join(DocumentRun, DocumentRun.document_id == Document.id).where(DocumentRun.id == run_id)
    )
    return str(org_id) if org_id is not None else None


def ensure_budget_available(session: Session, org_id: str | None) -> None:
    if org_id is None:
        return
    org = session.get(Org, org_id)
    if org is None or org.monthly_budget_usd is None:
        return
    status = budget_status(session, org_id)
    if status.exhausted:
        raise OrgBudgetExhausted(
            f"organisation {status.org_name} has used its monthly model budget "
            f"(${status.spent_usd:.2f} of ${status.monthly_budget_usd:.2f}); raise the budget or wait for next month"
        )


def set_monthly_budget(session: Session, org_id: str, monthly_budget_usd: float | None) -> Org:
    org = session.get(Org, org_id)
    if org is None:
        raise LookupError("organisation not found")
    if monthly_budget_usd is not None and monthly_budget_usd < 0:
        raise ValueError("monthly_budget_usd must be >= 0")
    org.monthly_budget_usd = None if monthly_budget_usd is None else Decimal(str(round(monthly_budget_usd, 2)))
    session.flush()
    invalidate_cache(org_id)
    return org


# The executor checks every tick; one status query per organisation per interval is enough.
_CACHE_TTL_SECONDS = 10.0
_cache: dict[str, tuple[float, OrgBudgetStatus | None]] = {}
_cache_lock = threading.Lock()


def cached_exhausted_status(session: Session, org_id: str, *, clock=time.monotonic) -> OrgBudgetStatus | None:
    """The status when the organisation's budget is exhausted, else None; cached briefly per process."""
    now = clock()
    with _cache_lock:
        hit = _cache.get(org_id)
        if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
    org = session.get(Org, org_id)
    status = None
    if org is not None and org.monthly_budget_usd is not None:
        current = budget_status(session, org_id)
        status = current if current.exhausted else None
    with _cache_lock:
        _cache[org_id] = (now, status)
    return status


def invalidate_cache(org_id: str | None = None) -> None:
    with _cache_lock:
        if org_id is None:
            _cache.clear()
        else:
            _cache.pop(org_id, None)
