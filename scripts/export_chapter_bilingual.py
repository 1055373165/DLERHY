"""Export a chapter as a bilingual block-pair HTML for review.

Layout: every block in the chapter renders as one row containing two
columns — English source (left) and Chinese translation (right). When
zh translation is missing, the cell shows ``[未译]`` (per spec §决策3,
forbidden to fall back to source text in body positions).

This is the **review channel**, not the polished reading channel:
- Repair passes (bullet split, ordered list grouping, multi-panel
  cluster, figure bbox synthesis, callout glue) are intentionally
  NOT applied here — readers diff the bilingual against zh-only HTML
  to surface translation defects, while structural repairs are
  validated through the zh-only export's QA pipeline.
- Block boundaries are sacred: a block in DB is one ROW here. This
  is the block-pair grid invariant from spec §决策1.
- Untranslatable artifacts (image placeholders, code) are typed —
  they don't pretend to be translations.

Env vars (same convention as ``export_chapter_zh_html.py``):
    DOCUMENT_ID, CHAPTER_ID, ORDINAL_LO, ORDINAL_HI, OUTPUT_PATH,
    CHAPTER_LABEL, CHAPTER_TITLE, SOURCE_LABEL.

Writes ``OUTPUT_PATH`` (HTML) plus a sibling ``*.qa_report.json``
shaped like the zh-only QA report (so qa_run_chapter.sh can merge).
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from sqlalchemy import select

from book_agent.core.config import get_settings
from book_agent.domain.models import Block, Chapter, Document, Sentence
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope

# Reuse helpers + DB lookup from the zh-only exporter so the two channels
# stay in lockstep on what counts as a chunk, what's untranslatable, etc.
import export_chapter_zh_html as zhmod  # noqa: E402


DOCUMENT_ID = os.environ["DOCUMENT_ID"]
CHAPTER_ID = os.environ["CHAPTER_ID"]
ORDINAL_LO = int(os.environ["ORDINAL_LO"])
ORDINAL_HI = int(os.environ["ORDINAL_HI"])
OUTPUT_PATH = Path(os.environ["OUTPUT_PATH"])
CHAPTER_LABEL = os.environ.get("CHAPTER_LABEL", "")
CHAPTER_TITLE = os.environ.get("CHAPTER_TITLE", "")
SOURCE_LABEL = os.environ.get("SOURCE_LABEL", "")


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    max-width: 1200px;
    margin: 1.5rem auto;
    padding: 0 1.25rem 4rem;
    font-family: -apple-system, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    font-size: 15px;
    line-height: 1.7;
    color: #222;
    background: #fafafa;
  }}
  @media (prefers-color-scheme: dark) {{
    body {{ color: #ddd; background: #181818; }}
    h1, h2 {{ color: #f0f0f0; }}
    blockquote {{ background: #222; color: #aaa; }}
    .pair {{ border-color: rgba(255,255,255,.08); }}
    .pair[data-role="figure"] {{ background: #1d1d1d; }}
    .pair[data-role="heading"] {{ background: #1f2a3a; }}
    .pair .en {{ background: rgba(255,255,255,.02); }}
    .pair .zh {{ background: rgba(255,255,255,.04); }}
    .role-tag {{ background: #333; color: #aaa; }}
    .missing-translation {{ color: #d9a23b; background: rgba(217,162,59,.12); }}
    .untranslatable-stub {{ color: #888; }}
    code, pre {{ background: #1f1f1f; }}
  }}
  h1 {{ font-size: 1.8rem; margin: 0 0 .25rem; }}
  h1 small {{ display: block; font-size: .9rem; color: #666; font-weight: 400; margin-top: .15rem; }}
  blockquote {{
    margin: 1rem 0; padding: .55rem .9rem;
    border-left: 4px solid #888; background: #efefef;
    color: #555; font-size: .85rem; border-radius: 0 4px 4px 0;
  }}

  /* Block-pair grid: one row per DB block, two columns en | zh.
     Hard-aligned: row height grows with the taller column,
     never reflows. */
  .pair {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1.25rem;
    padding: .75rem 1rem;
    border-bottom: 1px solid rgba(127,127,127,.18);
    align-items: start;
  }}
  .pair[data-role="heading"] {{
    background: #eef4fb;
    font-weight: 600;
  }}
  .pair[data-role="figure"] {{
    background: #fff8ee;
    align-items: center;
  }}
  .pair[data-role="caption"] {{
    font-size: .9rem;
    font-style: italic;
    color: #555;
  }}
  .pair[data-role="code"] pre,
  .pair[data-role="listing"] pre {{
    background: #f3f3f3; padding: .6rem .8rem; border-radius: 4px;
    font-size: .85rem; overflow-x: auto; white-space: pre;
  }}
  .pair .en, .pair .zh {{
    padding: .25rem 0;
    word-wrap: break-word;
    overflow-wrap: anywhere;
  }}
  .pair .en {{ color: #1c2541; font-family: Georgia, serif; }}
  .pair .zh {{ color: #222; }}
  .role-tag {{
    display: inline-block; font-size: .7rem; padding: 1px 5px;
    margin-right: .35rem; vertical-align: middle;
    background: #d9e3ee; color: #345; border-radius: 3px;
    font-family: ui-monospace, monospace; font-weight: 500;
  }}
  .missing-translation {{
    color: #b08018; background: rgba(176,128,24,.10);
    padding: 0 4px; border-radius: 3px;
    font-size: .85rem; font-style: italic;
  }}
  .untranslatable-stub {{ color: #888; font-style: italic; font-size: .88rem; }}
  .pair img {{
    max-width: 100%; height: auto; border-radius: 4px;
    background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.08);
  }}
  footer {{
    margin-top: 2rem; padding-top: .75rem;
    border-top: 1px solid rgba(127,127,127,.2);
    font-size: .8rem; color: #777; text-align: center;
  }}
</style>
</head>
<body>
<h1>{chapter_label} {chapter_title} <small>{source_label} · 中英对照（review）</small></h1>
<blockquote>
共 {block_count} 个 block；翻译覆盖率 {coverage_pct}%（{translated}/{translatable_total} 可译块；{untranslatable} 个 untranslatable artifacts）。
</blockquote>
{body}
<footer>book-agent · bilingual review channel · 模型 {models_used}</footer>
</body>
</html>
"""


def _join_chunks(chunks: list[str]) -> str:
    """Join translated chunks with CJK-aware spacing (mirrors zh exporter)."""
    if not chunks:
        return ""
    cleaned = [c.strip() for c in chunks if c and c.strip()]
    if not cleaned:
        return ""
    out = [cleaned[0]]
    for cur in cleaned[1:]:
        prev = out[-1]
        prev_last = prev[-1] if prev else ""
        cur_first = cur[:1] if cur else ""

        def _is_cjk(ch: str) -> bool:
            if not ch:
                return False
            cp = ord(ch)
            return (
                0x4E00 <= cp <= 0x9FFF
                or 0x3000 <= cp <= 0x303F
                or 0xFF00 <= cp <= 0xFFEF
                or 0x3400 <= cp <= 0x4DBF
            )

        sep = "" if _is_cjk(prev_last) or _is_cjk(cur_first) else " "
        out.append(sep + cur)
    return "".join(out)


def _is_untranslatable_block(block, session) -> bool:
    """True if the block isn't a translation candidate.

    A block is untranslatable when EITHER:
    - its type is figure/image/code/equation (structurally non-text), OR
    - every Sentence under it has ``translatable=False`` (page headers,
      chapter labels, decorative artifacts).
    """
    btype = (block.block_type or "").lower()
    if btype in {"figure", "image", "code", "code_block", "equation"}:
        return True
    sents = session.execute(
        select(Sentence).where(Sentence.block_id == block.id)
    ).scalars().all()
    if not sents:
        return True
    return all(not s.translatable for s in sents)


def _role_tag(block) -> str:
    btype = (block.block_type or "").lower()
    return btype or "block"


def _block_role_for_html(block) -> str:
    """The role attribute on the .pair div drives CSS styling."""
    btype = (block.block_type or "").lower()
    if btype in {"figure", "image"}:
        return "figure"
    if btype in {"caption", "figure_caption"}:
        return "caption"
    if btype == "heading":
        return "heading"
    if btype in {"code", "code_block"}:
        return "code"
    return "paragraph"


def _render_pair(block, en_html_inner: str, zh_html_inner: str) -> str:
    role = _block_role_for_html(block)
    role_tag = _role_tag(block)
    return (
        f'<div class="pair" data-pair-id="{block.id}" data-role="{role}" data-ord="{block.ordinal}">\n'
        f'  <div class="en"><span class="role-tag">{role_tag}#{block.ordinal}</span>{en_html_inner}</div>\n'
        f'  <div class="zh">{zh_html_inner}</div>\n'
        f'</div>'
    )


def main() -> int:
    settings = get_settings()
    engine = build_engine(database_url=settings.database_url)
    factory = build_session_factory(engine=engine)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    body_parts: list[str] = []
    total_blocks = 0
    translatable_total = 0
    translated_blocks = 0
    untranslatable_blocks = 0
    missing_translations = 0
    image_render_count = 0

    with session_scope(factory) as session:
        document = session.get(Document, DOCUMENT_ID)
        chapter = session.get(Chapter, CHAPTER_ID)
        if document is None or chapter is None:
            raise SystemExit("Document or chapter not found")

        models_used = zhmod._models_used_for_range(session) if hasattr(zhmod, "_models_used_for_range") else "deepseek"

        blocks = (
            session.execute(
                select(Block)
                .where(Block.chapter_id == CHAPTER_ID)
                .where(Block.ordinal >= ORDINAL_LO)
                .where(Block.ordinal <= ORDINAL_HI)
                .order_by(Block.ordinal.asc())
            )
            .scalars()
            .all()
        )
        total_blocks = len(blocks)

        for block in blocks:
            btype = (block.block_type or "").lower()
            src_text = (block.source_text or "").strip()

            # ---- left column: English source ----
            if btype in {"figure", "image"}:
                # Reuse zh exporter's image renderer so the bilingual sees
                # the same crops and figcaptions readers see in zh-only.
                data_uri, alt_text, skip_reason = zhmod._block_image_data(
                    session, block
                )
                if data_uri:
                    image_render_count += 1
                    en_inner = f'<img src="{data_uri}" alt="" loading="lazy">'
                    if alt_text:
                        en_inner += f'<br><span class="caption-source">{html.escape(alt_text)}</span>'
                else:
                    en_inner = '<span class="untranslatable-stub">[图未渲染]</span>'
            elif btype in {"code", "code_block"}:
                en_inner = f"<pre><code>{html.escape(src_text)}</code></pre>"
            elif btype == "heading":
                en_inner = f"<strong>{html.escape(src_text)}</strong>"
            else:
                en_inner = html.escape(src_text).replace("\n", "<br>")

            # ---- right column: Chinese translation ----
            if _is_untranslatable_block(block, session):
                untranslatable_blocks += 1
                if btype in {"figure", "image"}:
                    # Pull caption translation if available so the reader
                    # gets the figure description in Chinese.
                    cap_zh = ""
                    linked = zhmod._linked_caption_anchor(block)
                    if linked:
                        cap = session.execute(
                            select(Block)
                            .where(Block.chapter_id == CHAPTER_ID)
                            .where(Block.source_anchor == linked)
                        ).scalars().first()
                        if cap is not None:
                            cap_chunks, _ = zhmod._block_zh_chunks(session, cap)
                            cap_zh = _join_chunks(cap_chunks)
                    if cap_zh:
                        zh_inner = f'<span class="caption-zh">{html.escape(cap_zh)}</span>'
                    else:
                        zh_inner = '<span class="untranslatable-stub">[图：见左侧]</span>'
                elif btype in {"code", "code_block"}:
                    zh_inner = '<span class="untranslatable-stub">[代码块不翻译]</span>'
                else:
                    zh_inner = '<span class="untranslatable-stub">[未译标记]</span>'
            else:
                translatable_total += 1
                chunks, _untrans = zhmod._block_zh_chunks(session, block)
                zh_text = _join_chunks(chunks)
                if zh_text:
                    translated_blocks += 1
                    zh_inner = html.escape(zh_text).replace("\n", "<br>")
                    if btype == "heading":
                        zh_inner = f"<strong>{zh_inner}</strong>"
                else:
                    missing_translations += 1
                    src_excerpt = html.escape(src_text[:80])
                    zh_inner = (
                        f'<span class="missing-translation" '
                        f'data-source="{src_excerpt}">[未译]</span>'
                    )

            body_parts.append(_render_pair(block, en_inner, zh_inner))

    coverage_pct = (
        round(100.0 * translated_blocks / max(translatable_total, 1), 1)
        if translatable_total
        else 0.0
    )

    # qa_report shape mirrors zh exporter so qa_run merger works unchanged.
    qa_report = {
        "output_path": str(OUTPUT_PATH),
        "chapter": {"label": CHAPTER_LABEL, "title": CHAPTER_TITLE},
        "ordinal_range": [ORDINAL_LO, ORDINAL_HI],
        "channel": "bilingual",
        "totals": {
            "total_blocks": total_blocks,
            "rendered_blocks": total_blocks,
            "untranslated_blocks": missing_translations,
            "untranslatable_blocks": untranslatable_blocks,
            "images_rendered": image_render_count,
            "images_skipped": 0,
            "translation_coverage_pct": coverage_pct,
        },
        "repair_stats": {},
        "repair_details": {},
        "image_skip_reasons": {},
        "warnings": (
            [f"missing_translations={missing_translations}"]
            if missing_translations
            else []
        ),
        "errors": (
            ["coverage_below_95pct"]
            if translatable_total and coverage_pct < 95.0
            else []
        ),
        "models_used": models_used,
    }

    output = HTML_TEMPLATE.format(
        title=html.escape(f"{CHAPTER_LABEL} {CHAPTER_TITLE} — {SOURCE_LABEL}"),
        chapter_label=html.escape(CHAPTER_LABEL),
        chapter_title=html.escape(CHAPTER_TITLE),
        source_label=html.escape(SOURCE_LABEL),
        block_count=total_blocks,
        translatable_total=translatable_total,
        translated=translated_blocks,
        untranslatable=untranslatable_blocks,
        coverage_pct=coverage_pct,
        models_used=html.escape(models_used or "deepseek"),
        body="\n".join(body_parts),
    )
    OUTPUT_PATH.write_text(output, encoding="utf-8")
    qa_path = OUTPUT_PATH.with_name(OUTPUT_PATH.stem + ".qa_report.json")
    qa_path.write_text(
        json.dumps(qa_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"[bilingual] wrote {OUTPUT_PATH}")
    print(f"[bilingual] wrote {qa_path}")
    print(
        f"[bilingual] blocks={total_blocks} translatable={translatable_total} "
        f"translated={translated_blocks} untranslatable={untranslatable_blocks} "
        f"missing={missing_translations} coverage={coverage_pct}%"
    )

    qa_strict = os.getenv("QA_STRICT", "0").strip() in {"1", "true", "yes", "on"}
    if qa_strict and qa_report["errors"]:
        print("[qa] STRICT MODE: failing due to coverage", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
