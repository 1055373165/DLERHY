"""Export Chapter 1 of the LLM-book smoke test to Chinese-only Markdown.

Document: d71027f0-6537-58d1-8e47-42ef2834fca4
Chapter container: de30483c-ec5f-5d3d-a728-69de943db663
Chapter 1 block ordinal range: 34..129 (inclusive)

The recovery service lumped multiple narrative chapters into one DB chapter
in this PDF, so the standard export_document_merged_markdown export gate
fails (most blocks aren't translated). Instead we pull translated segments
directly via SQL, in block/sentence order, and emit a Chinese Markdown
file that mirrors block-level structure (headings, paragraphs, code,
captions). Per project memory: user wants Chinese Markdown, not bilingual
or PDF-in-place.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sqlalchemy import select

from book_agent.core.config import get_settings
from book_agent.domain.enums import TargetSegmentStatus
from book_agent.domain.models import Block, Chapter, Document, Sentence
from book_agent.domain.models.translation import (
    AlignmentEdge,
    TargetSegment,
    TranslationRun,
)
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope


DOCUMENT_ID = "d71027f0-6537-58d1-8e47-42ef2834fca4"
CHAPTER_ID = "de30483c-ec5f-5d3d-a728-69de943db663"
ORDINAL_LO = 34
ORDINAL_HI = 129
OUTPUT_PATH = ROOT / ".test-tmp" / "ch1-export" / "chapter1-zh.md"


def _block_zh_text(session, block) -> str:
    rows = session.execute(
        select(Sentence.id, Sentence.ordinal_in_block, Sentence.source_text)
        .where(Sentence.block_id == block.id)
        .where(Sentence.translatable.is_(True))
        .order_by(Sentence.ordinal_in_block.asc())
    ).all()
    if not rows:
        return ""
    parts: list[str] = []
    for sentence_id, _, source_text in rows:
        target = session.execute(
            select(TargetSegment.text_zh, TargetSegment.final_status, TargetSegment.ordinal)
            .join(AlignmentEdge, AlignmentEdge.target_segment_id == TargetSegment.id)
            .where(AlignmentEdge.sentence_id == sentence_id)
            .where(TargetSegment.final_status != TargetSegmentStatus.SUPERSEDED)
            .order_by(TargetSegment.ordinal.asc())
            .limit(1)
        ).first()
        if target is not None and target[0]:
            parts.append(target[0])
        else:
            parts.append(f"[未翻译: {source_text}]")
    return "\n".join(parts)


def _render_block_md(block, zh_text: str) -> str:
    btype = (block.block_type or "paragraph").lower()
    if not zh_text:
        return ""
    if btype == "heading":
        # 简单按字数选择标题级别 — 启发式但够用
        return f"\n## {zh_text.strip()}\n"
    if btype == "image":
        return ""
    if btype in {"code", "code_block"}:
        return f"\n```\n{zh_text}\n```\n"
    if btype in {"caption", "figure_caption"}:
        return f"\n*{zh_text.strip()}*\n"
    return f"\n{zh_text}\n"


def main() -> int:
    settings = get_settings()
    engine = build_engine(database_url=settings.database_url)
    factory = build_session_factory(engine=engine)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    rendered: list[str] = []
    untranslated_block_count = 0
    rendered_block_count = 0
    with session_scope(factory) as session:
        document = session.get(Document, DOCUMENT_ID)
        chapter = session.get(Chapter, CHAPTER_ID)
        if document is None or chapter is None:
            raise SystemExit(f"Document or chapter not found")
        rendered.append(
            f"# 第 1 章: 大语言模型的全貌\n\n"
            f"> 来源: {document.title_src or document.source_path}\n"
            f"> 译文范围: 章节内段落 {ORDINAL_LO}..{ORDINAL_HI}\n"
        )
        blocks = session.execute(
            select(Block)
            .where(Block.chapter_id == CHAPTER_ID)
            .where(Block.ordinal >= ORDINAL_LO)
            .where(Block.ordinal <= ORDINAL_HI)
            .order_by(Block.ordinal.asc())
        ).scalars().all()
        for block in blocks:
            zh = _block_zh_text(session, block)
            if not zh:
                untranslated_block_count += 1
                continue
            md = _render_block_md(block, zh)
            if md:
                rendered.append(md)
                rendered_block_count += 1

    output = "\n".join(rendered).strip() + "\n"
    OUTPUT_PATH.write_text(output, encoding="utf-8")
    print(
        f"[export] wrote {OUTPUT_PATH}\n"
        f"[export] rendered_blocks={rendered_block_count} "
        f"untranslated_skipped={untranslated_block_count} "
        f"total_blocks_in_range={rendered_block_count + untranslated_block_count}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
