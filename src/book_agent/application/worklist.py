"""Chapter worklist queue, chapter detail and owner assignment."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.application import analytics
from book_agent.application.issue_queries import IssueQueries
from book_agent.application.memory_proposals import ChapterMemoryProposalService
from book_agent.application.read_models import (
    ChapterWorklistAction,
    ChapterWorklistAssignmentHistoryEntry,
    ChapterWorklistAssignmentSummary,
    ChapterWorklistIssue,
    DocumentChapterWorklist,
    DocumentChapterWorklistDetail,
)
from book_agent.domain.enums import (
    ActorType,
    PacketStatus,
)
from book_agent.domain.models import ChapterWorklistAssignment
from book_agent.domain.models.ops import AuditEvent
from book_agent.domain.models.review import (
    IssueAction,
    ReviewIssue,
)
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.ops import OpsRepository
from book_agent.infra.repositories.review import ReviewRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChapterWorklistService:
    """Builds the chapter review queue and records owner assignments."""

    def __init__(
        self,
        session: Session,
        bootstrap_repository: BootstrapRepository,
        review_repository: ReviewRepository,
        ops_repository: OpsRepository,
        issue_queries: IssueQueries,
        memory_proposals: ChapterMemoryProposalService,
    ) -> None:
        self.session = session
        self.bootstrap_repository = bootstrap_repository
        self.review_repository = review_repository
        self.ops_repository = ops_repository
        self.issue_queries = issue_queries
        self.memory_proposals = memory_proposals

    def get_document_chapter_worklist(
        self,
        document_id: str,
        *,
        queue_priority: str | None = None,
        sla_status: str | None = None,
        owner_ready: bool | None = None,
        needs_immediate_attention: bool | None = None,
        assigned: bool | None = None,
        assigned_owner_name: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> DocumentChapterWorklist:
        self.bootstrap_repository.load_document_bundle(document_id)

        issue_chapter_breakdown = self.issue_queries.chapter_breakdown(document_id)
        issue_chapter_heatmap = analytics.issue_chapter_heatmap(issue_chapter_breakdown)
        issue_chapter_activity = self.issue_queries.chapter_activity_map(document_id)
        issue_chapter_worklist_meta = self.issue_queries.chapter_worklist_meta(document_id)
        chapter_assignment_map = self.issue_queries.chapter_assignment_map(document_id)
        chapter_memory_proposal_map = self.memory_proposals.proposal_queue_map(document_id)
        entries = analytics.issue_chapter_queue(
            issue_chapter_heatmap,
            issue_chapter_activity,
            issue_chapter_worklist_meta,
            chapter_assignment_map,
            chapter_memory_proposal_map,
        )

        filtered_entries = [
            entry
            for entry in entries
            if (queue_priority is None or entry.queue_priority == queue_priority)
            and (sla_status is None or entry.sla_status == sla_status)
            and (owner_ready is None or entry.owner_ready == owner_ready)
            and (
                needs_immediate_attention is None
                or entry.needs_immediate_attention == needs_immediate_attention
            )
            and (assigned is None or entry.is_assigned == assigned)
            and (
                assigned_owner_name is None
                or entry.assigned_owner_name == assigned_owner_name
            )
        ]
        paged_entries = filtered_entries[offset : (offset + limit) if limit is not None else None]

        queue_priority_counts: dict[str, int] = {}
        sla_status_counts: dict[str, int] = {}
        for entry in entries:
            queue_priority_counts[entry.queue_priority] = (
                queue_priority_counts.get(entry.queue_priority, 0) + 1
            )
            sla_status_counts[entry.sla_status] = sla_status_counts.get(entry.sla_status, 0) + 1

        owner_workload_summary = analytics.owner_workload_summary(entries)

        return DocumentChapterWorklist(
            document_id=document_id,
            worklist_count=len(entries),
            filtered_worklist_count=len(filtered_entries),
            entry_count=len(paged_entries),
            offset=offset,
            limit=limit,
            has_more=(offset + len(paged_entries)) < len(filtered_entries),
            applied_queue_priority_filter=queue_priority,
            applied_sla_status_filter=sla_status,
            applied_owner_ready_filter=owner_ready,
            applied_needs_immediate_attention_filter=needs_immediate_attention,
            applied_assigned_filter=assigned,
            applied_assigned_owner_filter=assigned_owner_name,
            queue_priority_counts=queue_priority_counts,
            sla_status_counts=sla_status_counts,
            immediate_attention_count=sum(1 for entry in entries if entry.needs_immediate_attention),
            owner_ready_count=sum(1 for entry in entries if entry.owner_ready),
            assigned_count=sum(1 for entry in entries if entry.is_assigned),
            owner_workload_summary=owner_workload_summary,
            owner_workload_highlights=analytics.owner_workload_highlights(owner_workload_summary),
            highlights=analytics.issue_chapter_worklist_highlights(entries),
            entries=paged_entries,
        )

    def get_document_chapter_worklist_detail(
        self,
        document_id: str,
        chapter_id: str,
    ) -> DocumentChapterWorklistDetail:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        chapter_bundle = next(
            (chapter_bundle for chapter_bundle in bundle.chapters if chapter_bundle.chapter.id == chapter_id),
            None,
        )
        if chapter_bundle is None:
            raise ValueError(f"Chapter not found in document: {chapter_id}")

        issue_family_breakdown = [
            entry
            for entry in self.issue_queries.chapter_breakdown(document_id)
            if entry.chapter_id == chapter_id
        ]
        chapter_activity = self.issue_queries.chapter_activity_map(document_id)
        chapter_worklist_meta = self.issue_queries.chapter_worklist_meta(document_id)
        chapter_assignment_map = self.issue_queries.chapter_assignment_map(document_id)
        chapter_memory_proposal_map = self.memory_proposals.proposal_queue_map(document_id)
        queue_entries = analytics.issue_chapter_queue(
            analytics.issue_chapter_heatmap(issue_family_breakdown),
            chapter_activity,
            chapter_worklist_meta,
            chapter_assignment_map,
            chapter_memory_proposal_map,
        )
        queue_entry = queue_entries[0] if queue_entries else None
        quality_summary = self.review_repository.load_quality_summaries_for_document(document_id).get(chapter_id)
        memory_proposals = self.memory_proposals.proposal_surface(
            document_id=document_id,
            chapter_id=chapter_id,
        )
        recent_actions = self._recent_actions(chapter_id)
        assignment_history = self._assignment_history(chapter_id)

        return DocumentChapterWorklistDetail(
            document_id=document_id,
            chapter_id=chapter_id,
            ordinal=chapter_bundle.chapter.ordinal,
            title_src=chapter_bundle.chapter.title_src,
            chapter_status=chapter_bundle.chapter.status.value,
            packet_count=len(chapter_bundle.translation_packets),
            translated_packet_count=sum(
                1
                for packet in chapter_bundle.translation_packets
                if packet.status == PacketStatus.TRANSLATED
            ),
            current_issue_count=sum(entry.issue_count for entry in issue_family_breakdown),
            current_open_issue_count=sum(entry.open_issue_count for entry in issue_family_breakdown),
            current_triaged_issue_count=sum(entry.triaged_issue_count for entry in issue_family_breakdown),
            current_active_blocking_issue_count=sum(
                entry.active_blocking_issue_count for entry in issue_family_breakdown
            ),
            assignment=chapter_assignment_map.get(chapter_id),
            queue_entry=queue_entry,
            quality_summary=analytics.stored_quality_summary(quality_summary),
            issue_family_breakdown=issue_family_breakdown,
            recent_issues=self._recent_issues(chapter_id),
            recent_actions=recent_actions,
            assignment_history=assignment_history,
            memory_proposals=memory_proposals,
            timeline=analytics.chapter_worklist_timeline(
                recent_actions=recent_actions,
                assignment_history=assignment_history,
                memory_decisions=memory_proposals.recent_decisions,
            ),
        )

    def assign_document_chapter_worklist_owner(
        self,
        document_id: str,
        chapter_id: str,
        *,
        owner_name: str,
        assigned_by: str,
        note: str | None = None,
    ) -> ChapterWorklistAssignmentSummary:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        if not any(chapter_bundle.chapter.id == chapter_id for chapter_bundle in bundle.chapters):
            raise ValueError(f"Chapter not found in document: {chapter_id}")

        assignment = self.session.scalar(
            select(ChapterWorklistAssignment).where(ChapterWorklistAssignment.chapter_id == chapter_id)
        )
        if assignment is None:
            assignment = ChapterWorklistAssignment(
                document_id=document_id,
                chapter_id=chapter_id,
            )
            self.session.add(assignment)
        assignment.document_id = document_id
        assignment.chapter_id = chapter_id
        assignment.owner_name = owner_name
        assignment.assigned_by = assigned_by
        assignment.note = note
        assignment.assigned_at = _utcnow()
        self.session.flush()
        self.session.refresh(assignment)

        audit = AuditEvent(
            object_type="chapter",
            object_id=chapter_id,
            action="chapter.worklist.assignment.set",
            actor_type=ActorType.HUMAN,
            actor_id=assigned_by,
            created_at=_utcnow(),
            payload_json={
                "document_id": document_id,
                "chapter_id": chapter_id,
                "owner_name": owner_name,
                "note": note,
            },
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()
        return analytics.assignment_summary(assignment)

    def clear_document_chapter_worklist_owner(
        self,
        document_id: str,
        chapter_id: str,
        *,
        cleared_by: str,
        note: str | None = None,
    ) -> ChapterWorklistAssignmentSummary:
        self.bootstrap_repository.load_document_bundle(document_id)
        assignment = self.session.scalar(
            select(ChapterWorklistAssignment).where(
                ChapterWorklistAssignment.document_id == document_id,
                ChapterWorklistAssignment.chapter_id == chapter_id,
            )
        )
        if assignment is None:
            raise ValueError(f"Chapter worklist assignment not found: {chapter_id}")

        summary = analytics.assignment_summary(assignment)
        self.session.delete(assignment)
        self.session.flush()
        audit = AuditEvent(
            object_type="chapter",
            object_id=chapter_id,
            action="chapter.worklist.assignment.cleared",
            actor_type=ActorType.HUMAN,
            actor_id=cleared_by,
            created_at=_utcnow(),
            payload_json={
                "document_id": document_id,
                "chapter_id": chapter_id,
                "owner_name": summary.owner_name,
                "note": note,
            },
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()
        return summary

    def _recent_issues(
        self,
        chapter_id: str,
        *,
        limit: int = 10,
    ) -> list[ChapterWorklistIssue]:
        issues = self.session.scalars(
            select(ReviewIssue)
            .where(ReviewIssue.chapter_id == chapter_id)
            .order_by(ReviewIssue.updated_at.desc(), ReviewIssue.created_at.desc())
            .limit(limit)
        ).all()
        return [
            ChapterWorklistIssue(
                issue_id=issue.id,
                issue_type=issue.issue_type,
                root_cause_layer=issue.root_cause_layer.value,
                severity=issue.severity.value,
                status=issue.status.value,
                blocking=issue.blocking,
                detector=issue.detector.value,
                suggested_action=issue.suggested_action,
                created_at=issue.created_at.isoformat(),
                updated_at=issue.updated_at.isoformat(),
            )
            for issue in issues
        ]

    def _recent_actions(
        self,
        chapter_id: str,
        *,
        limit: int = 10,
    ) -> list[ChapterWorklistAction]:
        rows = self.session.execute(
            select(IssueAction, ReviewIssue.issue_type)
            .join(ReviewIssue, IssueAction.issue_id == ReviewIssue.id)
            .where(ReviewIssue.chapter_id == chapter_id)
            .order_by(IssueAction.updated_at.desc(), IssueAction.created_at.desc())
            .limit(limit)
        ).all()
        return [
            ChapterWorklistAction(
                action_id=action.id,
                issue_id=action.issue_id,
                issue_type=issue_type,
                action_type=action.action_type.value,
                scope_type=action.scope_type.value,
                scope_id=action.scope_id,
                status=action.status.value,
                created_by=action.created_by.value,
                created_at=action.created_at.isoformat(),
                updated_at=action.updated_at.isoformat(),
            )
            for action, issue_type in rows
        ]

    def _assignment_history(
        self,
        chapter_id: str,
        *,
        limit: int = 20,
    ) -> list[ChapterWorklistAssignmentHistoryEntry]:
        assignment_actions = {
            "chapter.worklist.assignment.set": "set",
            "chapter.worklist.assignment.cleared": "cleared",
        }
        events = self.session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.object_type == "chapter",
                AuditEvent.object_id == chapter_id,
                AuditEvent.action.in_(tuple(assignment_actions.keys())),
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
        ).all()
        return [
            ChapterWorklistAssignmentHistoryEntry(
                event_id=event.id,
                event_type=assignment_actions[event.action],
                owner_name=(event.payload_json.get("owner_name") if event.payload_json else None),
                performed_by=event.actor_id,
                note=(event.payload_json.get("note") if event.payload_json else None),
                created_at=event.created_at.isoformat(),
            )
            for event in events
        ]
