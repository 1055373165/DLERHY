"""Chapter memory proposal queries and review decisions."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.application.read_models import (
    ChapterMemoryProposalDecisionAuditSummary,
    ChapterMemoryProposalDecisionResult,
    ChapterMemoryProposalQueueSummary,
    ChapterMemoryProposalSummary,
    ChapterMemoryProposalSurface,
)
from book_agent.domain.enums import (
    ActorType,
    MemoryProposalStatus,
    MemoryScopeType,
    MemoryStatus,
    SnapshotType,
)
from book_agent.domain.models import (
    ChapterMemoryProposal,
    MemorySnapshot,
)
from book_agent.domain.models.ops import AuditEvent
from book_agent.services.memory_service import MemoryService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChapterMemoryProposalService:
    """Lists chapter memory proposals and records approve / reject decisions."""

    def __init__(
        self,
        session: Session,
        memory_service: MemoryService,
    ) -> None:
        self.session = session
        self.memory_service = memory_service

    def list_chapter_memory_proposals(
        self,
        document_id: str,
        chapter_id: str,
        *,
        status: str | None = None,
    ) -> list[ChapterMemoryProposalSummary]:
        normalized_status = None
        if status is not None:
            normalized_status = MemoryProposalStatus(str(status).strip().lower())
        proposals = self.memory_service.list_chapter_proposals(
            document_id=document_id,
            chapter_id=chapter_id,
            status=normalized_status,
        )
        audit_map = self._latest_decision_map(proposal.id for proposal in proposals)
        return [
            self._proposal_summary(
                proposal,
                last_decision=audit_map.get(proposal.id),
            )
            for proposal in proposals
        ]

    def approve_chapter_memory_proposal(
        self,
        document_id: str,
        chapter_id: str,
        proposal_id: str,
        *,
        actor_name: str | None = None,
        note: str | None = None,
    ) -> ChapterMemoryProposalDecisionResult:
        committed_snapshot = self.memory_service.approve_proposal(
            document_id=document_id,
            chapter_id=chapter_id,
            proposal_id=proposal_id,
        )
        decision_audit = self._record_decision_audit(
            proposal_id=proposal_id,
            document_id=document_id,
            chapter_id=chapter_id,
            decision="approved",
            actor_name=actor_name,
            note=note,
        )
        proposal = self.memory_service.chapter_memory_repository.load_proposal(proposal_id=proposal_id)
        if proposal is None:
            raise ValueError(f"Chapter memory proposal not found after approval: {proposal_id}")
        return ChapterMemoryProposalDecisionResult(
            document_id=document_id,
            chapter_id=chapter_id,
            decision="approved",
            proposal=self._proposal_summary(proposal, last_decision=decision_audit),
            committed_snapshot_id=committed_snapshot.id,
            committed_snapshot_version=committed_snapshot.version,
        )

    def reject_chapter_memory_proposal(
        self,
        document_id: str,
        chapter_id: str,
        proposal_id: str,
        *,
        actor_name: str | None = None,
        note: str | None = None,
    ) -> ChapterMemoryProposalDecisionResult:
        proposal = self.memory_service.reject_proposal(
            document_id=document_id,
            chapter_id=chapter_id,
            proposal_id=proposal_id,
        )
        decision_audit = self._record_decision_audit(
            proposal_id=proposal_id,
            document_id=document_id,
            chapter_id=chapter_id,
            decision="rejected",
            actor_name=actor_name,
            note=note,
        )
        return ChapterMemoryProposalDecisionResult(
            document_id=document_id,
            chapter_id=chapter_id,
            decision="rejected",
            proposal=self._proposal_summary(proposal, last_decision=decision_audit),
        )

    def _proposal_summary(
        self,
        proposal: ChapterMemoryProposal,
        *,
        last_decision: ChapterMemoryProposalDecisionAuditSummary | None = None,
    ) -> ChapterMemoryProposalSummary:
        return ChapterMemoryProposalSummary(
            proposal_id=proposal.id,
            packet_id=proposal.packet_id,
            translation_run_id=proposal.translation_run_id,
            status=proposal.status.value,
            base_snapshot_version=proposal.base_snapshot_version,
            committed_snapshot_id=proposal.committed_snapshot_id,
            created_at=proposal.created_at.isoformat(),
            updated_at=proposal.updated_at.isoformat(),
            last_decision=last_decision,
        )

    def proposal_surface(
        self,
        *,
        document_id: str,
        chapter_id: str,
    ) -> ChapterMemoryProposalSurface:
        proposals = self.memory_service.list_chapter_proposals(
            document_id=document_id,
            chapter_id=chapter_id,
            status=None,
        )
        counts_by_status = {
            MemoryProposalStatus.PROPOSED.value: 0,
            MemoryProposalStatus.COMMITTED.value: 0,
            MemoryProposalStatus.REJECTED.value: 0,
        }
        audit_map = self._latest_decision_map(proposal.id for proposal in proposals)
        pending_proposals: list[ChapterMemoryProposalSummary] = []
        latest_proposal_updated_at: str | None = None
        for proposal in proposals:
            counts_by_status[proposal.status.value] = counts_by_status.get(proposal.status.value, 0) + 1
            updated_at = proposal.updated_at.isoformat()
            if latest_proposal_updated_at is None or updated_at > latest_proposal_updated_at:
                latest_proposal_updated_at = updated_at
            if proposal.status == MemoryProposalStatus.PROPOSED:
                pending_proposals.append(
                    self._proposal_summary(
                        proposal,
                        last_decision=audit_map.get(proposal.id),
                    )
                )

        latest_snapshot = self.memory_service.load_latest_chapter_memory(
            document_id=document_id,
            chapter_id=chapter_id,
        )
        return ChapterMemoryProposalSurface(
            proposal_count=len(proposals),
            pending_proposal_count=len(pending_proposals),
            counts_by_status=counts_by_status,
            latest_proposal_updated_at=latest_proposal_updated_at,
            active_snapshot_version=(latest_snapshot.version if latest_snapshot is not None else None),
            pending_proposals=pending_proposals,
            recent_decisions=self._recent_decisions(chapter_id=chapter_id),
        )

    def _latest_decision_map(
        self,
        proposal_ids,
    ) -> dict[str, ChapterMemoryProposalDecisionAuditSummary]:
        normalized_proposal_ids = tuple(dict.fromkeys(str(proposal_id) for proposal_id in proposal_ids if proposal_id))
        if not normalized_proposal_ids:
            return {}
        rows = self.session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.object_type == "chapter_memory_proposal",
                AuditEvent.object_id.in_(normalized_proposal_ids),
                AuditEvent.action.in_(("chapter.memory_proposal.approved", "chapter.memory_proposal.rejected")),
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        ).all()
        latest_by_proposal: dict[str, ChapterMemoryProposalDecisionAuditSummary] = {}
        for audit in rows:
            proposal_id = str(audit.object_id)
            if proposal_id in latest_by_proposal:
                continue
            latest_by_proposal[proposal_id] = self._decision_audit_summary(audit)
        return latest_by_proposal

    def _recent_decisions(
        self,
        *,
        chapter_id: str,
        limit: int = 5,
    ) -> list[ChapterMemoryProposalDecisionAuditSummary]:
        audits = self.session.scalars(
            select(AuditEvent)
            .join(ChapterMemoryProposal, ChapterMemoryProposal.id == AuditEvent.object_id)
            .where(
                AuditEvent.object_type == "chapter_memory_proposal",
                ChapterMemoryProposal.chapter_id == chapter_id,
                AuditEvent.action.in_(("chapter.memory_proposal.approved", "chapter.memory_proposal.rejected")),
            )
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(limit)
        ).all()
        return [self._decision_audit_summary(audit) for audit in audits]

    def _decision_audit_summary(
        self,
        audit: AuditEvent,
    ) -> ChapterMemoryProposalDecisionAuditSummary:
        payload = dict(audit.payload_json or {})
        action = str(audit.action)
        decision = "approved" if action.endswith(".approved") else "rejected"
        return ChapterMemoryProposalDecisionAuditSummary(
            proposal_id=str(audit.object_id),
            decision=decision,
            actor_type=audit.actor_type.value,
            actor_id=audit.actor_id,
            note=(str(payload.get("note")) if payload.get("note") is not None else None),
            created_at=audit.created_at.isoformat(),
        )

    def _record_decision_audit(
        self,
        *,
        proposal_id: str,
        document_id: str,
        chapter_id: str,
        decision: str,
        actor_name: str | None,
        note: str | None,
    ) -> ChapterMemoryProposalDecisionAuditSummary:
        normalized_actor_name = (actor_name or "").strip() or None
        normalized_note = (note or "").strip() or None
        audit = AuditEvent(
            object_type="chapter_memory_proposal",
            object_id=proposal_id,
            action=f"chapter.memory_proposal.{decision}",
            actor_type=(ActorType.HUMAN if normalized_actor_name else ActorType.SYSTEM),
            actor_id=(normalized_actor_name or "memory-proposal-api"),
            created_at=_utcnow(),
            payload_json={
                "document_id": document_id,
                "chapter_id": chapter_id,
                "proposal_id": proposal_id,
                "decision": decision,
                "note": normalized_note,
            },
        )
        self.session.add(audit)
        self.session.flush()
        return self._decision_audit_summary(audit)

    def proposal_queue_map(
        self,
        document_id: str,
    ) -> dict[str, ChapterMemoryProposalQueueSummary]:
        proposal_rows = self.session.execute(
            select(
                ChapterMemoryProposal.chapter_id,
                ChapterMemoryProposal.status,
                func.count(ChapterMemoryProposal.id),
                func.max(ChapterMemoryProposal.updated_at),
            )
            .where(ChapterMemoryProposal.document_id == document_id)
            .group_by(ChapterMemoryProposal.chapter_id, ChapterMemoryProposal.status)
        ).all()
        snapshot_rows = self.session.execute(
            select(MemorySnapshot.scope_id, MemorySnapshot.version)
            .where(
                MemorySnapshot.document_id == document_id,
                MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                MemorySnapshot.status == MemoryStatus.ACTIVE,
            )
        ).all()

        by_chapter: dict[str, ChapterMemoryProposalQueueSummary] = {}
        for chapter_id, proposal_status, proposal_count, latest_updated_at in proposal_rows:
            normalized_chapter_id = str(chapter_id)
            summary = by_chapter.get(normalized_chapter_id)
            if summary is None:
                summary = ChapterMemoryProposalQueueSummary(
                    proposal_count=0,
                    pending_proposal_count=0,
                    counts_by_status={
                        MemoryProposalStatus.PROPOSED.value: 0,
                        MemoryProposalStatus.COMMITTED.value: 0,
                        MemoryProposalStatus.REJECTED.value: 0,
                    },
                    latest_proposal_updated_at=None,
                    active_snapshot_version=None,
                )
                by_chapter[normalized_chapter_id] = summary

            status_value = proposal_status.value
            count_value = int(proposal_count or 0)
            summary.proposal_count += count_value
            summary.counts_by_status[status_value] = summary.counts_by_status.get(status_value, 0) + count_value
            if status_value == MemoryProposalStatus.PROPOSED.value:
                summary.pending_proposal_count += count_value
            updated_at_value = latest_updated_at.isoformat() if latest_updated_at is not None else None
            if (
                updated_at_value is not None
                and (
                    summary.latest_proposal_updated_at is None
                    or updated_at_value > summary.latest_proposal_updated_at
                )
            ):
                summary.latest_proposal_updated_at = updated_at_value

        for scope_id, version in snapshot_rows:
            normalized_chapter_id = str(scope_id)
            summary = by_chapter.get(normalized_chapter_id)
            if summary is None:
                summary = ChapterMemoryProposalQueueSummary(
                    proposal_count=0,
                    pending_proposal_count=0,
                    counts_by_status={
                        MemoryProposalStatus.PROPOSED.value: 0,
                        MemoryProposalStatus.COMMITTED.value: 0,
                        MemoryProposalStatus.REJECTED.value: 0,
                    },
                    latest_proposal_updated_at=None,
                    active_snapshot_version=None,
                )
                by_chapter[normalized_chapter_id] = summary
            summary.active_snapshot_version = int(version)

        return by_chapter
