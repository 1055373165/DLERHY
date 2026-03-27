from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.enums import MemoryProposalStatus, MemoryScopeType, MemoryStatus, SnapshotType
from book_agent.domain.models import ChapterMemoryProposal, MemorySnapshot


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

    def load_proposal_by_translation_run(self, *, translation_run_id: str) -> ChapterMemoryProposal | None:
        return self.session.scalars(
            select(ChapterMemoryProposal).where(ChapterMemoryProposal.translation_run_id == translation_run_id)
        ).first()

    def create_or_replace_proposal(
        self,
        *,
        current_snapshot: MemorySnapshot | None,
        document_id: str,
        chapter_id: str,
        packet_id: str,
        translation_run_id: str,
        proposed_content_json: dict[str, Any],
    ) -> ChapterMemoryProposal:
        now = _utcnow()
        proposal_id = stable_id("chapter-memory-proposal", translation_run_id)
        proposal = self.session.get(ChapterMemoryProposal, proposal_id)
        if proposal is None:
            proposal = ChapterMemoryProposal(
                id=proposal_id,
                document_id=document_id,
                chapter_id=chapter_id,
                packet_id=packet_id,
                translation_run_id=translation_run_id,
                base_snapshot_id=current_snapshot.id if current_snapshot is not None else None,
                base_snapshot_version=current_snapshot.version if current_snapshot is not None else None,
                proposed_content_json=proposed_content_json,
                status=MemoryProposalStatus.PROPOSED,
                committed_snapshot_id=None,
                committed_at=None,
                created_at=now,
                updated_at=now,
            )
        else:
            proposal.base_snapshot_id = current_snapshot.id if current_snapshot is not None else None
            proposal.base_snapshot_version = current_snapshot.version if current_snapshot is not None else None
            proposal.proposed_content_json = proposed_content_json
            proposal.status = MemoryProposalStatus.PROPOSED
            proposal.committed_snapshot_id = None
            proposal.committed_at = None
            proposal.updated_at = now
        self.session.merge(proposal)
        self.session.flush()
        return proposal

    def mark_proposal_committed(
        self,
        proposal: ChapterMemoryProposal,
        *,
        committed_snapshot: MemorySnapshot,
    ) -> ChapterMemoryProposal:
        now = _utcnow()
        proposal.status = MemoryProposalStatus.COMMITTED
        proposal.committed_snapshot_id = committed_snapshot.id
        proposal.committed_at = now
        proposal.updated_at = now
        self.session.merge(proposal)
        self.session.flush()
        return proposal
