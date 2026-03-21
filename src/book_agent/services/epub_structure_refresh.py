from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from book_agent.domain.enums import SourceType
from book_agent.domain.models import Document
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.services.bootstrap import ParseService


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
    matched_chapter_count: int
    skipped_chapter_count: int


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
        refreshed_chapter_by_key = {
            (chapter.ordinal, str((chapter.metadata_json or {}).get("href") or "").strip()): chapter
            for chapter in parse_artifacts.chapters
        }

        refreshed_chapter_ids: list[str] = []
        skipped_chapter_count = 0
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
                "skipped_chapter_count": skipped_chapter_count,
                "chapter_scope_ids": sorted(selected_chapter_ids),
            },
        }
        document.updated_at = now
        self.session.merge(document)
        self.session.flush()
        return EpubStructureRefreshArtifacts(
            document_id=document.id,
            refreshed_chapter_ids=refreshed_chapter_ids,
            matched_chapter_count=len(refreshed_chapter_ids),
            skipped_chapter_count=skipped_chapter_count,
        )
