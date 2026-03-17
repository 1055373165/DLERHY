from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.enums import MemoryScopeType, MemoryStatus, SnapshotType
from book_agent.domain.models import MemorySnapshot


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ChapterTranslationMemoryRepository:
    session: Session

    def load_latest(self, *, document_id: str, chapter_id: str) -> MemorySnapshot | None:
        return self.session.scalars(
            select(MemorySnapshot)
            .where(
                MemorySnapshot.document_id == document_id,
                MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                MemorySnapshot.scope_id == chapter_id,
                MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                MemorySnapshot.status == MemoryStatus.ACTIVE,
            )
            .order_by(MemorySnapshot.version.desc())
        ).first()

    def supersede_and_create_next(
        self,
        *,
        current_snapshot: MemorySnapshot | None,
        document_id: str,
        chapter_id: str,
        content_json: dict[str, Any],
    ) -> MemorySnapshot:
        now = _utcnow()
        next_version = (current_snapshot.version + 1) if current_snapshot is not None else 1
        if current_snapshot is not None:
            current_snapshot.status = MemoryStatus.SUPERSEDED
            self.session.merge(current_snapshot)

        snapshot = MemorySnapshot(
            id=stable_id(
                "snapshot",
                document_id,
                SnapshotType.CHAPTER_TRANSLATION_MEMORY.value,
                chapter_id,
                next_version,
            ),
            document_id=document_id,
            scope_type=MemoryScopeType.CHAPTER,
            scope_id=chapter_id,
            snapshot_type=SnapshotType.CHAPTER_TRANSLATION_MEMORY,
            version=next_version,
            content_json=content_json,
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )
        self.session.merge(snapshot)
        return snapshot
