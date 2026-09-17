from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import (
    ActionStatus,
    ArtifactStatus,
    Detector,
    IssueEventKind,
    IssueStatus,
    MemoryScopeType,
    MemoryStatus,
    RootCauseLayer,
    SnapshotType,
    TermStatus,
)
from book_agent.domain.models import Block, Chapter, Document, MemorySnapshot, Sentence
from book_agent.domain.models.review import ChapterQualitySummary, IssueAction, ReviewIssue, ReviewIssueEvent
from book_agent.domain.models.translation import AlignmentEdge, TargetSegment, TermEntry, TranslationPacket, TranslationRun


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_export_gate_issue(issue: ReviewIssue) -> bool:
    """Issues the export gate owns (creates and auto-resolves); the review pass must not touch them."""
    if issue.root_cause_layer == RootCauseLayer.EXPORT:
        return True
    return issue.issue_type == "LAYOUT_VALIDATION_FAILURE" and (
        (issue.evidence_json or {}).get("reason") == "export_layout_validation"
    )


def review_pass_owns(issue: ReviewIssue) -> bool:
    """Issues the rule-based review pass may auto-resolve when it no longer
    sees them: everything except export-time issues (the export gate owns
    those) and model-detected ones (the Reviewer Agent pass owns those).
    Human-filed issues are closed by a clean pass after the repair rerun,
    as before."""
    return issue.detector != Detector.MODEL and not is_export_gate_issue(issue)


@dataclass(slots=True)
class IssueSyncResult:
    """What one detection pass did to the issue ledger. ``issues``/``actions``
    are the persisted rows for the detected set, in detection order."""

    issues: list[ReviewIssue] = field(default_factory=list)
    actions: list[IssueAction] = field(default_factory=list)
    opened: list[ReviewIssue] = field(default_factory=list)
    updated: list[ReviewIssue] = field(default_factory=list)
    reopened: list[ReviewIssue] = field(default_factory=list)
    seen_while_closed: list[ReviewIssue] = field(default_factory=list)
    resolved: list[ReviewIssue] = field(default_factory=list)
    replanned_actions: list[IssueAction] = field(default_factory=list)


_MATERIAL_FIELDS = ("severity", "blocking", "evidence_json", "packet_id", "sentence_id", "block_id", "confidence")


@dataclass(slots=True)
class ChapterReviewBundle:
    document: Document
    chapter: Chapter
    blocks: list[Block]
    sentences: list[Sentence]
    packets: list[TranslationPacket]
    chapter_brief: MemorySnapshot | None
    chapter_translation_memory: MemorySnapshot | None
    translation_runs: list[TranslationRun]
    target_segments: list[TargetSegment]
    alignment_edges: list[AlignmentEdge]
    term_entries: list[TermEntry]
    existing_issues: list[ReviewIssue]


class ReviewRepository:
    def __init__(self, session: Session):
        self.session = session

    def load_chapter_bundle(self, chapter_id: str) -> ChapterReviewBundle:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError(f"Chapter not found: {chapter_id}")
        document = self.session.get(Document, chapter.document_id)
        if document is None:
            raise ValueError(f"Document not found: {chapter.document_id}")

        sentences = self.session.scalars(
            select(Sentence).where(Sentence.chapter_id == chapter_id)
        ).all()
        blocks = self.session.scalars(
            select(Block)
            .where(
                Block.chapter_id == chapter_id,
                Block.status == ArtifactStatus.ACTIVE,
            )
            .order_by(Block.ordinal)
        ).all()
        packets = self.session.scalars(
            select(TranslationPacket).where(TranslationPacket.chapter_id == chapter_id)
        ).all()
        packet_ids = [packet.id for packet in packets]
        translation_runs = self.session.scalars(
            select(TranslationRun).where(TranslationRun.packet_id.in_(packet_ids))
        ).all() if packet_ids else []
        target_segments = self.session.scalars(
            select(TargetSegment).where(TargetSegment.chapter_id == chapter_id)
        ).all()
        target_ids = [segment.id for segment in target_segments]
        alignment_edges = self.session.scalars(
            select(AlignmentEdge).where(AlignmentEdge.target_segment_id.in_(target_ids))
        ).all() if target_ids else []
        term_entries = self.session.scalars(
            select(TermEntry).where(
                TermEntry.document_id == chapter.document_id,
                TermEntry.status == TermStatus.ACTIVE,
                or_(
                    TermEntry.scope_type == MemoryScopeType.GLOBAL,
                    (
                        TermEntry.scope_type == MemoryScopeType.CHAPTER
                    ) & (
                        TermEntry.scope_id == chapter_id
                    ),
                ),
            )
        ).all()
        chapter_brief = self.session.scalar(
            select(MemorySnapshot).where(
                MemorySnapshot.document_id == chapter.document_id,
                MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                MemorySnapshot.scope_id == chapter_id,
                MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_BRIEF,
                MemorySnapshot.status == MemoryStatus.ACTIVE,
            )
        )
        chapter_translation_memory = self.session.scalar(
            select(MemorySnapshot).where(
                MemorySnapshot.document_id == chapter.document_id,
                MemorySnapshot.scope_type == MemoryScopeType.CHAPTER,
                MemorySnapshot.scope_id == chapter_id,
                MemorySnapshot.snapshot_type == SnapshotType.CHAPTER_TRANSLATION_MEMORY,
                MemorySnapshot.status == MemoryStatus.ACTIVE,
            )
        )
        existing_issues = self.session.scalars(
            select(ReviewIssue).where(ReviewIssue.chapter_id == chapter_id)
        ).all()

        return ChapterReviewBundle(
            document=document,
            chapter=chapter,
            blocks=blocks,
            sentences=sentences,
            packets=packets,
            chapter_brief=chapter_brief,
            chapter_translation_memory=chapter_translation_memory,
            translation_runs=translation_runs,
            target_segments=target_segments,
            alignment_edges=alignment_edges,
            term_entries=term_entries,
            existing_issues=existing_issues,
        )

    def save_review_artifacts(
        self,
        review_issues: list[ReviewIssue],
        issue_actions: list[IssueAction],
        chapter: Chapter,
        chapter_quality_summary: ChapterQualitySummary | None = None,
    ) -> None:
        """Persist chapter state and summary. Issues and actions go through
        ``sync_issues``; rows passed here are merged only when not yet persisted
        (callers that already synced pass the persisted instances)."""
        for issue in review_issues:
            if issue not in self.session:
                self.session.merge(issue)
        self.session.flush()
        for action in issue_actions:
            if action not in self.session:
                self.session.merge(action)
        self.session.merge(chapter)
        if chapter_quality_summary is not None:
            self.session.merge(chapter_quality_summary)
        self.session.flush()

    def load_quality_summaries_for_document(self, document_id: str) -> dict[str, ChapterQualitySummary]:
        summaries = self.session.scalars(
            select(ChapterQualitySummary).where(ChapterQualitySummary.document_id == document_id)
        ).all()
        return {summary.chapter_id: summary for summary in summaries}

    def upsert_chapter_quality_summary(
        self,
        *,
        document_id: str,
        chapter_id: str,
        issue_count: int,
        action_count: int,
        resolved_issue_count: int,
        coverage_ok: bool,
        alignment_ok: bool,
        term_ok: bool,
        format_ok: bool,
        blocking_issue_count: int,
        low_confidence_count: int,
        format_pollution_count: int,
    ) -> ChapterQualitySummary:
        summary = self.session.scalar(
            select(ChapterQualitySummary).where(ChapterQualitySummary.chapter_id == chapter_id)
        )
        if summary is None:
            summary = ChapterQualitySummary(
                document_id=document_id,
                chapter_id=chapter_id,
            )
        summary.document_id = document_id
        summary.chapter_id = chapter_id
        summary.issue_count = issue_count
        summary.action_count = action_count
        summary.resolved_issue_count = resolved_issue_count
        summary.coverage_ok = coverage_ok
        summary.alignment_ok = alignment_ok
        summary.term_ok = term_ok
        summary.format_ok = format_ok
        summary.blocking_issue_count = blocking_issue_count
        summary.low_confidence_count = low_confidence_count
        summary.format_pollution_count = format_pollution_count
        return summary

    def sync_issues(
        self,
        detected: list[ReviewIssue],
        actions: list[IssueAction],
        *,
        owned_existing: Iterable[ReviewIssue],
        resolution_note: str,
        actor_id: str,
        now: datetime | None = None,
    ) -> IssueSyncResult:
        """Reconcile one detection pass with the issue ledger.

        ``detected`` are freshly built (transient) issues with stable ids;
        ``owned_existing`` are the persisted issues this pass is responsible
        for, i.e. the only ones it may auto-resolve when it no longer sees them.

        Rules (R8): a first sighting inserts version 1 and an ``opened`` event.
        A re-sighting of an active issue keeps ``created_at`` and its status
        (TRIAGED stays TRIAGED) and only bumps the version when something
        material changed. A re-sighting of a system-resolved issue reopens it
        (``reopen_count`` grows, the resolution note is cleared) and re-plans
        its actions. A re-sighting of a system-TRIAGED issue (an action ran
        and awaited rerun validation) returns it to OPEN; human triage stays. A re-sighting of a human-closed issue (WONTFIX, or
        RESOLVED with ``decided_by``) changes nothing but ``last_seen_at`` and
        records ``seen_while_closed``. Actions keep their status across
        rounds; a COMPLETED action is re-planned only when its issue is seen
        again after the action finished.
        """
        now = now or _utcnow()
        result = IssueSyncResult()
        owned_by_id = {issue.id: issue for issue in owned_existing}
        detected_ids: set[str] = set()
        for incoming in detected:
            detected_ids.add(incoming.id)
            existing = owned_by_id.get(incoming.id) or self.session.get(ReviewIssue, incoming.id)
            if existing is None:
                incoming.version = 1
                incoming.reopen_count = 0
                incoming.last_seen_at = now
                incoming.created_at = incoming.created_at or now
                incoming.updated_at = incoming.updated_at or now
                self.session.add(incoming)
                self._append_issue_event(
                    incoming,
                    kind=IssueEventKind.OPENED,
                    from_status=None,
                    actor_id=actor_id,
                    note=None,
                    now=now,
                )
                result.issues.append(incoming)
                result.opened.append(incoming)
                continue
            self._resync_existing(existing, incoming, actor_id=actor_id, now=now, result=result)
            result.issues.append(existing)

        # No ORM relationship links actions to issues, so the unit of work
        # would not order the inserts; new issues must exist before their actions.
        self.session.flush()
        reopened_ids = {issue.id for issue in result.reopened}
        for action in actions:
            result.actions.append(self._sync_action(action, now=now, reopened=action.issue_id in reopened_ids, result=result))

        for issue in owned_by_id.values():
            if issue.id in detected_ids or issue.status not in (IssueStatus.OPEN, IssueStatus.TRIAGED):
                continue
            from_status = issue.status
            issue.status = IssueStatus.RESOLVED
            issue.resolution_note = resolution_note
            issue.updated_at = now
            issue.version = int(issue.version or 1) + 1
            self._append_issue_event(
                issue,
                kind=IssueEventKind.RESOLVED,
                from_status=from_status,
                actor_id=actor_id,
                note=resolution_note,
                now=now,
            )
            result.resolved.append(issue)
        self.session.flush()
        return result

    def _resync_existing(
        self,
        existing: ReviewIssue,
        incoming: ReviewIssue,
        *,
        actor_id: str,
        now: datetime,
        result: IssueSyncResult,
    ) -> None:
        existing.last_seen_at = now
        if existing.human_decided:
            self._append_issue_event(
                existing,
                kind=IssueEventKind.SEEN_WHILE_CLOSED,
                from_status=existing.status,
                actor_id=actor_id,
                note=None,
                now=now,
                evidence=dict(incoming.evidence_json or {}),
            )
            result.seen_while_closed.append(existing)
            return
        changed = any(getattr(existing, name) != getattr(incoming, name) for name in _MATERIAL_FIELDS)
        for name in _MATERIAL_FIELDS:
            setattr(existing, name, getattr(incoming, name))
        existing.detector = incoming.detector
        existing.root_cause_layer = incoming.root_cause_layer
        existing.suggested_action = incoming.suggested_action
        if existing.status == IssueStatus.RESOLVED:
            from_status = existing.status
            existing.status = IssueStatus.OPEN
            existing.resolution_note = None
            existing.decided_by = None
            existing.decided_at = None
            existing.reopen_count = int(existing.reopen_count or 0) + 1
            existing.version = int(existing.version or 1) + 1
            existing.updated_at = now
            self._append_issue_event(
                existing,
                kind=IssueEventKind.REOPENED,
                from_status=from_status,
                actor_id=actor_id,
                note=None,
                now=now,
            )
            result.reopened.append(existing)
            return
        if existing.status == IssueStatus.TRIAGED and existing.decided_by is None:
            # System triage means "action executed, awaiting rerun validation".
            # Seeing the issue again is that validation failing: back to OPEN.
            existing.status = IssueStatus.OPEN
            existing.resolution_note = None
            existing.version = int(existing.version or 1) + 1
            existing.updated_at = now
            self._append_issue_event(
                existing,
                kind=IssueEventKind.UPDATED,
                from_status=IssueStatus.TRIAGED,
                actor_id=actor_id,
                note="Rerun validation still detects the issue.",
                now=now,
            )
            result.updated.append(existing)
            return
        if changed:
            existing.version = int(existing.version or 1) + 1
            existing.updated_at = now
            self._append_issue_event(
                existing,
                kind=IssueEventKind.UPDATED,
                from_status=existing.status,
                actor_id=actor_id,
                note=None,
                now=now,
            )
            result.updated.append(existing)

    def _sync_action(
        self,
        action: IssueAction,
        *,
        now: datetime,
        reopened: bool,
        result: IssueSyncResult,
    ) -> IssueAction:
        existing = self.session.get(IssueAction, action.id)
        if existing is None:
            self.session.add(action)
            return action
        finished_at = existing.updated_at or now
        if finished_at.tzinfo is None:  # SQLite returns naive UTC timestamps
            finished_at = finished_at.replace(tzinfo=timezone.utc)
        replan = reopened or (existing.status == ActionStatus.COMPLETED and finished_at < now)
        if replan and existing.status != ActionStatus.RUNNING:
            existing.status = ActionStatus.PLANNED
            existing.reason_json = {
                **(existing.reason_json or {}),
                "replanned_at": now.isoformat(),
                "replan_count": int((existing.reason_json or {}).get("replan_count", 0)) + 1,
            }
            existing.updated_at = now
            result.replanned_actions.append(existing)
        return existing

    def _append_issue_event(
        self,
        issue: ReviewIssue,
        *,
        kind: IssueEventKind,
        from_status: IssueStatus | None,
        actor_id: str | None,
        note: str | None,
        now: datetime,
        actor_kind: str = "system",
        evidence: dict[str, Any] | None = None,
    ) -> ReviewIssueEvent:
        event = ReviewIssueEvent(
            issue_id=issue.id,
            version=int(issue.version or 1),
            kind=kind,
            from_status=from_status,
            to_status=issue.status,
            actor_kind=actor_kind,
            actor_id=actor_id,
            note=note,
            evidence_json=evidence if evidence is not None else dict(issue.evidence_json or {}),
            created_at=now,
        )
        self.session.add(event)
        return event

    def record_human_transition(
        self,
        issue: ReviewIssue,
        *,
        to_status: IssueStatus,
        kind: IssueEventKind,
        actor_id: str,
        note: str | None,
        now: datetime | None = None,
    ) -> ReviewIssueEvent:
        """A human decision: sets the status, stamps decided_by and appends the event."""
        now = now or _utcnow()
        from_status = issue.status
        issue.status = to_status
        issue.resolution_note = note
        issue.decided_by = actor_id
        issue.decided_at = now
        issue.updated_at = now
        issue.version = int(issue.version or 1) + 1
        if kind == IssueEventKind.REOPENED:
            issue.reopen_count = int(issue.reopen_count or 0) + 1
            issue.resolution_note = None
        event = self._append_issue_event(
            issue, kind=kind, from_status=from_status, actor_id=actor_id, note=note, now=now, actor_kind="human"
        )
        self.session.flush()
        return event

    def list_issue_events(self, issue_id: str) -> list[ReviewIssueEvent]:
        return list(
            self.session.scalars(
                select(ReviewIssueEvent)
                .where(ReviewIssueEvent.issue_id == issue_id)
                .order_by(ReviewIssueEvent.version.asc(), ReviewIssueEvent.created_at.asc(), ReviewIssueEvent.id.asc())
            ).all()
        )

    def resolve_missing_issues(self, chapter_id: str, active_issue_ids: set[str], resolution_note: str) -> list[ReviewIssue]:
        """Kept for callers that only resolve: the review-owned active issues of a chapter not in ``active_issue_ids``."""
        existing = self.session.scalars(
            select(ReviewIssue).where(
                ReviewIssue.chapter_id == chapter_id,
                ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
            )
        ).all()
        now = _utcnow()
        resolved: list[ReviewIssue] = []
        for issue in existing:
            if issue.id in active_issue_ids or not review_pass_owns(issue):
                continue
            from_status = issue.status
            issue.status = IssueStatus.RESOLVED
            issue.resolution_note = resolution_note
            issue.updated_at = now
            issue.version = int(issue.version or 1) + 1
            self._append_issue_event(
                issue, kind=IssueEventKind.RESOLVED, from_status=from_status, actor_id="services.review", note=resolution_note, now=now
            )
            resolved.append(issue)
        return resolved

    def _merge_collection(self, collection: list[object]) -> None:
        deduped: dict[str, object] = {}
        passthrough: list[object] = []
        for item in collection:
            item_id = getattr(item, "id", None)
            if item_id is None:
                passthrough.append(item)
                continue
            deduped[str(item_id)] = item
        for item in [*deduped.values(), *passthrough]:
            self.session.merge(item)
