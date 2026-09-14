"""Document review with packet auto followups, and the blocker repair loop run before export."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.application import analytics
from book_agent.application.issue_actions import IssueActionWorkflow
from book_agent.application.read_models import (
    ChapterReviewResult,
    ChapterReviewSkip,
    DocumentBlockerRepairExecution,
    DocumentBlockerRepairResult,
    DocumentReviewResult,
    ReviewAutoFollowupExecution,
)
from book_agent.domain.enums import (
    ActionType,
    ActorType,
    IssueStatus,
    JobScopeType,
    PacketStatus,
)
from book_agent.domain.models import Chapter
from book_agent.domain.models.ops import AuditEvent
from book_agent.domain.models.review import (
    IssueAction,
    ReviewIssue,
)
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.export import ExportRepository
from book_agent.infra.repositories.ops import OpsRepository
from book_agent.orchestrator.rerun import build_rerun_plan, packet_scope_ids_for_issue
from book_agent.services.chapter_concept_autolock import (
    ChapterConceptAutoLockService,
    build_default_concept_resolver,
)
from book_agent.services.review import ReviewArtifacts, ReviewService
from book_agent.services.translation import TranslationService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


MAX_SAFE_UNLOCKED_CONCEPT_PACKET_FOLLOWUP = 3
MAX_SAFE_STALE_CHAPTER_BRIEF_PACKET_FOLLOWUP = 3


class ReviewRepairService:
    """Reviews documents, auto-executes safe followups and repairs export blockers."""

    def __init__(
        self,
        session: Session,
        bootstrap_repository: BootstrapRepository,
        export_repository: ExportRepository,
        ops_repository: OpsRepository,
        review_service: ReviewService,
        translation_service: TranslationService,
        issue_actions: IssueActionWorkflow,
    ) -> None:
        self.session = session
        self.bootstrap_repository = bootstrap_repository
        self.export_repository = export_repository
        self.ops_repository = ops_repository
        self.review_service = review_service
        self.translation_service = translation_service
        self.issue_actions = issue_actions

    def review_document(
        self,
        document_id: str,
        *,
        auto_execute_packet_followups: bool = False,
        max_auto_followup_attempts: int = 2,
    ) -> DocumentReviewResult:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        return self._review_document_impl(
            bundle,
            chapter_results=[],
            total_issue_count=0,
            total_action_count=0,
            auto_execute_packet_followups=auto_execute_packet_followups,
            max_auto_followup_attempts=max_auto_followup_attempts,
        )

    def _review_document_impl(
        self,
        bundle,
        *,
        chapter_results: list[ChapterReviewResult],
        total_issue_count: int,
        total_action_count: int,
        auto_execute_packet_followups: bool,
        max_auto_followup_attempts: int,
    ) -> DocumentReviewResult:
        auto_followup_executions: list[ReviewAutoFollowupExecution] = []
        attempted_action_ids: set[str] = set()
        skipped_chapters: list[ChapterReviewSkip] = []

        for chapter_bundle in bundle.chapters:
            if chapter_bundle.translation_packets and not all(
                packet.status == PacketStatus.TRANSLATED for packet in chapter_bundle.translation_packets
            ):
                pending = sum(
                    1 for p in chapter_bundle.translation_packets
                    if p.status != PacketStatus.TRANSLATED and p.status != PacketStatus.FAILED
                )
                failed = sum(
                    1 for p in chapter_bundle.translation_packets
                    if p.status == PacketStatus.FAILED
                )
                skipped_chapters.append(
                    ChapterReviewSkip(
                        chapter_id=chapter_bundle.chapter.id,
                        reason=(
                            "translate_incomplete" if failed == 0
                            else "translate_failed"
                        ),
                        pending_packet_count=pending,
                        failed_packet_count=failed,
                    )
                )
                continue

            artifacts: ReviewArtifacts = self.review_service.review_chapter(chapter_bundle.chapter.id)
            if auto_execute_packet_followups:
                artifacts = self._apply_review_auto_followups(
                    chapter_id=chapter_bundle.chapter.id,
                    artifacts=artifacts,
                    attempted_action_ids=attempted_action_ids,
                    executions=auto_followup_executions,
                    attempt_limit=max_auto_followup_attempts,
                )
            total_issue_count += len(artifacts.issues)
            total_action_count += len(artifacts.actions)
            chapter = self.session.get(Chapter, chapter_bundle.chapter.id)
            chapter_results.append(
                ChapterReviewResult(
                    chapter_id=chapter_bundle.chapter.id,
                    status=(chapter.status.value if chapter is not None else chapter_bundle.chapter.status.value),
                    issue_count=len(artifacts.issues),
                    action_count=len(artifacts.actions),
                    blocking_issue_count=artifacts.summary.blocking_issue_count,
                    coverage_ok=artifacts.summary.coverage_ok,
                    alignment_ok=artifacts.summary.alignment_ok,
                    term_ok=artifacts.summary.term_ok,
                    format_ok=artifacts.summary.format_ok,
                    low_confidence_count=artifacts.summary.low_confidence_count,
                    format_pollution_count=artifacts.summary.format_pollution_count,
                    resolved_issue_count=len(artifacts.resolved_issue_ids),
                    naturalness_summary=analytics.naturalness_summary(artifacts.summary.naturalness_summary),
                )
            )

        return DocumentReviewResult(
            document_id=bundle.document.id,
            total_issue_count=total_issue_count,
            total_action_count=total_action_count,
            chapter_results=chapter_results,
            skipped_chapters=skipped_chapters,
            total_chapter_count=len(bundle.chapters),
            auto_followup_requested=auto_execute_packet_followups,
            auto_followup_applied=bool(auto_followup_executions),
            auto_followup_attempt_count=len(auto_followup_executions),
            auto_followup_attempt_limit=(max_auto_followup_attempts if auto_execute_packet_followups else None),
            auto_followup_executions=auto_followup_executions,
        )

    def repair_document_blockers_until_exportable(
        self,
        document_id: str,
        *,
        max_rounds: int = 4,
        max_actions_per_round: int = 64,
    ) -> DocumentBlockerRepairResult:
        round_limit = max(1, int(max_rounds))
        action_limit = max(1, int(max_actions_per_round))
        attempted_action_ids: set[str] = set()
        executions: list[DocumentBlockerRepairExecution] = []

        blocking_issues = self._list_document_active_blocking_issues(document_id)
        blocking_issue_count_before = len(blocking_issues)
        if blocking_issue_count_before == 0:
            return DocumentBlockerRepairResult(
                document_id=document_id,
                blocking_issue_count_before=0,
                blocking_issue_count_after=0,
                requested=False,
                applied=False,
                round_count=0,
                round_limit=round_limit,
                executions=[],
            )

        stop_reason: str | None = None
        round_count = 0

        while round_count < round_limit:
            blocking_issues = self._list_document_active_blocking_issues(document_id)
            if not blocking_issues:
                break
            candidate_actions = self._document_blocker_candidate_actions(
                issues=blocking_issues,
                attempted_action_ids=attempted_action_ids,
            )
            if not candidate_actions:
                stop_reason = "no_new_actions"
                break
            issue_by_id = {issue.id: issue for issue in blocking_issues}
            candidate_actions, blocked_actions = self.issue_actions.split_actions_by_manual_hold(
                issue_by_id=issue_by_id,
                actions=candidate_actions,
            )
            if not candidate_actions:
                stop_reason = "manual_hold_required"
                self._record_document_blocker_repair_stop(
                    document_id=document_id,
                    executions=executions,
                    round_limit=round_limit,
                    stop_reason=stop_reason,
                    issue_ids=[action.issue_id for action in blocked_actions],
                    followup_action_ids=[
                        str(getattr(action, "id", None) or getattr(action, "action_id", None) or "")
                        for action in blocked_actions
                    ],
                )
                break

            round_count += 1
            for action in candidate_actions[:action_limit]:
                issue = issue_by_id.get(action.issue_id)
                attempted_action_ids.add(action.id)
                result = self.issue_actions.execute_action(action.id, run_followup=True)
                rerun_execution = result.rerun_execution
                executions.append(
                    DocumentBlockerRepairExecution(
                        action_id=action.id,
                        issue_id=action.issue_id,
                        issue_type=(issue.issue_type if issue is not None else "unknown"),
                        action_type=action.action_type.value,
                        rerun_scope_type=result.action_execution.rerun_plan.scope_type.value,
                        rerun_scope_ids=result.action_execution.rerun_plan.scope_ids,
                        followup_executed=rerun_execution is not None,
                        rerun_packet_ids=(
                            rerun_execution.translated_packet_ids if rerun_execution is not None else []
                        ),
                        rerun_translation_run_ids=(
                            rerun_execution.translation_run_ids if rerun_execution is not None else []
                        ),
                        issue_resolved=(
                            rerun_execution.issue_resolved if rerun_execution is not None else None
                        ),
                    )
                )
                self._record_document_blocker_repair_execution(
                    document_id=document_id,
                    chapter_id=(issue.chapter_id if issue is not None else None),
                    execution=executions[-1],
                    attempt_index=len(executions),
                    round_index=round_count,
                    round_limit=round_limit,
                )

        blocking_issue_count_after = len(self._list_document_active_blocking_issues(document_id))
        if blocking_issue_count_after > 0 and stop_reason is None and round_count >= round_limit:
            stop_reason = "max_rounds_reached"

        return DocumentBlockerRepairResult(
            document_id=document_id,
            blocking_issue_count_before=blocking_issue_count_before,
            blocking_issue_count_after=blocking_issue_count_after,
            requested=True,
            applied=bool(executions),
            round_count=round_count,
            round_limit=round_limit,
            executions=executions,
            stop_reason=stop_reason,
        )

    def _list_document_active_blocking_issues(self, document_id: str) -> list[ReviewIssue]:
        return list(
            self.session.scalars(
                select(ReviewIssue)
                .where(
                    ReviewIssue.document_id == document_id,
                    ReviewIssue.blocking.is_(True),
                    ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED]),
                )
                .order_by(ReviewIssue.created_at.asc(), ReviewIssue.id.asc())
            ).all()
        )

    def _document_blocker_candidate_actions(
        self,
        *,
        issues: list[ReviewIssue],
        attempted_action_ids: set[str],
    ) -> list[IssueAction]:
        if not issues:
            return []

        issue_by_id = {issue.id: issue for issue in issues}
        actions = self.export_repository.list_planned_issue_actions(list(issue_by_id))
        chapter_ordinals = self._chapter_ordinal_map(
            [issue.chapter_id for issue in issues if issue.chapter_id]
        )

        def _action_scope_rank(action: IssueAction) -> int:
            if action.scope_type == JobScopeType.DOCUMENT:
                return 0
            if action.scope_type == JobScopeType.CHAPTER:
                return 1
            if action.scope_type == JobScopeType.PACKET:
                return 2
            return 3

        def _action_priority(action: IssueAction) -> int:
            return {
                ActionType.REPARSE_DOCUMENT: 0,
                ActionType.REPARSE_CHAPTER: 1,
                ActionType.RESEGMENT_CHAPTER: 2,
                ActionType.REALIGN_ONLY: 3,
                ActionType.REBUILD_PACKET_THEN_RERUN: 4,
                ActionType.RERUN_PACKET: 5,
                ActionType.UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED: 6,
                ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED: 7,
                ActionType.REBUILD_CHAPTER_BRIEF: 8,
                ActionType.REEXPORT_ONLY: 9,
                ActionType.EDIT_TARGET_ONLY: 10,
                ActionType.MANUAL_FINALIZE: 11,
            }.get(action.action_type, 99)

        def _issue_priority(issue: ReviewIssue) -> int:
            return {
                "MISORDERING": 0,
                "STRUCTURE_POLLUTION": 1,
                "ALIGNMENT_FAILURE": 2,
                "OMISSION": 3,
                "CONTEXT_FAILURE": 4,
                "TERM_CONFLICT": 5,
                "UNLOCKED_KEY_CONCEPT": 6,
                "STYLE_DRIFT": 7,
            }.get(issue.issue_type, 20)

        ordered_actions = sorted(
            (
                action
                for action in actions
                if action.id not in attempted_action_ids and action.issue_id in issue_by_id
            ),
            key=lambda action: (
                _action_scope_rank(action),
                _action_priority(action),
                _issue_priority(issue_by_id[action.issue_id]),
                chapter_ordinals.get(issue_by_id[action.issue_id].chapter_id or "", 10**9),
                str(action.scope_id or ""),
                action.id,
            ),
        )

        selected: list[IssueAction] = []
        reserved_document = False
        reserved_chapter_ids: set[str] = set()
        reserved_packet_ids: set[str] = set()
        reserved_sentence_ids: set[str] = set()

        for action in ordered_actions:
            if reserved_document:
                break
            issue = issue_by_id[action.issue_id]
            rerun_plan = build_rerun_plan(issue, action)
            scope_ids = [scope_id for scope_id in rerun_plan.scope_ids if scope_id]
            if rerun_plan.scope_type == JobScopeType.DOCUMENT:
                selected = [action]
                reserved_document = True
                break
            if rerun_plan.scope_type == JobScopeType.CHAPTER:
                chapter_id = scope_ids[0] if scope_ids else (issue.chapter_id or "")
                if not chapter_id or chapter_id in reserved_chapter_ids:
                    continue
                selected.append(action)
                reserved_chapter_ids.add(chapter_id)
                continue
            if rerun_plan.scope_type == JobScopeType.PACKET:
                if issue.chapter_id and issue.chapter_id in reserved_chapter_ids:
                    continue
                if not scope_ids or any(packet_id in reserved_packet_ids for packet_id in scope_ids):
                    continue
                selected.append(action)
                reserved_packet_ids.update(scope_ids)
                continue
            if rerun_plan.scope_type == JobScopeType.SENTENCE:
                sentence_id = scope_ids[0] if scope_ids else (issue.sentence_id or "")
                if not sentence_id or sentence_id in reserved_sentence_ids:
                    continue
                selected.append(action)
                reserved_sentence_ids.add(sentence_id)
                continue
        return selected

    def _chapter_ordinal_map(self, chapter_ids: list[str]) -> dict[str, int]:
        normalized_ids = sorted({chapter_id for chapter_id in chapter_ids if chapter_id})
        if not normalized_ids:
            return {}
        rows = self.session.execute(
            select(Chapter.id, Chapter.ordinal).where(Chapter.id.in_(normalized_ids))
        ).all()
        return {str(chapter_id): int(ordinal or 0) for chapter_id, ordinal in rows}

    def _apply_review_auto_followups(
        self,
        *,
        chapter_id: str,
        artifacts: ReviewArtifacts,
        attempted_action_ids: set[str],
        executions: list[ReviewAutoFollowupExecution],
        attempt_limit: int,
    ) -> ReviewArtifacts:
        current_artifacts = artifacts
        while len(executions) < attempt_limit:
            issue_by_id = {issue.id: issue for issue in current_artifacts.issues}
            candidate_actions = self._review_auto_followup_candidate_actions(
                current_artifacts,
                issue_by_id=issue_by_id,
                attempted_action_ids=attempted_action_ids,
            )
            if not candidate_actions:
                break
            candidate_actions, blocked_actions = self.issue_actions.split_actions_by_manual_hold(
                issue_by_id=issue_by_id,
                actions=candidate_actions,
            )
            if not candidate_actions:
                self._record_review_auto_followup_stop(
                    chapter_id=chapter_id,
                    executions=executions,
                    attempt_limit=attempt_limit,
                    stop_reason="manual_hold_required",
                    issue_ids=[action.issue_id for action in blocked_actions],
                    followup_action_ids=[
                        str(getattr(action, "id", None) or getattr(action, "action_id", None) or "")
                        for action in blocked_actions
                    ],
                )
                break
            followup_action = candidate_actions[0]
            if len(executions) >= attempt_limit:
                break
            issue = issue_by_id.get(followup_action.issue_id)
            if issue is not None and issue.issue_type == "UNLOCKED_KEY_CONCEPT":
                if not self._auto_lock_review_unlocked_concept(issue):
                    fallback_action = self._fallback_stale_brief_action_for_unlocked_concept(
                        artifacts=current_artifacts,
                        issue_by_id=issue_by_id,
                        attempted_action_ids=attempted_action_ids,
                        issue=issue,
                    )
                    if fallback_action is None:
                        continue
                    followup_action = fallback_action
                    issue = issue_by_id.get(followup_action.issue_id)
            projected_rerun_plan = (
                build_rerun_plan(issue, followup_action)
                if issue is not None
                else None
            )
            attempted_action_ids.add(followup_action.id)
            result = self.issue_actions.execute_action(
                followup_action.id,
                run_followup=(
                    projected_rerun_plan is not None
                    and projected_rerun_plan.scope_type == JobScopeType.PACKET
                ),
            )
            executions.append(
                ReviewAutoFollowupExecution(
                    action_id=followup_action.id,
                    issue_id=followup_action.issue_id,
                    issue_type=(issue.issue_type if issue is not None else "unknown"),
                    action_type=followup_action.action_type.value,
                    rerun_scope_type=result.action_execution.rerun_plan.scope_type.value,
                    rerun_scope_ids=result.action_execution.rerun_plan.scope_ids,
                    followup_executed=result.rerun_execution is not None,
                    rerun_packet_ids=(
                        result.rerun_execution.translated_packet_ids if result.rerun_execution else []
                    ),
                    rerun_translation_run_ids=(
                        result.rerun_execution.translation_run_ids if result.rerun_execution else []
                    ),
                    issue_resolved=(
                        result.rerun_execution.issue_resolved if result.rerun_execution else None
                    ),
                )
            )
            self._record_review_auto_followup_execution(
                chapter_id=chapter_id,
                execution=executions[-1],
                attempt_index=len(executions),
                attempt_limit=attempt_limit,
            )
            if result.rerun_execution is not None and result.rerun_execution.review_artifacts is not None:
                current_artifacts = result.rerun_execution.review_artifacts
            else:
                current_artifacts = self.review_service.review_chapter(chapter_id)
        return current_artifacts

    def _review_auto_followup_candidate_actions(
        self,
        artifacts: ReviewArtifacts,
        *,
        issue_by_id: dict[str, ReviewIssue],
        attempted_action_ids: set[str],
    ) -> list[IssueAction]:
        # Keep STALE_CHAPTER_BRIEF out of the general auto-followup pool.
        # It is only safe as a packet-scoped fallback when concept auto-lock fails
        # on the same affected packet set.
        eligible_issue_types = {"STYLE_DRIFT", "TERM_CONFLICT", "UNLOCKED_KEY_CONCEPT"}
        packet_issue_counts: dict[str, int] = {}
        packet_non_style_issue_counts: dict[str, int] = {}
        packet_issue_types: dict[str, set[str]] = {}
        for issue in artifacts.issues:
            if issue.issue_type not in eligible_issue_types:
                continue
            if issue.issue_type == "UNLOCKED_KEY_CONCEPT" and not self._review_issue_supports_unlocked_concept_auto_followup(issue):
                continue
            if issue.blocking and not self._review_issue_supports_blocking_auto_followup(issue):
                continue
            for packet_id in self._review_issue_followup_packet_ids(issue):
                packet_issue_counts[packet_id] = packet_issue_counts.get(packet_id, 0) + 1
                packet_issue_types.setdefault(packet_id, set()).add(issue.issue_type)
                if issue.issue_type != "STYLE_DRIFT":
                    packet_non_style_issue_counts[packet_id] = (
                        packet_non_style_issue_counts.get(packet_id, 0) + 1
                    )

        filtered_actions: list[IssueAction] = []
        projected_rerun_plans: dict[str, object] = {}
        for action in artifacts.actions:
            if action.id in attempted_action_ids:
                continue
            issue = issue_by_id.get(action.issue_id)
            if issue is None:
                continue
            if issue.issue_type == "UNLOCKED_KEY_CONCEPT" and not self._review_issue_supports_unlocked_concept_auto_followup(issue):
                continue
            if issue.blocking and not self._review_issue_supports_blocking_auto_followup(issue):
                continue
            if issue.issue_type not in eligible_issue_types:
                continue
            rerun_plan = build_rerun_plan(issue, action)
            if rerun_plan.scope_type != JobScopeType.PACKET or not rerun_plan.scope_ids:
                continue
            projected_rerun_plans[action.id] = rerun_plan
            filtered_actions.append(action)

        candidate_actions: list[IssueAction] = []
        seen_packet_ids: set[str] = set()

        def _packet_priority(packet_ids: list[str]) -> int:
            if any(
                packet_non_style_issue_counts.get(packet_id, 0) > 0
                or len(packet_issue_types.get(packet_id, set())) > 1
                for packet_id in packet_ids
            ):
                return 0
            if any(packet_issue_counts.get(packet_id, 0) > 0 for packet_id in packet_ids):
                return 1
            return 2

        def _candidate_priority(action: IssueAction) -> tuple[int, int, int, int, int, str, str]:
            issue = issue_by_id.get(action.issue_id)
            rerun_plan = projected_rerun_plans.get(action.id)
            if issue is None:
                return (2, 2, 3, 0, 0, str(action.scope_id), action.id)
            type_priority = {
                "TERM_CONFLICT": 0,
                "UNLOCKED_KEY_CONCEPT": 1,
                "STYLE_DRIFT": 2,
            }.get(issue.issue_type, 3)
            scope_ids = rerun_plan.scope_ids if rerun_plan is not None else [str(action.scope_id)]
            packet_priority = _packet_priority(scope_ids)
            packet_non_style_weight = sum(
                packet_non_style_issue_counts.get(packet_id, 0) for packet_id in scope_ids
            )
            packet_weight = (
                sum(packet_issue_counts.get(packet_id, 0) for packet_id in scope_ids)
            )
            return (
                0 if issue.blocking else 1,
                type_priority,
                packet_priority,
                -packet_non_style_weight,
                -packet_weight,
                ",".join(scope_ids),
                action.id,
            )

        for action in sorted(
            filtered_actions,
            key=_candidate_priority,
        ):
            rerun_plan = projected_rerun_plans[action.id]
            if any(packet_id in seen_packet_ids for packet_id in rerun_plan.scope_ids):
                continue
            seen_packet_ids.update(rerun_plan.scope_ids)
            candidate_actions.append(action)
        return candidate_actions

    def _review_issue_supports_blocking_auto_followup(self, issue: ReviewIssue) -> bool:
        return bool(
            issue.issue_type == "TERM_CONFLICT"
            and issue.packet_id
            and str((issue.evidence_json or {}).get("expected_target_term") or "").strip()
        )

    def _review_issue_supports_unlocked_concept_auto_followup(self, issue: ReviewIssue) -> bool:
        packet_ids_seen = self._review_issue_followup_packet_ids(issue)
        return bool(
            issue.issue_type == "UNLOCKED_KEY_CONCEPT"
            and packet_ids_seen
            and len(packet_ids_seen) <= MAX_SAFE_UNLOCKED_CONCEPT_PACKET_FOLLOWUP
            and str((issue.evidence_json or {}).get("source_term") or "").strip()
        )

    def _review_issue_supports_stale_brief_auto_followup(self, issue: ReviewIssue) -> bool:
        packet_ids_seen = self._review_issue_followup_packet_ids(issue)
        missing_concepts = [
            str(term).strip()
            for term in list((issue.evidence_json or {}).get("missing_concepts") or [])
            if str(term).strip()
        ]
        return bool(
            issue.issue_type == "STALE_CHAPTER_BRIEF"
            and packet_ids_seen
            and len(packet_ids_seen) <= MAX_SAFE_STALE_CHAPTER_BRIEF_PACKET_FOLLOWUP
            and missing_concepts
        )

    def _review_issue_followup_packet_ids(self, issue: ReviewIssue) -> list[str]:
        return packet_scope_ids_for_issue(issue)

    def _auto_lock_review_unlocked_concept(self, issue: ReviewIssue) -> bool:
        source_term = str((issue.evidence_json or {}).get("source_term") or "").strip()
        if not source_term or issue.chapter_id is None:
            return False
        artifacts = ChapterConceptAutoLockService(
            self.session,
            resolver=build_default_concept_resolver(translation_worker=self.translation_service.worker),
        ).auto_lock_chapter_concepts(
            issue.chapter_id,
            source_terms=[source_term],
            min_times_seen=1,
        )
        return any(record.source_term.casefold() == source_term.casefold() for record in artifacts.locked_records)

    def _fallback_stale_brief_action_for_unlocked_concept(
        self,
        *,
        artifacts: ReviewArtifacts,
        issue_by_id: dict[str, ReviewIssue],
        attempted_action_ids: set[str],
        issue: ReviewIssue,
    ) -> IssueAction | None:
        packet_ids_seen = set(self._review_issue_followup_packet_ids(issue))
        if not packet_ids_seen:
            return None
        for action in artifacts.actions:
            if action.id in attempted_action_ids:
                continue
            fallback_issue = issue_by_id.get(action.issue_id)
            if fallback_issue is None or fallback_issue.issue_type != "STALE_CHAPTER_BRIEF":
                continue
            if not self._review_issue_supports_stale_brief_auto_followup(fallback_issue):
                continue
            fallback_packet_ids = set(self._review_issue_followup_packet_ids(fallback_issue))
            if fallback_packet_ids != packet_ids_seen:
                continue
            rerun_plan = build_rerun_plan(fallback_issue, action)
            if rerun_plan.scope_type != JobScopeType.PACKET or not rerun_plan.scope_ids:
                continue
            return action
        return None

    def _record_review_auto_followup_execution(
        self,
        *,
        chapter_id: str,
        execution: ReviewAutoFollowupExecution,
        attempt_index: int,
        attempt_limit: int,
    ) -> None:
        audit = AuditEvent(
            object_type="chapter",
            object_id=chapter_id,
            action="review.auto_followup.executed",
            actor_type=ActorType.SYSTEM,
            actor_id="document-review-workflow",
            payload_json={
                "attempt_index": attempt_index,
                "attempt_limit": attempt_limit,
                "issue_id": execution.issue_id,
                "action_id": execution.action_id,
                "issue_type": execution.issue_type,
                "action_type": execution.action_type,
                "rerun_scope_type": execution.rerun_scope_type,
                "rerun_scope_ids": execution.rerun_scope_ids,
                "followup_executed": execution.followup_executed,
                "rerun_packet_ids": execution.rerun_packet_ids,
                "rerun_translation_run_ids": execution.rerun_translation_run_ids,
                "issue_resolved": execution.issue_resolved,
            },
            created_at=_utcnow(),
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()

    def _record_review_auto_followup_stop(
        self,
        *,
        chapter_id: str,
        executions: list[ReviewAutoFollowupExecution],
        attempt_limit: int,
        stop_reason: str,
        issue_ids: list[str],
        followup_action_ids: list[str],
    ) -> None:
        audit = AuditEvent(
            object_type="chapter",
            object_id=chapter_id,
            action="review.auto_followup.stopped",
            actor_type=ActorType.SYSTEM,
            actor_id="document-review-workflow",
            payload_json={
                "stop_reason": stop_reason,
                "attempt_count": len(executions),
                "attempt_limit": attempt_limit,
                "issue_ids": issue_ids,
                "followup_action_ids": followup_action_ids,
            },
            created_at=_utcnow(),
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()

    def _record_document_blocker_repair_execution(
        self,
        *,
        document_id: str,
        chapter_id: str | None,
        execution: DocumentBlockerRepairExecution,
        attempt_index: int,
        round_index: int,
        round_limit: int,
    ) -> None:
        audit = AuditEvent(
            object_type="document",
            object_id=document_id,
            action="document.blocker_repair.executed",
            actor_type=ActorType.SYSTEM,
            actor_id="document-review-workflow",
            payload_json={
                "chapter_id": chapter_id,
                "attempt_index": attempt_index,
                "round_index": round_index,
                "round_limit": round_limit,
                "issue_id": execution.issue_id,
                "action_id": execution.action_id,
                "issue_type": execution.issue_type,
                "action_type": execution.action_type,
                "rerun_scope_type": execution.rerun_scope_type,
                "rerun_scope_ids": execution.rerun_scope_ids,
                "followup_executed": execution.followup_executed,
                "rerun_packet_ids": execution.rerun_packet_ids,
                "rerun_translation_run_ids": execution.rerun_translation_run_ids,
                "issue_resolved": execution.issue_resolved,
            },
            created_at=_utcnow(),
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()

    def _record_document_blocker_repair_stop(
        self,
        *,
        document_id: str,
        executions: list[DocumentBlockerRepairExecution],
        round_limit: int,
        stop_reason: str,
        issue_ids: list[str],
        followup_action_ids: list[str],
    ) -> None:
        audit = AuditEvent(
            object_type="document",
            object_id=document_id,
            action="document.blocker_repair.stopped",
            actor_type=ActorType.SYSTEM,
            actor_id="document-review-workflow",
            payload_json={
                "stop_reason": stop_reason,
                "attempt_count": len(executions),
                "round_limit": round_limit,
                "issue_ids": issue_ids,
                "followup_action_ids": followup_action_ids,
            },
            created_at=_utcnow(),
        )
        self.ops_repository.save_audits([audit])
        self.session.flush()
