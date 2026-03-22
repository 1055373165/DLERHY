from __future__ import annotations

from dataclasses import dataclass

from book_agent.domain.enums import ActionType, JobScopeType
from book_agent.domain.models.review import IssueAction, ReviewIssue
from book_agent.services.term_normalization import normalize_term_rendering
from book_agent.workers.contracts import ConceptCandidate


@dataclass(slots=True)
class RerunPlan:
    issue_id: str
    action_type: ActionType
    scope_type: JobScopeType
    scope_ids: list[str]
    concept_overrides: tuple[ConceptCandidate, ...] = ()
    style_hints: tuple[str, ...] = ()


def concept_overrides_for_issue(issue: ReviewIssue) -> tuple[ConceptCandidate, ...]:
    evidence = issue.evidence_json or {}
    source_term = str(evidence.get("source_term") or "").strip()
    canonical_zh = str(
        evidence.get("expected_target_term")
        or evidence.get("preferred_target_term")
        or evidence.get("preferred_hint")
        or ""
    ).strip()
    canonical_zh = normalize_term_rendering(source_term, canonical_zh)
    if issue.issue_type != "TERM_CONFLICT" or not source_term or not canonical_zh:
        return ()
    return (
        ConceptCandidate(
            source_term=source_term,
            canonical_zh=canonical_zh,
            status="locked",
            times_seen=1,
        ),
    )


def style_hints_for_issue(issue: ReviewIssue) -> tuple[str, ...]:
    evidence = issue.evidence_json or {}
    preferred_hint = str(evidence.get("preferred_hint") or "").strip()
    style_rule = str(evidence.get("style_rule") or "").strip()
    prompt_guidance = str(evidence.get("prompt_guidance") or "").strip()
    matched_target_excerpt = str(evidence.get("matched_target_excerpt") or "").strip()
    if issue.issue_type != "STYLE_DRIFT":
        return ()
    hints: list[str] = []
    if preferred_hint:
        if style_rule:
            hints.append(f"Rerun focus [{style_rule}]: prefer '{preferred_hint}' over literal phrasing in this packet.")
        else:
            hints.append(f"Rerun focus: prefer '{preferred_hint}' over literal phrasing in this packet.")
    if matched_target_excerpt:
        if style_rule:
            hints.append(f"Observed literal phrasing [{style_rule}]: {matched_target_excerpt}")
        else:
            hints.append(f"Observed literal phrasing: {matched_target_excerpt}")
    if prompt_guidance:
        if style_rule:
            hints.append(f"Rerun guidance [{style_rule}]: {prompt_guidance}")
        else:
            hints.append(f"Rerun guidance: {prompt_guidance}")
    return tuple(hints)


def packet_scope_ids_for_issue(issue: ReviewIssue) -> list[str]:
    evidence = issue.evidence_json or {}
    packet_ids_seen = [
        str(packet_id).strip()
        for packet_id in list(evidence.get("packet_ids_seen") or [])
        if str(packet_id).strip()
    ]
    if issue.issue_type in {"UNLOCKED_KEY_CONCEPT", "STALE_CHAPTER_BRIEF"} and packet_ids_seen:
        return packet_ids_seen
    return [issue.packet_id] if issue.packet_id else []


def merge_concept_overrides(
    groups: list[tuple[ConceptCandidate, ...]],
) -> tuple[ConceptCandidate, ...]:
    merged: dict[str, ConceptCandidate] = {}
    for group in groups:
        for concept in group:
            if not concept.source_term:
                continue
            merged[concept.source_term.casefold()] = concept
    ordered = list(merged.values())
    ordered.sort(key=lambda item: item.source_term.casefold())
    return tuple(ordered)


def merge_style_hints(groups: list[tuple[str, ...]]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for hint in group:
            normalized = hint.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
    return tuple(merged)


def build_rerun_plan(issue: ReviewIssue, action: IssueAction) -> RerunPlan:
    if action.action_type == ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED and issue.issue_type == "UNLOCKED_KEY_CONCEPT":
        packet_scope_ids = packet_scope_ids_for_issue(issue)
        if packet_scope_ids:
            return RerunPlan(
                issue_id=issue.id,
                action_type=action.action_type,
                scope_type=JobScopeType.PACKET,
                scope_ids=packet_scope_ids,
                concept_overrides=concept_overrides_for_issue(issue),
                style_hints=style_hints_for_issue(issue),
            )
    if action.action_type == ActionType.REBUILD_CHAPTER_BRIEF and issue.issue_type == "STALE_CHAPTER_BRIEF":
        packet_scope_ids = packet_scope_ids_for_issue(issue)
        if packet_scope_ids:
            return RerunPlan(
                issue_id=issue.id,
                action_type=action.action_type,
                scope_type=JobScopeType.PACKET,
                scope_ids=packet_scope_ids,
                concept_overrides=concept_overrides_for_issue(issue),
                style_hints=style_hints_for_issue(issue),
            )
    scope_ids = [action.scope_id] if action.scope_id else []
    return RerunPlan(
        issue_id=issue.id,
        action_type=action.action_type,
        scope_type=action.scope_type,
        scope_ids=scope_ids,
        concept_overrides=concept_overrides_for_issue(issue),
        style_hints=style_hints_for_issue(issue),
    )
