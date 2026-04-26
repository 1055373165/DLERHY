"""Diagnose Figure 3.2 → 3.3 caption mismatch in Chapter 3.

Reads the existing DB state for chapter `de30483c-...` (Chapter 3) and
prints, for every IMAGE/FIGURE/CAPTION block in reading order:
- ordinal, block_type, source_anchor, page
- The linked_caption_text on image/figure blocks
- The linked_caption_block_id (so we can match figures to captions)
- The first 100 chars of the block's source_text (i.e. the actual
  caption text or alt-text)

Goal: identify which figure's `linked_caption_text` points at "Figure 3.3"
when the visually-correct caption block is "Figure 3.2: ..."
"""
# ruff: noqa: E402
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

for _v in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_v, None)

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.config import get_settings
from book_agent.infra.db.session import build_engine
from book_agent.domain.models import Block, DocumentImage

CHAPTER_ID = os.environ.get("CHAPTER_ID", "de30483c-ec5f-5d3d-a728-69de943db663")


def main() -> int:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    with Session(engine) as session:
        blocks = session.execute(
            select(Block)
            .where(Block.chapter_id == CHAPTER_ID)
            .order_by(Block.ordinal.asc())
        ).scalars().all()

        relevant = [b for b in blocks if b.block_type.value in {"image", "figure", "caption"}]
        print(f"[diag] chapter_id={CHAPTER_ID}  blocks total={len(blocks)}  image/figure/caption={len(relevant)}")
        print()
        print(f"{'ord':>4} {'type':<8} {'page':>4} {'anchor':<28} {'linked_to':<28} | source_text / linked_caption_text")
        print("-" * 140)

        for b in relevant:
            meta = b.source_metadata or {}
            page = meta.get("source_page_start") or "?"
            linked_anchor = meta.get("linked_caption_source_anchor") or meta.get("caption_for_source_anchor") or "-"
            preview = (b.source_text or "").strip().replace("\n", " ")[:90]
            linked_caption = (meta.get("linked_caption_text") or "").strip().replace("\n", " ")[:90]
            extra = f" linked_caption_text={linked_caption!r}" if linked_caption else ""
            print(
                f"{b.ordinal:>4} {b.block_type.value:<8} {str(page):>4} {b.source_anchor or '-':<28} "
                f"{linked_anchor:<28} | {preview!r}{extra}"
            )

        print()
        print("=== DocumentImage rows (alt_text source for figcaption) ===")
        block_ids = [b.id for b in relevant if b.block_type.value in {"image", "figure"}]
        if block_ids:
            images = session.execute(
                select(DocumentImage).where(DocumentImage.block_id.in_(block_ids))
            ).scalars().all()
            for img in images:
                blk = next((b for b in relevant if b.id == img.block_id), None)
                ord_ = blk.ordinal if blk else "?"
                alt = (img.alt_text or "").strip().replace("\n", " ")[:100]
                print(f"  block_ordinal={ord_:>4} page={img.page_number}  alt_text={alt!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
