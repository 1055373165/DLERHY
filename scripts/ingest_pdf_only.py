"""Ingest a PDF into the DB (parse + persist blocks/sentences). No translation.

This is a thin wrapper over ``DocumentWorkflowService.bootstrap_document``
that exists so we can re-parse the source PDF after structural fixes
(figure clustering, caption linkage) without also re-spending API budget
on retranslation.

Usage::

    BOOK_AGENT_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55432/book_agent \
        .venv/bin/python scripts/ingest_pdf_only.py \
            --source-path artifacts/uploads/.../llm-book.pdf

Reports the resulting document_id, chapter list, and per-chapter block
count so callers can plug those into the translate/export step.
"""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
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

from book_agent.core.config import get_settings
from book_agent.domain.enums import BlockType
from book_agent.domain.models import Block, Chapter, Document
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.factory import build_translation_worker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--export-root", default="artifacts/exports")
    args = parser.parse_args()

    source_path = Path(args.source_path).resolve()
    if not source_path.is_file():
        print(f"source not found: {source_path}", file=sys.stderr)
        return 1

    settings = get_settings()
    engine = build_engine(database_url=settings.database_url)
    factory = build_session_factory(engine=engine)

    with session_scope(factory) as session:
        translation_worker = build_translation_worker(settings)
        service = DocumentWorkflowService(
            session,
            export_root=args.export_root,
            translation_worker=translation_worker,
        )
        summary = service.bootstrap_document(source_path)

    # Re-open a session for read-back so the bootstrap commit lands first.
    with session_scope(factory) as session:
        document = session.scalar(select(Document).where(Document.id == summary.document_id))
        if document is None:
            print("post-bootstrap: document not found", file=sys.stderr)
            return 2
        chapters = session.execute(
            select(Chapter).where(Chapter.document_id == document.id).order_by(Chapter.ordinal)
        ).scalars().all()
        report: list[dict] = []
        for ch in chapters:
            counts: dict[str, int] = {}
            blocks = session.execute(
                select(Block).where(Block.chapter_id == ch.id)
            ).scalars().all()
            for blk in blocks:
                key = blk.block_type.value if hasattr(blk.block_type, "value") else str(blk.block_type)
                counts[key] = counts.get(key, 0) + 1
            report.append({
                "ordinal": ch.ordinal,
                "id": ch.id,
                "title": ch.title_src or ch.title_tgt or "",
                "block_total": len(blocks),
                "block_counts": counts,
            })

        print(json.dumps({
            "document_id": document.id,
            "title": document.title,
            "chapters": report,
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
