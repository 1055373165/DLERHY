from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.enums import LockLevel, MemoryScopeType, TermStatus, TermType
from book_agent.domain.models import Chapter, MemorySnapshot
from book_agent.infra.repositories.chapter_memory import ChapterTranslationMemoryRepository
from book_agent.domain.models.translation import TermEntry


def _copy_content(snapshot: MemorySnapshot | None, chapter: Chapter) -> dict[str, Any]:
    if snapshot is not None:
        content = dict(snapshot.content_json or {})
    else:
        content = {
            "schema_version": 1,
            "chapter_id": chapter.id,
            "chapter_title": chapter.title_src,
            "heading_path": [chapter.title_src] if chapter.title_src else [],
            "chapter_brief": None,
            "active_concepts": [],
            "recent_accepted_translations": [],
            "last_packet_id": None,
            "last_translation_run_id": None,
        }
    content.setdefault("schema_version", 1)
    content.setdefault("chapter_id", chapter.id)
    content.setdefault("chapter_title", chapter.title_src)
    content.setdefault("heading_path", [chapter.title_src] if chapter.title_src else [])
    content.setdefault("chapter_brief", None)
    content.setdefault("active_concepts", [])
    content.setdefault("recent_accepted_translations", [])
    content.setdefault("last_packet_id", None)
    content.setdefault("last_translation_run_id", None)
    return content


@dataclass(frozen=True, slots=True)
class ChapterConceptLockResult:
    chapter_id: str
    source_term: str
    canonical_zh: str
    snapshot_version: int
    created_new_concept: bool
    term_entry_id: str
    term_entry_version: int
    created_new_term_entry: bool


@dataclass(slots=True)
class ChapterConceptLockService:
    session: Session
    repository: ChapterTranslationMemoryRepository = field(init=False)

    def __post_init__(self) -> None:
        self.repository = ChapterTranslationMemoryRepository(self.session)

    def lock_concept(
        self,
        *,
        chapter_id: str,
        source_term: str,
        canonical_zh: str,
        status: str = "locked",
    ) -> ChapterConceptLockResult:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError(f"Unknown chapter_id: {chapter_id}")

        source_term = source_term.strip()
        canonical_zh = canonical_zh.strip()
        if not source_term:
            raise ValueError("source_term must not be empty")
        if not canonical_zh:
            raise ValueError("canonical_zh must not be empty")

        current_snapshot = self.repository.load_latest(
            document_id=chapter.document_id,
            chapter_id=chapter.id,
        )
        content = _copy_content(current_snapshot, chapter)
        concepts = content.get("active_concepts", [])
        if not isinstance(concepts, list):
            concepts = []

        created_new = True
        updated_concepts: list[dict[str, Any]] = []
        source_key = source_term.casefold()
        for concept in concepts:
            if not isinstance(concept, dict):
                continue
            existing_source = str(concept.get("source_term") or "").strip()
            if existing_source.casefold() == source_key:
                updated = dict(concept)
                updated["source_term"] = existing_source or source_term
                updated["canonical_zh"] = canonical_zh
                updated["status"] = status
                updated["confidence"] = 1.0
                updated_concepts.append(updated)
                created_new = False
            else:
                updated_concepts.append(dict(concept))

        if created_new:
            updated_concepts.append(
                {
                    "source_term": source_term,
                    "canonical_zh": canonical_zh,
                    "status": status,
                    "confidence": 1.0,
                    "first_seen_packet_id": None,
                    "last_seen_packet_id": None,
                    "times_seen": 1,
                }
            )

        updated_concepts.sort(
            key=lambda item: (
                0 if item.get("canonical_zh") else 1,
                -(int(item.get("times_seen") or 0)),
                str(item.get("source_term") or "").casefold(),
            )
        )
        content["active_concepts"] = updated_concepts[:12]

        next_snapshot = self.repository.supersede_and_create_next(
            current_snapshot=current_snapshot,
            document_id=chapter.document_id,
            chapter_id=chapter.id,
            content_json=content,
        )
        term_entry, created_new_term_entry = self._upsert_locked_term_entry(
            chapter=chapter,
            source_term=source_term,
            canonical_zh=canonical_zh,
        )
        self.session.flush()
        return ChapterConceptLockResult(
            chapter_id=chapter.id,
            source_term=source_term,
            canonical_zh=canonical_zh,
            snapshot_version=next_snapshot.version,
            created_new_concept=created_new,
            term_entry_id=term_entry.id,
            term_entry_version=term_entry.version,
            created_new_term_entry=created_new_term_entry,
        )

    def _upsert_locked_term_entry(
        self,
        *,
        chapter: Chapter,
        source_term: str,
        canonical_zh: str,
    ) -> tuple[TermEntry, bool]:
        active_entries = self.session.scalars(
            select(TermEntry).where(
                TermEntry.document_id == chapter.document_id,
                TermEntry.scope_type == MemoryScopeType.CHAPTER,
                TermEntry.scope_id == chapter.id,
                TermEntry.status == TermStatus.ACTIVE,
            )
        ).all()
        source_key = source_term.casefold()
        matched_entries = [
            entry
            for entry in active_entries
            if entry.source_term.casefold() == source_key
        ]
        if matched_entries:
            latest = max(matched_entries, key=lambda entry: entry.version)
            if (
                latest.target_term == canonical_zh
                and latest.lock_level == LockLevel.LOCKED
                and latest.term_type == TermType.CONCEPT
            ):
                return latest, False
            for entry in matched_entries:
                entry.status = TermStatus.SUPERSEDED
                self.session.merge(entry)
            next_version = latest.version + 1
            created_new = False
        else:
            next_version = 1
            created_new = True

        entry = TermEntry(
            id=stable_id(
                "term-entry",
                chapter.document_id,
                chapter.id,
                source_term.casefold(),
                next_version,
            ),
            document_id=chapter.document_id,
            scope_type=MemoryScopeType.CHAPTER,
            scope_id=chapter.id,
            source_term=source_term,
            target_term=canonical_zh,
            term_type=TermType.CONCEPT,
            lock_level=LockLevel.LOCKED,
            status=TermStatus.ACTIVE,
            version=next_version,
        )
        self.session.merge(entry)
        return entry, created_new
