from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from book_agent.domain.block_rules import protected_policy_for_block
from book_agent.core.ids import stable_id
from book_agent.domain.enums import ActorType, ArtifactStatus, BlockType, SourceType
from book_agent.domain.models import Block, Chapter, Document, DocumentImage
from book_agent.domain.models.ops import AuditEvent
from book_agent.domain.structure.pdf import _looks_like_table
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.services.bootstrap import ParseService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _chapter_href(chapter: Chapter) -> str | None:
    metadata = chapter.metadata_json or {}
    href = metadata.get("href")
    if isinstance(href, str) and href.strip():
        return href.strip()
    anchor_start = str(chapter.anchor_start or "").strip()
    if not anchor_start:
        return None
    return anchor_start.split("#", 1)[0].strip() or None


def _clone_document(document: Document) -> Document:
    return Document(
        id=document.id,
        source_type=document.source_type,
        file_fingerprint=document.file_fingerprint,
        source_path=document.source_path,
        title=document.title,
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
        skipped_chapter_count = 0
        skipped_block_count = 0

        for key, existing_chapter in existing_chapter_by_key.items():
            refreshed_chapter = refreshed_chapters_by_key.get(key)
            if refreshed_chapter is None:
                skipped_chapter_count += 1
                continue
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
            if refreshed_block_type in {
                BlockType.TABLE,
                BlockType.CAPTION,
                BlockType.EQUATION,
                BlockType.IMAGE,
                BlockType.FIGURE,
            }:
                existing_block.block_type = refreshed_block_type
                existing_block.source_text = refreshed_block.source_text
                existing_block.normalized_text = " ".join(str(refreshed_block.source_text or "").split())
                existing_block.protected_policy = protected_policy_for_block(
                    refreshed_block_type.value,
                    refreshed_block.source_span_json,
                )
            existing_block.parse_confidence = refreshed_block.parse_confidence
            existing_block.status = ArtifactStatus.ACTIVE
            existing_block.source_span_json = self._merge_preserved_block_metadata(
                existing_block.source_span_json,
                refreshed_source_span,
            )
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

        document.title = working_document.title or document.title
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
        )

    def _merge_preserved_block_metadata(
        self,
        existing_metadata: dict[str, Any] | None,
        refreshed_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        preserved = {
            key: value
            for key, value in dict(existing_metadata or {}).items()
            if key.startswith("repair_")
        }
        return {**preserved, **refreshed_metadata}

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
            else f"document-images/{document.id}/{block.id}.png"
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

    def _save_audits(
        self,
        *,
        document: Document,
        refreshed_chapter_ids: list[str],
        refreshed_block_ids: list[str],
        now: datetime,
    ) -> None:
        document_audit = AuditEvent(
            id=stable_id("audit", "document", document.id, "document.pdf_structure_refreshed", now.isoformat()),
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
        self.session.merge(document_audit)
        for chapter_id in refreshed_chapter_ids:
            self.session.merge(
                AuditEvent(
                    id=stable_id("audit", "chapter", chapter_id, "chapter.pdf_structure_refreshed", now.isoformat()),
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

        lines = [line for line in str(existing_block.source_text or "").splitlines() if line.strip()]
        if existing_block.block_type != BlockType.TABLE and not _looks_like_table(len(lines), lines):
            return False

        refreshed_page_blocks = refreshed_blocks_by_page.get(page_start, [])
        for refreshed_block in refreshed_page_blocks:
            refreshed_role = str((refreshed_block.source_span_json or {}).get("pdf_block_role") or "")
            if refreshed_role in {"table_like", "caption"}:
                return True
        return False
