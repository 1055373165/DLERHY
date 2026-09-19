"""A month's model usage of one organisation, per book: the basis for charging it.

Reads the same ledger as the monthly budget (``llm.call.completed`` events
carrying the organisation), so the statement total equals the budget's
``spent_usd`` for that month. Each call is attributed to a book through its
run, else the ``document_id`` it carries; calls tied to neither (a provider
connection test) are listed as one line without a book.
"""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from book_agent.domain.event_kinds import LLM_CALL_COMPLETED
from book_agent.domain.models import Document
from book_agent.domain.models.auth import DEFAULT_ORG_ID, Org
from book_agent.domain.models.ops import DocumentRun, Event


@dataclass(slots=True)
class UsageLine:
    document_id: str | None
    title: str | None
    call_count: int = 0
    token_in: int = 0
    token_out: int = 0
    cost_usd: float = 0.0


@dataclass(slots=True)
class UsageStatement:
    org_id: str
    org_name: str
    month: str
    period_start: str
    period_end: str
    call_count: int
    token_in: int
    token_out: int
    cost_usd: float
    # Calls whose provider had no price: their tokens are counted, their cost is not.
    unpriced_call_count: int
    documents: list[UsageLine] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)

    def to_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["month", "document_id", "title", "call_count", "token_in", "token_out", "cost_usd"])
        for line in self.documents:
            writer.writerow(
                [self.month, line.document_id or "", line.title or "", line.call_count, line.token_in, line.token_out, f"{line.cost_usd:.6f}"]
            )
        writer.writerow([self.month, "", "TOTAL", self.call_count, self.token_in, self.token_out, f"{self.cost_usd:.6f}"])
        return buffer.getvalue()


def month_bounds(month: str | None, *, now: datetime | None = None) -> tuple[str, datetime, datetime]:
    """("YYYY-MM", start, end) in UTC; ``month`` None means the current month."""
    if month is None:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        year, number = current.year, current.month
    else:
        try:
            parsed = datetime.strptime(month, "%Y-%m")
        except ValueError as exc:
            raise ValueError("month must look like 2026-09") from exc
        year, number = parsed.year, parsed.month
    start = datetime(year, number, 1, tzinfo=timezone.utc)
    end = datetime(year + (number == 12), number % 12 + 1, 1, tzinfo=timezone.utc)
    return f"{year:04d}-{number:02d}", start, end


def usage_statement(session: Session, org_id: str, *, month: str | None = None) -> UsageStatement:
    org = session.get(Org, org_id)
    if org is None:
        raise LookupError("organisation not found")
    label, start, end = month_bounds(month)
    # Rows written before events carried their organisation used the literal "default".
    org_keys = [org_id, "default"] if org_id == DEFAULT_ORG_ID else [org_id]
    cost = Event.payload["cost_usd"].as_float()
    rows = session.execute(
        select(
            Event.run_id,
            Event.payload["document_id"].as_string(),
            func.count(),
            func.coalesce(func.sum(Event.payload["token_in"].as_integer()), 0),
            func.coalesce(func.sum(Event.payload["token_out"].as_integer()), 0),
            func.coalesce(func.sum(cost), 0.0),
            func.sum(case((cost.is_(None), 1), else_=0)),
        )
        .where(
            Event.kind == LLM_CALL_COMPLETED,
            Event.org_id.in_(org_keys),
            Event.occurred_at >= start,
            Event.occurred_at < end,
        )
        .group_by(Event.run_id, Event.payload["document_id"].as_string())
    ).all()

    run_ids = {run_id for run_id, *_ in rows if run_id is not None}
    document_of_run = (
        dict(session.execute(select(DocumentRun.id, DocumentRun.document_id).where(DocumentRun.id.in_(run_ids))).all())
        if run_ids
        else {}
    )
    lines: dict[str | None, UsageLine] = {}
    unpriced = 0
    for run_id, payload_document_id, calls, token_in, token_out, cost_usd, unpriced_calls in rows:
        document_id = document_of_run.get(run_id) or payload_document_id
        document_id = str(document_id) if document_id else None
        line = lines.setdefault(document_id, UsageLine(document_id=document_id, title=None))
        line.call_count += int(calls)
        line.token_in += int(token_in or 0)
        line.token_out += int(token_out or 0)
        line.cost_usd += float(cost_usd or 0.0)
        unpriced += int(unpriced_calls or 0)

    known = [key for key in lines if key is not None]
    if known:
        for document in session.scalars(select(Document).where(Document.id.in_(known))):
            lines[str(document.id)].title = document.title_tgt or document.title or document.title_src
    ordered = sorted(lines.values(), key=lambda line: (-line.cost_usd, -(line.token_in + line.token_out)))
    for line in ordered:
        line.cost_usd = round(line.cost_usd, 6)
    return UsageStatement(
        org_id=str(org.id),
        org_name=org.name,
        month=label,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        call_count=sum(line.call_count for line in ordered),
        token_in=sum(line.token_in for line in ordered),
        token_out=sum(line.token_out for line in ordered),
        cost_usd=round(sum(line.cost_usd for line in ordered), 6),
        unpriced_call_count=unpriced,
        documents=ordered,
    )
