from dataclasses import dataclass

from book_agent.domain.enums import ActionType, RootCauseLayer


@dataclass(frozen=True)
class IssueRoutingContext:
    issue_type: str
    root_cause_layer: RootCauseLayer
    involves_locked_term: bool = False
    translation_content_ok: bool = False


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
    if context.issue_type == "ALIGNMENT_FAILURE" and context.translation_content_ok:
        return ActionType.REALIGN_ONLY
    if context.issue_type == "EXPORT_FAILURE":
        return ActionType.REEXPORT_ONLY
    if context.issue_type in {"OMISSION", "MISTRANSLATION_SEMANTIC", "MISTRANSLATION_LOGIC"}:
        return ActionType.RERUN_PACKET
    return ActionType.EDIT_TARGET_ONLY
