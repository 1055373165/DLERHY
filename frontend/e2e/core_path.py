"""The reader's main path in a real browser: bring a book -> ... -> read the Chinese version.

Measures what the UI/UX spec (docs/specs/ui-ux-overhaul/SPEC.md §2.3) sets targets for: clicks
from bringing the book to the first Chinese text, and how often the reader leaves the book's page;
and checks that a "ready to read" browser notification is sent (Notification is mocked in the page).

A book (``--source file``, an EPUB of five chapters) goes the pilot way (docs/agent-upgrade/08):
upload -> 免费试译前两章 -> read the two chapters the page opens on -> 翻译全书 -> read the book.
A web page (``--source link``) has no pilot: it translates straight away.

Everything is local and costs nothing: a throwaway SQLite database, the echo translation model,
a local article (e2e/fixtures/article.html) and this repo's frontend built into a temp directory
and served by the API itself. The dev servers on 8999/4173 are not touched.

    uv run python frontend/e2e/core_path.py                        # a pasted link
    uv run python frontend/e2e/core_path.py --source file          # an EPUB, the main way in: the free pilot first
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
import zipfile
import time
import urllib.request
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


EPUB_CHAPTERS = [
    ("Why Momentum Matters", "chapter1.xhtml", "<h1>Why Momentum Matters</h1><p>Momentum is the tendency of a trend to continue.</p>"),
    ("What the evidence says", "chapter2.xhtml", "<h1>What the evidence says</h1><p>Past winners keep winning for several months.</p>"),
]


def _body(topic: str, sentences: int = 24) -> str:
    return " ".join(f"{topic} sentence {index} says one more thing about momentum." for index in range(1, sentences + 1))


# A book long enough to have a pilot: front matter, then five numbered chapters. The pilot is
# chapters 1 and 2; the words "Carry", "Decay" and "Crowding" appear only in chapters 3-5.
PILOT_EPUB_CHAPTERS = [
    ("Preface", "preface.xhtml", f"<h1>Preface</h1><p>{_body('Preface', 8)}</p>"),
    ("1 Why Momentum Matters", "chapter1.xhtml", f"<h1>1 Why Momentum Matters</h1><p>{_body('Trend')}</p>"),
    ("2 What the evidence says", "chapter2.xhtml", f"<h1>2 What the evidence says</h1><p>{_body('Evidence')}</p>"),
    ("3 Carry", "chapter3.xhtml", f"<h1>3 Carry</h1><p>{_body('Carry')}</p>"),
    ("4 Decay", "chapter4.xhtml", f"<h1>4 Decay</h1><p>{_body('Decay')}</p>"),
    ("5 Crowding", "chapter5.xhtml", f"<h1>5 Crowding</h1><p>{_body('Crowding')}</p>"),
]


def _write_epub(path: Path, chapters=EPUB_CHAPTERS, title: str = "Momentum Handbook") -> Path:
    """An EPUB of the given chapters; by default two, the smallest thing the ingest path accepts (and too short for a pilot)."""
    manifest = " ".join(
        ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />']
        + [f'<item id="c{i}" href="{href}" media-type="application/xhtml+xml" />' for i, (_, href, _) in enumerate(chapters, 1)]
    )
    spine = " ".join(f'<itemref idref="c{i}" />' for i, _ in enumerate(chapters, 1))
    links = "".join(f'<li><a href="{href}">{title_}</a></li>' for title_, href, _ in chapters)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" /></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0" encoding="UTF-8"?><package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">'
            f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title>'
            "<dc:creator>Test Author</dc:creator><dc:language>en</dc:language></metadata>"
            f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>",
        )
        archive.writestr(
            "OEBPS/nav.xhtml",
            '<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            f'<body><nav epub:type="toc"><ol>{links}</ol></nav></body></html>',
        )
        for _title, href, body in chapters:
            archive.writestr(
                f"OEBPS/{href}",
                f'<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>',
            )
    return path


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
        # Arbitrary test prices, small enough that the local article is well within the $1 cap.
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


def _wait_until(condition, page, *, timeout_ms: int) -> None:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if condition():
            return
        page.wait_for_timeout(100)
    raise AssertionError("timed out waiting for the page to be ready")


def _first_visible(page, locators, timeout_ms: int):
    """The first of several candidate controls to become visible (old and new layouts name them differently)."""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for locator in locators:
            if locator.count() and locator.first.is_visible():
                return locator.first
        page.wait_for_timeout(250)
    return None


def _frame_text(page, *, timeout_ms: int = 120_000) -> str:
    frame = page.frame_locator("iframe").first
    frame.locator("body").wait_for(timeout=timeout_ms)
    return frame.locator("body").inner_text(timeout=60_000)


def _pilot_path(page, click, shots: Path | None) -> dict:
    """The free pilot, from the button after import to reading its two chapters and choosing the whole book."""
    started = time.time()
    offer = _first_visible(page, [page.get_by_role("button", name="免费试译前两章")], timeout_ms=30_000)
    if offer is None:
        raise AssertionError("no free pilot offered after import: " + page.locator("main, body").first.inner_text()[:400].replace("\n", " | "))
    if shots:
        page.screenshot(path=str(shots / "pilot-1-offered.png"))
    click(offer, "free pilot")
    dialog = page.get_by_role("dialog", name="免费试译")
    dialog.wait_for(timeout=20_000)
    if shots:
        page.screenshot(path=str(shots / "pilot-2-dialog.png"))
    click(dialog.get_by_role("button", name="开始免费试译"), "confirm free pilot")
    page.wait_for_timeout(1_200)
    if shots:
        page.screenshot(path=str(shots / "pilot-3-running.png"))

    # When the pilot is done the page opens by itself on its result: the two chapters, the problems, the choice.
    result = page.get_by_role("group", name="试译结果")
    result.wait_for(timeout=300_000)
    _wait_until(lambda: page.url.split("?")[0].endswith("/read"), page, timeout_ms=20_000)
    opened_by_itself = True
    text = _frame_text(page)
    if "Trend sentence" not in text or "Carry sentence" in text:
        raise AssertionError("the pilot's reading copy is not its two chapters: " + text[:300].replace("\n", " | "))
    page.wait_for_timeout(400)
    if shots:
        page.screenshot(path=str(shots / "pilot-4-result.png"))
        page.screenshot(path=str(shots / "pilot-4-result-full.png"), full_page=True)
    pilot_seconds = round(time.time() - started, 1)

    click(result.get_by_role("button", name=re.compile("^翻译全书")), "translate whole book")
    confirm = _first_visible(page, [page.get_by_role("dialog", name="翻译全书")], timeout_ms=5_000)
    if confirm is not None:
        # Above the auto-start cap (or without prices) the whole book asks once.
        if shots:
            page.screenshot(path=str(shots / "pilot-5-whole-book-dialog.png"))
        click(confirm.get_by_role("button", name="翻译全书"), "confirm whole book")
    return {"opened_by_itself": opened_by_itself, "pilot_text_sample": text[:80], "seconds_to_pilot": pilot_seconds}


def run(*, base: str, article_url: str, epub: Path, source: str, headed: bool, shots: Path | None) -> dict:
    from playwright.sync_api import sync_playwright

    clicks: list[str] = []
    paths: list[str] = []

    def click(locator, label: str) -> None:
        clicks.append(label)
        locator.click()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        # A stand-in for the browser's notifications: granted, and the reader has turned them on.
        page.add_init_script(
            """
            window.__notifications = [];
            window.Notification = class {
              static permission = "granted";
              static requestPermission = async () => "granted";
              constructor(title, options) { window.__notifications.push({ title, body: options && options.body }); }
              close() {}
            };
            localStorage.setItem("book-agent.notify", "on");
            """
        )
        page.on("framenavigated", lambda frame: frame == page.main_frame and paths.append(re.sub(r"^https?://[^/]+", "", frame.url).split("?")[0]))
        page.goto(f"{base}/")

        if source == "file":
            chooser = page.get_by_label("选择书稿文件")
            chooser.wait_for(state="attached", timeout=20_000)
            # Choosing a file is one action for the reader (the dialog itself is the browser's).
            clicks.append("choose file")
            chooser.set_input_files(str(epub))
        else:
            link_input = page.get_by_role("textbox", name="网页链接").first
            link_input.wait_for(timeout=20_000)
            link_input.fill(article_url)  # pasting: not counted as a click
            fetch_button = page.get_by_role("button", name=re.compile("抓取网页|翻译这个网页")).first
            # The button enables once the page has taken the pasted link.
            _wait_until(lambda: fetch_button.is_enabled(), page, timeout_ms=10_000)
            click(fetch_button, "fetch")

        pilot: dict | None = None
        if source == "file":
            # A book: the free pilot first, then the whole book.
            pilot = _pilot_path(page, click, shots)
            pilot["clicks_to_pilot"] = clicks.index("translate whole book")
            auto_started = False
        else:
            # A web page has no pilot. Either the run starts by itself, or there is one button to start it.
            start = _first_visible(
                page,
                [page.get_by_role("button", name=re.compile(r"^开始翻译|^开始整书转换"))],
                timeout_ms=20_000,
            )
            auto_started = start is None
            if start is not None:
                click(start, "start")
                # Starting by hand asks once: cost, fast reading mode, notifications.
                dialog = page.get_by_role("dialog", name="开始翻译")
                dialog.wait_for(timeout=20_000)
                click(dialog.get_by_role("button", name="开始翻译"), "confirm start")

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
        try:
            frame.locator("body").wait_for(timeout=120_000)
        except Exception:
            if shots:
                page.screenshot(path=str(shots / "core-path-stuck.png"))
            document_id = re.search(r"/books/([^/]+)", page.url)
            overview = page.request.get(f"{base}/v1/documents/{document_id.group(1)}/overview").json() if document_id else {}
            raise AssertionError(
                "the Chinese text never showed: "
                + page.locator("main, body").first.inner_text()[:600].replace("\n", " | ")
                + " overview: "
                + json.dumps({k: overview.get(k) for k in ("headline", "readable", "activity", "primary_action")}, ensure_ascii=False)
                + " translation: "
                + json.dumps({k: (overview.get("translation") or {}).get(k) for k in ("state", "steps")}, ensure_ascii=False)
            )
        text = frame.locator("body").inner_text(timeout=60_000)
        if source == "file" and "Carry sentence" not in text:
            raise AssertionError("the whole book's reading copy lacks chapter 3: " + text[:300].replace("\n", " | "))
        if shots:
            page.screenshot(path=str(shots / ("pilot-6-whole-book-read.png" if source == "file" else "core-path-read.png")))
        # The inbox is polled every 30 s: the "ready to read" notification comes within one poll.
        page.wait_for_function(
            "window.__notifications.some((n) => n.title.startsWith('可以阅读了'))", timeout=45_000
        )
        notifications = page.evaluate("window.__notifications")
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
        "source": source,
        "auto_started": auto_started,
        "read_text_sample": text[:80],
        "notifications": notifications,
        **({"pilot": pilot} if pilot is not None else {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=("link", "file"), default="link", help="what the reader brings: a web page link or an EPUB")
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
                epub=_write_epub(work / "momentum.epub", PILOT_EPUB_CHAPTERS),
                source=args.source,
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
