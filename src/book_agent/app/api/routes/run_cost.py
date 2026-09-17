"""Cost dashboard endpoint.

Aggregates the ``llm.call.completed`` events attributed to a run (tokens,
cost, latency; per chapter as well) and, when the run declares a budget,
reports the remaining headroom so the UI can flag imminent cutoffs without
re-implementing the math client-side. The same aggregation feeds the run
summary and the budget guardrails, so all three agree.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from book_agent.app.api.deps import get_db_session
from book_agent.infra.repositories.run_control import RunControlRepository

router = APIRouter()


@router.get("/{run_id}/cost")
def get_run_cost(
    run_id: str,
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    repository = RunControlRepository(session)
    try:
        repository.get_run(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="run not found")
    usage = repository.usage_from_events(run_id)
    budget = repository.get_budget_for_run(run_id)

    totals = {
        "call_count": usage["call_count"],
        "token_in": usage["token_in"],
        "token_out": usage["token_out"],
        "total_tokens": usage["total_tokens"],
        "cost_usd": usage["cost_usd"],
        "latency_ms": usage["latency_ms"],
        "first_call_at": usage["first_call_at"].isoformat() if usage["first_call_at"] else None,
        "last_call_at": usage["last_call_at"].isoformat() if usage["last_call_at"] else None,
    }
    budget_row = (
        {
            "max_total_cost_usd": budget.max_total_cost_usd,
            "max_total_token_in": budget.max_total_token_in,
            "max_total_token_out": budget.max_total_token_out,
        }
        if budget is not None
        else None
    )
    return {
        "run_id": run_id,
        "totals": totals,
        "budget": _compute_budget_headroom(budget_row, totals),
        "chapters": repository.usage_by_chapter_from_events(run_id),
    }


def _compute_budget_headroom(
    budget_row: dict[str, Any] | None, totals: dict[str, Any]
) -> dict[str, Any] | None:
    if budget_row is None:
        return None
    limits = {
        "max_total_cost_usd": (
            float(budget_row["max_total_cost_usd"])
            if budget_row["max_total_cost_usd"] is not None
            else None
        ),
        "max_total_token_in": budget_row["max_total_token_in"],
        "max_total_token_out": budget_row["max_total_token_out"],
    }
    if all(value is None for value in limits.values()):
        return None

    def _remaining(limit: float | int | None, used: float | int) -> float | int | None:
        if limit is None:
            return None
        return max(limit - used, 0)

    return {
        "limits": limits,
        "used": {
            "cost_usd": totals["cost_usd"],
            "token_in": totals["token_in"],
            "token_out": totals["token_out"],
        },
        "remaining": {
            "cost_usd": _remaining(limits["max_total_cost_usd"], totals["cost_usd"]),
            "token_in": _remaining(limits["max_total_token_in"], totals["token_in"]),
            "token_out": _remaining(limits["max_total_token_out"], totals["token_out"]),
        },
    }
