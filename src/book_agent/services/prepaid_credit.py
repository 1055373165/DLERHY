"""Prepaid credit: organisations top up, their model usage is charged against the balance.

The balance is credits (top-ups, refunds, adjustments in ``credit_entries``)
minus charged usage: the provider cost of the organisation's
``llm.call.completed`` events since it went prepaid (its first credit entry),
times ``billing_price_multiplier``. Usage is read from the same ledger as the
monthly budget and the usage statement, so the three agree; nothing is
debited row by row, so a crash can never charge a call twice or not at all.

A prepaid organisation with no balance left cannot start, resume or retry
runs, and its running runs pause (``billing.credit_exhausted``) until it
tops up. It also cannot translate with a provider that has no prices: those
calls would cost nothing against the balance.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.domain.event_kinds import LLM_CALL_COMPLETED
from book_agent.domain.models.auth import CREDIT_ENTRY_KINDS, DEFAULT_ORG_ID, CreditEntry, Org
from book_agent.domain.models.ops import Event

STOP_REASON = "billing.credit_exhausted"


class OrgCreditExhausted(ValueError):
    pass


class UnpricedProviderForPrepaid(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CreditStatus:
    org_id: str
    org_name: str
    prepaid: bool
    credited_usd: float
    charged_usd: float
    price_multiplier: float
    prepaid_since: datetime | None

    @property
    def balance_usd(self) -> float:
        return round(self.credited_usd - self.charged_usd, 6)

    @property
    def exhausted(self) -> bool:
        return self.prepaid and self.balance_usd <= 0

    def to_json(self) -> dict:
        return {
            "org_id": self.org_id,
            "org_name": self.org_name,
            "prepaid": self.prepaid,
            "credited_usd": round(self.credited_usd, 6),
            "charged_usd": round(self.charged_usd, 6),
            "balance_usd": self.balance_usd,
            "exhausted": self.exhausted,
            "price_multiplier": self.price_multiplier,
            "prepaid_since": self.prepaid_since.isoformat() if self.prepaid_since else None,
        }


def _org(session: Session, org_id: str) -> Org:
    org = session.get(Org, org_id)
    if org is None:
        raise LookupError("organisation not found")
    return org


def credit_status(session: Session, org_id: str, *, price_multiplier: float) -> CreditStatus:
    org = _org(session, org_id)
    credited = float(
        session.scalar(select(func.coalesce(func.sum(CreditEntry.amount_usd), 0)).where(CreditEntry.org_id == org_id)) or 0
    )
    charged = 0.0
    if org.prepaid_since is not None:
        # Rows written before events carried their organisation used the literal "default".
        org_keys = [org_id, "default"] if org_id == DEFAULT_ORG_ID else [org_id]
        cost = float(
            session.scalar(
                select(func.coalesce(func.sum(Event.payload["cost_usd"].as_float()), 0.0)).where(
                    Event.kind == LLM_CALL_COMPLETED,
                    Event.org_id.in_(org_keys),
                    # Compared in SQL against the stored value, not a Python round trip of it.
                    Event.occurred_at >= select(Org.prepaid_since).where(Org.id == org_id).scalar_subquery(),
                )
            )
            or 0.0
        )
        charged = cost * price_multiplier
    return CreditStatus(
        org_id=str(org.id),
        org_name=org.name,
        prepaid=org.prepaid_since is not None,
        credited_usd=credited,
        charged_usd=charged,
        price_multiplier=price_multiplier,
        prepaid_since=org.prepaid_since,
    )


def add_credit(
    session: Session,
    org_id: str,
    amount_usd: float,
    *,
    kind: str = "top_up",
    reference: str | None = None,
    note: str | None = None,
    created_by: str | None = None,
) -> tuple[CreditEntry, bool]:
    """Record a credit movement; returns (entry, created). The same ``reference`` twice returns the first entry."""
    org = _org(session, org_id)
    if kind not in CREDIT_ENTRY_KINDS:
        raise ValueError(f"kind must be one of {', '.join(CREDIT_ENTRY_KINDS)}")
    if kind == "top_up" and amount_usd <= 0:
        raise ValueError("a top-up must be positive")
    if amount_usd == 0:
        raise ValueError("amount_usd must not be 0")
    reference = (reference or "").strip() or None
    if reference is not None:
        existing = session.scalar(
            select(CreditEntry).where(CreditEntry.org_id == org_id, CreditEntry.reference == reference)
        )
        if existing is not None:
            return existing, False
    entry = CreditEntry(
        org_id=org_id,
        amount_usd=Decimal(str(round(amount_usd, 4))),
        kind=kind,
        reference=reference,
        note=note,
        created_by=created_by,
    )
    session.add(entry)
    if org.prepaid_since is None:
        # The database clock, like the events' occurred_at it is compared with.
        org.prepaid_since = func.now()
    session.flush()
    invalidate_cache(org_id)
    return entry, True


def list_credit_entries(session: Session, org_id: str, *, limit: int = 50) -> list[CreditEntry]:
    return list(
        session.scalars(
            select(CreditEntry)
            .where(CreditEntry.org_id == org_id)
            .order_by(CreditEntry.created_at.desc(), CreditEntry.id.desc())
            .limit(limit)
        )
    )


def ensure_credit_available(session: Session, org_id: str | None, *, price_multiplier: float) -> None:
    if org_id is None:
        return
    org = session.get(Org, org_id)
    if org is None or org.prepaid_since is None:
        return
    status = credit_status(session, org_id, price_multiplier=price_multiplier)
    if status.exhausted:
        raise OrgCreditExhausted(
            f"组织「{status.org_name}」的预付余额已用完（余额 ${status.balance_usd:.2f}）；充值后即可继续。"
        )


def ensure_priced_for_prepaid(session: Session, org_id: str | None, *, prices_known: bool) -> None:
    """A prepaid organisation may only translate with a provider whose prices are set."""
    if org_id is None or prices_known:
        return
    org = session.get(Org, org_id)
    if org is not None and org.prepaid_since is not None:
        raise UnpricedProviderForPrepaid(
            "预付额度按用量扣费，当前服务商没有设置单价：请在「服务商」页填入输入、输出单价后再开始。"
        )


# The executor checks every tick; one balance query per organisation per interval is enough.
_CACHE_TTL_SECONDS = 10.0
_cache: dict[str, tuple[float, CreditStatus | None]] = {}
_cache_lock = threading.Lock()


def cached_exhausted_status(
    session: Session, org_id: str, *, price_multiplier: float, clock=time.monotonic
) -> CreditStatus | None:
    """The status when a prepaid organisation's balance is used up, else None; cached briefly per process."""
    now = clock()
    with _cache_lock:
        hit = _cache.get(org_id)
        if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
    org = session.get(Org, org_id)
    status = None
    if org is not None and org.prepaid_since is not None:
        current = credit_status(session, org_id, price_multiplier=price_multiplier)
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
