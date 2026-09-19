"""Issue action execution and the repeated-failure manual hold shared by auto followups."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from book_agent.application.read_models import ActionWorkflowResult
from book_agent.domain.models.ops import AuditEvent
from book_agent.domain.models.review import ReviewIssue
from book_agent.services.actions import IssueActionExecutor
from book_agent.services.rerun import RerunService

AUTO_FOLLOWUP_REPEAT_FAILURE_LIMIT = 2
AUTO_FOLLOWUP_EXECUTION_AUDIT_ACTIONS = {
    "review.auto_followup.executed",
    "document.blocker_repair.executed",
    "export.auto_followup.executed",
}


class IssueActionWorkflow:
    """Executes issue actions (optionally with their rerun) and decides which auto followups need a human."""

    def __init__(
        self,
        session: Session,
        action_executor: IssueActionExecutor,
        rerun_service: RerunService,
    ) -> None:
        self.session = session
        self.action_executor = action_executor
        self.rerun_service = rerun_service

    def execute_action(self, action_id: str, run_followup: bool = False) -> ActionWorkflowResult:
        action_execution = self.action_executor.execute(action_id)
        rerun_execution = None
        if run_followup:
            rerun_execution = self.rerun_service.execute(action_execution.rerun_plan)
        return ActionWorkflowResult(
            action_execution=action_execution,
            rerun_execution=rerun_execution,
        )

    def split_actions_by_manual_hold(
        self,
        *,
        issue_by_id: dict[str, ReviewIssue],
        actions: list[Any],
    ) -> tuple[list[Any], list[Any]]:
        eligible: list[Any] = []
        blocked: list[Any] = []
        for action in actions:
            issue_id = str(getattr(action, "issue_id", "") or "")
            action_id = str(
                getattr(action, "id", None)
                or getattr(action, "action_id", None)
                or ""
            )
            issue = issue_by_id.get(issue_id)
            if issue is None or not action_id:
                eligible.append(action)
                continue
            if self._failed_execution_count(issue=issue, action_id=action_id) >= AUTO_FOLLOWUP_REPEAT_FAILURE_LIMIT:
                blocked.append(action)
                continue
            eligible.append(action)
        return eligible, blocked

    def _failed_execution_count(
        self,
        *,
        issue: ReviewIssue,
        action_id: str,
    ) -> int:
        scope_filters = [
            and_(
                AuditEvent.object_type == "document",
                AuditEvent.object_id == issue.document_id,
            ),
        ]
        if issue.chapter_id:
            scope_filters.append(
                and_(
                    AuditEvent.object_type == "chapter",
                    AuditEvent.object_id == issue.chapter_id,
                )
            )
        events = self.session.scalars(
            select(AuditEvent).where(
                AuditEvent.action.in_(sorted(AUTO_FOLLOWUP_EXECUTION_AUDIT_ACTIONS)),
                or_(*scope_filters),
            )
        ).all()
        failure_count = 0
        for event in events:
            payload = event.payload_json or {}
            if str(payload.get("action_id") or "") != action_id:
                continue
            if payload.get("issue_resolved") is False:
                failure_count += 1
        return failure_count
