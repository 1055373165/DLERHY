"""Read and human-transition service for review issues (the issue workbench API)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import ActionStatus, IssueEventKind, IssueStatus
from book_agent.domain.models import Chapter, Sentence
from book_agent.domain.models.review import IssueAction, ReviewIssue, ReviewIssueEvent
from book_agent.infra.repositories.review import ReviewRepository, active_target_texts


class IssueTransitionError(ValueError):
    """The requested human transition is not allowed from the issue's current status."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class IssuePage:
    total_count: int
    entries: list[ReviewIssue]


@dataclass(slots=True)
class IssueDetail:
    issue: ReviewIssue
    events: list[ReviewIssueEvent]
    actions: list[IssueAction]
    source_text: str | None
    target_text: str | None
    chapter_title: str | None


# Allowed human transitions: current status -> {target status}.
_TRANSITIONS: dict[IssueStatus, set[IssueStatus]] = {
    IssueStatus.OPEN: {IssueStatus.TRIAGED, IssueStatus.WONTFIX, IssueStatus.RESOLVED},
    IssueStatus.TRIAGED: {IssueStatus.WONTFIX, IssueStatus.RESOLVED, IssueStatus.OPEN},
    IssueStatus.RESOLVED: {IssueStatus.OPEN},
    IssueStatus.WONTFIX: {IssueStatus.OPEN},
}

_EVENT_FOR_TARGET = {
    IssueStatus.TRIAGED: IssueEventKind.TRIAGED,
    IssueStatus.WONTFIX: IssueEventKind.WONTFIX,
    IssueStatus.RESOLVED: IssueEventKind.RESOLVED,
    IssueStatus.OPEN: IssueEventKind.REOPENED,
}


class IssueService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = ReviewRepository(session)

    def list_issues(
        self,
        document_id: str,
        *,
        status: str | None = "active",
        chapter_id: str | None = None,
        issue_type: str | None = None,
        blocking: bool | None = None,
        detector: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> IssuePage:
        conditions = [ReviewIssue.document_id == document_id]
        if status == "active":
            conditions.append(ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]))
        elif status and status != "all":
            conditions.append(ReviewIssue.status == IssueStatus(status))
        if chapter_id:
            conditions.append(ReviewIssue.chapter_id == chapter_id)
        if issue_type:
            conditions.append(ReviewIssue.issue_type == issue_type)
        if blocking is not None:
            conditions.append(ReviewIssue.blocking.is_(blocking))
        if detector:
            conditions.append(ReviewIssue.detector == detector)
        total = int(self.session.scalar(select(func.count()).select_from(ReviewIssue).where(*conditions)) or 0)
        entries = list(
            self.session.scalars(
                select(ReviewIssue)
                .where(*conditions)
                .order_by(
                    ReviewIssue.blocking.desc(),
                    ReviewIssue.severity.desc(),
                    ReviewIssue.created_at.asc(),
                    ReviewIssue.id.asc(),
                )
                .offset(max(0, offset))
                .limit(max(1, min(limit, 200)))
            ).all()
        )
        return IssuePage(total_count=total, entries=entries)

    def get_issue(self, issue_id: str) -> ReviewIssue:
        issue = self.session.get(ReviewIssue, issue_id)
        if issue is None:
            raise LookupError(f"Review issue not found: {issue_id}")
        return issue

    def detail(self, issue_id: str) -> IssueDetail:
        issue = self.get_issue(issue_id)
        events = self.repository.list_issue_events(issue_id)
        actions = list(
            self.session.scalars(
                select(IssueAction).where(IssueAction.issue_id == issue_id).order_by(IssueAction.created_at.asc())
            ).all()
        )
        source_text = None
        target_text = None
        if issue.sentence_id:
            sentence = self.session.get(Sentence, issue.sentence_id)
            if sentence is not None:
                source_text = sentence.source_text
                target_text = active_target_texts(self.session, [sentence.id]).get(sentence.id)
        chapter_title = None
        if issue.chapter_id:
            chapter = self.session.get(Chapter, issue.chapter_id)
            if chapter is not None:
                chapter_title = chapter.title_src
        return IssueDetail(
            issue=issue,
            events=events,
            actions=actions,
            source_text=source_text,
            target_text=target_text,
            chapter_title=chapter_title,
        )

    def transition(self, issue_id: str, *, to_status: IssueStatus, actor_id: str, note: str | None) -> ReviewIssue:
        issue = self.get_issue(issue_id)
        allowed = _TRANSITIONS.get(issue.status, set())
        if to_status not in allowed:
            raise IssueTransitionError(
                f"Issue {issue_id} cannot go from {issue.status.value} to {to_status.value}."
            )
        self.repository.record_human_transition(
            issue,
            to_status=to_status,
            kind=_EVENT_FOR_TARGET[to_status],
            actor_id=actor_id,
            note=note,
        )
        if to_status == IssueStatus.OPEN:
            # A human reopen puts the planned repair back on the table.
            for action in self.session.scalars(select(IssueAction).where(IssueAction.issue_id == issue_id)).all():
                if action.status != ActionStatus.RUNNING:
                    action.status = ActionStatus.PLANNED
                    action.updated_at = _utcnow()
        self.session.flush()
        return issue
