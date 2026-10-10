from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from sqlalchemy.orm import Session

from book_agent.domain.block_rules import protected_policy_for_block
from book_agent.core.ids import stable_id
from book_agent.domain.enums import ActorType, ArtifactStatus, BlockType, SourceType
from book_agent.domain.models import Block, Chapter, Document, DocumentImage
from book_agent.domain.models.ops import AuditEvent
from book_agent.ingestion.text import _looks_like_table
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.services.bootstrap import ParseService
from book_agent.services.parse_revision_fork import STALE_FLAG

# Ordinals from here up are parked blocks (relocated footnotes, retired blocks), not reading order.
_PARKED_ORDINAL_BASE = 1_000_000
# Renumbering goes through this range first so the (chapter, ordinal) unique constraint holds.
_ORDINAL_SHIFT_OFFSET = 500_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _collapse_whitespace(text: str | None) -> str:
    return " ".join(str(text or "").split())


def _chapter_href(chapter: Chapter) -> str | None:
    metadata = chapter.metadata_json or {}
    href = metadata.get("href")
    if isinstance(href, str) and href.strip():
        return href.strip()
    anchor_start = str(chapter.anchor_start or "").strip()
    if not anchor_start:
        return None
    return anchor_start.split("#", 1)[0].strip() or None


def _page_box(source_span: dict | None) -> tuple[int, list[float]] | None:
    """The page and bounding box of a single-region block."""
    regions = ((source_span or {}).get("source_bbox_json") or {}).get("regions") or []
    if len(regions) != 1:
        return None
    bbox = regions[0].get("bbox")
    page = regions[0].get("page_number")
    if not isinstance(page, int) or not isinstance(bbox, list) or len(bbox) != 4:
        return None
    return page, [float(value) for value in bbox]


def _bbox_overlap(first: dict | None, second: dict | None) -> float:
    """Overlap of two blocks' boxes on one page, as a share of the smaller box (0 on other pages)."""
    a, b = _page_box(first), _page_box(second)
    if a is None or b is None or a[0] != b[0]:
        return 0.0
    (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = a[1], b[1]
    width = min(ax1, bx1) - max(ax0, bx0)
    height = min(ay1, by1) - max(ay0, by0)
    if width <= 0 or height <= 0:
        return 0.0
    smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return width * height / smaller if smaller > 0 else 0.0


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
class PdfStructureRefreshArtifacts:
    document_id: str
    refreshed_chapter_ids: list[str]
    refreshed_block_ids: list[str]
    created_document_image_ids: list[str]
    updated_document_image_ids: list[str]
    matched_chapter_count: int
    refreshed_block_count: int
    refreshed_document_image_count: int
    skipped_chapter_count: int
    skipped_block_count: int
    # Blocks whose refreshed source text no longer matches their existing
    # sentences. Refresh does not re-segment: sentences are referenced by
    # packets, translations and alignment edges, so these blocks need a
    # packet rebuild and retranslation before their translations are trusted.
    stale_sentence_block_ids: list[str] = field(default_factory=list)
    # Blocks the new parse finds that no existing block stands for (a footnote the old parse glued
    # to a running head, now a body paragraph of its own). They have no sentences yet and carry the
    # stale flag, so the fork that follows segments, packs and translates them.
    created_block_ids: list[str] = field(default_factory=list)
    # Set when the refresh was followed by a sentence-level parse-revision fork.
    parse_revision_fork: Any = None


class PdfStructureRefreshService:
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
    ) -> PdfStructureRefreshArtifacts:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        document = bundle.document
        if document.source_type not in {SourceType.PDF_TEXT, SourceType.PDF_MIXED, SourceType.PDF_SCAN}:
            raise ValueError("PDF structure refresh only supports PDF documents.")
        source_path = Path(str(document.source_path or "")).expanduser()
        if not source_path.is_file():
            raise ValueError(f"Document source file not found: {source_path}")

        working_document = _clone_document(document)
        parse_artifacts = self.parse_service.parse(working_document, source_path)
        now = _utcnow()

        selected_chapter_ids = set(chapter_ids or [chapter_bundle.chapter.id for chapter_bundle in bundle.chapters])
        existing_block_id_by_source_anchor = {
            block.source_anchor: block.id
            for chapter_bundle in bundle.chapters
            for block in chapter_bundle.blocks
            if isinstance(block.source_anchor, str) and block.source_anchor.strip()
        }
        existing_chapter_by_key = {
            (chapter_bundle.chapter.ordinal, _chapter_href(chapter_bundle.chapter)): chapter_bundle.chapter
            for chapter_bundle in bundle.chapters
            if chapter_bundle.chapter.id in selected_chapter_ids
        }
        existing_block_by_source_anchor = {
            block.source_anchor: block
            for chapter_bundle in bundle.chapters
            if chapter_bundle.chapter.id in selected_chapter_ids
            for block in chapter_bundle.blocks
            if isinstance(block.source_anchor, str) and block.source_anchor.strip()
        }
        existing_sentence_text_by_block_id: dict[str, str] = {}
        for chapter_bundle in bundle.chapters:
            for sentence in sorted(chapter_bundle.sentences, key=lambda item: (item.block_id, item.ordinal_in_block)):
                existing_sentence_text_by_block_id[sentence.block_id] = " ".join(
                    part
                    for part in (existing_sentence_text_by_block_id.get(sentence.block_id), sentence.source_text)
                    if part
                )
        existing_images_by_block_id = {
            image.block_id: image
            for image in bundle.document_images
            if isinstance(image.block_id, str) and image.block_id.strip()
        }

        refreshed_chapters_by_key = {
            (chapter.ordinal, _chapter_href(chapter)): chapter
            for chapter in parse_artifacts.chapters
        }
        refreshed_blocks_by_source_anchor = {
            block.source_anchor: block
            for block in parse_artifacts.blocks
            if isinstance(block.source_anchor, str) and block.source_anchor.strip()
        }
        self._rematch_moved_figures(
            existing_block_by_source_anchor, existing_block_id_by_source_anchor, refreshed_blocks_by_source_anchor
        )
        refreshed_split_fragments_by_base_anchor: dict[str, list[Block]] = {}
        for refreshed_block in parse_artifacts.blocks:
            refreshed_metadata = dict(refreshed_block.source_span_json or {})
            split_anchor_base = refreshed_metadata.get("pdf_split_source_anchor_base")
            if not isinstance(split_anchor_base, str) or not split_anchor_base.strip():
                continue
            refreshed_split_fragments_by_base_anchor.setdefault(split_anchor_base.strip(), []).append(refreshed_block)
        refreshed_blocks_by_page: dict[int, list[Any]] = {}
        for refreshed_block in parse_artifacts.blocks:
            source_span = dict(refreshed_block.source_span_json or {})
            page_start = source_span.get("source_page_start")
            page_end = source_span.get("source_page_end")
            if isinstance(page_start, int) and isinstance(page_end, int) and page_start == page_end:
                refreshed_blocks_by_page.setdefault(page_start, []).append(refreshed_block)
        refreshed_block_by_id = {block.id: block for block in parse_artifacts.blocks}
        refreshed_images_by_source_anchor = {}
        for image in parse_artifacts.document_images:
            refreshed_block = refreshed_block_by_id.get(image.block_id)
            if refreshed_block is None or not refreshed_block.source_anchor:
                continue
            refreshed_images_by_source_anchor[refreshed_block.source_anchor] = image

        refreshed_chapter_ids: list[str] = []
        refreshed_block_ids: list[str] = []
        created_document_image_ids: list[str] = []
        updated_document_image_ids: list[str] = []
        invalidated_block_ids: list[str] = []
        stale_sentence_block_ids: list[str] = []
        created_block_ids: list[str] = []
        skipped_chapter_count = 0
        skipped_block_count = 0
        matched_chapters: list[tuple[Chapter, Chapter]] = []

        for key, existing_chapter in existing_chapter_by_key.items():
            refreshed_chapter = refreshed_chapters_by_key.get(key)
            if refreshed_chapter is None:
                skipped_chapter_count += 1
                continue
            matched_chapters.append((existing_chapter, refreshed_chapter))
            existing_chapter.title_src = refreshed_chapter.title_src or existing_chapter.title_src
            existing_chapter.anchor_start = refreshed_chapter.anchor_start
            existing_chapter.anchor_end = refreshed_chapter.anchor_end
            existing_chapter.risk_level = refreshed_chapter.risk_level
            existing_chapter.metadata_json = {
                **(existing_chapter.metadata_json or {}),
                **(refreshed_chapter.metadata_json or {}),
            }
            existing_chapter.updated_at = now
            self.session.merge(existing_chapter)
            refreshed_chapter_ids.append(existing_chapter.id)

        for source_anchor, existing_block in existing_block_by_source_anchor.items():
            refreshed_block = refreshed_blocks_by_source_anchor.get(source_anchor)
            if refreshed_block is None:
                skipped_block_count += 1
                continue
            refreshed_source_span = self._remap_block_source_span(
                refreshed_block.source_span_json,
                existing_block_id_by_source_anchor,
            )
            refreshed_block_type = BlockType(refreshed_block.block_type)
            existing_block.block_type = refreshed_block_type
            existing_block.source_text = refreshed_block.source_text
            existing_block.normalized_text = " ".join(str(refreshed_block.source_text or "").split())
            existing_block.protected_policy = protected_policy_for_block(
                refreshed_block_type.value,
                refreshed_block.source_span_json,
            )
            existing_block.parse_confidence = refreshed_block.parse_confidence
            existing_block.status = ArtifactStatus.ACTIVE
            refreshed_split_fragments = refreshed_split_fragments_by_base_anchor.get(source_anchor, [])
            existing_block.source_span_json = self._merge_preserved_block_metadata(
                existing_block.source_span_json,
                refreshed_source_span,
                refreshed_split_fragments=refreshed_split_fragments,
            )
            existing_sentence_text = existing_sentence_text_by_block_id.get(existing_block.id)
            if existing_sentence_text is not None and _collapse_whitespace(existing_sentence_text) != _collapse_whitespace(
                existing_block.source_text
            ):
                existing_block.source_span_json = {
                    **existing_block.source_span_json,
                    "refresh_sentences_stale": True,
                }
                stale_sentence_block_ids.append(existing_block.id)
            existing_block.updated_at = now
            self.session.merge(existing_block)
            refreshed_block_ids.append(existing_block.id)

            if existing_block.block_type not in {BlockType.IMAGE, BlockType.FIGURE}:
                continue
            refreshed_image = refreshed_images_by_source_anchor.get(source_anchor)
            if refreshed_image is None:
                continue
            existing_image = existing_images_by_block_id.get(existing_block.id)
            image = self._upsert_document_image(
                document=document,
                block=existing_block,
                existing_image=existing_image,
                refreshed_image=refreshed_image,
                remapped_block_metadata=refreshed_source_span,
                now=now,
            )
            self.session.merge(image)
            if existing_image is None:
                created_document_image_ids.append(image.id)
            else:
                updated_document_image_ids.append(image.id)

        for source_anchor, existing_block in existing_block_by_source_anchor.items():
            if source_anchor in refreshed_blocks_by_source_anchor:
                continue
            if not self._should_invalidate_unmatched_block(existing_block, refreshed_blocks_by_page):
                continue
            existing_block.status = ArtifactStatus.INVALIDATED
            existing_block.source_span_json = {
                **dict(existing_block.source_span_json or {}),
                "refresh_invalidated_at": now.isoformat(),
                "refresh_invalidated_reason": "missing_from_refreshed_parse",
            }
            existing_block.updated_at = now
            self.session.merge(existing_block)
            invalidated_block_ids.append(existing_block.id)

        for existing_chapter, refreshed_chapter in matched_chapters:
            created_block_ids.extend(
                self._insert_new_blocks(
                    document=document,
                    existing_chapter=existing_chapter,
                    refreshed_blocks=[block for block in parse_artifacts.blocks if block.chapter_id == refreshed_chapter.id],
                    existing_block_by_source_anchor=existing_block_by_source_anchor,
                    existing_block_id_by_source_anchor=existing_block_id_by_source_anchor,
                    refreshed_images_by_source_anchor=refreshed_images_by_source_anchor,
                    created_document_image_ids=created_document_image_ids,
                    now=now,
                )
            )

        document.title = working_document.title or document.title
        document.title_src = working_document.title_src or document.title_src
        document.title_tgt = working_document.title_tgt or document.title_tgt
        document.author = working_document.author or document.author
        document.src_lang = working_document.src_lang or document.src_lang
        document.metadata_json = {
            **(document.metadata_json or {}),
            **(working_document.metadata_json or {}),
            "pdf_structure_refresh": {
                "refreshed_at": now.isoformat(),
                "matched_chapter_count": len(refreshed_chapter_ids),
                "refreshed_block_count": len(refreshed_block_ids),
                "refreshed_document_image_count": len(created_document_image_ids) + len(updated_document_image_ids),
                "invalidated_block_count": len(invalidated_block_ids),
                "created_block_count": len(created_block_ids),
                "stale_sentence_block_count": len(stale_sentence_block_ids),
                "chapter_scope_ids": sorted(selected_chapter_ids),
            },
        }
        document.updated_at = now
        self.session.merge(document)

        self._save_audits(
            document=document,
            refreshed_chapter_ids=refreshed_chapter_ids,
            refreshed_block_ids=refreshed_block_ids,
            now=now,
        )
        self.session.flush()
        return PdfStructureRefreshArtifacts(
            document_id=document.id,
            refreshed_chapter_ids=refreshed_chapter_ids,
            refreshed_block_ids=refreshed_block_ids,
            created_document_image_ids=created_document_image_ids,
            updated_document_image_ids=updated_document_image_ids,
            matched_chapter_count=len(refreshed_chapter_ids),
            refreshed_block_count=len(refreshed_block_ids),
            refreshed_document_image_count=len(created_document_image_ids) + len(updated_document_image_ids),
            skipped_chapter_count=skipped_chapter_count,
            skipped_block_count=skipped_block_count,
            stale_sentence_block_ids=stale_sentence_block_ids,
            created_block_ids=created_block_ids,
        )

    def _rematch_moved_figures(
        self,
        existing_block_by_source_anchor: dict[str, Block],
        existing_block_id_by_source_anchor: dict[str, str],
        refreshed_blocks_by_source_anchor: dict[str, Block],
    ) -> None:
        """A figure's anchor is its primary element's index, and a new figure-region rule can pick
        another element of the same picture. An existing figure whose anchor the new parse lacks takes
        over the new anchor of the figure on its page that overlaps it most, so it keeps its id, image,
        caption links and the edits made to it, instead of being dropped and added again."""
        figure_types = {BlockType.FIGURE.value, BlockType.IMAGE.value}
        orphans = [
            block
            for anchor, block in existing_block_by_source_anchor.items()
            if anchor not in refreshed_blocks_by_source_anchor and str(getattr(block.block_type, "value", block.block_type)) in figure_types
        ]
        candidates = [
            block
            for anchor, block in refreshed_blocks_by_source_anchor.items()
            if anchor not in existing_block_id_by_source_anchor and str(block.block_type) in figure_types
        ]
        taken: set[str] = set()
        for existing in orphans:
            best: tuple[float, Block] | None = None
            for refreshed in candidates:
                if refreshed.source_anchor in taken:
                    continue
                overlap = _bbox_overlap(existing.source_span_json, refreshed.source_span_json)
                if overlap >= 0.5 and (best is None or overlap > best[0]):
                    best = (overlap, refreshed)
            if best is None:
                continue
            new_anchor = best[1].source_anchor
            taken.add(new_anchor)
            old_anchor = existing.source_anchor
            existing.source_anchor = new_anchor
            del existing_block_by_source_anchor[old_anchor]
            existing_block_by_source_anchor[new_anchor] = existing
            existing_block_id_by_source_anchor.pop(old_anchor, None)
            existing_block_id_by_source_anchor[new_anchor] = existing.id

    def _insert_new_blocks(
        self,
        *,
        document: Document,
        existing_chapter: Chapter,
        refreshed_blocks: list[Block],
        existing_block_by_source_anchor: dict[str, Block],
        existing_block_id_by_source_anchor: dict[str, str],
        refreshed_images_by_source_anchor: dict[str, Any],
        created_document_image_ids: list[str],
        now: datetime,
    ) -> list[str]:
        """Add the refreshed blocks of this chapter whose anchor no existing block has, each right
        after the block the new parse puts before it. A block whose text an active block of the
        chapter already holds is not added twice."""
        chapter_blocks = [
            block for block in existing_block_by_source_anchor.values() if block.chapter_id == existing_chapter.id
        ]
        active_texts = [
            _collapse_whitespace(block.source_text)
            for block in chapter_blocks
            if block.status == ArtifactStatus.ACTIVE
        ]
        # (existing block it follows, or None for the chapter start; refreshed block)
        inserts: list[tuple[Block | None, Block]] = []
        previous: Block | None = None
        for refreshed_block in sorted(refreshed_blocks, key=lambda block: block.ordinal):
            anchor = refreshed_block.source_anchor
            if not isinstance(anchor, str) or not anchor.strip():
                continue
            existing = existing_block_by_source_anchor.get(anchor)
            if existing is not None:
                if existing.chapter_id == existing_chapter.id and existing.ordinal < _PARKED_ORDINAL_BASE:
                    previous = existing
                continue
            if anchor in existing_block_id_by_source_anchor:
                continue
            text = _collapse_whitespace(refreshed_block.source_text)
            # A figure's text is a placeholder ("[Figure]"); its anchor alone says it is new.
            is_figure = str(refreshed_block.block_type) in {BlockType.FIGURE.value, BlockType.IMAGE.value}
            if not text or (not is_figure and any(text in held for held in active_texts)):
                continue
            inserts.append((previous, refreshed_block))
        if not inserts:
            return []

        # Make room: every block after an insertion point moves down by the insertions before it.
        in_range = sorted(
            (
                block
                for block in self.session.query(Block).filter(
                    Block.chapter_id == existing_chapter.id, Block.ordinal < _PARKED_ORDINAL_BASE
                )
            ),
            key=lambda block: block.ordinal,
        )
        after_ordinals = sorted(previous.ordinal if previous is not None else 0 for previous, _ in inserts)
        moves = []
        for block in in_range:
            shift = sum(1 for ordinal in after_ordinals if ordinal < block.ordinal)
            if shift:
                moves.append((block, block.ordinal + shift))
        for block, _ in moves:
            block.ordinal += _ORDINAL_SHIFT_OFFSET
        self.session.flush()
        for block, ordinal in moves:
            block.ordinal = ordinal
        self.session.flush()

        created: list[str] = []
        placed_after: dict[int, int] = {}
        taken_ids = {block.id for block in in_range}
        for previous, refreshed_block in inserts:
            base = previous.ordinal if previous is not None else 0
            placed_after[base] = placed_after.get(base, 0) + 1
            block_id = refreshed_block.id
            if block_id in taken_ids or self.session.get(Block, block_id) is not None:
                # Ids derive from the ordinal: a new block may land on an existing block's id.
                block_id = stable_id("block", refreshed_block.id, "refresh", refreshed_block.source_text or "")
            taken_ids.add(block_id)
            block_type = BlockType(refreshed_block.block_type)
            source_span = self._remap_block_source_span(
                refreshed_block.source_span_json, existing_block_id_by_source_anchor
            )
            block = Block(
                id=block_id,
                chapter_id=existing_chapter.id,
                ordinal=base + placed_after[base],
                block_type=block_type,
                parse_revision_id=previous.parse_revision_id if previous is not None else None,
                source_text=refreshed_block.source_text,
                normalized_text=" ".join(str(refreshed_block.source_text or "").split()),
                source_anchor=refreshed_block.source_anchor,
                source_span_json={**source_span, STALE_FLAG: True, "refresh_created_at": now.isoformat()},
                parse_confidence=refreshed_block.parse_confidence,
                protected_policy=protected_policy_for_block(block_type.value, refreshed_block.source_span_json),
                status=ArtifactStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            self.session.add(block)
            self.session.flush()
            created.append(block.id)
            refreshed_image = refreshed_images_by_source_anchor.get(refreshed_block.source_anchor)
            if block_type in {BlockType.IMAGE, BlockType.FIGURE} and refreshed_image is not None:
                image = self._upsert_document_image(
                    document=document,
                    block=block,
                    existing_image=None,
                    refreshed_image=refreshed_image,
                    remapped_block_metadata=source_span,
                    now=now,
                )
                self.session.merge(image)
                created_document_image_ids.append(image.id)
        return created

    def _merge_preserved_block_metadata(
        self,
        existing_metadata: dict[str, Any] | None,
        refreshed_metadata: dict[str, Any],
        *,
        refreshed_split_fragments: list[Block] | None = None,
    ) -> dict[str, Any]:
        preserved = {
            key: value
            for key, value in dict(existing_metadata or {}).items()
            if key.startswith("repair_")
        }
        merged = {**preserved, **refreshed_metadata}
        split_render_fragments = self._merge_preserved_split_render_fragments(
            existing_metadata,
            refreshed_split_fragments or [],
        )
        if split_render_fragments:
            merged["refresh_split_render_fragments"] = split_render_fragments
        else:
            merged.pop("refresh_split_render_fragments", None)
        return merged

    def _merge_preserved_split_render_fragments(
        self,
        existing_metadata: dict[str, Any] | None,
        refreshed_split_fragments: list[Block],
    ) -> list[dict[str, Any]]:
        existing_fragments = list(dict(existing_metadata or {}).get("refresh_split_render_fragments") or [])
        preserved_by_signature: dict[tuple[str, str], dict[str, Any]] = {}
        for fragment in existing_fragments:
            if not isinstance(fragment, dict):
                continue
            split_kind = str(fragment.get("split_kind") or "").strip()
            source_text = str(fragment.get("source_text") or "").strip()
            if not split_kind or not source_text:
                continue
            preserved_by_signature[(split_kind, source_text)] = fragment

        merged_fragments: list[dict[str, Any]] = []
        for block in refreshed_split_fragments:
            metadata = dict(block.source_span_json or {})
            split_kind = str(metadata.get("pdf_mixed_code_prose_split") or "").strip()
            source_text = str(block.source_text or "").strip()
            if not split_kind or not source_text:
                continue
            fragment_payload = {
                "split_kind": split_kind,
                "block_type": block.block_type.value,
                "source_anchor": block.source_anchor,
                "source_text": block.source_text,
                "source_metadata": metadata,
            }
            preserved_fragment = preserved_by_signature.get((split_kind, source_text))
            if preserved_fragment is not None:
                for key in (
                    "target_text",
                    "repair_generated_at",
                    "repair_worker_name",
                    "repair_prompt_version",
                ):
                    value = preserved_fragment.get(key)
                    if isinstance(value, str) and value.strip():
                        fragment_payload[key] = value
            merged_fragments.append(fragment_payload)
        return merged_fragments

    def _remap_block_source_span(
        self,
        refreshed_source_span: dict[str, Any] | None,
        existing_block_id_by_source_anchor: dict[str, str],
    ) -> dict[str, Any]:
        metadata = dict(refreshed_source_span or {})
        metadata.pop("linked_caption_block_id", None)
        metadata.pop("caption_for_block_id", None)
        metadata.pop("artifact_group_context_block_ids", None)
        metadata.pop("artifact_group_block_id", None)

        linked_caption_source_anchor = metadata.get("linked_caption_source_anchor")
        if isinstance(linked_caption_source_anchor, str):
            linked_caption_block_id = existing_block_id_by_source_anchor.get(linked_caption_source_anchor)
            if linked_caption_block_id:
                metadata["linked_caption_block_id"] = linked_caption_block_id

        caption_for_source_anchor = metadata.get("caption_for_source_anchor")
        if isinstance(caption_for_source_anchor, str):
            caption_for_block_id = existing_block_id_by_source_anchor.get(caption_for_source_anchor)
            if caption_for_block_id:
                metadata["caption_for_block_id"] = caption_for_block_id

        artifact_group_context_source_anchors = metadata.get("artifact_group_context_source_anchors")
        if isinstance(artifact_group_context_source_anchors, list):
            artifact_group_context_block_ids = [
                existing_block_id_by_source_anchor.get(str(source_anchor))
                for source_anchor in artifact_group_context_source_anchors
                if isinstance(source_anchor, str)
            ]
            artifact_group_context_block_ids = [
                block_id
                for block_id in artifact_group_context_block_ids
                if isinstance(block_id, str) and block_id.strip()
            ]
            if artifact_group_context_block_ids:
                metadata["artifact_group_context_block_ids"] = artifact_group_context_block_ids

        artifact_group_source_anchor = metadata.get("artifact_group_source_anchor")
        if isinstance(artifact_group_source_anchor, str):
            artifact_group_block_id = existing_block_id_by_source_anchor.get(artifact_group_source_anchor)
            if artifact_group_block_id:
                metadata["artifact_group_block_id"] = artifact_group_block_id
        return metadata

    def _upsert_document_image(
        self,
        *,
        document: Document,
        block: Block,
        existing_image: DocumentImage | None,
        refreshed_image: DocumentImage,
        remapped_block_metadata: dict[str, Any],
        now: datetime,
    ) -> DocumentImage:
        storage_path = (
            existing_image.storage_path
            if existing_image is not None and str(existing_image.storage_path or "").strip()
            else f"document-images/{document.id}/{block.id}.{self._normalized_image_extension(remapped_block_metadata.get('image_ext'))}"
        )
        storage_status = (
            (existing_image.metadata_json or {}).get("storage_status")
            if existing_image is not None
            else "logical_only"
        )
        metadata_json = {
            **(existing_image.metadata_json if existing_image is not None else {}),
            "source_path": remapped_block_metadata.get("source_path"),
            "anchor": remapped_block_metadata.get("anchor"),
            "image_ext": remapped_block_metadata.get("image_ext"),
            "linked_caption_block_id": remapped_block_metadata.get("linked_caption_block_id"),
            "storage_status": storage_status,
        }
        image = existing_image or DocumentImage(
            id=stable_id("document-image", document.id, block.id),
            document_id=document.id,
            block_id=block.id,
            page_number=refreshed_image.page_number,
            image_type=refreshed_image.image_type,
            storage_path=storage_path,
            bbox_json=refreshed_image.bbox_json,
            metadata_json=metadata_json,
            created_at=now,
        )
        image.document_id = document.id
        image.block_id = block.id
        image.page_number = refreshed_image.page_number
        image.image_type = refreshed_image.image_type
        image.storage_path = storage_path
        image.bbox_json = refreshed_image.bbox_json
        image.ocr_text = refreshed_image.ocr_text
        image.latex = refreshed_image.latex
        image.alt_text = refreshed_image.alt_text
        image.width_px = refreshed_image.width_px
        image.height_px = refreshed_image.height_px
        image.metadata_json = metadata_json
        return image

    def _normalized_image_extension(self, candidate: object) -> str:
        if not isinstance(candidate, str):
            return "png"
        normalized = candidate.strip().lower().lstrip(".")
        if not normalized or not re.fullmatch(r"[a-z0-9]+", normalized):
            return "png"
        return normalized

    def _save_audits(
        self,
        *,
        document: Document,
        refreshed_chapter_ids: list[str],
        refreshed_block_ids: list[str],
        now: datetime,
    ) -> None:
        document_audit = AuditEvent(
            object_type="document",
            object_id=document.id,
            action="document.pdf_structure_refreshed",
            actor_type=ActorType.SYSTEM,
            actor_id="pdf-structure-refresh-service",
            payload_json={
                "refreshed_chapter_count": len(refreshed_chapter_ids),
                "refreshed_block_count": len(refreshed_block_ids),
            },
            created_at=now,
        )
        self.session.add(document_audit)
        for chapter_id in refreshed_chapter_ids:
            self.session.add(
                AuditEvent(
                    object_type="chapter",
                    object_id=chapter_id,
                    action="chapter.pdf_structure_refreshed",
                    actor_type=ActorType.SYSTEM,
                    actor_id="pdf-structure-refresh-service",
                    payload_json={"document_id": document.id},
                    created_at=now,
                )
            )

    def _should_invalidate_unmatched_block(
        self,
        existing_block: Block,
        refreshed_blocks_by_page: dict[int, list[Any]],
    ) -> bool:
        source_span = dict(existing_block.source_span_json or {})
        page_start = source_span.get("source_page_start")
        page_end = source_span.get("source_page_end")
        if not isinstance(page_start, int) or not isinstance(page_end, int) or page_start != page_end:
            return False

        refreshed_page_blocks = refreshed_blocks_by_page.get(page_start, [])
        if existing_block.block_type in {BlockType.FIGURE, BlockType.IMAGE}:
            # A figure the new parse no longer finds on a page it read (a sidebar's tinted panel
            # taken for a figure, its text now paragraphs of their own) would show the text twice.
            return bool(refreshed_page_blocks)

        lines = [line for line in str(existing_block.source_text or "").splitlines() if line.strip()]
        if existing_block.block_type != BlockType.TABLE and not _looks_like_table(len(lines), lines):
            return False

        for refreshed_block in refreshed_page_blocks:
            refreshed_role = str((refreshed_block.source_span_json or {}).get("pdf_block_role") or "")
            if refreshed_role in {"table_like", "caption"}:
                return True
        return False
