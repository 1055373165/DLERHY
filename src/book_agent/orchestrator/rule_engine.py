"""Route review and export issues to followup actions.

``resolve_action`` picks the action type; ``build_issue_action`` is the single
issue -> IssueAction mapping shared by review and the export gate (they used
to keep diverging copies of the routing context and scope rules).
"""

from dataclasses import dataclass

from book_agent.core.ids import stable_id
from book_agent.domain.enums import ActionActorType, ActionStatus, ActionType, JobScopeType, RootCauseLayer
from book_agent.domain.models.review import IssueAction, ReviewIssue


@dataclass(frozen=True)
class IssueRoutingContext:
    issue_type: str
    root_cause_layer: RootCauseLayer
    involves_locked_term: bool = False
    translation_content_ok: bool = False
    requires_packet_rerun: bool = False


def resolve_action(context: IssueRoutingContext) -> ActionType:
    if context.root_cause_layer == RootCauseLayer.PARSE:
        return ActionType.REPARSE_DOCUMENT
    if context.root_cause_layer == RootCauseLayer.STRUCTURE:
        return ActionType.REPARSE_CHAPTER
    if context.root_cause_layer == RootCauseLayer.SEGMENT:
        return ActionType.RESEGMENT_CHAPTER
    if context.issue_type == "STYLE_DRIFT":
        if context.root_cause_layer == RootCauseLayer.MEMORY:
            return ActionType.REBUILD_CHAPTER_BRIEF
        return ActionType.RERUN_PACKET
    if context.issue_type == "CONTEXT_FAILURE":
        if context.root_cause_layer == RootCauseLayer.MEMORY:
            return ActionType.REBUILD_CHAPTER_BRIEF
        if context.root_cause_layer == RootCauseLayer.PACKET:
            return ActionType.REBUILD_PACKET_THEN_RERUN
        return ActionType.REBUILD_CHAPTER_BRIEF
    if context.issue_type == "TERM_CONFLICT" and context.involves_locked_term:
        return ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED
    if context.issue_type == "UNLOCKED_KEY_CONCEPT":
        return ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED
    if context.issue_type == "STALE_CHAPTER_BRIEF":
        return ActionType.REBUILD_CHAPTER_BRIEF
    if context.issue_type == "ENTITY_CONFLICT":
        return ActionType.UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED
    if context.issue_type == "DUPLICATION":
        if context.root_cause_layer == RootCauseLayer.PACKET:
            return ActionType.REBUILD_PACKET_THEN_RERUN
        return ActionType.REEXPORT_ONLY
    if context.issue_type in {"LOW_CONFIDENCE", "FORMAT_POLLUTION"}:
        return ActionType.RERUN_PACKET
    if context.issue_type == "MISTRANSLATION_REFERENCE":
        return ActionType.REBUILD_PACKET_THEN_RERUN
    if context.issue_type == "ALIGNMENT_FAILURE":
        if context.requires_packet_rerun:
            return ActionType.RERUN_PACKET
        if context.translation_content_ok:
            return ActionType.REALIGN_ONLY
        return ActionType.RERUN_PACKET
    if context.issue_type == "EXPORT_FAILURE":
        return ActionType.REEXPORT_ONLY
    if context.issue_type in {"OMISSION", "MISTRANSLATION_SEMANTIC", "MISTRANSLATION_LOGIC"}:
        return ActionType.RERUN_PACKET
    return ActionType.EDIT_TARGET_ONLY


_PACKET_SCOPED_ACTIONS = {ActionType.RERUN_PACKET, ActionType.REBUILD_PACKET_THEN_RERUN, ActionType.REALIGN_ONLY}
_CHAPTER_SCOPED_ACTIONS = {
    ActionType.RESEGMENT_CHAPTER,
    ActionType.REPARSE_CHAPTER,
    ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED,
    ActionType.UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED,
    ActionType.REBUILD_CHAPTER_BRIEF,
    ActionType.REEXPORT_ONLY,
}


def routing_context_for_issue(issue: ReviewIssue) -> IssueRoutingContext:
    return IssueRoutingContext(
        issue_type=issue.issue_type,
        root_cause_layer=issue.root_cause_layer,
        involves_locked_term=issue.issue_type == "TERM_CONFLICT",
        translation_content_ok=issue.issue_type != "OMISSION",
        requires_packet_rerun=bool((issue.evidence_json or {}).get("requires_packet_rerun")),
    )


def scope_for_action(issue: ReviewIssue, action_type: ActionType) -> tuple[JobScopeType, str | None]:
    if action_type in _PACKET_SCOPED_ACTIONS and issue.packet_id:
        return JobScopeType.PACKET, issue.packet_id
    if action_type == ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED and issue.packet_id:
        if issue.issue_type == "TERM_CONFLICT":
            return JobScopeType.PACKET, issue.packet_id
        if issue.issue_type == "UNLOCKED_KEY_CONCEPT":
            packet_ids_seen = [
                str(packet_id).strip()
                for packet_id in list((issue.evidence_json or {}).get("packet_ids_seen") or [])
                if str(packet_id).strip()
            ]
            if len(packet_ids_seen) == 1:
                return JobScopeType.PACKET, issue.packet_id
    if action_type in _CHAPTER_SCOPED_ACTIONS:
        return JobScopeType.CHAPTER, issue.chapter_id
    if action_type == ActionType.REPARSE_DOCUMENT:
        return JobScopeType.DOCUMENT, issue.document_id
    return JobScopeType.SENTENCE, issue.sentence_id


def build_issue_action(issue: ReviewIssue) -> IssueAction:
    action_type = resolve_action(routing_context_for_issue(issue))
    scope_type, scope_id = scope_for_action(issue, action_type)
    return IssueAction(
        id=stable_id("issue-action", issue.id, action_type.value),
        issue_id=issue.id,
        action_type=action_type,
        scope_type=scope_type,
        scope_id=scope_id,
        status=ActionStatus.PLANNED,
        reason_json={
            "issue_type": issue.issue_type,
            "packet_id": issue.packet_id,
            "root_cause_layer": issue.root_cause_layer.value,
        },
        created_by=ActionActorType.SYSTEM,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
    )
