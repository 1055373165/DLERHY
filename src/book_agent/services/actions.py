from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    ActionStatus,
    ActionType,
    ActorType,
    ChapterStatus,
    InvalidatedByType,
    InvalidatedObjectType,
    JobScopeType,
    PacketStatus,
    SentenceStatus,
    TargetSegmentStatus,
)
from book_agent.domain.models import ArtifactInvalidation, AuditEvent, Sentence
from book_agent.infra.repositories.ops import OpsRepository, PacketInvalidationBundle
from book_agent.orchestrator.rerun import RerunPlan, build_rerun_plan


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ActionExecutionArtifacts:
    rerun_plan: RerunPlan
    invalidations: list[ArtifactInvalidation]
    audits: list[AuditEvent]


class IssueActionExecutor:
    def __init__(self, repository: OpsRepository):
        self.repository = repository

    def execute(self, action_id: str) -> ActionExecutionArtifacts:
        action = self.repository.get_issue_action(action_id)
        issue = self.repository.get_issue(action.issue_id)
        now = _utcnow()
        action.status = ActionStatus.RUNNING
        self.repository.mark_issue_triaged(issue, "Action executed; awaiting rerun validation.")
        rerun_plan = build_rerun_plan(issue, action)

        invalidations: list[ArtifactInvalidation] = []
        audits: list[AuditEvent] = []

        if rerun_plan.action_type == ActionType.REALIGN_ONLY and rerun_plan.scope_type == JobScopeType.PACKET:
            for packet_id in rerun_plan.scope_ids:
                audits.append(self._audit("packet", packet_id, "packet.marked_for_realign", issue.id, now))
        elif rerun_plan.action_type == ActionType.REPARSE_CHAPTER and rerun_plan.scope_type == JobScopeType.CHAPTER:
            for chapter_id in rerun_plan.scope_ids:
                audits.append(self._audit("chapter", chapter_id, "chapter.marked_for_reparse", issue.id, now))
        elif rerun_plan.action_type == ActionType.REPARSE_DOCUMENT and rerun_plan.scope_type == JobScopeType.DOCUMENT:
            for document_id in rerun_plan.scope_ids:
                audits.append(self._audit("document", document_id, "document.marked_for_reparse", issue.id, now))
        elif rerun_plan.scope_type == JobScopeType.PACKET:
            for packet_id in rerun_plan.scope_ids:
                invalidations.extend(self._invalidate_packet_scope(packet_id, issue.id, now))
        elif rerun_plan.scope_type == JobScopeType.CHAPTER:
            for chapter_id in rerun_plan.scope_ids:
                for bundle in self.repository.list_packet_bundles_for_chapter(chapter_id):
                    invalidations.extend(self._invalidate_packet_bundle(bundle, issue.id, now))
                chapter = self.repository.get_chapter(chapter_id)
                chapter.status = ChapterStatus.PACKET_BUILT
                audits.append(self._audit("chapter", chapter.id, "chapter.invalidated", issue.id, now))
        elif rerun_plan.scope_type == JobScopeType.SENTENCE and rerun_plan.scope_ids:
            for sentence_id in rerun_plan.scope_ids:
                audits.append(self._audit("sentence", sentence_id, "sentence.marked_for_manual_review", issue.id, now))

        action.status = ActionStatus.COMPLETED
        action.updated_at = now
        self.repository.save_invalidations(action, invalidations, audits)
        self.repository.session.flush()
        return ActionExecutionArtifacts(rerun_plan=rerun_plan, invalidations=invalidations, audits=audits)

    def _invalidate_packet_scope(self, packet_id: str, issue_id: str, now: datetime) -> list[ArtifactInvalidation]:
        bundle = self.repository.get_packet_bundle(packet_id)
        return self._invalidate_packet_bundle(bundle, issue_id, now)

    def _invalidate_packet_bundle(
        self,
        bundle: PacketInvalidationBundle,
        issue_id: str,
        now: datetime,
    ) -> list[ArtifactInvalidation]:
        invalidations: list[ArtifactInvalidation] = []
        bundle.packet.status = PacketStatus.INVALIDATED
        bundle.packet.updated_at = now
        invalidations.append(self._invalidation(InvalidatedObjectType.PACKET, bundle.packet.id, issue_id, now))

        for run in bundle.translation_runs:
            invalidations.append(self._invalidation(InvalidatedObjectType.TRANSLATION_RUN, run.id, issue_id, now))
        for segment in bundle.target_segments:
            segment.final_status = TargetSegmentStatus.SUPERSEDED
            segment.updated_at = now
            invalidations.append(self._invalidation(InvalidatedObjectType.TARGET_SEGMENT, segment.id, issue_id, now))
        for edge in bundle.alignment_edges:
            invalidations.append(self._invalidation(InvalidatedObjectType.ALIGNMENT_EDGE, edge.id, issue_id, now))

        for sentence_id in bundle.sentence_ids:
            sentence = self.repository.session.get(Sentence, sentence_id)
            if sentence is not None:
                sentence.sentence_status = SentenceStatus.PENDING
                sentence.updated_at = now
                invalidations.append(self._invalidation(InvalidatedObjectType.SENTENCE, sentence.id, issue_id, now))
        return invalidations

    def _invalidation(
        self,
        object_type: InvalidatedObjectType,
        object_id: str,
        issue_id: str,
        now: datetime,
    ) -> ArtifactInvalidation:
        return ArtifactInvalidation(
            id=stable_id("artifact-invalidation", object_type.value, object_id, issue_id),
            object_type=object_type,
            object_id=object_id,
            invalidated_by_type=InvalidatedByType.ISSUE,
            invalidated_by_id=issue_id,
            reason_json={"issue_id": issue_id},
            created_at=now,
        )

    def _audit(self, object_type: str, object_id: str, action: str, issue_id: str, now: datetime) -> AuditEvent:
        return AuditEvent(
            id=stable_id("audit", object_type, object_id, action, issue_id),
            object_type=object_type,
            object_id=object_id,
            action=action,
            actor_type=ActorType.SYSTEM,
            actor_id="issue-action-executor",
            payload_json={"issue_id": issue_id},
            created_at=now,
        )
