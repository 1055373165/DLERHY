"""The reader's main path in a real browser: paste a link -> ... -> read the Chinese version.

Measures what the UI/UX spec (docs/specs/ui-ux-overhaul/SPEC.md §2.3) sets targets for: clicks
from pasting the link to the first Chinese text, and how often the reader leaves the book's page.

Everything is local and costs nothing: a throwaway SQLite database, the echo translation model,
a local article (e2e/fixtures/article.html) and this repo's frontend built into a temp directory
and served by the API itself. The dev servers on 8999/4173 are not touched.

    uv run python frontend/e2e/core_path.py                        # measure and print
    uv run python frontend/e2e/core_path.py --max-clicks 3 --max-leaves 0   # fail above the targets
    uv run python frontend/e2e/core_path.py --priced               # provider has prices: auto-start path

Needs Playwright with Chromium (uv sync --extra pdf; playwright install chromium) and node.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve_fixtures() -> tuple[http.server.ThreadingHTTPServer, int]:
    port = _free_port()
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

    handler = partial(Quiet, directory=str(FIXTURES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def _build_frontend(out_dir: Path) -> None:
    subprocess.run(
        ["npx", "vite", "build", "--outDir", str(out_dir), "--emptyOutDir", "--logLevel", "error"],
        cwd=FRONTEND,
        check=True,
    )


def _start_api(work: Path, dist: Path, *, priced: bool) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    db = work / "e2e.db"
    env = {
        **os.environ,
        "BOOK_AGENT_APP_SCOPE": "e2e",
        "BOOK_AGENT_DATABASE_URL": f"sqlite+pysqlite:///{db}",
        "BOOK_AGENT_AUTH_MODE": "disabled",
        "BOOK_AGENT_TRANSLATION_BACKEND": "echo",
        "BOOK_AGENT_TRANSLATION_MODEL": "echo-worker",
        "BOOK_AGENT_WEB_ALLOW_PRIVATE_HOSTS": "true",
        "BOOK_AGENT_UPLOAD_ROOT": str(work / "uploads"),
        "BOOK_AGENT_EXPORT_ROOT": str(work / "exports"),
        "BOOK_AGENT_FRONTEND_DIST_DIR": str(dist),
    }
    if priced:
        env["BOOK_AGENT_TRANSLATION_INPUT_COST_PER_1M_TOKENS"] = "0.3"
        env["BOOK_AGENT_TRANSLATION_OUTPUT_COST_PER_1M_TOKENS"] = "1.0"
    # SQLite has no migrations here: create the schema the way the unit tests do.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import book_agent.domain.models; from book_agent.infra.db.base import Base; "
            "from book_agent.infra.db.session import build_engine; "
            f"Base.metadata.create_all(build_engine({str(f'sqlite+pysqlite:///{db}')!r}))",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "book_agent.app.main:create_app", "--factory", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT,
        env=env,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/health", timeout=2) as response:
                if response.status == 200:
                    return process, port
        except OSError:
            time.sleep(0.5)
    process.terminate()
    raise RuntimeError("API did not start")


def _first_visible(page, locators, timeout_ms: int):
    """The first of several candidate controls to become visible (old and new layouts name them differently)."""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for locator in locators:
            if locator.count() and locator.first.is_visible():
                return locator.first
        page.wait_for_timeout(250)
    return None


def run(*, base: str, article_url: str, headed: bool, shots: Path | None) -> dict:
    from playwright.sync_api import sync_playwright

    clicks: list[str] = []
    paths: list[str] = []

    def click(locator, label: str) -> None:
        clicks.append(label)
        locator.click()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("framenavigated", lambda frame: frame == page.main_frame and paths.append(re.sub(r"^https?://[^/]+", "", frame.url).split("?")[0]))
        page.goto(f"{base}/")

        link_input = page.get_by_role("textbox", name="网页链接").first
        link_input.wait_for(timeout=20_000)
        link_input.fill(article_url)  # pasting: not counted as a click
        click(page.get_by_role("button", name=re.compile("抓取网页|翻译这个网页")).first, "fetch")

        # Either the run starts by itself, or there is one button to start it.
        start = _first_visible(
            page,
            [page.get_by_role("button", name=re.compile(r"^开始翻译|^开始整书转换"))],
            timeout_ms=20_000,
        )
        auto_started = start is None
        if start is not None:
            click(start, "start")

        read = _first_visible(
            page,
            [page.get_by_role("link", name="阅读中文版"), page.get_by_role("tab", name=re.compile("阅读"))],
            timeout_ms=300_000,
        )
        if read is None:
            history = page.request.get(f"{base}/v1/documents/history?limit=1").json()
            entry = (history.get("entries") or [{}])[0]
            run_id = entry.get("latest_run_id")
            run = page.request.get(f"{base}/v1/runs/{run_id}").json() if run_id else {}
            raise AssertionError(
                "no way to read after the run: "
                + json.dumps({key: entry.get(key) for key in ("status", "merged_export_ready", "latest_run_status", "latest_run_current_stage")})
                + " run: "
                + json.dumps(
                    {
                        "status": run.get("status"),
                        "stop_reason": run.get("stop_reason"),
                        "stages": {
                            key: {k: value.get(k) for k in ("status", "error_class", "error_message") if value.get(k)}
                            for key, value in (((run.get("status_detail_json") or {}).get("pipeline") or {}).get("_cached_pipeline_stages") or {}).items()
                        },
                    },
                    ensure_ascii=False,
                )[:3000]
            )
        # A "read" tab that is already selected needs no click.
        if read.get_attribute("aria-selected") != "true":
            click(read, "read")
        frame = page.frame_locator("iframe").first
        frame.locator("body").wait_for(timeout=120_000)
        text = frame.locator("body").inner_text(timeout=60_000)
        if shots:
            page.screenshot(path=str(shots / "core-path-read.png"))
        browser.close()

    visited = [path for index, path in enumerate(paths) if index == 0 or path != paths[index - 1]]
    book_pages = re.compile(r"^/books/[^/]+")
    leaves = sum(
        1
        for before, after in zip(visited, visited[1:])
        if book_pages.match(before) and not (book_pages.match(after) and book_pages.match(after).group(0) == book_pages.match(before).group(0))
    )
    return {
        "clicks": len(clicks),
        "click_steps": clicks,
        "page_switches": max(0, len(visited) - 1),
        "left_book_page": leaves,
        "paths": visited,
        "auto_started": auto_started,
        "read_text_sample": text[:80],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--priced", action="store_true", help="give the provider prices, so a cheap page may start by itself")
    parser.add_argument("--max-clicks", type=int)
    parser.add_argument("--max-leaves", type=int, help="most times the reader may leave the book's page")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()

    fixtures, fixtures_port = _serve_fixtures()
    # Not under the system temp dir: export rows refuse temp-dir paths (exports_file_path_no_tempdir_check).
    (ROOT / ".test-tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="e2e-", dir=ROOT / ".test-tmp") as tmp:
        work = Path(tmp)
        _build_frontend(work / "dist")
        api, port = _start_api(work, work / "dist", priced=args.priced)
        try:
            result = run(
                base=f"http://127.0.0.1:{port}",
                article_url=f"http://127.0.0.1:{fixtures_port}/article.html",
                headed=args.headed,
                shots=args.screenshots,
            )
        finally:
            api.terminate()
            api.wait(timeout=30)
            fixtures.shutdown()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    failures = []
    if args.max_clicks is not None and result["clicks"] > args.max_clicks:
        failures.append(f"{result['clicks']} clicks > {args.max_clicks}")
    if args.max_leaves is not None and result["left_book_page"] > args.max_leaves:
        failures.append(f"left the book page {result['left_book_page']} times > {args.max_leaves}")
    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
