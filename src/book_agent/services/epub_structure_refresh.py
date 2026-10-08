from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.enums import ArtifactStatus, SourceType
from book_agent.domain.models import Block, Document
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.services.bootstrap import ParseService


# Blocks of a chapter: the parser's ordinals stay below the parked split-offs of structure edits;
# blocks a refresh retires go above both, out of the way of every later renumbering.
_PARKED_ORDINAL_BASE = 1_000_000
_SHIFT_OFFSET = 2_000_000
_RETIRED_ORDINAL_BASE = 4_000_000


def _text_key(block: Block) -> str:
    return " ".join(str(block.source_text or "").split()).casefold()


def align_blocks(existing: list[Block], refreshed: list[Block]) -> list[tuple[Block | None, Block | None]]:
    """Existing and refreshed blocks paired by their text, in reading order.

    Blocks with the same text pair up wherever they are; a run of changed blocks pairs position by
    position (the old ordinal match, where nothing moved); what is left over is new or gone. A
    parser that now finds a block it used to drop (a definition term) adds that block instead of
    shifting the text of every block after it onto its neighbour.
    """
    pairs: list[tuple[Block | None, Block | None]] = []
    matcher = SequenceMatcher(None, [_text_key(b) for b in existing], [_text_key(b) for b in refreshed], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old, new = existing[i1:i2], refreshed[j1:j2]
        if tag in {"equal", "replace"}:
            common = min(len(old), len(new))
            pairs.extend(zip(old[:common], new[:common]))
            old, new = old[common:], new[common:]
        pairs.extend((block, None) for block in old)
        pairs.extend((None, block) for block in new)
    return pairs


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _compose_anchor(href: str, anchor: str | None) -> str:
    return f"{href}#{anchor}" if anchor else href


def _clone_document(document: Document) -> Document:
    return Document(
        id=document.id,
        source_type=document.source_type,
        file_fingerprint=document.file_fingerprint,
        source_path=document.source_path,
        title=document.title,
        title_src=document.title_src,
        title_tgt=document.title_tgt,
        author=document.author,
        src_lang=document.src_lang,
        tgt_lang=document.tgt_lang,
        status=document.status,
        parser_version=document.parser_version,
        segmentation_version=document.segmentation_version,
        active_book_profile_version=document.active_book_profile_version,
        metadata_json=dict(document.metadata_json or {}),
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@dataclass(slots=True)
class EpubStructureRefreshArtifacts:
    document_id: str
    refreshed_chapter_ids: list[str]
    refreshed_block_ids: list[str]
    created_block_ids: list[str]
    invalidated_block_ids: list[str]
    matched_chapter_count: int
    refreshed_block_count: int
    created_block_count: int
    invalidated_block_count: int
    skipped_chapter_count: int
    skipped_block_count: int
    # Set when the refresh was followed by a sentence-level parse-revision fork.
    parse_revision_fork: Any = None


class EpubStructureRefreshService:
    def __init__(
        self,
        session: Session,
        bootstrap_repository: BootstrapRepository,
        parse_service: ParseService | None = None,
    ) -> None:
        self.session = session
        self.bootstrap_repository = bootstrap_repository
        self.parse_service = parse_service or ParseService()

    def refresh_document(
        self,
        document_id: str,
        *,
        chapter_ids: list[str] | None = None,
    ) -> EpubStructureRefreshArtifacts:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        document = bundle.document
        if document.source_type != SourceType.EPUB:
            raise ValueError("EPUB structure refresh only supports EPUB documents.")
        source_path = Path(str(document.source_path or "")).expanduser()
        if not source_path.is_file():
            raise ValueError(f"Document source file not found: {source_path}")

        working_document = _clone_document(document)
        parse_artifacts = self.parse_service.parse(working_document, source_path)
        now = _utcnow()

        selected_chapter_ids = set(chapter_ids or [chapter_bundle.chapter.id for chapter_bundle in bundle.chapters])
        existing_chapter_by_key = {
            (chapter_bundle.chapter.ordinal, str((chapter_bundle.chapter.metadata_json or {}).get("href") or "").strip()):
            chapter_bundle.chapter
            for chapter_bundle in bundle.chapters
            if chapter_bundle.chapter.id in selected_chapter_ids
        }
        existing_blocks_by_chapter_id = {
            chapter_bundle.chapter.id: sorted(chapter_bundle.blocks, key=lambda block: block.ordinal)
            for chapter_bundle in bundle.chapters
            if chapter_bundle.chapter.id in selected_chapter_ids
        }
        refreshed_chapter_by_key = {
            (chapter.ordinal, str((chapter.metadata_json or {}).get("href") or "").strip()): chapter
            for chapter in parse_artifacts.chapters
        }
        refreshed_blocks_by_chapter_id: dict[str, list[Block]] = {}
        for refreshed_block in parse_artifacts.blocks:
            refreshed_blocks_by_chapter_id.setdefault(refreshed_block.chapter_id, []).append(refreshed_block)
        for chapter_id in list(refreshed_blocks_by_chapter_id.keys()):
            refreshed_blocks_by_chapter_id[chapter_id] = sorted(
                refreshed_blocks_by_chapter_id[chapter_id],
                key=lambda block: block.ordinal,
            )

        refreshed_chapter_ids: list[str] = []
        refreshed_block_ids: list[str] = []
        created_block_ids: list[str] = []
        invalidated_block_ids: list[str] = []
        skipped_chapter_count = 0
        skipped_block_count = 0
        for key, existing_chapter in existing_chapter_by_key.items():
            refreshed_chapter = refreshed_chapter_by_key.get(key)
            if refreshed_chapter is None:
                skipped_chapter_count += 1
                continue
            existing_chapter.title_src = refreshed_chapter.title_src or existing_chapter.title_src
            existing_chapter.anchor_start = refreshed_chapter.anchor_start or existing_chapter.anchor_start
            existing_chapter.anchor_end = refreshed_chapter.anchor_end or existing_chapter.anchor_end
            existing_chapter.metadata_json = {
                **(existing_chapter.metadata_json or {}),
                **(refreshed_chapter.metadata_json or {}),
            }
            existing_chapter.updated_at = now
            self.session.merge(existing_chapter)
            refreshed_chapter_ids.append(existing_chapter.id)

            existing_blocks = existing_blocks_by_chapter_id.get(existing_chapter.id, [])
            refreshed_blocks = refreshed_blocks_by_chapter_id.get(refreshed_chapter.id, [])
            pairs = align_blocks(existing_blocks, refreshed_blocks)
            retired_ordinal = self._make_room(existing_chapter.id, existing_blocks)
            taken_ids = {block.id for block in existing_blocks}
            for existing_block, refreshed_block in pairs:
                if existing_block is not None and refreshed_block is not None:
                    # Matched by text: a block the parser now finds before it (a definition term it
                    # used to drop) moves it down instead of shifting every later block's text.
                    existing_block.ordinal = refreshed_block.ordinal
                    existing_block.block_type = refreshed_block.block_type
                    existing_block.source_text = refreshed_block.source_text
                    existing_block.normalized_text = refreshed_block.normalized_text
                    existing_block.source_anchor = refreshed_block.source_anchor
                    existing_block.source_span_json = self._merge_preserved_block_metadata(
                        existing_block.source_span_json,
                        refreshed_block.source_span_json,
                    )
                    existing_block.parse_confidence = refreshed_block.parse_confidence
                    existing_block.protected_policy = refreshed_block.protected_policy
                    existing_block.status = ArtifactStatus.ACTIVE
                    existing_block.updated_at = now
                    self.session.merge(existing_block)
                    refreshed_block_ids.append(existing_block.id)
                    continue
                if existing_block is None and refreshed_block is not None:
                    if refreshed_block.id in taken_ids:
                        # Ids derive from the ordinal: a new block may land on an existing block's id.
                        refreshed_block.id = stable_id("block", refreshed_block.id, "refresh", refreshed_block.source_text or "")
                    taken_ids.add(refreshed_block.id)
                    refreshed_block.status = ArtifactStatus.ACTIVE
                    refreshed_block.created_at = now
                    refreshed_block.updated_at = now
                    self.session.merge(refreshed_block)
                    created_block_ids.append(refreshed_block.id)
                    continue
                if existing_block is not None and refreshed_block is None:
                    retired_ordinal += 1
                    existing_block.ordinal = retired_ordinal
                    existing_block.status = ArtifactStatus.INVALIDATED
                    existing_block.source_span_json = {
                        **dict(existing_block.source_span_json or {}),
                        "refresh_invalidated_at": now.isoformat(),
                        "refresh_invalidated_reason": "missing_from_refreshed_epub_parse",
                    }
                    existing_block.updated_at = now
                    self.session.merge(existing_block)
                    invalidated_block_ids.append(existing_block.id)
                    continue
                skipped_block_count += 1

        document.title = working_document.title or document.title
        document.title_src = working_document.title_src or document.title_src
        document.title_tgt = working_document.title_tgt or document.title_tgt
        document.author = working_document.author or document.author
        document.src_lang = working_document.src_lang or document.src_lang
        document.metadata_json = {
            **(document.metadata_json or {}),
            **(working_document.metadata_json or {}),
            "epub_structure_refresh": {
                "refreshed_at": now.isoformat(),
                "matched_chapter_count": len(refreshed_chapter_ids),
                "refreshed_block_count": len(refreshed_block_ids),
                "created_block_count": len(created_block_ids),
                "invalidated_block_count": len(invalidated_block_ids),
                "skipped_chapter_count": skipped_chapter_count,
                "skipped_block_count": skipped_block_count,
                "chapter_scope_ids": sorted(selected_chapter_ids),
            },
        }
        document.updated_at = now
        self.session.merge(document)
        self.session.flush()
        return EpubStructureRefreshArtifacts(
            document_id=document.id,
            refreshed_chapter_ids=refreshed_chapter_ids,
            refreshed_block_ids=refreshed_block_ids,
            created_block_ids=created_block_ids,
            invalidated_block_ids=invalidated_block_ids,
            matched_chapter_count=len(refreshed_chapter_ids),
            refreshed_block_count=len(refreshed_block_ids),
            created_block_count=len(created_block_ids),
            invalidated_block_count=len(invalidated_block_ids),
            skipped_chapter_count=skipped_chapter_count,
            skipped_block_count=skipped_block_count,
        )

    def _make_room(self, chapter_id: str, existing_blocks: list[Block]) -> int:
        """Move the chapter's blocks out of the parser's ordinal range so they can be renumbered
        without tripping the (chapter, ordinal) unique constraint; retired blocks still in that range
        go to the retired range. Returns the highest retired ordinal (the next retirement goes above)."""
        in_range = list(
            self.session.scalars(
                select(Block).where(Block.chapter_id == chapter_id, Block.ordinal < _PARKED_ORDINAL_BASE)
            ).all()
        )
        for block in in_range:
            block.ordinal = block.ordinal + _SHIFT_OFFSET
        self.session.flush()
        active_ids = {block.id for block in existing_blocks}
        retired = int(
            self.session.scalar(
                select(func.coalesce(func.max(Block.ordinal), _RETIRED_ORDINAL_BASE)).where(
                    Block.chapter_id == chapter_id, Block.ordinal >= _RETIRED_ORDINAL_BASE
                )
            )
        )
        for block in in_range:
            if block.id not in active_ids:
                retired += 1
                block.ordinal = retired
        self.session.flush()
        return retired

    def _merge_preserved_block_metadata(
        self,
        existing_metadata: dict[str, object] | None,
        refreshed_metadata: dict[str, object] | None,
    ) -> dict[str, object]:
        preserved = {
            key: value
            for key, value in dict(existing_metadata or {}).items()
            if str(key).startswith("repair_")
        }
        return {**preserved, **dict(refreshed_metadata or {})}
