"""Export-time translation selection, misalignment evidence and alignment issues.

Also maps export issues to their followup actions.
"""


from __future__ import annotations

from datetime import datetime, timezone

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    ActionActorType,
    ActionStatus,
    ActionType,
    Detector,
    IssueStatus,
    JobScopeType,
    RootCauseLayer,
    SentenceStatus,
    Severity,
    TargetSegmentStatus,
)
from book_agent.domain.models.review import IssueAction, ReviewIssue
from book_agent.export.models import (
    ExportMisalignmentEvidence,
)
from book_agent.infra.repositories.export import ChapterExportBundle
from book_agent.orchestrator.rule_engine import IssueRoutingContext, resolve_action


def snapshot_version_map(bundle: ChapterExportBundle) -> dict[str, int]:
    return {
        snapshot.snapshot_type.value: snapshot.version
        for snapshot in bundle.active_snapshots
    }


def build_target_map(bundle: ChapterExportBundle) -> dict[str, object]:
    return {
        segment.id: segment
        for segment in bundle.target_segments
        if segment.final_status != TargetSegmentStatus.SUPERSEDED
    }


def sentence_target_map(bundle: ChapterExportBundle) -> dict[str, list[str]]:
    target_map = build_target_map(bundle)
    run_rank_map = translation_run_rank_map(bundle)
    sentence_target_candidates: dict[str, list[str]] = {}
    for edge in bundle.alignment_edges:
        if edge.target_segment_id not in target_map:
            continue
        sentence_target_candidates.setdefault(edge.sentence_id, []).append(edge.target_segment_id)
    sentence_targets: dict[str, list[str]] = {}
    for sentence_id, candidate_ids in sentence_target_candidates.items():
        preferred_ids = preferred_target_ids_for_sentence(
            candidate_ids,
            target_map=target_map,
            run_rank_map=run_rank_map,
        )
        if preferred_ids:
            sentence_targets[sentence_id] = preferred_ids
    return sentence_targets


def translation_run_rank_map(bundle: ChapterExportBundle) -> dict[str, tuple[datetime, int, int]]:
    packet_priority = {
        "translate": 0,
        "review": 1,
        "retranslate": 2,
    }
    packet_type_by_id = {
        packet.id: str(packet.packet_type or "").strip().casefold()
        for packet in bundle.packets
    }
    rank_map: dict[str, tuple[datetime, int, int]] = {}
    for run in bundle.translation_runs:
        run_timestamp = run.updated_at or run.created_at or datetime.min.replace(tzinfo=timezone.utc)
        if run_timestamp.tzinfo is None:
            run_timestamp = run_timestamp.replace(tzinfo=timezone.utc)
        rank_map[run.id] = (
            run_timestamp,
            packet_priority.get(packet_type_by_id.get(run.packet_id, ""), 0),
            int(getattr(run, "attempt", 0) or 0),
        )
    return rank_map


def preferred_target_ids_for_sentence(
    candidate_ids: list[str],
    *,
    target_map: dict[str, object],
    run_rank_map: dict[str, tuple[datetime, int, int]],
) -> list[str]:
    ordered_candidates: list[str] = []
    seen_target_ids: set[str] = set()
    for candidate_id in candidate_ids:
        if candidate_id not in target_map or candidate_id in seen_target_ids:
            continue
        seen_target_ids.add(candidate_id)
        ordered_candidates.append(candidate_id)
    if len(ordered_candidates) < 2:
        return ordered_candidates

    best_run_id: str | None = None
    best_rank: tuple[datetime, int, int] | None = None
    for candidate_id in ordered_candidates:
        run_id = str(getattr(target_map[candidate_id], "translation_run_id", "") or "")
        rank = run_rank_map.get(run_id)
        if rank is None:
            rank = (datetime.min.replace(tzinfo=timezone.utc), 0, 0)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_run_id = run_id

    if not best_run_id:
        return ordered_candidates
    return [
        candidate_id
        for candidate_id in ordered_candidates
        if str(getattr(target_map[candidate_id], "translation_run_id", "") or "") == best_run_id
    ]


def target_ids_for_block_sentences(
    block_sentences: list[object],
    sentence_targets: dict[str, list[str]],
    target_map: dict[str, object],
) -> list[str]:
    target_ids: list[str] = []
    seen_target_ids: set[str] = set()
    for sentence in block_sentences:
        for target_id in sentence_targets.get(sentence.id, []):
            if target_id in target_map and target_id not in seen_target_ids:
                seen_target_ids.add(target_id)
                target_ids.append(target_id)
    return target_ids


def build_export_misalignment_evidence(bundle: ChapterExportBundle) -> ExportMisalignmentEvidence:
    active_target_map = build_target_map(bundle)
    rendered_targets_by_sentence = sentence_target_map(bundle)
    all_target_ids_by_sentence: dict[str, list[str]] = {}
    inactive_target_segment_ids_with_edges: set[str] = set()
    for edge in bundle.alignment_edges:
        all_target_ids_by_sentence.setdefault(edge.sentence_id, []).append(edge.target_segment_id)
        if edge.target_segment_id not in active_target_map:
            inactive_target_segment_ids_with_edges.add(edge.target_segment_id)

    missing_target_sentence_ids: list[str] = []
    sentence_ids_with_only_inactive_targets: list[str] = []
    for sentence in bundle.sentences:
        if not sentence.translatable or sentence.sentence_status == SentenceStatus.BLOCKED:
            continue
        if sentence.id in rendered_targets_by_sentence:
            continue
        missing_target_sentence_ids.append(sentence.id)
        if sentence.id in all_target_ids_by_sentence:
            sentence_ids_with_only_inactive_targets.append(sentence.id)

    rendered_target_ids = {
        target_id
        for target_ids in rendered_targets_by_sentence.values()
        for target_id in target_ids
    }
    preferred_run_ids = {
        str(getattr(active_target_map[target_id], "translation_run_id", "") or "")
        for target_id in rendered_target_ids
        if target_id in active_target_map
    }
    orphan_candidate_target_ids = [
        target_segment_id
        for target_segment_id, target_segment in active_target_map.items()
        if not preferred_run_ids
        or str(getattr(target_segment, "translation_run_id", "") or "") in preferred_run_ids
    ]
    orphan_target_segment_ids = sorted(
        target_segment_id
        for target_segment_id in orphan_candidate_target_ids
        if target_segment_id not in rendered_target_ids
    )

    return ExportMisalignmentEvidence(
        active_target_map=active_target_map,
        rendered_targets_by_sentence=rendered_targets_by_sentence,
        missing_target_sentence_ids=sorted(missing_target_sentence_ids),
        sentence_ids_with_only_inactive_targets=sorted(sentence_ids_with_only_inactive_targets),
        orphan_target_segment_ids=orphan_target_segment_ids,
        inactive_target_segment_ids_with_edges=sorted(inactive_target_segment_ids_with_edges),
    )


def build_export_alignment_issues(
    bundle: ChapterExportBundle,
    now: datetime,
) -> list[ReviewIssue]:
    evidence = build_export_misalignment_evidence(bundle)
    if not evidence.has_anomalies:
        return []

    sentence_to_packet: dict[str, str] = {}
    packet_sentence_ids: dict[str, list[str]] = {}
    for packet in bundle.packets:
        current_sentence_ids = packet_current_sentence_ids(packet)
        packet_sentence_ids[packet.id] = current_sentence_ids
        for sentence_id in current_sentence_ids:
            sentence_to_packet[sentence_id] = packet.id

    run_to_packet = {
        run.id: run.packet_id
        for run in bundle.translation_runs
    }
    target_by_id = {segment.id: segment for segment in bundle.target_segments}

    issues_by_packet: dict[str, dict[str, list[str]]] = {}

    def _packet_bucket(packet_id: str) -> dict[str, list[str]]:
        return issues_by_packet.setdefault(
            packet_id,
            {
                "missing_target_sentence_ids": [],
                "sentence_ids_with_only_inactive_targets": [],
                "orphan_target_segment_ids": [],
                "inactive_target_segment_ids_with_edges": [],
            },
        )

    for sentence_id in evidence.missing_target_sentence_ids:
        packet_id = sentence_to_packet.get(sentence_id)
        if packet_id is None:
            continue
        _packet_bucket(packet_id)["missing_target_sentence_ids"].append(sentence_id)

    for sentence_id in evidence.sentence_ids_with_only_inactive_targets:
        packet_id = sentence_to_packet.get(sentence_id)
        if packet_id is None:
            continue
        _packet_bucket(packet_id)["sentence_ids_with_only_inactive_targets"].append(sentence_id)

    for target_segment_id in evidence.orphan_target_segment_ids:
        target_segment = target_by_id.get(target_segment_id)
        if target_segment is None:
            continue
        packet_id = run_to_packet.get(target_segment.translation_run_id)
        if packet_id is None:
            continue
        _packet_bucket(packet_id)["orphan_target_segment_ids"].append(target_segment_id)

    for target_segment_id in evidence.inactive_target_segment_ids_with_edges:
        target_segment = target_by_id.get(target_segment_id)
        if target_segment is None:
            continue
        packet_id = run_to_packet.get(target_segment.translation_run_id)
        if packet_id is None:
            continue
        _packet_bucket(packet_id)["inactive_target_segment_ids_with_edges"].append(target_segment_id)

    issues: list[ReviewIssue] = []
    for packet in bundle.packets:
        packet_evidence = issues_by_packet.get(packet.id)
        if packet_evidence is None:
            continue
        has_blocking_packet_anomaly = bool(
            packet_evidence["missing_target_sentence_ids"]
            or packet_evidence["sentence_ids_with_only_inactive_targets"]
            or packet_evidence["orphan_target_segment_ids"]
        )
        if not has_blocking_packet_anomaly:
            continue
        representative_sentence_id = (
            packet_evidence["missing_target_sentence_ids"][:1]
            or packet_evidence["sentence_ids_with_only_inactive_targets"][:1]
            or packet_sentence_ids.get(packet.id, [])[:1]
        )
        sentence_id = representative_sentence_id[0] if representative_sentence_id else None
        issues.append(
            ReviewIssue(
                id=stable_id("review-issue", bundle.chapter.document_id, bundle.chapter.id, packet.id, "ALIGNMENT_FAILURE", "export"),
                document_id=bundle.chapter.document_id,
                chapter_id=bundle.chapter.id,
                sentence_id=sentence_id,
                packet_id=packet.id,
                issue_type="ALIGNMENT_FAILURE",
                root_cause_layer=RootCauseLayer.EXPORT,
                severity=Severity.HIGH,
                blocking=True,
                detector=Detector.RULE,
                confidence=1.0,
                evidence_json={
                    "reason": "export_time_misalignment",
                    "packet_id": packet.id,
                    **packet_evidence,
                },
                status=IssueStatus.OPEN,
                suggested_action=ActionType.REALIGN_ONLY.value,
                created_at=now,
                updated_at=now,
            )
        )
    return issues


def packet_current_sentence_ids(packet) -> list[str]:
    sentence_ids: list[str] = []
    for block in packet.packet_json.get("current_blocks", []):
        sentence_ids.extend(block.get("sentence_ids", []))
    return sentence_ids


def build_action(issue: ReviewIssue) -> IssueAction:
    action_type = resolve_action(
        IssueRoutingContext(
            issue_type=issue.issue_type,
            root_cause_layer=issue.root_cause_layer,
            translation_content_ok=True,
        )
    )
    scope_type, scope_id = scope_for_action(issue, action_type)
    return IssueAction(
        id=stable_id("issue-action", issue.id, action_type.value),
        issue_id=issue.id,
        action_type=action_type,
        scope_type=scope_type,
        scope_id=scope_id,
        status=ActionStatus.PLANNED,
        reason_json={"issue_type": issue.issue_type, "packet_id": issue.packet_id, "root_cause_layer": issue.root_cause_layer.value},
        created_by=ActionActorType.SYSTEM,
        created_at=issue.created_at,
        updated_at=issue.updated_at,
    )


def scope_for_action(issue: ReviewIssue, action_type: ActionType) -> tuple[JobScopeType, str | None]:
    if action_type in {ActionType.RERUN_PACKET, ActionType.REBUILD_PACKET_THEN_RERUN, ActionType.REALIGN_ONLY} and issue.packet_id:
        return JobScopeType.PACKET, issue.packet_id
    if action_type in {
        ActionType.RESEGMENT_CHAPTER,
        ActionType.REPARSE_CHAPTER,
        ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED,
        ActionType.UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED,
        ActionType.REBUILD_CHAPTER_BRIEF,
        ActionType.REEXPORT_ONLY,
    }:
        return JobScopeType.CHAPTER, issue.chapter_id
    if action_type == ActionType.REPARSE_DOCUMENT:
        return JobScopeType.DOCUMENT, issue.document_id
    return JobScopeType.SENTENCE, issue.sentence_id
