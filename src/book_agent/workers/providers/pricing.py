"""Peak and off-peak token prices.

Some providers charge a multiple of their listed price at peak hours. A credential stores its
off-peak prices, the peak multiplier, and when the peak is; a call is priced by the moment it
finishes.

The peak is a schedule of UTC windows by weekday (``peak_schedule_utc``): DeepSeek's pricing page
(checked 2026-10-11) sets it at "mon-fri 01:00-04:00,06:00-10:00", every other hour, weekends
included, being off-peak. The older form, one off-peak window ("16:30-00:30", wrapping past
midnight, every day), is still read when no schedule is set; it was the provider's rule once and
counted 10:00-16:30 UTC and weekends as peak once the rule changed (docs/specs/cost-governance).
"""

from __future__ import annotations

import re
from datetime import datetime

_WINDOW = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")


def parse_off_peak_window(text: str) -> tuple[int, int]:
    """"16:30-00:30" → (990, 30), minutes after midnight UTC; raises ValueError on anything else."""
    match = _WINDOW.match(text.strip())
    if not match:
        raise ValueError(f"off-peak window must look like 16:30-00:30 (UTC), got {text!r}")
    start = int(match.group(1)) * 60 + int(match.group(2))
    end = int(match.group(3)) * 60 + int(match.group(4))
    if start == end:
        raise ValueError("off-peak window must not start and end at the same time")
    return start, end


_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _weekdays(text: str) -> frozenset[int]:
    days: set[int] = set()
    for part in text.split(","):
        first, _, last = part.strip().partition("-")
        if first not in _DAYS or (last and last not in _DAYS):
            raise ValueError(f"unknown weekday in {text!r}: use mon, tue, wed, thu, fri, sat, sun")
        start, end = _DAYS.index(first), _DAYS.index(last or first)
        days.update(range(start, end + 1) if start <= end else [*range(start, 7), *range(0, end + 1)])
    return frozenset(days)


def parse_peak_schedule(text: str) -> list[tuple[frozenset[int], int, int]]:
    """"mon-fri 01:00-04:00,06:00-10:00; sat 02:00-03:00" -> [(weekdays 0-6, start, end minute)].

    Each window is one day's minutes, start before end (a window past midnight is two windows).
    Raises ValueError on anything else.
    """
    windows: list[tuple[frozenset[int], int, int]] = []
    for group in [part.strip() for part in text.split(";") if part.strip()]:
        days_text, _, spans_text = group.partition(" ")
        if not spans_text.strip():
            raise ValueError(f"peak schedule group {group!r} must look like 'mon-fri 01:00-04:00,06:00-10:00'")
        days = _weekdays(days_text.strip().casefold())
        for span in spans_text.split(","):
            match = re.match(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]|24):([0-5]\d)$", span.strip())
            if not match:
                raise ValueError(f"peak window {span.strip()!r} must look like 01:00-04:00 (UTC)")
            start = int(match.group(1)) * 60 + int(match.group(2))
            end = int(match.group(3)) * 60 + int(match.group(4))
            if not start < end <= 24 * 60:
                raise ValueError(f"peak window {span.strip()!r} must start before it ends, within one day")
            windows.append((days, start, end))
    if not windows:
        raise ValueError("peak schedule is empty")
    return windows


def is_peak(at: datetime, *, off_peak_window_utc: str | None = None, peak_schedule_utc: str | None = None) -> bool:
    """Whether a call finishing at ``at`` (aware, any zone) is at peak; the schedule wins over the window."""
    utc = at.utctimetuple()
    minute = utc.tm_hour * 60 + utc.tm_min
    if peak_schedule_utc:
        return any(utc.tm_wday in days and start <= minute < end for days, start, end in parse_peak_schedule(peak_schedule_utc))
    if not off_peak_window_utc:
        return False
    start, end = parse_off_peak_window(off_peak_window_utc)
    off_peak = start <= minute < end if start < end else minute >= start or minute < end
    return not off_peak


def price_multiplier_at(
    at: datetime,
    *,
    peak_multiplier: float | None,
    off_peak_window_utc: str | None,
    peak_schedule_utc: str | None = None,
) -> float:
    """The factor on the stored (off-peak) prices for a call finishing at ``at`` (aware, any zone)."""
    if not peak_multiplier or not (off_peak_window_utc or peak_schedule_utc):
        return 1.0
    peak = is_peak(at, off_peak_window_utc=off_peak_window_utc, peak_schedule_utc=peak_schedule_utc)
    return float(peak_multiplier) if peak else 1.0
