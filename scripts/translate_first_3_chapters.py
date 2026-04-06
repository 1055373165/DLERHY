#!/usr/bin/env python3
"""Translate the first 3 content chapters of each book using DeepSeek, then stop."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

STATE_JSON = Path("artifacts/review/translate-agent-rollout-state-current.json")
VENV_PYTHON = Path("/Users/smy/project/book-agent/.venv/bin/python")
SMOKE_SCRIPT = "scripts/run_pdf_chapter_smoke.py"
TARGET_CONTENT_CHAPTERS = 3

FRONTMATTER_PATTERNS = (
    "about this book", "about the author", "acknowledg", "bibliography",
    "contents", "copyright", "cover", "dedication", "foreword", "glossary",
    "imprint", "index", "introduction to this edition", "list of figures",
    "list of tables", "preface", "table of contents", "title page",
    "about the cover", "colophon", "references", "appendix",
)


def is_frontmatter(title: str) -> bool:
    if not title:
        return True
    t = title.strip().lower()
    if any(p in t for p in FRONTMATTER_PATTERNS):
        return True
    # Skip part headings like "Part 1 Getting started..."
    if t.startswith("part ") and len(t.split()) <= 8:
        return True
    return False


def get_chapter_status(db_path: str) -> list[dict]:
    """Get per-chapter translation status from a book's database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute('''
        SELECT c.ordinal, c.title_src,
               COUNT(tp.id) as total_packets,
               SUM(CASE WHEN tp.status = 'translated' THEN 1 ELSE 0 END) as translated,
               SUM(CASE WHEN tp.status = 'built' THEN 1 ELSE 0 END) as built
        FROM chapters c
        LEFT JOIN translation_packets tp ON tp.chapter_id = c.id
        GROUP BY c.id
        ORDER BY c.ordinal
    ''').fetchall()
    result = []
    for r in rows:
        result.append({
            "ordinal": r["ordinal"],
            "title_src": r["title_src"] or "",
            "total_packets": r["total_packets"],
            "translated": r["translated"],
            "built": r["built"],
            "is_frontmatter": is_frontmatter(r["title_src"]),
            "is_fully_translated": r["built"] == 0 and r["total_packets"] > 0,
        })
    conn.close()
    return result


def count_content_chapters_done(chapters: list[dict]) -> tuple[int, list[dict]]:
    """Count how many non-frontmatter chapters are fully translated."""
    done = 0
    content_chapters = [c for c in chapters if not c["is_frontmatter"] and c["total_packets"] > 0]
    for c in content_chapters:
        if c["is_fully_translated"]:
            done += 1
    return done, content_chapters


def get_next_untranslated_content_ordinal(chapters: list[dict]) -> int | None:
    """Find the ordinal of the next content chapter with built packets remaining."""
    for c in chapters:
        if not c["is_frontmatter"] and c["built"] > 0:
            return c["ordinal"]
    return None


def load_books() -> list[dict]:
    """Load book state from the rollout state JSON."""
    state = json.loads(STATE_JSON.read_text())
    books = []
    for b in state["books"]:
        qi = b["queue_index"]
        ls = b.get("live_state") or {}
        bs = b.get("benchmark_state") or {}
        root = ls.get("root", "")
        verdict = bs.get("verdict", "unknown")
        if not root or verdict == "no_go":
            books.append({"queue_index": qi, "root": None, "verdict": verdict, "skipped": True})
            continue
        db_path = os.path.join(root, "book-agent.db")
        if not os.path.exists(db_path):
            books.append({"queue_index": qi, "root": root, "verdict": verdict, "skipped": True, "reason": "db_missing"})
            continue
        books.append({
            "queue_index": qi,
            "root": root,
            "db_path": db_path,
            "verdict": verdict,
            "skipped": False,
            "source_path": b.get("path", ""),
        })
    return books


def run_chapter_translation(book: dict, chapter_ordinal: int, packet_limit: int | None = None) -> dict:
    """Run the chapter smoke script for a specific chapter."""
    root = book["root"]
    db_url = f"sqlite+pysqlite:///{os.path.join(root, 'book-agent.db')}"
    export_root = os.path.join(root, "exports")

    # Generate unique report path
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(root, f"report-ch{chapter_ordinal}-{ts}.json")

    cmd = [
        str(VENV_PYTHON), SMOKE_SCRIPT,
        "--source-path", book["source_path"],
        "--database-url", db_url,
        "--export-root", export_root,
        "--report-path", report_path,
        "--chapter-ordinal", str(chapter_ordinal),
        "--auto-lock-unlocked-concepts",
    ]
    if packet_limit:
        cmd.extend(["--packet-limit", str(packet_limit)])

    print(f"  CMD: {' '.join(cmd[-8:])}", flush=True)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=1800,  # 30 min timeout per chapter
        cwd="/Users/smy/project/book-agent",
    )

    # Parse last line of stdout for summary
    summary = {}
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line.startswith("{"):
            try:
                summary = json.loads(line)
            except json.JSONDecodeError:
                pass

    return {
        "chapter_ordinal": chapter_ordinal,
        "returncode": result.returncode,
        "report_path": report_path,
        "summary": summary,
        "stderr_tail": result.stderr[-500:] if result.stderr else "",
    }


def main():
    print("=" * 60)
    print("TRANSLATE FIRST 3 CONTENT CHAPTERS PER BOOK")
    print(f"Start: {datetime.now().isoformat()}")
    print("=" * 60)

    books = load_books()

    # Status overview
    for book in books:
        qi = book["queue_index"]
        if book.get("skipped"):
            print(f"[{qi}] SKIPPED ({book.get('verdict', '?')})")
            continue
        chapters = get_chapter_status(book["db_path"])
        done, content_chs = count_content_chapters_done(chapters)
        total_content = len(content_chs)
        print(f"[{qi}] {done}/{min(TARGET_CONTENT_CHAPTERS, total_content)} content chapters done | total_content_chapters={total_content}")
        for c in content_chs[:6]:
            status = "DONE" if c["is_fully_translated"] else f"{c['translated']}/{c['total_packets']}"
            print(f"     ch.{c['ordinal']:2d} [{status:>8s}] built={c['built']:3d} | {c['title_src'][:45]}")

    print()

    # Translation loop
    MAX_ROUNDS = 200
    for round_num in range(1, MAX_ROUNDS + 1):
        all_done = True
        any_work = False

        print(f"\n--- Round {round_num} @ {datetime.now().strftime('%H:%M:%S')} ---")

        for book in books:
            qi = book["queue_index"]
            if book.get("skipped"):
                continue

            chapters = get_chapter_status(book["db_path"])
            done, content_chs = count_content_chapters_done(chapters)
            target = min(TARGET_CONTENT_CHAPTERS, len(content_chs))

            if done >= target:
                print(f"  [{qi}] COMPLETE ({done}/{target} content chapters)")
                continue

            all_done = False

            # Find next content chapter to translate
            next_ordinal = get_next_untranslated_content_ordinal(chapters)
            if next_ordinal is None:
                print(f"  [{qi}] NO BUILT PACKETS (waiting for pipeline)")
                continue

            any_work = True

            # Find how many built packets remain in this chapter
            ch_info = next(c for c in chapters if c["ordinal"] == next_ordinal)
            print(f"  [{qi}] Translating ch.{next_ordinal} ({ch_info['built']} built pkts) | {ch_info['title_src'][:40]}")

            try:
                result = run_chapter_translation(book, next_ordinal, packet_limit=20)
                rc = result["returncode"]
                s = result["summary"]
                fully = s.get("fully_translated", False)
                print(f"  [{qi}] ch.{next_ordinal} rc={rc} fully_translated={fully}")
                if result["stderr_tail"]:
                    # Only print errors
                    for line in result["stderr_tail"].split("\n"):
                        if "error" in line.lower() or "traceback" in line.lower():
                            print(f"  [{qi}] ERR: {line[:100]}")
            except subprocess.TimeoutExpired:
                print(f"  [{qi}] TIMEOUT on ch.{next_ordinal}")
            except Exception as e:
                print(f"  [{qi}] ERROR: {e}")

        if all_done:
            print("\n=== ALL BOOKS COMPLETE (first 3 content chapters) ===")
            break

        if not any_work:
            print("\nNo work available this round, waiting...")

    # Final status
    print("\n" + "=" * 60)
    print("FINAL STATUS")
    print("=" * 60)
    for book in books:
        qi = book["queue_index"]
        if book.get("skipped"):
            print(f"[{qi}] SKIPPED ({book.get('verdict', '?')})")
            continue
        chapters = get_chapter_status(book["db_path"])
        done, content_chs = count_content_chapters_done(chapters)
        print(f"[{qi}] {done} content chapters fully translated")
        for c in content_chs[:6]:
            status = "DONE" if c["is_fully_translated"] else f"{c['translated']}/{c['total_packets']}"
            print(f"     ch.{c['ordinal']:2d} [{status:>8s}] | {c['title_src'][:45]}")

    print(f"\nEnd: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
