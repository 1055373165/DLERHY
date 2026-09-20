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


def _seed_issues(db: Path, document_id: str) -> int:
    """Put a few review issues on the book, and put them back if a later pass closed them.

    A run with the echo backend finds none, so the 问题 page would be checked empty — and an
    empty page cannot show the detail pane where the layout bugs live. Opening 阅读 triggers an
    export, whose gate reconciles the ledger and resolves issues no detector still sees, so this
    is called again before each pass rather than once.

    The text is as long and as awkward as a real book's: a chapter title that does not wrap, a
    sentence that fills the column, and a URL with no spaces to break at.
    """
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from book_agent.domain.enums import Detector, IssueStatus, RootCauseLayer, Severity
    from book_agent.domain.models.document import Block, Sentence
    from book_agent.domain.models.review import ReviewIssue
    from book_agent.infra.db.session import build_engine

    long_source = (
        "Momentum compounds when the same small action repeats on a schedule, and the effect is "
        "invisible until the curve turns; see the longitudinal dataset at "
        "https://example.org/research/longitudinal-momentum-study-2019-2024/appendix-b.html"
    )
    seeds = [
        {
            "issue_type": "MISTRANSLATION_SEMANTIC",
            "severity": Severity.HIGH,
            "blocking": False,
            "detector": Detector.MODEL,
            "root_cause_layer": RootCauseLayer.TRANSLATION,
            "evidence_json": {
                # A model-detected issue carries its own explanation, and it is a whole sentence:
                # this is what the list row has to show.
                "explanation": (
                    "审校代理认为这句把 compounding 译成「复合」是错的：原文说的是同一个小动作反复之后收益不断"
                    "累积，而「复合」在中文里会让读者想到化学反应或复合材料，和上下文对不上。"
                ),
                "reason": "译文把 compounding 理解成了「复合」，这里指的是收益随时间累积，读者会以为在讲化学反应。",
                "source": long_source,
                "target": "当同一个小动作按计划重复时，动量就会复合，而这个效果在曲线拐弯之前是看不见的；纵向数据见附录 B。",
                "suggestion": "当同一个小动作按计划重复时，势头会不断累积；效果要到曲线拐弯时才看得出来。",
            },
        },
        {
            "issue_type": "TERM_CONFLICT",
            "severity": Severity.MEDIUM,
            "blocking": False,
            "detector": Detector.RULE,
            "root_cause_layer": RootCauseLayer.MEMORY,
            "evidence_json": {
                "reason": "「momentum」在第一章译作「动量」，这里译作「势头」，与锁定的译法不一致。",
                "term": "momentum",
                "locked": "势头",
                "found": "动量",
            },
        },
        {
            "issue_type": "OMISSION",
            "severity": Severity.CRITICAL,
            "blocking": False,
            "detector": Detector.RULE,
            "root_cause_layer": RootCauseLayer.ALIGNMENT,
            "evidence_json": {
                "reason": "原文的最后一个从句在译文里找不到对应内容。",
                "source": "The evidence says it plainly, even when the effect takes months to show up.",
                "target": "证据说得很清楚。",
            },
        },
    ]

    engine = build_engine(f"sqlite+pysqlite:///{db}")
    added = 0
    with Session(engine) as session:
        block = session.scalars(select(Block).limit(1)).first()
        sentence = session.scalars(
            select(Sentence).order_by(func.length(Sentence.source_text).desc()).limit(1)
        ).first()
        for seed in seeds:
            existing = session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.issue_type == seed["issue_type"],
                )
            ).first()
            if existing is not None:
                existing.status = IssueStatus.OPEN
                existing.decided_by = None
                existing.decided_at = None
                existing.resolution_note = None
            else:
                session.add(
                    ReviewIssue(
                        document_id=document_id,
                        chapter_id=block.chapter_id if block else None,
                        block_id=block.id if block else None,
                        sentence_id=sentence.id if sentence else None,
                        status=IssueStatus.OPEN,
                        confidence=0.82,
                        version=1,
                        **seed,
                    )
                )
            added += 1
        session.commit()
    engine.dispose()
    return added


# A real book has long sentences, long chapter titles and URLs with nothing to break at.
# The 问题 page shows a sentence pair, so a fixture of five-word sentences hides every width bug.
LONG_SENTENCE = (
    "Momentum compounds when the same small action repeats on a schedule, and the effect stays "
    "invisible until the curve turns, which is why the longitudinal dataset published at "
    "https://example.org/research/longitudinal-momentum-study-2019-2024/appendix-b.html matters "
    "more than any single quarter of returns."
)
LONG_TITLE = "Why Momentum Matters More Than Any Single Quarter of Returns"


def _wordy_epub(source: Path, target: Path) -> Path:
    """The same book, with a chapter long enough to show how the pages behave under real text."""
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as copy:
        for item in original.infolist():
            data = original.read(item.filename)
            if item.filename.endswith("chapter1.xhtml"):
                data = (
                    f"<?xml version='1.0' encoding='UTF-8'?><html xmlns='http://www.w3.org/1999/xhtml'><body>"
                    f"<h1>{LONG_TITLE}</h1><p>{LONG_SENTENCE}</p>"
                    f"<p>Momentum is the tendency of a trend to continue.</p></body></html>"
                ).encode()
            elif item.filename.endswith("content.opf") or item.filename.endswith("nav.xhtml"):
                data = data.replace(b"Why Momentum Matters", LONG_TITLE.encode())
            copy.writestr(item, data)
    return target


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


# Words a Chinese page may still show: names, formats, and the product's own name.
# The fixture book is in English, so its title, author and text are allowed too.
ALLOWED_ENGLISH = {
    "Book", "Agent", "API", "OpenAPI", "PDF", "EPUB", "Markdown", "HTML", "CSV", "key", "token",
    "pdf", "epub", "BOOK", "md", "OCR", "ZH", "Momentum", "Handbook", "Test", "Author", "Keyboard",
    "Walk", "compounding", "CH", "echo", "worker", "offline", "built", "Built", "in",
}

# The fixture book is an English book: its own title and chapter titles appear as content,
# in the reader, in the issue list's chapter line and in the library. Those words are the book.
ALLOWED_ENGLISH |= set(re.findall(r"[A-Za-z]{2,}", LONG_TITLE))
ALLOWED_ENGLISH |= {"Momentum", "Handbook", "What", "the", "evidence", "says"}

# The book's own text is the book, not the interface: the reader and the source/target pair show it.
BOOK_TEXT_SELECTORS = "iframe, ._pairText, ._markdown, [class*='pairText'], [class*='markdown'], [class*='readerBody']"


def _chinese_check(page, *, name: str) -> list[str]:
    """English left in the interface. The M2 review missed a whole page this way."""
    words = page.evaluate(
        """(args) => {
            const [allowed, bookText] = args;
            const found = new Set();
            const strip = (text) => (text || "").replace(/https?:\/\/\S+/g, "").replace(/\/\S+/g, "");
            const add = (text) => {
              for (const word of strip(text).match(/[A-Za-z]{2,}/g) || []) found.add(word);
            };
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            for (let node = walker.nextNode(); node; node = walker.nextNode()) {
              const parent = node.parentElement;
              if (!parent) continue;
              // Technical details behind a <details>, and the book's own text, are not interface copy.
              if (parent.closest("details, pre, code")) continue;
              if (parent.closest(bookText)) continue;
              const style = getComputedStyle(parent);
              if (style.display === "none" || style.visibility === "hidden") continue;
              add(node.textContent);
            }
            for (const el of document.body.querySelectorAll("[aria-label], [title], [placeholder]")) {
              for (const attr of ["aria-label", "title", "placeholder"]) add(el.getAttribute(attr));
            }
            return [...found].filter((word) => !allowed.includes(word));
        }""",
        [sorted(ALLOWED_ENGLISH), BOOK_TEXT_SELECTORS],
    )
    return [f"{name}: 界面上还有英文「{word}」" for word in sorted(words)]


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


# Every width a reader might open this in: phone, small tablet, laptop, desktop, wide.
WIDTHS = [420, 768, 1024, 1280, 1440]

# Whatever the book's state, the status card offers exactly one obvious next move.
PRIMARY_ACTION = re.compile("阅读中文版|开始翻译|继续|重试|停止翻译")


def _overflow(page) -> list[str]:
    """Anything that makes the page draggable sideways, or reaches past the right edge.

    Content inside something that scrolls on its own is clipped there and does not drag the page.
    """
    return page.evaluate(
        """() => {
            const problems = [];
            const root = document.documentElement;
            const width = root.clientWidth;
            if (root.scrollWidth > width + 1) {
              problems.push(`页面可以左右拖动：scrollWidth ${root.scrollWidth} > ${width}`);
            }
            const scrolls = (node) => {
              for (let el = node.parentElement; el && el !== root; el = el.parentElement) {
                const overflow = getComputedStyle(el).overflowX;
                if (overflow === "auto" || overflow === "scroll" || overflow === "hidden") return true;
              }
              return false;
            };
            const named = new Set();
            const clipped = [];
            for (const el of document.querySelectorAll("body *")) {
              const rect = el.getBoundingClientRect();
              if (rect.width < 1 || rect.height < 1) continue;
              if (rect.right <= width + 1) continue;
              const name = (typeof el.className === "string" && el.className) || el.tagName;
              if (scrolls(el)) {
                if (clipped.length < 5 && !clipped.includes(name)) clipped.push(name);
                continue;
              }
              if (named.has(name)) continue;
              named.add(name);
              problems.push(`${name} 右边缘到 ${Math.round(rect.right)}，超出 ${width}`);
              if (named.size >= 5) break;
            }
            // The page scrolls but every wide element sits in something that scrolls on its own:
            // name them anyway, or the finding says nothing about where to look.
            if (problems.length && named.size === 0 && clipped.length) {
              problems.push(`可疑元素（在会滚动的容器里）：${clipped.join("、")}`);
            }
            // Content wider than the box it is drawn in, with nothing to clip it: this is what
            // pushes the page sideways even when every box still looks like it fits.
            const spilling = [...document.querySelectorAll("body *")].filter((el) => {
              if (getComputedStyle(el).overflowX !== "visible") return false;
              return el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0;
            });
            const deepest = spilling.filter((el) => !spilling.some((other) => other !== el && el.contains(other)));
            for (const el of deepest.slice(0, 5)) {
              const name = (typeof el.className === "string" && el.className) || el.tagName;
              problems.push(`${name} 里的内容宽 ${el.scrollWidth}，装它的框只有 ${el.clientWidth}`);
            }
            return problems;
        }"""
    )


def _primary_action(page) -> list[str]:
    """The status card must always show the one thing to do next, at any width.

    A book with nothing to do — the finished translation, seen from the 阅读 tab — says so in a
    line instead of a card. What must never happen is a card that states a status and offers no
    way to act on it.
    """
    banner = page.locator("section[aria-label=状态]")
    if banner.count() == 0:
        if page.locator("nav[aria-label=这本书]").count() == 0:
            return ["书页上既没有状态卡片，也没有标签栏"]
        return []
    action = banner.get_by_role("button").or_(banner.get_by_role("link")).filter(has_text=PRIMARY_ACTION)
    visible = [i for i in range(action.count()) if action.nth(i).is_visible()]
    if visible:
        first = action.nth(visible[0])
        box = first.bounding_box()
        width = page.viewport_size["width"]
        if box and (box["x"] < 0 or box["x"] + box["width"] > width + 1):
            return [f"主操作「{first.inner_text().strip()}」有一部分在屏幕外"]
        return []
    return [f"状态卡片里没有可见的主操作，卡片上写着「{banner.inner_text().strip()[:60]}」"]


# What a reader always needs within reach, whatever the window is.
MAIN_TABS = ["阅读", "进度", "问题"]


def _tabs_check(page) -> list[str]:
    """Every main tab is on screen without scrolling sideways, and 更多 holds the rest."""
    problems: list[str] = []
    tabs = page.locator("nav[aria-label=这本书]")
    if tabs.count() == 0:
        return ["书页上没有标签栏"]
    width = page.viewport_size["width"]
    for label in MAIN_TABS + ["更多"]:
        item = tabs.get_by_text(re.compile(f"^{label}")).first
        if item.count() == 0 or not item.is_visible():
            problems.append(f"标签「{label}」不在标签栏里")
            continue
        box = item.bounding_box()
        if box and (box["x"] < 0 or box["x"] + box["width"] > width + 1):
            problems.append(f"标签「{label}」有一部分在屏幕外")
    return problems


def _responsive_checks(browser, base: str, destinations: list[tuple[str, str]], shots: Path, *, reseed) -> list[str]:
    """Five widths × both themes × every page: no sideways scrolling, and a reachable main action."""
    problems: list[str] = []
    for theme in ("light", "dark"):
        context = browser.new_context(viewport={"width": WIDTHS[-1], "height": 900}, color_scheme=theme)
        page = context.new_page()
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            for name, path in destinations:
                if name == "book-issues":
                    reseed()
                page.goto(f"{base}{path}")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(250)
                for problem in _overflow(page):
                    problems.append(f"{name} {width}px {theme}: {problem}")
                if name.startswith("book-"):
                    for problem in _primary_action(page):
                        problems.append(f"{name} {width}px {theme}: {problem}")
                    for problem in _tabs_check(page):
                        problems.append(f"{name} {width}px {theme}: {problem}")
        context.close()
    return problems


# What only the tabs behind 更多 need. Opening a book must not ask for any of it.
# 进度 shows the run's milestones, so `events?...milestones=true` is not an advanced call;
# the raw 100-event tail is, and it belongs to whoever unfolds 技术细节.
ADVANCED_ENDPOINTS = {
    "术语与决策": ("/terms", "/book-guide"),
    "运行记录": ("/agent-turns", "events?limit=100"),
    "逐章审校": ("/chapters",),
}


def _lazy_tabs_check(page, base: str, document_id: str, seen: dict) -> list[str]:
    """Opening a book asks for nothing the advanced tabs need; opening one asks for its own."""
    problems: list[str] = []
    calls: list[str] = []
    page.on("request", lambda request: calls.append(request.url) if "/v1/" in request.url else None)

    # Both tabs a reader can land on, neither of which may fetch an advanced tab's data.
    for tab, label in (("read", "阅读"), ("progress", "进度")):
        calls.clear()
        page.goto(f"{base}/books/{document_id}/{tab}")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1_500)
        opening = [url.split("/v1/")[-1] for url in calls]
        seen[f"打开书页停在「{label}」"] = opening
        for advanced, endpoints in ADVANCED_ENDPOINTS.items():
            for endpoint in endpoints:
                hit = [call for call in opening if endpoint in call]
                if hit:
                    problems.append(f"停在「{label}」就请求了「{advanced}」的接口：{hit[0]}")

    page.goto(f"{base}/books/{document_id}/read")
    page.wait_for_load_state("networkidle")

    # And each advanced tab asks for its own data the moment it is opened.
    for label, endpoints in ADVANCED_ENDPOINTS.items():
        calls.clear()
        page.get_by_role("button", name=re.compile("^更多")).click()
        page.get_by_role("link", name=label).click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1_000)
        opened = [url.split("/v1/")[-1] for url in calls]
        seen[f"点开「{label}」"] = opened
        if not any(endpoint in call for endpoint in endpoints for call in opened):
            problems.append(f"点开「{label}」之后也没有请求它的接口（看到的是 {opened[:6]}）")
    return problems


def _theme_check(page, base: str, shots: Path) -> list[str]:
    """设置 → 界面配色 must override the system, and survive a reload."""
    problems: list[str] = []
    page.goto(f"{base}/settings")
    _settle(page)

    def background() -> str:
        return page.evaluate("getComputedStyle(document.body).backgroundColor")

    def brightness(colour: str) -> float:
        numbers = [float(n) for n in re.findall(r"[\d.]+", colour)[:3]]
        return sum(numbers) / 3 if numbers else 0.0

    light = background()
    if brightness(light) < 128:
        problems.append(f"系统是浅色，页面却是 {light}")

    page.get_by_label("界面配色").select_option("dark")
    page.wait_for_timeout(300)
    dark = background()
    if brightness(dark) > 100:
        problems.append(f"选了深色，背景仍是 {dark}")
    page.screenshot(path=str(shots / "settings-toggled-dark.png"))

    page.reload()
    _settle(page)
    if background() != dark:
        problems.append("刷新之后配色没有保持")

    page.get_by_label("界面配色").select_option("system")
    page.wait_for_timeout(300)
    if background() != light:
        problems.append("改回跟随系统后没有回到浅色")
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


def run(*, base: str, epub: Path, shots: Path, headed: bool, db: Path) -> dict:
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

        # The 问题 page is only worth checking with issues on it.
        seeded = _seed_issues(db, document_id)
        listed = page.request.get(f"{base}/v1/documents/{document_id}/issues?status=active").json()
        print(f"[look] 书 {document_id}：种了 {seeded} 条问题，接口按 active 看到 {listed.get('total_count')} 条", file=sys.stderr)
        if seeded == 0 or listed.get("total_count", 0) != seeded:
            raise AssertionError(f"种了 {seeded} 条问题，接口看到 {listed.get('total_count')} 条：{json.dumps(listed, ensure_ascii=False)[:400]}")

        destinations = [("home", "/"), ("library", "/library"), ("inbox", "/inbox"), ("settings", "/settings")]
        destinations += [(f"book-{tab}", f"/books/{document_id}/{tab}") for tab in BOOK_TABS]

        # Temporary: see exactly what the 问题 page asks for and gets back.
        page.on(
            "response",
            lambda response: print(f"[look] {response.status} {response.url}", file=sys.stderr)
            if "/v1/documents/" in response.url and "/issues" in response.url
            else None,
        )

        style_problems: list[str] = []
        for name, path in destinations:
            if name == "book-issues":
                _seed_issues(db, document_id)
            findings += _visit(page, base, path, name=name, shots=shots, theme="light", check=True)
            style_problems += [f"{name}: {problem}" for problem in _style_checks(page, name=name)]
            style_problems += _chinese_check(page, name=name)
            if name.startswith("book-"):
                style_problems += [f"{name}: {problem}" for problem in _sidebar_checks(page)]

        # Narrow: the tabs and the sidebar have to survive a phone-width window.
        page.set_viewport_size({"width": 420, "height": 860})
        _seed_issues(db, document_id)
        for name, path in [
            ("home", "/"),
            ("book-progress", f"/books/{document_id}/progress"),
            ("book-read", f"/books/{document_id}/read"),
            ("book-issues", f"/books/{document_id}/issues"),
        ]:
            findings += _visit(page, base, path, name=f"{name}-narrow", shots=shots, theme="light", check=True)
        page.set_viewport_size({"width": 1280, "height": 900})

        # ── The same pages in the dark ──
        dark = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme="dark")
        dark_page = dark.new_page()
        for name, path in destinations:
            if name == "book-issues":
                _seed_issues(db, document_id)
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
        theme_problems = _theme_check(keyboard_page, base, shots)
        lazy_page = keyboard.new_page()
        requests_seen: dict[str, list[str]] = {}
        theme_problems += _lazy_tabs_check(lazy_page, base, document_id, requests_seen)
        keyboard.close()

        responsive_problems = _responsive_checks(browser, base, destinations, shots, reseed=lambda: _seed_issues(db, document_id))

        context.close()
        browser.close()

    return {
        "violations": findings,
        "keyboard": keyboard_problems,
        "style": style_problems + theme_problems,
        "responsive": responsive_problems,
        "requests": requests_seen,
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

    # Under the repo, not the system temp dir: the exports table refuses a temp-dir path,
    # so a run whose workspace lives in $TMPDIR can never finish its exports.
    (ROOT / ".test-tmp").mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="look-", dir=ROOT / ".test-tmp"))
    dist = work / "dist"
    api = None
    try:
        _build_frontend(dist)
        api, port = _start_api(work, dist, priced=False)
        epub = _wordy_epub(_write_epub(work / "plain.epub"), work / "sample.epub")
        report = run(
            base=f"http://127.0.0.1:{port}",
            epub=epub,
            shots=args.screenshots.resolve(),
            headed=args.headed,
            db=work / "e2e.db",
        )
    finally:
        if api is not None:
            api.terminate()
            api.wait(timeout=30)
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = not report["violations"] and not report["keyboard"] and not report["style"] and not report["responsive"]
    print(
        "\n无障碍检查通过"
        if ok
        else (
            f"\n{len(report['violations'])} 项 axe 问题，{len(report['keyboard'])} 项键盘问题，"
            f"{len(report['style'])} 项样式问题，{len(report['responsive'])} 项版面问题"
        ),
        file=sys.stderr,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
