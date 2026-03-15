from __future__ import annotations

from dataclasses import dataclass

from book_agent.domain.enums import ActionType, JobScopeType
from book_agent.domain.models.review import IssueAction, ReviewIssue


@dataclass(slots=True)
class RerunPlan:
    issue_id: str
    action_type: ActionType
    scope_type: JobScopeType
    scope_ids: list[str]


def build_rerun_plan(issue: ReviewIssue, action: IssueAction) -> RerunPlan:
    scope_ids = [action.scope_id] if action.scope_id else []
    return RerunPlan(
        issue_id=issue.id,
        action_type=action.action_type,
        scope_type=action.scope_type,
        scope_ids=scope_ids,
    )

