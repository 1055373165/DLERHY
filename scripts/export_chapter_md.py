"""Export a chapter as Chinese-primary Markdown with foldable English source.

Mirrors the legacy merged-markdown rendering before commit 06ff3fc
stripped the <details>/<summary> source toggle: each translatable
block emits its Chinese translation as the visible body, with the
English source folded inside <details><summary>英文原文</summary>.

Figures are rendered as caption-only references — no image crop —
because the user's review intent for markdown is to validate text and
structure, and image-bbox tuning is handled separately in the HTML
exporter (per project history "我们之前已经一点点优化了 Pdf 图片的裁剪方式").

Env vars:
    DOCUMENT_ID, CHAPTER_ID, ORDINAL_LO, ORDINAL_HI, OUTPUT_PATH,
    CHAPTER_LABEL, CHAPTER_TITLE, SOURCE_LABEL.
"""
from __future__ import annotations

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

from sqlalchemy import select  # noqa: E402

from book_agent.core.config import get_settings  # noqa: E402
from book_agent.domain.models import Block, Chapter, Document, Sentence  # noqa: E402
from book_agent.infra.db.session import (  # noqa: E402
    build_engine,
    build_session_factory,
    session_scope,
)

import export_chapter_zh_html as zhmod  # noqa: E402


DOCUMENT_ID = os.environ["DOCUMENT_ID"]
CHAPTER_ID = os.environ["CHAPTER_ID"]
ORDINAL_LO = int(os.environ["ORDINAL_LO"])
ORDINAL_HI = int(os.environ["ORDINAL_HI"])
OUTPUT_PATH = Path(os.environ["OUTPUT_PATH"])
CHAPTER_LABEL = os.environ.get("CHAPTER_LABEL", "")
CHAPTER_TITLE = os.environ.get("CHAPTER_TITLE", "")
SOURCE_LABEL = os.environ.get("SOURCE_LABEL", "")


def _join_chunks(chunks: list[str]) -> str:
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


def _is_translatable_block(block, session) -> bool:
    btype = (block.block_type or "").lower()
    if btype in {"figure", "image", "code", "code_block", "equation"}:
        return False
    sents = (
        session.execute(select(Sentence).where(Sentence.block_id == block.id))
        .scalars()
        .all()
    )
    return bool(sents) and any(s.translatable for s in sents)


def _details_source(text: str) -> str:
    """Mirror legacy ExportService._markdown_details_source."""
    normalized = (text or "").strip()
    if not normalized:
        return ""
    return f"<details>\n<summary>英文原文</summary>\n\n{normalized}\n\n</details>"


def _render_block_md(block, session) -> str:
    """Render one block to markdown, mirroring legacy _render_block_markdown.

    Translatable blocks: visible Chinese body + folded <details> with
    English source. Untranslatable artifacts (figures / code / page
    headers) get a caption-or-stub line so structure stays visible.
    """
    btype = (block.block_type or "").lower()
    src_text = (block.source_text or "").strip()

    # Figures: caption-only reference. NO image embed.
    if btype in {"figure", "image"}:
        cap_en = ""
        cap_zh = ""
        linked = zhmod._linked_caption_anchor(block)
        if linked:
            cap = (
                session.execute(
                    select(Block)
                    .where(Block.chapter_id == CHAPTER_ID)
                    .where(Block.source_anchor == linked)
                )
                .scalars()
                .first()
            )
            if cap is not None:
                cap_en = (cap.source_text or "").strip()
                cap_chunks, _ = zhmod._block_zh_chunks(session, cap)
                cap_zh = _join_chunks(cap_chunks)
        if cap_zh:
            parts = [f"*图：{cap_zh}*"]
            if cap_en and cap_en != cap_zh:
                parts.append(_details_source(cap_en))
            return "\n\n".join(parts)
        if cap_en:
            return f"*图：{cap_en}*"
        return "*（图，无配文）*"

    # Code: keep verbatim source as fenced block.
    if btype in {"code", "code_block"}:
        return f"```\n{src_text}\n```"

    # Equations: pass through as inline fence.
    if btype == "equation":
        return f"$$\n{src_text}\n$$"

    # Page header / chapter label artifacts: drop silently — mirrors
    # the merged-markdown decision to skip these chunks (they're not
    # part of reading flow).
    if not _is_translatable_block(block, session):
        return ""

    # Translatable: zh body + folded en source.
    chunks, _untrans = zhmod._block_zh_chunks(session, block)
    zh_text = _join_chunks(chunks)

    if btype == "heading":
        # Headings render as h3 with zh text; en source folded.
        if zh_text:
            parts = [f"### {zh_text}"]
        else:
            parts = [f"### {src_text}"]
        if zh_text and src_text and src_text != zh_text:
            parts.append(_details_source(src_text))
        return "\n\n".join(parts)

    if btype in {"caption", "figure_caption"}:
        body = f"*{zh_text or src_text}*"
        parts = [body]
        if zh_text and src_text and src_text != zh_text:
            parts.append(_details_source(src_text))
        return "\n\n".join(parts)

    if btype == "list_item":
        body = zh_text or src_text
        marker = "" if re.match(r"^\s*(?:[-*+]\s+|\d+\.\s+)", body) else "- "
        parts = [f"{marker}{body}".rstrip()]
        if zh_text and src_text and src_text != zh_text:
            parts.append(_details_source(src_text))
        return "\n\n".join(parts)

    if btype == "quote":
        lines = [f"> {ln}" for ln in (zh_text or src_text).splitlines() if ln.strip()]
        parts = ["\n".join(lines)]
        if zh_text and src_text and src_text != zh_text:
            parts.append(_details_source(src_text))
        return "\n\n".join(parts)

    # Default: paragraph.
    if zh_text:
        parts = [zh_text]
    else:
        # No zh translation — shouldn't happen for translatable blocks
        # we've reached here, but mark explicitly.
        parts = [f"*[未译]* {src_text}"]
    if zh_text and src_text and src_text != zh_text:
        parts.append(_details_source(src_text))
    return "\n\n".join(parts)


def main() -> int:
    settings = get_settings()
    engine = build_engine(database_url=settings.database_url)
    factory = build_session_factory(engine=engine)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    total = 0
    translatable_total = 0
    translated = 0
    skipped_artifact = 0

    with session_scope(factory) as session:
        document = session.get(Document, DOCUMENT_ID)
        chapter = session.get(Chapter, CHAPTER_ID)
        if document is None or chapter is None:
            raise SystemExit("Document or chapter not found")

        models_used = (
            zhmod._models_used_for_range(session)
            if hasattr(zhmod, "_models_used_for_range")
            else "deepseek"
        )

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
        total = len(blocks)

        # Top heading (single ## per the merged-md convention).
        lines.append(f"## {CHAPTER_LABEL}: {CHAPTER_TITLE}")
        lines.append("")

        for block in blocks:
            md = _render_block_md(block, session)
            if not md:
                skipped_artifact += 1
                continue
            if _is_translatable_block(block, session):
                translatable_total += 1
                # crude check: zh body present iff first part isn't *[未译]*
                if "*[未译]*" not in md.split("\n\n", 1)[0]:
                    translated += 1
            lines.append(md)
            lines.append("")

    output = "\n".join(lines).rstrip() + "\n"
    OUTPUT_PATH.write_text(output, encoding="utf-8")

    coverage = (
        round(100.0 * translated / max(translatable_total, 1), 1)
        if translatable_total
        else 0.0
    )
    print(f"[md] wrote {OUTPUT_PATH}")
    print(
        f"[md] blocks={total} translatable={translatable_total} "
        f"translated={translated} skipped_artifact={skipped_artifact} "
        f"coverage={coverage}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
