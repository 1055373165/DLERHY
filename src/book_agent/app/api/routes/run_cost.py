"""Cost dashboard endpoint backed by ``cost_rollup_*`` materialized views.

Reads aggregated spend (token_in / token_out / cost_usd) grouped by run and
by chapter. When the run declares a budget on ``DocumentRun.budget_json``,
the response also reports remaining headroom so the UI can flag imminent
cutoffs without re-implementing the math client-side.

The endpoint refreshes the materialized views on every call — cheap enough
at our event cardinality (a few thousand llm.call.completed rows per run),
and guarantees freshness without needing a background scheduler.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.engine import Engine

from book_agent.app.api.deps import get_session_factory


router = APIRouter()


@router.get("/{run_id}/cost")
def get_run_cost(
    request: Request,
    run_id: str,
    refresh: bool = Query(default=True, description="Refresh rollup before reading."),
) -> dict[str, Any]:
    session_factory = get_session_factory(request)
    engine: Engine = session_factory.kw["bind"]
    if engine.dialect.name != "postgresql":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Cost rollup requires PostgreSQL.",
        )

    with engine.begin() as conn:
        if refresh:
            conn.execute(text("SELECT refresh_cost_rollup()"))

        run_row = conn.execute(
            text(
                """
                SELECT run_id, call_count, token_in, token_out, total_tokens,
                       cost_usd, first_call_at, last_call_at
                FROM cost_rollup_by_run
                WHERE run_id = :run_id
                """
            ),
            {"run_id": run_id},
        ).mappings().first()

        chapter_rows = conn.execute(
            text(
                """
                SELECT chapter_id, call_count, token_in, token_out, total_tokens, cost_usd
                FROM cost_rollup_by_chapter
                WHERE run_id = :run_id
                ORDER BY cost_usd DESC, chapter_id ASC
                """
            ),
            {"run_id": run_id},
        ).mappings().all()

        budget_row = conn.execute(
            text(
                """
                SELECT max_total_cost_usd, max_total_token_in, max_total_token_out
                FROM run_budgets
                WHERE run_id::text = :run_id
                """
            ),
            {"run_id": run_id},
        ).mappings().first()

    totals = {
        "call_count": int(run_row["call_count"]) if run_row else 0,
        "token_in": int(run_row["token_in"]) if run_row else 0,
        "token_out": int(run_row["token_out"]) if run_row else 0,
        "total_tokens": int(run_row["total_tokens"]) if run_row else 0,
        "cost_usd": float(run_row["cost_usd"]) if run_row else 0.0,
        "first_call_at": run_row["first_call_at"].isoformat() if run_row and run_row["first_call_at"] else None,
        "last_call_at": run_row["last_call_at"].isoformat() if run_row and run_row["last_call_at"] else None,
    }

    budget = _compute_budget_headroom(budget_row, totals)

    return {
        "run_id": run_id,
        "totals": totals,
        "budget": budget,
        "chapters": [
            {
                "chapter_id": row["chapter_id"],
                "call_count": int(row["call_count"]),
                "token_in": int(row["token_in"]),
                "token_out": int(row["token_out"]),
                "total_tokens": int(row["total_tokens"]),
                "cost_usd": float(row["cost_usd"]),
            }
            for row in chapter_rows
        ],
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
