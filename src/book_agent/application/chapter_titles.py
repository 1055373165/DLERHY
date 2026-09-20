"""Chapter titles for the interface, decided the same way the export decides them.

A web page's chapter is titled by the article's own first heading, not by the document title
(which carries the site's name); a book's chapter is titled by its first heading when that
heading reads like a title. Both rules live in ``book_agent.export.titles``; this module only
feeds them the chapter's blocks with the Chinese text a reader currently sees, so the interface
never re-derives the answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.domain.enums import ArtifactStatus
from book_agent.domain.models.document import Block, Chapter, Document, Sentence
from book_agent.export.titles import resolve_chapter_title
from book_agent.infra.repositories.review import active_target_texts

# Enough to find the first heading of an article that opens with a byline or a date line.
# Reading every block of every chapter to title a list would cost far more than it is worth.
BLOCKS_PER_CHAPTER = 12


@dataclass(slots=True)
class _TitleBlock:
    block_type: str
    source_text: str | None
    target_text: str | None


def _blocks_by_chapter(session: Session, chapter_ids: list[str]) -> dict[str, list[Block]]:
    if not chapter_ids:
        return {}
    rows = session.scalars(
        select(Block)
        .where(Block.chapter_id.in_(chapter_ids), Block.status == ArtifactStatus.ACTIVE)
        .order_by(Block.chapter_id, Block.ordinal)
    ).all()
    out: dict[str, list[Block]] = {}
    for block in rows:
        found = out.setdefault(str(block.chapter_id), [])
        if len(found) < BLOCKS_PER_CHAPTER:
            found.append(block)
    return out


def _target_text_by_block(session: Session, blocks: list[Block]) -> dict[str, str]:
    """The Chinese a block currently reads as: its sentences' active translations, in order."""
    if not blocks:
        return {}
    sentences = session.scalars(
        select(Sentence)
        .where(Sentence.block_id.in_([block.id for block in blocks]), Sentence.retired_by_revision_id.is_(None))
        .order_by(Sentence.block_id, Sentence.ordinal_in_block)
    ).all()
    texts = active_target_texts(session, [sentence.id for sentence in sentences])
    joined: dict[str, list[str]] = {}
    for sentence in sentences:
        text = (texts.get(str(sentence.id)) or "").strip()
        if text:
            joined.setdefault(str(sentence.block_id), []).append(text)
    return {block_id: "".join(parts) for block_id, parts in joined.items()}


def display_titles(session: Session, document: Document, chapters: list[Chapter]) -> dict[str, str | None]:
    """{chapter_id: the title to show}, for the chapters given."""
    if not chapters:
        return {}
    by_chapter = _blocks_by_chapter(session, [str(chapter.id) for chapter in chapters])
    every_block = [block for blocks in by_chapter.values() for block in blocks]
    targets = _target_text_by_block(session, every_block)
    resolved: dict[str, str | None] = {}
    for chapter in chapters:
        blocks = [
            _TitleBlock(
                block_type=block.block_type.value if hasattr(block.block_type, "value") else str(block.block_type),
                source_text=block.source_text,
                target_text=targets.get(str(block.id)),
            )
            for block in by_chapter.get(str(chapter.id), [])
        ]
        resolved[str(chapter.id)] = resolve_chapter_title(document, chapter, blocks)
    return resolved
