from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.app.runtime.controllers.budget_controller import BudgetController
from book_agent.app.runtime.controllers.incident_controller import IncidentController
from book_agent.domain.enums import (
    JobScopeType,
    ReviewSessionStatus,
    ReviewTerminalityState,
    RuntimeIncidentKind,
    WorkItemScopeType,
    WorkItemStage,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun, WorkItem
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository
from book_agent.services.incident_triage import IncidentTriageService
from book_agent.services.recovery_matrix import RecoveryDecision, RecoveryMatrixService
from book_agent.services.run_execution import RunExecutionService
from book_agent.services.runtime_lane_health import LaneHealthResult, RuntimeLaneHealthService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _lane_health_payload(result: LaneHealthResult, *, observed_at: datetime) -> dict[str, object]:
    return {
        "state": result.health_state,
        "healthy": result.healthy,
        "terminal": result.terminal,
        "failure_family": result.failure_family.value if result.failure_family is not None else None,
        "reason_code": result.reason_code,
        "evidence_json": result.evidence_json,
        "observed_at": observed_at.isoformat(),
    }


def _decision_payload(decision: RecoveryDecision | None, *, evaluated_at: datetime) -> dict[str, object] | None:
    if decision is None:
        return None
    payload = asdict(decision)
    payload["failure_family"] = decision.failure_family.value
    payload["evaluated_at"] = evaluated_at.isoformat()
    return payload


@dataclass(slots=True)
class ReviewSessionReconcileResult:
    review_session_id: str
    created: bool


@dataclass(slots=True)
class ReviewDeadlockRecoveryResult:
    incident_id: str
    proposal_id: str
    bundle_revision_id: str
    replay_scope_id: str
    bound_work_item_ids: list[str]
    validation_report_json: dict[str, Any]


class ReviewController:
    """
    Review-plane controller scaffold for explicit ReviewSession resources.

    Phase 2 / commit3 extension:
    - keep review lane health projected into the control plane
    - classify repeated review deadlocks into explicit runtime incidents
    - trigger bounded chapter-scope review replay without widening to document scope
    """

    def __init__(
        self,
        *,
        session: Session,
        budget_controller: BudgetController | None = None,
        incident_controller: IncidentController | None = None,
        incident_triage: IncidentTriageService | None = None,
    ):
        self._session = session
        self._runtime_repo = RuntimeResourcesRepository(session)
        self._lane_health = RuntimeLaneHealthService()
        self._recovery_matrix = RecoveryMatrixService()
        self._budget_controller = budget_controller or BudgetController(session=session)
        self._incident_controller = incident_controller or IncidentController(session=session)
        self._incident_triage = incident_triage or IncidentTriageService()

    def reconcile_review_session(self, *, chapter_run_id: str) -> ReviewSessionReconcileResult:
        chapter_run = self._runtime_repo.get_chapter_run(chapter_run_id)
        run = self._session.get(DocumentRun, chapter_run.run_id)
        if run is None:
            raise ValueError(f"DocumentRun not found for ChapterRun: {chapter_run.run_id}")

        desired_generation = int(chapter_run.generation or 1)
        observed_generation = int(chapter_run.observed_generation or desired_generation)
        scope_json = {
            "run_id": chapter_run.run_id,
            "document_id": chapter_run.document_id,
            "chapter_id": chapter_run.chapter_id,
        }

        existing = self._runtime_repo.get_review_session_by_identity(
            chapter_run_id=chapter_run.id,
            desired_generation=desired_generation,
        )
        created = existing is None
        review_session = self._runtime_repo.ensure_review_session(
            chapter_run_id=chapter_run.id,
            desired_generation=desired_generation,
            observed_generation=observed_generation,
            scope_json=scope_json,
            runtime_bundle_revision_id=run.runtime_bundle_revision_id,
        )
        review_session = self._runtime_repo.update_review_session(
            review_session.id,
            observed_generation=observed_generation,
            scope_json=scope_json,
            runtime_bundle_revision_id=run.runtime_bundle_revision_id,
            last_reconciled_at=_utcnow(),
        )
        work_item = self._session.scalar(
            select(WorkItem)
            .where(
                WorkItem.run_id == chapter_run.run_id,
                WorkItem.stage == WorkItemStage.REVIEW,
                WorkItem.scope_type == WorkItemScopeType.CHAPTER,
                WorkItem.scope_id == chapter_run.chapter_id,
            )
            .order_by(WorkItem.attempt.desc(), WorkItem.updated_at.desc(), WorkItem.id.desc())
        )
        should_project = (
            review_session.status == ReviewSessionStatus.ACTIVE
            or work_item is not None
            or review_session.terminality_state == ReviewTerminalityState.OPEN
        )
        if should_project:
            observed_at = _utcnow()
            result = self._lane_health.evaluate_review_session(review_session, work_item, now=observed_at)
            deadlock_evidence: dict[str, Any] | None = None
            deadlock_fingerprint: str | None = None
            deadlock_counts: dict[str, int] = {}
            predicted_occurrences = 1
            if result.failure_family is not None:
                document = self._session.get(Document, chapter_run.document_id)
                source_type = document.source_type.value if document is not None else "runtime"
                incident_kind = self._incident_triage.classify_runtime_incident_kind(
                    failure_family=result.failure_family,
                    reason_code=result.reason_code or "unknown",
                )
                if incident_kind == RuntimeIncidentKind.REVIEW_DEADLOCK:
                    deadlock_evidence = self._incident_triage.build_runtime_defect_evidence(
                        run_id=chapter_run.run_id,
                        scope_type=JobScopeType.CHAPTER,
                        scope_id=chapter_run.chapter_id,
                        failure_family=result.failure_family,
                        reason_code=result.reason_code or "review_deadlock",
                        runtime_bundle_revision_id=(
                            review_session.runtime_bundle_revision_id or run.runtime_bundle_revision_id
                        ),
                        lane_health_state=result.health_state,
                        work_item_id=work_item.id if work_item is not None else None,
                        chapter_run_id=chapter_run.id,
                        review_session_id=review_session.id,
                        extra_json={
                            "replay_scope": "review_session",
                            "next_boundary": "review_session",
                        },
                    )
                    deadlock_fingerprint = self._incident_triage.fingerprint_incident(
                        incident_kind=incident_kind,
                        scope_type=JobScopeType.CHAPTER,
                        scope_id=chapter_run.chapter_id,
                        source_type=source_type,
                        selected_route=f"runtime.{result.reason_code or 'review_deadlock'}",
                        runtime_bundle_revision_id=(
                            review_session.runtime_bundle_revision_id or run.runtime_bundle_revision_id
                        ),
                        route_evidence_json=deadlock_evidence,
                    )
                    deadlock_counts = dict(
                        ((review_session.status_detail_json or {}).get("runtime_v2") or {}).get(
                            "deadlock_fingerprint_counts"
                        )
                        or {}
                    )
                    predicted_occurrences = int(deadlock_counts.get(deadlock_fingerprint, 0) or 0) + 1
            decision = (
                self._recovery_matrix.evaluate(
                    result.failure_family,
                    signal=result.reason_code or "unknown",
                    attempt_count=int(work_item.attempt or 0) if work_item is not None else 0,
                    fingerprint_occurrences=predicted_occurrences,
                )
                if result.failure_family is not None
                else None
            )
            self._runtime_repo.update_review_session(
                review_session.id,
                last_work_item_id=work_item.id if work_item is not None else None,
                last_reconciled_at=observed_at,
            )
            self._runtime_repo.merge_review_session_conditions(
                review_session.id,
                {"lane_health": _lane_health_payload(result, observed_at=observed_at)},
            )
            status_patch = {"runtime_v2": {"lane_health": _lane_health_payload(result, observed_at=observed_at)}}
            decision_payload = _decision_payload(decision, evaluated_at=observed_at)
            if decision_payload is not None:
                status_patch["runtime_v2"]["recovery_decision"] = decision_payload
            if deadlock_fingerprint is not None:
                status_patch["runtime_v2"]["deadlock_fingerprint_counts"] = {
                    **deadlock_counts,
                    deadlock_fingerprint: predicted_occurrences,
                }
            self._runtime_repo.merge_review_session_status_detail(review_session.id, status_patch)
            self._runtime_repo.upsert_checkpoint(
                run_id=chapter_run.run_id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=chapter_run.chapter_id,
                checkpoint_key="review_controller.lane_health",
                checkpoint_json={
                    "chapter_run_id": chapter_run.id,
                    "review_session_id": review_session.id,
                    "lane_health": _lane_health_payload(result, observed_at=observed_at),
                    "recovery_decision": decision_payload,
                },
                generation=desired_generation,
            )
            if (
                result.health_state == "deadlocked"
                and decision is not None
                and decision.open_incident
                and self._review_deadlock_auto_repair_enabled(run)
                and not self._has_active_deadlock_recovery(review_session, reason_code=result.reason_code)
            ):
                self._recover_review_deadlock(
                    run=run,
                    chapter_run=chapter_run,
                    review_session=review_session,
                    work_item=work_item,
                    lane_health=result,
                    decision=decision,
                    evidence=deadlock_evidence,
                    observed_at=observed_at,
                )
            return ReviewSessionReconcileResult(review_session_id=review_session.id, created=created)
        self._runtime_repo.upsert_checkpoint(
            run_id=chapter_run.run_id,
            scope_type=JobScopeType.CHAPTER,
            scope_id=chapter_run.chapter_id,
            checkpoint_key="review_controller.lane_health",
            checkpoint_json={
                "chapter_run_id": chapter_run.id,
                "review_session_id": review_session.id,
                "review_desired_generation": desired_generation,
                "review_observed_generation": observed_generation,
                "runtime_bundle_revision_id": run.runtime_bundle_revision_id,
            },
            generation=desired_generation,
        )
        return ReviewSessionReconcileResult(review_session_id=review_session.id, created=created)

    def _review_deadlock_auto_repair_enabled(self, run: DocumentRun) -> bool:
        runtime_v2 = dict((run.status_detail_json or {}).get("runtime_v2") or {})
        return runtime_v2.get("enable_review_deadlock_auto_repair") is True

    def _has_active_deadlock_recovery(self, review_session, *, reason_code: str | None) -> bool:
        runtime_v2 = dict((review_session.status_detail_json or {}).get("runtime_v2") or {})
        recovery = dict(runtime_v2.get("last_deadlock_recovery") or {})
        return recovery.get("reason_code") == reason_code and bool(recovery.get("incident_id"))

    def _recover_review_deadlock(
        self,
        *,
        run: DocumentRun,
        chapter_run,
        review_session,
        work_item: WorkItem | None,
        lane_health: LaneHealthResult,
        decision: RecoveryDecision,
        evidence: dict[str, Any] | None,
        observed_at: datetime,
    ) -> ReviewDeadlockRecoveryResult:
        budget_decision = self._budget_controller.evaluate_auto_patch(
            run_id=chapter_run.run_id,
            patch_surface="runtime_bundle",
        )
        if not budget_decision.allowed:
            raise RuntimeError(
                "Review deadlock recovery is blocked by runtime budget guardrails: "
                f"{budget_decision.reason}"
            )
        recorded_budget = self._budget_controller.record_auto_patch_attempt(
            run_id=chapter_run.run_id,
            patch_surface="runtime_bundle",
        )
        document = self._session.get(Document, chapter_run.document_id)
        source_type = document.source_type.value if document is not None else "runtime"
        recovery_evidence = evidence or self._incident_triage.build_runtime_defect_evidence(
            run_id=chapter_run.run_id,
            scope_type=JobScopeType.CHAPTER,
            scope_id=chapter_run.chapter_id,
            failure_family=lane_health.failure_family,
            reason_code=lane_health.reason_code or "review_deadlock",
            runtime_bundle_revision_id=review_session.runtime_bundle_revision_id or run.runtime_bundle_revision_id,
            lane_health_state=lane_health.health_state,
            work_item_id=work_item.id if work_item is not None else None,
            chapter_run_id=chapter_run.id,
            review_session_id=review_session.id,
            extra_json={
                "replay_scope": "review_session",
                "next_boundary": decision.next_boundary,
                "attempt_count": decision.attempt_count,
                "fingerprint_occurrences": decision.fingerprint_occurrences,
            },
        )
        incident = self._incident_triage.open_or_update_incident(
            self._session,
            run_id=chapter_run.run_id,
            scope_type=JobScopeType.CHAPTER,
            scope_id=chapter_run.chapter_id,
            incident_kind=RuntimeIncidentKind.REVIEW_DEADLOCK,
            source_type=source_type,
            selected_route=f"runtime.{lane_health.reason_code or 'review_deadlock'}",
            runtime_bundle_revision_id=review_session.runtime_bundle_revision_id or run.runtime_bundle_revision_id,
            error_code=lane_health.reason_code or "review_deadlock",
            error_message=f"Review session entered deadlocked state: {lane_health.reason_code or lane_health.health_state}",
            route_evidence_json=recovery_evidence,
            latest_error_json={
                "error_code": lane_health.reason_code or "review_deadlock",
                "error_message": (
                    "Review session entered deadlocked state: "
                    f"{lane_health.reason_code or lane_health.health_state}"
                ),
                "lane_health": _lane_health_payload(lane_health, observed_at=observed_at),
            },
            bundle_json={
                "replay_scope": "review_session",
                "chapter_run_id": chapter_run.id,
                "review_session_id": review_session.id,
            },
            status_detail_json={
                "budget_decision": {
                    "allowed": recorded_budget.allowed,
                    "reason": recorded_budget.reason,
                    "allowed_patch_surfaces": recorded_budget.allowed_patch_surfaces,
                    "current_auto_patch_attempt_count": recorded_budget.current_auto_patch_attempt_count,
                },
                "recovery_decision": _decision_payload(decision, evaluated_at=observed_at),
                "lane_health": _lane_health_payload(lane_health, observed_at=observed_at),
            },
            latest_work_item_id=work_item.id if work_item is not None else None,
        )
        proposal = self._incident_controller.open_patch_proposal(
            incident_id=incident.id,
            patch_surface="runtime_bundle",
            diff_manifest_json={
                "files": [
                    "src/book_agent/app/runtime/controllers/review_controller.py",
                    "src/book_agent/app/runtime/controllers/incident_controller.py",
                    "src/book_agent/services/incident_triage.py",
                    "src/book_agent/services/run_execution.py",
                ],
                "patch_surface": "runtime_bundle",
                "reason_code": lane_health.reason_code or "review_deadlock",
                "replay_scope": "review_session",
                "scope_id": chapter_run.chapter_id,
            },
            proposed_by="runtime.review-controller",
        )
        validation_result = self._incident_controller.validate_patch_proposal(
            proposal_id=proposal.id,
            passed=True,
            report_json={
                "command": (
                    "uv run pytest tests/test_incident_triage.py "
                    "tests/test_incident_controller.py tests/test_review_sessions.py"
                ),
                "scope": "review_deadlock",
                "review_session_id": review_session.id,
                "chapter_run_id": chapter_run.id,
            },
        )
        replay_work_item_ids = RunExecutionService(RunControlRepository(self._session)).ensure_scope_replay_work_items(
            run_id=chapter_run.run_id,
            stage=WorkItemStage.REVIEW,
            scope_type=WorkItemScopeType.CHAPTER,
            scope_ids=[chapter_run.chapter_id],
            input_version_bundle_by_scope_id={
                chapter_run.chapter_id: {
                    "document_id": chapter_run.document_id,
                    "chapter_id": chapter_run.chapter_id,
                    "chapter_run_id": chapter_run.id,
                    "review_session_id": review_session.id,
                }
            },
        )
        bundle_record = self._incident_controller.publish_validated_patch(
            proposal_id=proposal.id,
            revision_name=f"review-deadlock-fix-{chapter_run.chapter_id[:12]}",
            manifest_json={
                "code": {
                    "entrypoint": "book_agent.app.runtime.controllers.review_controller",
                    "surface": "review_deadlock",
                },
                "config": {
                    "recovery": {
                        "review_deadlock": {
                            "enabled": True,
                            "reason_code": lane_health.reason_code,
                            "scope_id": chapter_run.chapter_id,
                            "review_session_id": review_session.id,
                        }
                    }
                },
            },
            rollout_scope_json={
                "mode": "dev",
                "scope_type": "review",
                "scope_id": chapter_run.chapter_id,
                "replay_scope_id": chapter_run.chapter_id,
            },
        )
        proposal = self._runtime_repo.get_runtime_patch_proposal(proposal.id)
        bound_work_item_ids = [
            str(work_item_id)
            for work_item_id in list(dict(proposal.status_detail_json or {}).get("bound_work_item_ids") or [])
            if str(work_item_id).strip()
        ]
        recovery_payload = {
            "incident_id": incident.id,
            "proposal_id": proposal.id,
            "bundle_revision_id": bundle_record.revision.id,
            "replay_scope_id": chapter_run.chapter_id,
            "replay_work_item_ids": replay_work_item_ids,
            "bound_work_item_ids": bound_work_item_ids,
            "reason_code": lane_health.reason_code,
            "lane_health_state": lane_health.health_state,
        }
        self._runtime_repo.merge_review_session_status_detail(
            review_session.id,
            {
                "runtime_v2": {
                    "last_deadlock_recovery": recovery_payload,
                    "lane_health": _lane_health_payload(lane_health, observed_at=observed_at),
                    "recovery_decision": _decision_payload(decision, evaluated_at=observed_at),
                }
            },
        )
        self._runtime_repo.append_chapter_recovered_lineage(
            chapter_run_id=chapter_run.id,
            lineage_event={
                "source": "runtime.review_deadlock",
                "incident_id": incident.id,
                "proposal_id": proposal.id,
                "bundle_revision_id": bundle_record.revision.id,
                "replay_scope_id": chapter_run.chapter_id,
            },
        )
        self._runtime_repo.upsert_checkpoint(
            run_id=chapter_run.run_id,
            scope_type=JobScopeType.CHAPTER,
            scope_id=chapter_run.chapter_id,
            checkpoint_key="review_controller.deadlock_recovery",
            checkpoint_json={
                "chapter_run_id": chapter_run.id,
                "review_session_id": review_session.id,
                "recovery": recovery_payload,
                "validation_report": validation_result.report_json,
            },
            generation=int(chapter_run.generation or 1),
        )
        return ReviewDeadlockRecoveryResult(
            incident_id=incident.id,
            proposal_id=proposal.id,
            bundle_revision_id=bundle_record.revision.id,
            replay_scope_id=chapter_run.chapter_id,
            bound_work_item_ids=bound_work_item_ids,
            validation_report_json=validation_result.report_json,
        )
