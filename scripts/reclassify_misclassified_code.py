"""Re-classify body paragraphs that were misidentified as CODE blocks.

Earlier parser revisions flagged any block mixing monospace tokens with
prose (e.g. body sentences that mention ``stock``, ``capital``, ``dict``)
as CODE. Those blocks then had ``Sentence.translatable=False`` flipped on
them and were skipped by the translator entirely — so the user sees a
gap in the export where the body paragraph between two figures should
sit. This script flips them back to PARAGRAPH so a follow-up
translation pass can fill them in, without re-ingesting the whole PDF.

Detection mirrors the export-side ``_looks_like_real_code`` rule: a
block is real code only when the source has multi-line indentation or
clear programming syntax. Single-line bodies and prose paragraphs that
incidentally include inline mono identifiers do not qualify.
"""
# ruff: noqa: E402
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

for _v in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_v, None)

from sqlalchemy import select, update

from book_agent.core.config import get_settings
from book_agent.domain.enums import BlockType
from book_agent.domain.models import Block, Sentence
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope


_REAL_CODE_LINE_PATTERN = re.compile(
    r"^\s{2,}\S"
    r"|^\s*(?:def|class|return|import|from|if|elif|else|for|while|try|except|finally|with|yield|raise|print|let|var|const|function)\b"
    r"|^\s*[A-Za-z_]\w*\s*[=(]"
    r"|^\s*#",
    re.MULTILINE,
)


def looks_like_real_code(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    lines = [ln for ln in s.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    indented_lines = sum(1 for ln in lines if ln.startswith(("  ", "\t")))
    if indented_lines >= 2:
        return True
    if _REAL_CODE_LINE_PATTERN.search(s):
        if indented_lines >= 1 or sum(
            1 for ln in lines if _REAL_CODE_LINE_PATTERN.match(ln)
        ) >= 2:
            return True
    return False


CHAPTER_ID = os.environ.get("CHAPTER_ID", "de30483c-ec5f-5d3d-a728-69de943db663")
ORDINAL_LO = int(os.environ.get("ORDINAL_LO", "0"))
ORDINAL_HI = int(os.environ.get("ORDINAL_HI", "10000"))


def main() -> int:
    settings = get_settings()
    engine = build_engine(database_url=settings.database_url)
    factory = build_session_factory(engine=engine)

    fixed_blocks = 0
    fixed_sentences = 0
    skipped_real = 0

    with session_scope(factory) as session:
        blocks = session.execute(
            select(Block)
            .where(Block.chapter_id == CHAPTER_ID)
            .where(Block.block_type == "code")
            .where(Block.ordinal >= ORDINAL_LO)
            .where(Block.ordinal <= ORDINAL_HI)
            .order_by(Block.ordinal)
        ).scalars().all()
        for b in blocks:
            if looks_like_real_code(b.source_text or ""):
                skipped_real += 1
                continue
            print(
                f"  ord={b.ordinal:3} demoting CODE -> PARAGRAPH: "
                f"{(b.source_text or '')[:80]!r}"
            )
            b.block_type = BlockType.PARAGRAPH.value
            fixed_blocks += 1
            # Flip the sentences back to translatable so the next translation
            # pass picks them up.
            res = session.execute(
                update(Sentence)
                .where(Sentence.block_id == b.id)
                .where(Sentence.translatable.is_(False))
                .values(translatable=True)
            )
            fixed_sentences += res.rowcount or 0

    print(
        f"\n[reclassify] fixed_blocks={fixed_blocks} "
        f"fixed_sentences={fixed_sentences} "
        f"kept_real_code={skipped_real}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
