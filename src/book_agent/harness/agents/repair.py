"""Repair Agent: decide how to clear the blocking issues rule repair could not.

Runs as the ``repair`` agent stage after review, only when the run asks for
it (``repair_agent=on``). The rule loops in review still run first and keep
their hard caps; this agent only sees what is left. It chooses between the
planned actions (retranslate with the issue's hints, rebuild context,
update terms), a minimal ``edit_segment``, and ``mark_wontfix`` — the last
needs a human approval, so the stage waits in the approvals inbox.

The stage succeeds only when no active blocking issue remains
(``remaining_blockers``); otherwise the run fails exactly as it did before
this agent existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.domain.enums import ActorType, AgentItemKind, IssueStatus
from book_agent.domain.models import Chapter, Document, Sentence
from book_agent.domain.models.review import IssueAction, ReviewIssue
from book_agent.harness.kernel.budget import TurnBudget
from book_agent.harness.tools.book_tools import GetGlossaryArgs, ReadBlockArgs, SearchBookArgs, get_glossary, read_block, search_book
from book_agent.harness.tools.permissions import PermissionPolicy
from book_agent.harness.tools.registry import ToolContext, ToolError, ToolPermission, ToolRegistry, ToolSpec
from book_agent.infra.repositories.agent import AgentLedgerRepository
from book_agent.infra.repositories.review import active_target_texts

AGENT_KIND = "repair"
# Executing the same issue's actions more often than this is a loop; the
# agent must edit, escalate or leave it.
MAX_EXECUTIONS_PER_ISSUE = 2
MAX_LISTED_ISSUES = 40

SYSTEM_PROMPT = """You are the repair agent for a professional English-to-Simplified-Chinese book translation.
Review already ran its automatic repairs; the blocking issues it could not clear are listed by
list_blocking_issues. For each one decide the cheapest action that fixes it:
- execute_action: run a planned repair (retranslate the packet with the issue's hints, rebuild context,
  update the termbase then retranslate). Prefer it when the problem needs a fresh translation.
- edit_segment: when the fix is a small, certain change to one sentence (a wrong term, a mistranslated
  phrase, a missing clause). Give the complete corrected Chinese for the sentence's segment.
- mark_wontfix: only when the issue is a false alarm or the source itself is the problem. A human must
  approve it; explain why in the note.
Check the result of every call: it tells you whether the issue is resolved and what blocks remain. Do not
execute the same issue's actions more than twice. When list_blocking_issues is empty, or nothing more can be
done, reply with a short Chinese summary and no tool calls."""


def active_blocking_issues(session: Session, document_id: str) -> list[ReviewIssue]:
    return list(
        session.scalars(
            select(ReviewIssue)
            .where(
                ReviewIssue.document_id == document_id,
                ReviewIssue.blocking.is_(True),
                ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
            )
            .order_by(ReviewIssue.created_at.asc(), ReviewIssue.id.asc())
        ).all()
    )


def remaining_blockers(session: Session, document_id: str) -> int:
    return int(
        session.scalar(
            select(func.count(ReviewIssue.id)).where(
                ReviewIssue.document_id == document_id,
                ReviewIssue.blocking.is_(True),
                ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
            )
        )
        or 0
    )


def _executions_by_issue(ctx: ToolContext) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in AgentLedgerRepository(ctx.session).list_items(ctx.turn_id):
        content = item.content_json or {}
        if item.kind != AgentItemKind.TOOL_RESULT or content.get("name") != "execute_action":
            continue
        output = (content.get("result") or {}).get("output") or {}
        issue_id = output.get("issue_id")
        if issue_id:
            counts[str(issue_id)] = counts.get(str(issue_id), 0) + 1
    return counts


def _issue_for_document(ctx: ToolContext, issue_id: str) -> ReviewIssue:
    issue = ctx.session.get(ReviewIssue, issue_id)
    if issue is None or issue.document_id != ctx.document_id:
        raise ToolError(f"issue not found in this book: {issue_id}")
    return issue


def _workflow(ctx: ToolContext):
    factory = ctx.extras.get("workflow_factory")
    if factory is None:
        raise ToolError("repairs are not available in this environment (no workflow service)")
    return factory(ctx.session)


# --- tools ------------------------------------------------------------------------


class ListBlockingIssuesArgs(BaseModel):
    pass


class GetIssueArgs(BaseModel):
    issue_id: str


class ExecuteActionArgs(BaseModel):
    action_id: str = Field(description="A planned action id from list_blocking_issues or get_issue.")


class EditSegmentArgs(BaseModel):
    issue_id: str = Field(description="The issue this edit fixes (its sentence is edited).")
    new_text: str = Field(min_length=1, description="The complete corrected Chinese for the sentence's segment.")
    reason: str = Field(min_length=1, description="What was wrong, in Chinese, one sentence.")


class MarkWontfixArgs(BaseModel):
    issue_id: str
    note: str = Field(min_length=1, description="Why this is not a real problem, for the approving human.")


def list_blocking_issues(ctx: ToolContext, args: ListBlockingIssuesArgs) -> dict[str, Any]:
    issues = active_blocking_issues(ctx.session, ctx.document_id)
    executions = _executions_by_issue(ctx)
    sentence_ids = [issue.sentence_id for issue in issues if issue.sentence_id]
    targets = active_target_texts(ctx.session, sentence_ids)
    sources = dict(
        ctx.session.execute(select(Sentence.id, Sentence.source_text).where(Sentence.id.in_(sentence_ids))).all()
    ) if sentence_ids else {}
    actions_by_issue: dict[str, list[IssueAction]] = {}
    if issues:
        for action in ctx.session.scalars(
            select(IssueAction).where(IssueAction.issue_id.in_([issue.id for issue in issues]))
        ).all():
            actions_by_issue.setdefault(action.issue_id, []).append(action)
    chapters = {chapter.id: chapter for chapter in ctx.session.scalars(select(Chapter).where(Chapter.document_id == ctx.document_id)).all()}
    entries = []
    for issue in issues[:MAX_LISTED_ISSUES]:
        evidence = issue.evidence_json or {}
        chapter = chapters.get(issue.chapter_id or "")
        entries.append(
            {
                "issue_id": issue.id,
                "issue_type": issue.issue_type,
                "detector": issue.detector.value,
                "severity": issue.severity.value,
                "chapter": f"{chapter.ordinal}. {chapter.title_src or ''}".strip() if chapter else None,
                "has_sentence": bool(issue.sentence_id),
                "source_text": sources.get(issue.sentence_id or "") or evidence.get("source_text"),
                "current_translation": targets.get(issue.sentence_id or ""),
                "explanation": evidence.get("explanation") or evidence.get("reason"),
                "expected_target_term": evidence.get("expected_target_term"),
                "executions_so_far": executions.get(issue.id, 0),
                "planned_actions": [
                    {"action_id": action.id, "action_type": action.action_type.value, "scope": action.scope_type.value}
                    for action in actions_by_issue.get(issue.id, [])
                    if action.status.value == "planned"
                ],
            }
        )
    return {"blocking_issue_count": len(issues), "issues": entries}


def get_issue(ctx: ToolContext, args: GetIssueArgs) -> dict[str, Any]:
    from book_agent.services.issues import IssueService

    detail = IssueService(ctx.session).detail(_issue_for_document(ctx, args.issue_id).id)
    return {
        "issue_id": detail.issue.id,
        "issue_type": detail.issue.issue_type,
        "status": detail.issue.status.value,
        "blocking": bool(detail.issue.blocking),
        "evidence": dict(detail.issue.evidence_json or {}),
        "source_text": detail.source_text,
        "current_translation": detail.target_text,
        "history": [{"kind": event.kind.value, "note": event.note, "actor": event.actor_id} for event in detail.events],
        "actions": [
            {"action_id": action.id, "action_type": action.action_type.value, "status": action.status.value}
            for action in detail.actions
        ],
    }


def execute_action(ctx: ToolContext, args: ExecuteActionArgs) -> dict[str, Any]:
    from book_agent.services.actions import ActionNotExecutable

    action = ctx.session.get(IssueAction, args.action_id)
    if action is None:
        raise ToolError(f"action not found: {args.action_id}")
    issue = _issue_for_document(ctx, action.issue_id)
    if _executions_by_issue(ctx).get(issue.id, 0) >= MAX_EXECUTIONS_PER_ISSUE:
        raise ToolError(
            f"issue {issue.id} already had {MAX_EXECUTIONS_PER_ISSUE} repair executions; use edit_segment or mark_wontfix"
        )
    try:
        result = _workflow(ctx).execute_action(action.id, run_followup=True)
    except ActionNotExecutable as exc:
        raise ToolError(str(exc)) from exc
    ctx.session.flush()
    rerun = result.rerun_execution
    refreshed = ctx.session.get(ReviewIssue, issue.id)
    return {
        "issue_id": issue.id,
        "action_type": action.action_type.value,
        "issue_status": refreshed.status.value if refreshed is not None else "deleted",
        "issue_resolved": bool(rerun.issue_resolved) if rerun is not None else False,
        "retranslated_packets": len(rerun.translated_packet_ids) if rerun is not None else 0,
        "blocking_issues_left": remaining_blockers(ctx.session, ctx.document_id),
    }


def edit_segment(ctx: ToolContext, args: EditSegmentArgs) -> dict[str, Any]:
    from book_agent.services.review import ReviewService
    from book_agent.infra.repositories.review import ReviewRepository
    from book_agent.services.segment_edit import EditRejected, SegmentEditService

    issue = _issue_for_document(ctx, args.issue_id)
    if not issue.sentence_id:
        raise ToolError("this issue is not tied to a sentence; use execute_action")
    try:
        edit = SegmentEditService(ctx.session).edit_sentence(
            issue.sentence_id,
            args.new_text,
            reason=args.reason,
            actor_id=ctx.actor_id,
            actor_type=ActorType.MODEL,
            run_id=ctx.run_id,
        )
    except EditRejected as exc:
        raise ToolError(str(exc)) from exc
    # Rule checks see the new text at once; model findings on the sentence were closed by the edit.
    ReviewService(ReviewRepository(ctx.session)).review_chapter(edit.chapter_id)
    refreshed = ctx.session.get(ReviewIssue, issue.id)
    return {
        "issue_id": issue.id,
        "issue_status": refreshed.status.value if refreshed is not None else "deleted",
        "edited_sentences": len(edit.covered_sentence_ids),
        "attempt": edit.attempt,
        "blocking_issues_left": remaining_blockers(ctx.session, ctx.document_id),
    }


def mark_wontfix(ctx: ToolContext, args: MarkWontfixArgs) -> dict[str, Any]:
    from book_agent.services.issues import IssueService, IssueTransitionError

    issue = _issue_for_document(ctx, args.issue_id)
    approver = str(ctx.extras.get("approved_by") or "").strip()
    try:
        IssueService(ctx.session).transition(
            issue.id,
            to_status=IssueStatus.WONTFIX,
            actor_id=approver or ctx.actor_id,
            note=f"{args.note} (proposed by {ctx.actor_id})",
        )
    except IssueTransitionError as exc:
        raise ToolError(str(exc)) from exc
    return {"issue_id": issue.id, "issue_status": "wontfix", "blocking_issues_left": remaining_blockers(ctx.session, ctx.document_id)}


def repair_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec("list_blocking_issues", "The blocking issues left after rule repair, with planned actions.", ListBlockingIssuesArgs, ToolPermission.READ, list_blocking_issues),
            ToolSpec("get_issue", "One issue in full: evidence, current translation, history, actions.", GetIssueArgs, ToolPermission.READ, get_issue),
            ToolSpec(
                "execute_action",
                "Run a planned repair action and re-check the chapter.",
                ExecuteActionArgs,
                ToolPermission.WRITE_REVERSIBLE,
                execute_action,
                max_calls_per_turn=80,
            ),
            ToolSpec(
                "edit_segment",
                "Replace the translation of the issue's sentence with a minimal correction (new attempt, old text kept).",
                EditSegmentArgs,
                ToolPermission.WRITE_REVERSIBLE,
                edit_segment,
                max_calls_per_turn=80,
            ),
            ToolSpec(
                "mark_wontfix",
                "Close a false-alarm issue. Needs a human approval.",
                MarkWontfixArgs,
                ToolPermission.WRITE_IRREVERSIBLE,
                mark_wontfix,
                max_calls_per_turn=40,
            ),
            ToolSpec("search_book", "Find sentences containing a word or phrase.", SearchBookArgs, ToolPermission.READ, search_book),
            ToolSpec("read_block", "Read one block in full.", ReadBlockArgs, ToolPermission.READ, read_block),
            ToolSpec("get_glossary", "The current glossary (locked and preferred terms).", GetGlossaryArgs, ToolPermission.READ, get_glossary),
        ]
    )


@dataclass(slots=True)
class RepairTurnSeed:
    turn_id: str
    blocking_issue_count: int


class RepairAgent:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.ledger = AgentLedgerRepository(session)

    @staticmethod
    def registry() -> ToolRegistry:
        return repair_tool_registry()

    @staticmethod
    def policy() -> PermissionPolicy:
        return PermissionPolicy()

    def start_turn(
        self,
        *,
        document_id: str,
        model_name: str,
        run_id: str | None = None,
        work_item_id: str | None = None,
        budget: TurnBudget | None = None,
    ) -> RepairTurnSeed:
        document = self.session.get(Document, document_id)
        if document is None:
            raise ValueError(f"document not found: {document_id}")
        blockers = remaining_blockers(self.session, document_id)
        turn = self.ledger.create_turn(
            document_id=document_id,
            agent_kind=AGENT_KIND,
            scope_type="document",
            scope_id=document_id,
            run_id=run_id,
            work_item_id=work_item_id,
            model_name=model_name,
            budget=(budget or TurnBudget(max_steps=min(200, blockers * 4 + 10), max_tool_calls=min(400, blockers * 6 + 20))).to_json(),
            skills=["repair/default"],
        )
        self.ledger.append_item(turn.id, kind=AgentItemKind.SYSTEM, content={"text": SYSTEM_PROMPT})
        brief = (
            f"Book: {document.title_src or document.title or '(untitled)'}. Blocking issues left after rule repair: {blockers}. "
            + ("Start with list_blocking_issues." if blockers else "Nothing is blocking; reply with a one-line summary.")
        )
        self.ledger.append_item(turn.id, kind=AgentItemKind.USER, content={"text": brief})
        self.session.flush()
        return RepairTurnSeed(turn_id=turn.id, blocking_issue_count=blockers)
