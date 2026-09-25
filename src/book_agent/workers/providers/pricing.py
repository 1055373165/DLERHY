"""Peak and off-peak token prices.

Some providers charge a multiple of their listed price outside an off-peak window. DeepSeek, for
one, lists V4 Flash at $0.15 / $0.60 per million input / output tokens off-peak and twice that at
peak. A credential stores its off-peak prices, the peak multiplier, and the off-peak window in UTC
("16:30-00:30", which may wrap past midnight); a call is priced by the moment it finishes.
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


def price_multiplier_at(at: datetime, *, peak_multiplier: float | None, off_peak_window_utc: str | None) -> float:
    """The factor on the stored (off-peak) prices for a call finishing at ``at`` (aware, any zone)."""
    if not peak_multiplier or not off_peak_window_utc:
        return 1.0
    start, end = parse_off_peak_window(off_peak_window_utc)
    utc = at.utctimetuple()
    minute = utc.tm_hour * 60 + utc.tm_min
    off_peak = start <= minute < end if start < end else minute >= start or minute < end
    return 1.0 if off_peak else float(peak_multiplier)
