"""Review issue and assignment loaders shared by the export dashboard and the chapter worklist."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from book_agent.application import analytics
from book_agent.application.read_models import (
    ChapterWorklistAssignmentSummary,
    IssueActivityBreakdownEntry,
    IssueActivityTimelineEntry,
    IssueChapterBreakdownEntry,
    IssueChapterPressureEntry,
    IssueHotspotEntry,
)
from book_agent.domain.enums import IssueStatus
from book_agent.domain.models import (
    Chapter,
    ChapterWorklistAssignment,
)
from book_agent.domain.models.review import ReviewIssue


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IssueQueries:
    """Reads review issues, issue activity and worklist assignments for one document."""

    def __init__(
        self,
        session: Session,
    ) -> None:
        self.session = session

    def issue_hotspots(self, document_id: str) -> list[IssueHotspotEntry]:
        rows = self.session.execute(
            select(
                ReviewIssue.issue_type,
                ReviewIssue.root_cause_layer,
                func.count(ReviewIssue.id).label("issue_count"),
                func.sum(case((ReviewIssue.status == IssueStatus.OPEN, 1), else_=0)).label("open_issue_count"),
                func.sum(case((ReviewIssue.status == IssueStatus.TRIAGED, 1), else_=0)).label(
                    "triaged_issue_count"
                ),
                func.sum(case((ReviewIssue.status == IssueStatus.RESOLVED, 1), else_=0)).label(
                    "resolved_issue_count"
                ),
                func.sum(case((ReviewIssue.status == IssueStatus.WONTFIX, 1), else_=0)).label(
                    "wontfix_issue_count"
                ),
                func.sum(case((ReviewIssue.blocking.is_(True), 1), else_=0)).label("blocking_issue_count"),
                func.count(distinct(ReviewIssue.chapter_id)).label("chapter_count"),
                func.max(ReviewIssue.created_at).label("latest_seen_at"),
            )
            .where(ReviewIssue.document_id == document_id)
            .group_by(ReviewIssue.issue_type, ReviewIssue.root_cause_layer)
        ).all()
        hotspots = [
            IssueHotspotEntry(
                issue_type=issue_type,
                root_cause_layer=root_cause_layer.value,
                issue_count=issue_count or 0,
                open_issue_count=open_issue_count or 0,
                triaged_issue_count=triaged_issue_count or 0,
                resolved_issue_count=resolved_issue_count or 0,
                wontfix_issue_count=wontfix_issue_count or 0,
                blocking_issue_count=blocking_issue_count or 0,
                chapter_count=chapter_count or 0,
                latest_seen_at=latest_seen_at.isoformat() if latest_seen_at is not None else None,
            )
            for (
                issue_type,
                root_cause_layer,
                issue_count,
                open_issue_count,
                triaged_issue_count,
                resolved_issue_count,
                wontfix_issue_count,
                blocking_issue_count,
                chapter_count,
                latest_seen_at,
            ) in rows
        ]
        hotspots.sort(
            key=lambda entry: (
                -entry.open_issue_count,
                -entry.blocking_issue_count,
                -entry.issue_count,
                entry.issue_type,
                entry.root_cause_layer,
            )
        )
        return hotspots

    def chapter_pressure(self, document_id: str) -> list[IssueChapterPressureEntry]:
        rows = self.session.execute(
            select(
                Chapter.id,
                Chapter.ordinal,
                Chapter.title_src,
                Chapter.status,
                func.count(ReviewIssue.id).label("issue_count"),
                func.sum(case((ReviewIssue.status == IssueStatus.OPEN, 1), else_=0)).label("open_issue_count"),
                func.sum(case((ReviewIssue.status == IssueStatus.TRIAGED, 1), else_=0)).label(
                    "triaged_issue_count"
                ),
                func.sum(case((ReviewIssue.status == IssueStatus.RESOLVED, 1), else_=0)).label(
                    "resolved_issue_count"
                ),
                func.sum(case((ReviewIssue.blocking.is_(True), 1), else_=0)).label("blocking_issue_count"),
                func.max(ReviewIssue.created_at).label("latest_issue_at"),
            )
            .join(ReviewIssue, ReviewIssue.chapter_id == Chapter.id)
            .where(Chapter.document_id == document_id)
            .group_by(Chapter.id, Chapter.ordinal, Chapter.title_src, Chapter.status)
        ).all()
        chapters = [
            IssueChapterPressureEntry(
                chapter_id=chapter_id,
                ordinal=ordinal,
                title_src=title_src,
                chapter_status=chapter_status.value,
                issue_count=issue_count or 0,
                open_issue_count=open_issue_count or 0,
                triaged_issue_count=triaged_issue_count or 0,
                resolved_issue_count=resolved_issue_count or 0,
                blocking_issue_count=blocking_issue_count or 0,
                latest_issue_at=latest_issue_at.isoformat() if latest_issue_at is not None else None,
            )
            for (
                chapter_id,
                ordinal,
                title_src,
                chapter_status,
                issue_count,
                open_issue_count,
                triaged_issue_count,
                resolved_issue_count,
                blocking_issue_count,
                latest_issue_at,
            ) in rows
        ]
        chapters.sort(
            key=lambda entry: (
                -entry.open_issue_count,
                -entry.blocking_issue_count,
                -entry.issue_count,
                entry.ordinal,
                entry.chapter_id,
            )
        )
        return chapters

    def activity_timeline(self, document_id: str) -> list[IssueActivityTimelineEntry]:
        issues = self.session.scalars(
            select(ReviewIssue).where(ReviewIssue.document_id == document_id)
        ).all()
        return analytics.build_issue_activity_timeline(issues)

    def chapter_breakdown(self, document_id: str) -> list[IssueChapterBreakdownEntry]:
        rows = self.session.execute(
            select(
                Chapter.id,
                Chapter.ordinal,
                Chapter.title_src,
                Chapter.status,
                ReviewIssue.issue_type,
                ReviewIssue.root_cause_layer,
                func.count(ReviewIssue.id).label("issue_count"),
                func.sum(case((ReviewIssue.status == IssueStatus.OPEN, 1), else_=0)).label("open_issue_count"),
                func.sum(case((ReviewIssue.status == IssueStatus.TRIAGED, 1), else_=0)).label(
                    "triaged_issue_count"
                ),
                func.sum(case((ReviewIssue.status == IssueStatus.RESOLVED, 1), else_=0)).label(
                    "resolved_issue_count"
                ),
                func.sum(case((ReviewIssue.blocking.is_(True), 1), else_=0)).label("blocking_issue_count"),
                func.sum(
                    case(
                        (
                            ReviewIssue.blocking.is_(True)
                            & ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
                            1,
                        ),
                        else_=0,
                    )
                ).label("active_blocking_issue_count"),
                func.max(ReviewIssue.created_at).label("latest_seen_at"),
            )
            .join(ReviewIssue, ReviewIssue.chapter_id == Chapter.id)
            .where(Chapter.document_id == document_id)
            .group_by(
                Chapter.id,
                Chapter.ordinal,
                Chapter.title_src,
                Chapter.status,
                ReviewIssue.issue_type,
                ReviewIssue.root_cause_layer,
            )
        ).all()
        entries = [
            IssueChapterBreakdownEntry(
                chapter_id=chapter_id,
                ordinal=ordinal,
                title_src=title_src,
                chapter_status=chapter_status.value,
                issue_type=issue_type,
                root_cause_layer=root_cause_layer.value,
                issue_count=issue_count or 0,
                open_issue_count=open_issue_count or 0,
                triaged_issue_count=triaged_issue_count or 0,
                resolved_issue_count=resolved_issue_count or 0,
                blocking_issue_count=blocking_issue_count or 0,
                active_blocking_issue_count=active_blocking_issue_count or 0,
                latest_seen_at=latest_seen_at.isoformat() if latest_seen_at is not None else None,
            )
            for (
                chapter_id,
                ordinal,
                title_src,
                chapter_status,
                issue_type,
                root_cause_layer,
                issue_count,
                open_issue_count,
                triaged_issue_count,
                resolved_issue_count,
                blocking_issue_count,
                active_blocking_issue_count,
                latest_seen_at,
            ) in rows
        ]
        entries.sort(
            key=lambda entry: (
                entry.ordinal,
                -entry.open_issue_count,
                -entry.active_blocking_issue_count,
                -entry.issue_count,
                entry.issue_type,
                entry.root_cause_layer,
            )
        )
        return entries

    def chapter_activity_map(
        self,
        document_id: str,
    ) -> dict[str, list[IssueActivityTimelineEntry]]:
        issues = self.session.scalars(
            select(ReviewIssue).where(
                ReviewIssue.document_id == document_id,
                ReviewIssue.chapter_id.is_not(None),
            )
        ).all()
        grouped: dict[str, list[ReviewIssue]] = {}
        for issue in issues:
            if issue.chapter_id is None:
                continue
            grouped.setdefault(issue.chapter_id, []).append(issue)
        return {
            chapter_id: analytics.build_issue_activity_timeline(chapter_issues)
            for chapter_id, chapter_issues in grouped.items()
        }

    def chapter_worklist_meta(
        self,
        document_id: str,
    ) -> dict[str, dict[str, object]]:
        rows = self.session.execute(
            select(
                ReviewIssue.chapter_id,
                func.min(ReviewIssue.created_at).label("oldest_active_issue_at"),
            )
            .where(
                ReviewIssue.document_id == document_id,
                ReviewIssue.chapter_id.is_not(None),
                ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
            )
            .group_by(ReviewIssue.chapter_id)
        ).all()
        now = _utcnow()
        meta: dict[str, dict[str, object]] = {}
        for chapter_id, oldest_active_issue_at in rows:
            if chapter_id is None or oldest_active_issue_at is None:
                continue
            if oldest_active_issue_at.tzinfo is None:
                oldest_active_issue_at = oldest_active_issue_at.replace(tzinfo=timezone.utc)
            age_hours = max(0, int((now - oldest_active_issue_at).total_seconds() // 3600))
            meta[chapter_id] = {
                "oldest_active_issue_at": oldest_active_issue_at.isoformat(),
                "age_hours": age_hours,
            }
        return meta

    def chapter_assignment_map(
        self,
        document_id: str,
    ) -> dict[str, ChapterWorklistAssignmentSummary]:
        assignments = self.session.scalars(
            select(ChapterWorklistAssignment).where(
                ChapterWorklistAssignment.document_id == document_id
            )
        ).all()
        return {
            assignment.chapter_id: analytics.assignment_summary(assignment)
            for assignment in assignments
        }

    def activity_breakdown(self, document_id: str) -> list[IssueActivityBreakdownEntry]:
        issues = self.session.scalars(
            select(ReviewIssue).where(ReviewIssue.document_id == document_id)
        ).all()
        if not issues:
            return []

        grouped: dict[tuple[str, str], list[ReviewIssue]] = {}
        for issue in issues:
            key = (issue.issue_type, issue.root_cause_layer.value)
            grouped.setdefault(key, []).append(issue)

        breakdown = [
            IssueActivityBreakdownEntry(
                issue_type=issue_type,
                root_cause_layer=root_cause_layer,
                issue_count=len(group_issues),
                open_issue_count=sum(1 for issue in group_issues if issue.status == IssueStatus.OPEN),
                blocking_issue_count=sum(1 for issue in group_issues if issue.blocking),
                latest_seen_at=max(issue.created_at for issue in group_issues).isoformat(),
                timeline=analytics.build_issue_activity_timeline(group_issues),
            )
            for (issue_type, root_cause_layer), group_issues in grouped.items()
        ]
        breakdown.sort(
            key=lambda entry: (
                -entry.open_issue_count,
                -entry.blocking_issue_count,
                -entry.issue_count,
                entry.issue_type,
                entry.root_cause_layer,
            )
        )
        return breakdown

    def open_issue_counts(self, document_id: str) -> dict[str, int]:
        rows = self.session.execute(
            select(ReviewIssue.chapter_id, func.count(ReviewIssue.id))
            .where(
                ReviewIssue.document_id == document_id,
                ReviewIssue.status == IssueStatus.OPEN,
                ReviewIssue.chapter_id.is_not(None),
            )
            .group_by(ReviewIssue.chapter_id)
        ).all()
        return {chapter_id: count for chapter_id, count in rows if chapter_id is not None}
