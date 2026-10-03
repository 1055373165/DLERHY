"""The whole book's quality gate: the pilot's layered QA and repair loop, on every chapter.

docs/agent-upgrade/08-pilot-quality-gate.md §6–§7, applied once more after the paid whole-book
run. The run's last two stages:

* ``book_qa``: the layered checks (L1–L7, ``PilotQualityService``) on every translated chapter; its
  report (scope "book") is what the book page's 全书质检 panel shows;
* ``book_repair``: the repair loop (``PilotRepairService`` with scope "book") on those chapters,
  with the same classification, closed toolbox, verify-or-revert rule, three rounds and escalation
  to 需要你处理; it re-exports the whole book's reading copy when it kept anything.

Budget. The pilot's repair may spend twice the pilot's cost. Twice the whole book's cost would be
far more than a repair should take: the book's repair gets twice a small share of the book's
estimated translation cost (``BOOK_REPAIR_SHARE``), capped at ``BOOK_REPAIR_CAP_SHARE`` of it. The
repair's model calls are the reader's (the run's billing category), like the rest of the run.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.domain.models import Chapter
from book_agent.domain.models.review import PilotQualityReport, PilotRepair
from book_agent.domain.models.translation import TranslationPacket
from book_agent.services.pilot_repair_tools import RepairBudget

SCOPE_BOOK = "book"
BOOK_QA_TRIGGER = "book_qa"
# Twice this share of the book's estimated cost, and never more than the cap's share of it.
BOOK_REPAIR_MULTIPLIER = 2.0
BOOK_REPAIR_SHARE = 0.1
BOOK_REPAIR_CAP_SHARE = 0.15


def book_chapter_ids(session: Session, document_id: str) -> list[str]:
    """Every chapter with something to translate, in reading order."""
    with_packets = set(
        session.scalars(
            select(TranslationPacket.chapter_id).join(Chapter, Chapter.id == TranslationPacket.chapter_id).where(Chapter.document_id == document_id)
        ).all()
    )
    return [
        str(chapter_id)
        for chapter_id in session.scalars(select(Chapter.id).where(Chapter.document_id == document_id).order_by(Chapter.ordinal)).all()
        if chapter_id in with_packets
    ]


def book_repair_budget(session: Session, chapter_ids: Sequence[str], *, override_usd: float | None = None) -> RepairBudget:
    """2 × ``BOOK_REPAIR_SHARE`` of the book's estimated cost, capped at ``BOOK_REPAIR_CAP_SHARE`` of it.

    Without prices the same shares of the book's packets bound how many packets may be retranslated.
    """
    from book_agent.core.config import get_settings
    from book_agent.services.cost_estimate import estimate_document_cost

    packets = int(
        session.scalar(select(func.count(TranslationPacket.id)).where(TranslationPacket.chapter_id.in_(list(chapter_ids) or ["-"]))) or 0
    )
    share = min(BOOK_REPAIR_MULTIPLIER * BOOK_REPAIR_SHARE, BOOK_REPAIR_CAP_SHARE)
    estimate_usd = 0.0
    document_id = session.scalar(select(Chapter.document_id).where(Chapter.id == chapter_ids[0])) if chapter_ids else None
    if document_id is not None:
        try:
            estimate = estimate_document_cost(session, str(document_id), get_settings())
            estimate_usd = float(estimate.cost_usd or 0.0)
        except Exception:  # an unpriced provider: the packet bound still holds
            estimate_usd = 0.0
    budget_usd = override_usd if override_usd is not None else (round(share * estimate_usd, 6) if estimate_usd > 0 else None)
    return RepairBudget(
        budget_usd=budget_usd,
        max_packets=max(1, int(share * max(packets, 1))),
        cost_per_packet_usd=(estimate_usd / packets) if packets and estimate_usd > 0 else 0.0,
    )


def latest_book_report(session: Session, document_id: str) -> PilotQualityReport | None:
    from book_agent.services.pilot_quality import latest_report

    return latest_report(session, document_id, scope=SCOPE_BOOK)


def latest_book_repair(session: Session, document_id: str) -> PilotRepair | None:
    from book_agent.services.pilot_repair import latest_repair

    return latest_repair(session, document_id, scope=SCOPE_BOOK)


def overview(session: Session, document_id: str) -> dict[str, Any] | None:
    """What the book page shows of the whole book's QA and repair; None before the first book QA."""
    from book_agent.services.pilot_quality import overview_summary as quality_summary
    from book_agent.services.pilot_repair import overview_summary as repair_summary

    report = latest_book_report(session, document_id)
    repair = latest_book_repair(session, document_id)
    if report is None and repair is None:
        return None
    return {
        "quality": quality_summary(report),
        "repair": repair_summary(session, repair),
        "chapter_count": len(report.chapter_ids_json or []) if report is not None else 0,
    }
