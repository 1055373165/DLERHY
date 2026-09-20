"""What this deployment can produce, so the UI offers only what will work."""

from __future__ import annotations

import threading
import time
from pathlib import Path

# Checking for the browser starts Playwright's driver (a subprocess): cache the answer briefly,
# long enough for page loads, short enough that installing Chromium shows up within a minute.
_CACHE_SECONDS = 60.0
_lock = threading.Lock()
_cached: tuple[float, bool] | None = None


def pdf_renderer_installed() -> bool:
    """PDF export prints the merged HTML with Playwright's Chromium: both the package and the browser."""
    import importlib.util

    if importlib.util.find_spec("playwright") is None:
        return False
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).exists()
    except Exception:
        return False


def pdf_renderer_available() -> bool:
    """`pdf_renderer_installed`, cached for a minute."""
    global _cached
    with _lock:
        now = time.monotonic()
        if _cached is None or now - _cached[0] > _CACHE_SECONDS:
            _cached = (now, pdf_renderer_installed())
        return _cached[1]


def reset_cache() -> None:
    global _cached
    with _lock:
        _cached = None
