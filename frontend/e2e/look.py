"""Look at every page: screenshots in both themes, axe-core, and a keyboard walk.

Runs against a throwaway SQLite API with the echo backend, the same way core_path.py does,
so it needs no provider key and touches neither the dev database nor the running services.

    python frontend/e2e/look.py --screenshots .test-tmp/ui

Exit code 1 if axe finds a violation or the keyboard walk gets stuck.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core_path import FRONTEND, ROOT, _build_frontend, _start_api, _wait_until, _write_epub  # noqa: E402

AXE = FRONTEND / "node_modules" / "axe-core" / "axe.min.js"

# axe's own rule ids; these are the ones that matter for this app's pages.
AXE_OPTIONS = {
    "resultTypes": ["violations"],
    "runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]},
}

# The book page's tabs, by the path segment the router uses.
BOOK_TABS = ["progress", "read", "terms", "issues", "log", "chapters"]


def _another_epub(source: Path, target: Path, title: str) -> Path:
    """The same book under a different title: importing the first one twice finds the first book."""
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as copy:
        for item in original.infolist():
            data = original.read(item.filename)
            if item.filename.endswith("content.opf"):
                data = data.replace(b"<dc:title>Momentum Handbook</dc:title>", f"<dc:title>{title}</dc:title>".encode())
            copy.writestr(item, data)
    return target


def _axe(page) -> list[dict]:
    """Every accessibility violation axe can see on the page as it stands."""
    page.add_script_tag(path=str(AXE))
    return page.evaluate(
        """async (options) => {
            const result = await axe.run(document, options);
            return result.violations.map((v) => ({
              id: v.id,
              impact: v.impact,
              help: v.help,
              nodes: v.nodes.slice(0, 4).map((n) => ({ target: n.target.join(" "), summary: n.failureSummary })),
            }));
        }""",
        AXE_OPTIONS,
    )


def _settle(page) -> None:
    """Wait for the page's first paint of real content."""
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)


def _visit(page, base: str, path: str, *, name: str, shots: Path, theme: str, check: bool) -> list[dict]:
    page.goto(f"{base}{path}")
    _settle(page)
    page.screenshot(path=str(shots / f"{name}-{theme}.png"), full_page=True)
    if not check:
        return []
    return [{**violation, "page": name, "path": path} for violation in _axe(page)]


# Nothing on screen may be smaller than this: the old interface labelled things at 10px.
MIN_FONT_PX = 12


def _style_checks(page, *, name: str) -> list[str]:
    """What the eye was asked to check by hand: label sizes, and text too small to read."""
    return page.evaluate(
        """(min) => {
            const problems = [];
            const seen = new Set();
            for (const node of document.querySelectorAll("body *")) {
              const text = [...node.childNodes]
                .filter((c) => c.nodeType === 3)
                .map((c) => c.textContent.trim())
                .join("")
                .trim();
              if (!text) continue;
              const style = getComputedStyle(node);
              if (style.visibility === "hidden" || style.display === "none") continue;
              const size = parseFloat(style.fontSize);
              if (size < min) {
                const key = `${size}px`;
                if (seen.has(key + text.slice(0, 10))) continue;
                seen.add(key + text.slice(0, 10));
                problems.push(`「${text.slice(0, 14)}」只有 ${size}px`);
              }
            }
            return problems;
        }""",
        MIN_FONT_PX,
    )


def _sidebar_checks(page) -> list[str]:
    """The sidebar must read as words without being hovered, and a book must offer the way back."""
    problems = page.evaluate(
        """() => {
            const problems = [];
            const nav = document.querySelector("nav[aria-label=主导航]");
            if (!nav) return ["找不到主导航"];
            for (const link of nav.querySelectorAll("a")) {
              const label = link.textContent.trim();
              if (!label) { problems.push("导航里有一个没有文字的按钮"); continue; }
              const span = [...link.querySelectorAll("span")].find((s) => s.textContent.trim() === label);
              const style = getComputedStyle(span ?? link);
              if (parseFloat(style.opacity) < 1) problems.push(`导航「${label}」要悬停才看得见`);
              if ((span ?? link).getBoundingClientRect().width < 8) problems.push(`导航「${label}」没有占位`);
            }
            return problems;
        }"""
    )
    back = page.get_by_role("link", name=re.compile("返回"))
    if back.count() == 0:
        problems.append("书的页面上没有返回的入口")
    return problems


def _keyboard_walk(page, base: str, epub: Path, shots: Path) -> list[str]:
    """Import → start dialog → read, using only the keyboard. Returns what went wrong."""
    problems: list[str] = []
    page.goto(f"{base}/")
    _settle(page)

    # The skip link is the first stop, and it must lead somewhere.
    page.keyboard.press("Tab")
    first = page.evaluate("document.activeElement.textContent")
    if "跳到主要内容" not in (first or ""):
        problems.append(f"第一个 Tab 停在「{first}」，不是跳转链接")

    # Every control on the home page must be reachable by Tab.
    reached = set()
    for _ in range(40):
        page.keyboard.press("Tab")
        reached.add(page.evaluate("document.activeElement.getAttribute('aria-label') || document.activeElement.textContent || ''").strip())
    if not any("选择书稿文件" in label for label in reached):
        problems.append("Tab 走不到「选择书稿文件」")

    # Import a second book: the file input is the reader's way in.
    page.get_by_label("选择书稿文件").set_input_files(str(epub))
    start = page.get_by_role("button", name=re.compile(r"^开始翻译")).first
    start.wait_for(timeout=60_000)

    # Open the dialog from the keyboard.
    start.focus()
    page.keyboard.press("Enter")
    dialog = page.get_by_role("dialog", name="开始翻译")
    dialog.wait_for(timeout=20_000)
    page.wait_for_timeout(300)
    page.screenshot(path=str(shots / "start-dialog-keyboard.png"))

    if not dialog.evaluate("(node) => node.contains(document.activeElement)"):
        problems.append("对话框打开后，焦点没有进到对话框里")

    # Tab must stay inside the dialog: 30 presses, never escaping.
    for index in range(30):
        page.keyboard.press("Tab")
        if not dialog.evaluate("(node) => node.contains(document.activeElement)"):
            problems.append(f"第 {index + 1} 次 Tab 焦点跑出了对话框")
            break

    # Shift+Tab too, backwards past the first control.
    for index in range(30):
        page.keyboard.press("Shift+Tab")
        if not dialog.evaluate("(node) => node.contains(document.activeElement)"):
            problems.append(f"第 {index + 1} 次 Shift+Tab 焦点跑出了对话框")
            break

    # Escape closes it and gives the focus back to the button that opened it.
    page.keyboard.press("Escape")
    try:
        dialog.wait_for(state="hidden", timeout=5_000)
    except Exception:
        problems.append("Escape 关不掉对话框")
        return problems
    page.wait_for_timeout(200)
    focused = page.evaluate("document.activeElement.textContent || ''")
    if "开始翻译" not in focused:
        problems.append(f"关闭后焦点落在「{focused.strip()[:20]}」，没有回到打开它的按钮")

    # And the dialog can be driven to completion from the keyboard.
    start.focus()
    page.keyboard.press("Enter")
    dialog.wait_for(timeout=20_000)
    confirm = dialog.get_by_role("button", name="开始翻译")
    confirm.focus()
    page.keyboard.press("Enter")
    try:
        dialog.wait_for(state="hidden", timeout=20_000)
    except Exception:
        problems.append("键盘确认后对话框没有关闭")
    return problems


def run(*, base: str, epub: Path, shots: Path, headed: bool) -> dict:
    from playwright.sync_api import sync_playwright

    findings: list[dict] = []
    shots.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)

        # ── A book that has been through the whole run, so the pages have something on them ──
        context = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme="light")
        page = context.new_page()
        page.goto(f"{base}/")
        _settle(page)
        page.get_by_label("选择书稿文件").set_input_files(str(epub))
        start = page.get_by_role("button", name=re.compile(r"^开始翻译")).first
        start.wait_for(timeout=60_000)
        start.click()
        dialog = page.get_by_role("dialog", name="开始翻译")
        dialog.wait_for(timeout=20_000)
        dialog.get_by_role("button", name="开始翻译").click()
        # Starting sends the reader to the book's 进度 tab: that is where the id is.
        _wait_until(lambda: re.search(r"/books/([^/]+)", page.url) is not None, page, timeout_ms=30_000)
        document_id = re.search(r"/books/([^/]+)", page.url).group(1)
        # Let the run finish, so 进度, 阅读 and 运行记录 all have something on them.
        overview: dict = {}
        for _ in range(150):
            overview = page.request.get(f"{base}/v1/documents/{document_id}/overview").json()
            if overview.get("readable"):
                break
            page.wait_for_timeout(2_000)
        if not overview.get("readable"):
            raise AssertionError("翻译没有跑完：" + json.dumps(overview, ensure_ascii=False)[:800])

        destinations = [("home", "/"), ("library", "/library"), ("inbox", "/inbox"), ("settings", "/settings")]
        destinations += [(f"book-{tab}", f"/books/{document_id}/{tab}") for tab in BOOK_TABS]

        style_problems: list[str] = []
        for name, path in destinations:
            findings += _visit(page, base, path, name=name, shots=shots, theme="light", check=True)
            style_problems += [f"{name}: {problem}" for problem in _style_checks(page, name=name)]
            if name.startswith("book-"):
                style_problems += [f"{name}: {problem}" for problem in _sidebar_checks(page)]

        # Narrow: the tabs and the sidebar have to survive a phone-width window.
        page.set_viewport_size({"width": 420, "height": 860})
        for name, path in [("home", "/"), (f"book-progress", f"/books/{document_id}/progress")]:
            findings += _visit(page, base, path, name=f"{name}-narrow", shots=shots, theme="light", check=True)
        page.set_viewport_size({"width": 1280, "height": 900})

        # ── The same pages in the dark ──
        dark = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme="dark")
        dark_page = dark.new_page()
        for name, path in destinations:
            findings += _visit(dark_page, base, path, name=name, shots=shots, theme="dark", check=True)
        dark.close()

        # ── Keyboard ──
        keyboard = browser.new_context(viewport={"width": 1280, "height": 900})
        keyboard_page = keyboard.new_page()
        second = _another_epub(epub, epub.with_name("keyboard.epub"), "Keyboard Walk")
        try:
            keyboard_problems = _keyboard_walk(keyboard_page, base, second, shots)
        except Exception as error:  # a stuck walk is a finding, not a crash
            keyboard_page.screenshot(path=str(shots / "keyboard-stuck.png"))
            keyboard_problems = [f"键盘走查中断：{type(error).__name__}: {error}".split("Call log")[0].strip()]
        keyboard.close()

        context.close()
        browser.close()

    return {
        "violations": findings,
        "keyboard": keyboard_problems,
        "style": style_problems,
        "screenshots": sorted(p.name for p in shots.glob("*.png")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--screenshots", type=Path, default=ROOT / ".test-tmp" / "ui")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway database and build")
    args = parser.parse_args()

    if not AXE.exists():
        print("axe-core is missing: cd frontend && npm install", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="book-agent-look-"))
    dist = work / "dist"
    api = None
    try:
        _build_frontend(dist)
        api, port = _start_api(work, dist, priced=False)
        epub = _write_epub(work / "sample.epub")
        report = run(base=f"http://127.0.0.1:{port}", epub=epub, shots=args.screenshots.resolve(), headed=args.headed)
    finally:
        if api is not None:
            api.terminate()
            api.wait(timeout=30)
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = not report["violations"] and not report["keyboard"] and not report["style"]
    print(
        "\n无障碍检查通过"
        if ok
        else f"\n{len(report['violations'])} 项 axe 问题，{len(report['keyboard'])} 项键盘问题，{len(report['style'])} 项样式问题",
        file=sys.stderr,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
