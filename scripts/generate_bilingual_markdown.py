#!/usr/bin/env python3
"""Generate bilingual markdown deliverables from translated chapters."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime

STATE_JSON = Path("artifacts/review/translate-agent-rollout-state-current.json")
OUTPUT_DIR = Path("artifacts/review/bilingual-markdown-deliverables")

FRONTMATTER_PATTERNS = (
    "about this book", "about the author", "acknowledg", "bibliography",
    "contents", "copyright", "cover", "dedication", "foreword", "glossary",
    "imprint", "index", "introduction to this edition", "list of figures",
    "list of tables", "table of contents", "title page",
    "about the cover", "colophon", "references", "appendix",
)


def is_frontmatter(title: str) -> bool:
    if not title:
        return True
    t = title.strip().lower()
    if any(p in t for p in FRONTMATTER_PATTERNS):
        return True
    if t.startswith("part ") and len(t.split()) <= 8:
        return True
    return False


def extract_chapter_bilingual(conn: sqlite3.Connection, chapter_id: str) -> list[dict]:
    """Extract bilingual block pairs for a chapter, ordered by block ordinal."""
    # Get blocks with source text
    blocks = conn.execute('''
        SELECT b.ordinal, b.block_type, b.source_text
        FROM blocks b
        WHERE b.chapter_id = ?
        ORDER BY b.ordinal
    ''', (chapter_id,)).fetchall()

    # Get translations via alignment_edges (sentence→target_segment mapping)
    # Dedup by (block_ordinal, ts_id) to avoid repeats from many-to-many joins
    segments = conn.execute('''
        SELECT
            b.ordinal as block_ordinal,
            s.ordinal_in_block,
            ts.ordinal as ts_ordinal,
            ts.text_zh as zh,
            ts.segment_type,
            ts.id as ts_id
        FROM alignment_edges ae
        JOIN sentences s ON s.id = ae.sentence_id
        JOIN blocks b ON b.id = s.block_id
        JOIN target_segments ts ON ts.id = ae.target_segment_id
        JOIN translation_runs tr ON tr.id = ts.translation_run_id
        WHERE s.chapter_id = ?
          AND ts.final_status != 'superseded'
          AND tr.status = 'succeeded'
        ORDER BY b.ordinal, ts.ordinal, s.ordinal_in_block
    ''', (chapter_id,)).fetchall()

    # Dedup: keep unique (block_ordinal, ts_id) pairs
    block_translations: dict[int, list[dict]] = {}
    seen_keys: set[tuple[int, str]] = set()
    for seg in segments:
        bord = seg[0]
        ts_id = seg[5]
        key = (bord, ts_id)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if bord not in block_translations:
            block_translations[bord] = []
        block_translations[bord].append({
            "zh": seg[3],
            "type": seg[4],
        })

    # Also try getting translations from output_json for chapters where alignment_edges may be empty
    if not segments:
        tr_data = conn.execute('''
            SELECT tr.output_json, tp.chapter_id
            FROM translation_runs tr
            JOIN translation_packets tp ON tp.id = tr.packet_id
            WHERE tp.chapter_id = ?
              AND tr.status = 'succeeded'
              AND tr.output_json IS NOT NULL
            ORDER BY tp.created_at
        ''', (chapter_id,)).fetchall()

        for tr_row in tr_data:
            try:
                output = json.loads(tr_row[0])
                for seg in output.get("target_segments", []):
                    text_zh = seg.get("text_zh", "")
                    seg_type = seg.get("segment_type", "sentence")
                    if text_zh:
                        # We don't know the exact block, append to a running list
                        if -1 not in block_translations:
                            block_translations[-1] = []
                        block_translations[-1].append({
                            "src": "",
                            "zh": text_zh,
                            "type": seg_type,
                        })
            except (json.JSONDecodeError, KeyError):
                pass

    result = []
    for b_ord, b_type, b_src in blocks:
        trans = block_translations.get(b_ord, [])
        zh_text = "\n\n".join(t["zh"] for t in trans if t["zh"]) if trans else ""
        result.append({
            "ordinal": b_ord,
            "block_type": b_type,
            "source_text": b_src,
            "target_text": zh_text,
            "has_translation": bool(zh_text),
        })

    # If all translations were in the -1 bucket (no alignment), create a merged block
    if -1 in block_translations and not any(block_translations.get(b[0], []) for b in blocks):
        fallback_zh = "\n\n".join(t["zh"] for t in block_translations[-1] if t["zh"])
        if fallback_zh and result:
            # Attach all translations to the blocks proportionally
            # Simple approach: combine all source and all target
            all_src = "\n\n".join(b["source_text"] for b in result if b["source_text"])
            result = [{
                "ordinal": 0,
                "block_type": "merged",
                "source_text": all_src,
                "target_text": fallback_zh,
                "has_translation": True,
            }]

    return result


def generate_chapter_markdown(blocks: list[dict], chapter_title: str, ordinal: int) -> str:
    """Generate bilingual markdown for a chapter."""
    lines = [f"## 第{ordinal}章: {chapter_title}\n"]

    for block in blocks:
        if not block["source_text"] and not block["target_text"]:
            continue

        bt = block["block_type"]

        if bt == "heading":
            if block["target_text"]:
                lines.append(f"### {block['target_text']}")
                lines.append(f"*{block['source_text']}*\n")
            else:
                lines.append(f"### {block['source_text']}\n")
        elif bt in ("code", "artifact"):
            lines.append("```")
            lines.append(block["source_text"])
            lines.append("```\n")
        else:
            # Paragraph, list_item, quote, etc.
            if block["target_text"]:
                lines.append(block["target_text"])
                lines.append("")
                lines.append(f"> **原文:** {block['source_text']}")
                lines.append("")
            else:
                lines.append(f"*[未翻译]* {block['source_text']}")
                lines.append("")

    return "\n".join(lines)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    state = json.loads(STATE_JSON.read_text())
    books = state["books"]

    summary_lines = ["# 翻译审查 - 前三章双语对照\n"]
    summary_lines.append(f"生成时间: {datetime.now().isoformat()}\n")

    for b in books:
        qi = b["queue_index"]
        ls = b.get("live_state") or {}
        bs = b.get("benchmark_state") or {}
        root = ls.get("root", "")
        verdict = bs.get("verdict", "unknown")

        if not root or verdict == "no_go":
            summary_lines.append(f"## [{qi}] SKIPPED ({verdict})\n")
            continue

        db_path = os.path.join(root, "book-agent.db")
        if not os.path.exists(db_path):
            continue

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        doc = conn.execute("SELECT title, title_src FROM documents LIMIT 1").fetchone()
        book_title = doc["title_src"] or doc["title"] or f"Book-{qi}"

        # Get chapters
        chapters = conn.execute('''
            SELECT c.id, c.ordinal, c.title_src,
                   COUNT(tp.id) as total_packets,
                   SUM(CASE WHEN tp.status = 'translated' THEN 1 ELSE 0 END) as translated
            FROM chapters c
            LEFT JOIN translation_packets tp ON tp.chapter_id = c.id
            GROUP BY c.id
            ORDER BY c.ordinal
        ''').fetchall()

        # Find fully translated content chapters
        translated_chapters = []
        for ch in chapters:
            total = ch["total_packets"]
            trans = ch["translated"]
            built_remaining = total - trans
            is_fully_translated = built_remaining == 0 and total > 0
            if is_fully_translated:
                translated_chapters.append(ch)

        if not translated_chapters:
            summary_lines.append(f"## [{qi}] {book_title}: 无已翻译章节\n")
            conn.close()
            continue

        # Generate markdown file for this book
        safe_name = f"book-{qi:02d}-{book_title[:40].replace('/', '-').replace(' ', '-')}"
        md_path = OUTPUT_DIR / f"{safe_name}.md"

        md_lines = [f"# {book_title}\n"]
        md_lines.append(f"翻译模型: DeepSeek Chat | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        md_lines.append("---\n")

        chapter_count = 0
        for ch in translated_chapters:
            blocks = extract_chapter_bilingual(conn, ch["id"])
            translated_blocks = sum(1 for bl in blocks if bl["has_translation"])
            total_blocks = len(blocks)

            md_lines.append(f"\n# CH.{ch['ordinal']}: {ch['title_src']}")
            md_lines.append(f"*翻译覆盖: {translated_blocks}/{total_blocks} blocks*\n")

            for block in blocks:
                if not block["source_text"] and not block["target_text"]:
                    continue

                bt = block["block_type"]
                if bt == "heading":
                    if block["target_text"]:
                        md_lines.append(f"\n### {block['target_text']}")
                        md_lines.append(f"*{block['source_text']}*")
                    else:
                        md_lines.append(f"\n### {block['source_text']}")
                    md_lines.append("")
                elif bt in ("code", "artifact"):
                    md_lines.append("\n```")
                    md_lines.append(block["source_text"])
                    md_lines.append("```")
                    md_lines.append("")
                else:
                    if block["target_text"]:
                        md_lines.append(f"")
                        md_lines.append(block["target_text"])
                        md_lines.append("")
                        # Format source as blockquote with line breaks for readability
                        src = block["source_text"].replace("\n", "\n> ")
                        md_lines.append(f"> **原文:**")
                        md_lines.append(f"> ")
                        md_lines.append(f"> {src}")
                        md_lines.append("")
                        md_lines.append("---")
                        md_lines.append("")
                    else:
                        md_lines.append(f"\n*[未翻译]* {block['source_text']}")
                        md_lines.append("")

            chapter_count += 1

        md_content = "\n".join(md_lines)
        md_path.write_text(md_content, encoding="utf-8")

        summary_lines.append(f"## [{qi}] {book_title}")
        summary_lines.append(f"- 已翻译章节: {chapter_count}")
        summary_lines.append(f"- 文件: `{md_path.name}`")
        summary_lines.append(f"- 大小: {len(md_content) // 1024}KB\n")

        conn.close()

    # Write summary index
    summary_path = OUTPUT_DIR / "INDEX.md"
    summary_path.write_text("\n".join(summary_lines), encoding="utf-8")
    print(f"Generated files in: {OUTPUT_DIR}")
    print(f"Index: {summary_path}")

    # List generated files
    for f in sorted(OUTPUT_DIR.iterdir()):
        if f.suffix == ".md":
            print(f"  {f.name} ({f.stat().st_size // 1024}KB)")


if __name__ == "__main__":
    main()
