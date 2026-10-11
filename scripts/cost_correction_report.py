"""What the recorded call costs would be under the provider's current peak schedule. Read-only.

Until 2026-10-11 a call was priced with one off-peak window ("16:30-00:30" UTC, every day), while
DeepSeek prices the peak at Monday to Friday 01:00-04:00 and 06:00-10:00 UTC: calls between 10:00
and 16:30 UTC and at weekends were recorded at twice their price (docs/specs/cost-governance).

The credential's history is not stored, so whether the old multiplier applied is read from the
data, day by day: on a day whose calls in the old window's peak hours cost about twice the
off-peak ones per token, it applied; otherwise (no window configured that day) it did not.

    uv run --extra pdf python scripts/cost_correction_report.py [--since 2026-09-25] [--schedule "mon-fri 01:00-04:00,06:00-10:00"]

Prints, per organisation and per book: the recorded cost, the corrected cost, and the difference.
It changes nothing: whether to correct statements or return prepaid credit is the owner's call.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import text

from book_agent.app.main import _ensure_database_state, create_app
from book_agent.core.config import get_settings
from book_agent.workers.providers.pricing import is_peak

OLD_WINDOW = "16:30-00:30"
OLD_MULTIPLIER = 2.0
# Cost per token-equivalent (input weighted as a mostly cached prompt) above which a day's
# old-peak calls were charged the multiplier: off-peak days sit near 0.9, multiplied days near 1.9.
APPLIED_RATIO = 1.3


def _ratio(cost: float, token_in: int, token_out: int) -> float | None:
    weight = token_in * 0.04 + token_out * 0.6
    return cost * 1e6 / weight if weight > 0 and token_out > 0 else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--since", default="2026-09-25", help="first day (UTC) to report; the multiplier was added on 2026-09-25")
    parser.add_argument("--schedule", default="mon-fri 01:00-04:00,06:00-10:00", help="the provider's current peak schedule (UTC)")
    args = parser.parse_args()

    app = create_app()
    _ensure_database_state(app, settings=get_settings())
    with app.state.session_factory() as session:
        rows = session.execute(
            text(
                """
                select e.occurred_at, e.org_id, coalesce(d.title, '(no book)') as book,
                       (e.payload->>'cost_usd')::float as cost,
                       coalesce((e.payload->>'token_in')::int, 0) as token_in,
                       coalesce((e.payload->>'token_out')::int, 0) as token_out
                from events e
                left join chapters c on c.id::text = e.chapter_id::text
                left join document_runs r on r.id::text = e.run_id::text
                left join documents d on d.id = coalesce(c.document_id, r.document_id)
                where e.kind = 'llm.call.completed' and e.payload->>'cost_usd' is not null
                  and e.occurred_at >= :since
                """
            ),
            {"since": datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)},
        ).all()
        session.rollback()

    old_peak_ratios: dict[date, list[float]] = defaultdict(list)
    for row in rows:
        at = row.occurred_at.astimezone(timezone.utc)
        ratio = _ratio(row.cost, row.token_in, row.token_out)
        if ratio is not None and is_peak(at, off_peak_window_utc=OLD_WINDOW):
            old_peak_ratios[at.date()].append(ratio)
    applied = {day: statistics.median(ratios) >= APPLIED_RATIO for day, ratios in old_peak_ratios.items() if len(ratios) >= 20}

    totals: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    unknown_days: set[date] = set()
    for row in rows:
        at = row.occurred_at.astimezone(timezone.utc)
        old_peak = is_peak(at, off_peak_window_utc=OLD_WINDOW)
        if old_peak and at.date() not in applied:
            unknown_days.add(at.date())
        recorded_multiplier = OLD_MULTIPLIER if old_peak and applied.get(at.date(), True) else 1.0
        base = row.cost / recorded_multiplier
        corrected = base * (OLD_MULTIPLIER if is_peak(at, peak_schedule_utc=args.schedule) else 1.0)
        for key in ((str(row.org_id or "-"), "*"), (str(row.org_id or "-"), row.book)):
            totals[key][0] += row.cost
            totals[key][1] += corrected

    print(f"calls {len(rows)} since {args.since}; schedule now {args.schedule!r}; old window {OLD_WINDOW!r} x{OLD_MULTIPLIER}")
    print("days the old multiplier applied: " + ", ".join(f"{day}:{'yes' if flag else 'no'}" for day, flag in sorted(applied.items())))
    if unknown_days:
        print("days with too few old-peak calls to tell (assumed applied): " + ", ".join(str(day) for day in sorted(unknown_days)))
    print(f"\n{'organisation / book':60} {'recorded':>10} {'corrected':>10} {'difference':>11}")
    for (org, book), (recorded, corrected) in sorted(totals.items(), key=lambda item: (item[0][0], item[0][1] != "*", -item[1][0])):
        label = f"{org}  (all books)" if book == "*" else f"  {book[:56]}"
        print(f"{label:60} {recorded:10.3f} {corrected:10.3f} {corrected - recorded:+11.3f}")


if __name__ == "__main__":
    main()
