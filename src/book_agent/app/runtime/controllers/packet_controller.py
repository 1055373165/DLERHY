from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.app.runtime.controllers.chapter_controller import ChapterController
from book_agent.app.runtime.controllers.budget_controller import BudgetController
from book_agent.app.runtime.controllers.incident_controller import IncidentController
from book_agent.core.config import get_settings
from book_agent.domain.enums import (
    JobScopeType,
    RootCauseLayer,
    RuntimeIncidentKind,
    WorkItemScopeType,
    WorkItemStage,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import ChapterRun, DocumentRun, PacketTask, RuntimePatchProposal, WorkItem
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository
from book_agent.services.incident_triage import IncidentTriageService
from book_agent.services.recovery_matrix import RecoveryDecision, RecoveryMatrixService
from book_agent.services.runtime_lane_health import LaneHealthResult, RuntimeLaneHealthService
from book_agent.services.runtime_repair_blockage import project_runtime_repair_blockage
from book_agent.services.runtime_repair_planner import RuntimeRepairPlannerService


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


def _preferred_repair_dispatch(run: DocumentRun) -> dict[str, object]:
    runtime_v2 = dict((run.status_detail_json or {}).get("runtime_v2") or {})
    dispatch: dict[str, object] = {}
    execution_mode = str(runtime_v2.get("preferred_repair_execution_mode") or "").strip() or None
    executor_hint = str(runtime_v2.get("preferred_repair_executor_hint") or "").strip() or None
    transport_hint = str(runtime_v2.get("preferred_repair_transport_hint") or "").strip() or None
    if execution_mode is not None:
        dispatch["execution_mode"] = execution_mode
    if executor_hint is not None:
        dispatch["executor_hint"] = executor_hint
    if execution_mode is not None or executor_hint is not None:
        dispatch["executor_contract_version"] = int(
            runtime_v2.get("preferred_repair_executor_contract_version") or 1
        )
    if transport_hint is not None:
        dispatch["transport_hint"] = transport_hint
        dispatch["transport_contract_version"] = int(
            runtime_v2.get("preferred_repair_transport_contract_version") or 1
        )
    if dispatch:
        return dispatch
    remote_http_endpoint = str(get_settings().runtime_repair_transport_http_url or "").strip()
    if remote_http_endpoint:
        return {
            "execution_mode": "transport_backed",
            "executor_hint": "python_contract_transport_repair_executor",
            "executor_contract_version": 1,
            "transport_hint": "http_contract_repair_transport",
            "transport_contract_version": 1,
        }
    return dispatch


def _budget_payload(
    *,
    allowed: bool,
    reason: str | None,
    allowed_patch_surfaces: list[str],
    current_auto_patch_attempt_count: int,
) -> dict[str, object]:
    return {
        "allowed": allowed,
        "reason": reason,
        "allowed_patch_surfaces": allowed_patch_surfaces,
        "current_auto_patch_attempt_count": current_auto_patch_attempt_count,
    }


@dataclass(frozen=True, slots=True)
class PacketLaneProjection:
    task: PacketTask
    chapter_run: ChapterRun
    work_item: WorkItem | None
    lane_health: LaneHealthResult
    attempt_count: int
    runtime_bundle_revision_id: str | None
    chapter_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class PacketRuntimeDefectDispatch:
    payload: dict[str, object]
    created_new_proposal: bool


def _chapter_fingerprint_basis(
    result: LaneHealthResult,
    *,
    runtime_bundle_revision_id: str | None,
) -> dict[str, object] | None:
    if result.failure_family is None:
        return None
    return {
        "failure_family": result.failure_family.value,
        "reason_code": result.reason_code,
        "lane_health_state": result.health_state,
        "runtime_bundle_revision_id": runtime_bundle_revision_id,
        "last_error_class": result.evidence_json.get("last_error_class"),
        "work_item_status": result.evidence_json.get("work_item_status"),
    }


def _chapter_failure_fingerprint(
    result: LaneHealthResult,
    *,
    runtime_bundle_revision_id: str | None,
) -> str | None:
    basis = _chapter_fingerprint_basis(result, runtime_bundle_revision_id=runtime_bundle_revision_id)
    if basis is None:
        return None
    return json.dumps(basis, sort_keys=True, separators=(",", ":"))


class PacketController:
    """
    Packet-scoped controller.

    Current responsibility:
    - mirror-bind PacketTask rows to already-existing WorkItem attempts
    - project packet lane health and bounded recovery decisions
    - escalate repeated same-fingerprint packet failures to explicit chapter hold
    """

    def __init__(self, *, session: Session):
        self._session = session
        self._runtime_repo = RuntimeResourcesRepository(session)
        self._chapter_controller = ChapterController(session=session)
        self._budget_controller = BudgetController(session=session)
        self._incident_controller = IncidentController(session=session)
        self._incident_triage = IncidentTriageService()
        self._lane_health = RuntimeLaneHealthService()
        self._recovery_matrix = RecoveryMatrixService()
        self._repair_planner = RuntimeRepairPlannerService()

    def mirror_bind_work_items(self, *, run_id: str) -> int:
        """
        Returns the number of PacketTask rows updated with a (new) last_work_item_id.
        """
        packet_tasks = self._session.scalars(
            select(PacketTask)
            .join(ChapterRun, ChapterRun.id == PacketTask.chapter_run_id)
            .where(ChapterRun.run_id == run_id)
            .order_by(PacketTask.created_at.asc(), PacketTask.id.asc())
        ).all()

        updated = 0
        for task in packet_tasks:
            work_item = self._session.scalar(
                select(WorkItem)
                .where(
                    WorkItem.run_id == run_id,
                    WorkItem.stage == WorkItemStage.TRANSLATE,
                    WorkItem.scope_type == WorkItemScopeType.PACKET,
                    WorkItem.scope_id == task.packet_id,
                )
                .order_by(WorkItem.attempt.desc(), WorkItem.updated_at.desc(), WorkItem.id.desc())
            )
            if work_item is None:
                continue

            if task.last_work_item_id != work_item.id:
                task.last_work_item_id = work_item.id
                updated += 1
            task.attempt_count = max(int(task.attempt_count or 0), int(work_item.attempt or 0))
            task.last_error_class = work_item.error_class
            task.runtime_bundle_revision_id = work_item.runtime_bundle_revision_id

        self._session.flush()
        return updated

    def project_lane_health(self, *, run_id: str) -> int:
        observed_at = _utcnow()
        packet_tasks = self._session.execute(
            select(PacketTask, ChapterRun)
            .join(ChapterRun, ChapterRun.id == PacketTask.chapter_run_id)
            .where(ChapterRun.run_id == run_id)
            .order_by(PacketTask.created_at.asc(), PacketTask.id.asc())
        ).all()

        projections: list[PacketLaneProjection] = []
        fingerprint_occurrences: dict[tuple[str, str], int] = defaultdict(int)
        fingerprint_attempt_max: dict[tuple[str, str], int] = defaultdict(int)
        fingerprint_packet_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
        for task, chapter_run in packet_tasks:
            work_item = self._session.scalar(
                select(WorkItem)
                .where(
                    WorkItem.run_id == run_id,
                    WorkItem.stage == WorkItemStage.TRANSLATE,
                    WorkItem.scope_type == WorkItemScopeType.PACKET,
                    WorkItem.scope_id == task.packet_id,
                )
                .order_by(WorkItem.attempt.desc(), WorkItem.updated_at.desc(), WorkItem.id.desc())
            )
            result = self._lane_health.evaluate_packet_task(task, work_item, now=observed_at)
            attempt_count = max(int(task.attempt_count or 0), int(work_item.attempt or 0)) if work_item else int(task.attempt_count or 0)
            runtime_bundle_revision_id = (
                work_item.runtime_bundle_revision_id if work_item is not None else task.runtime_bundle_revision_id
            )
            chapter_fingerprint = _chapter_failure_fingerprint(
                result,
                runtime_bundle_revision_id=runtime_bundle_revision_id,
            )
            projections.append(
                PacketLaneProjection(
                    task=task,
                    chapter_run=chapter_run,
                    work_item=work_item,
                    lane_health=result,
                    attempt_count=attempt_count,
                    runtime_bundle_revision_id=runtime_bundle_revision_id,
                    chapter_fingerprint=chapter_fingerprint,
                )
            )
            if chapter_fingerprint is None:
                continue
            fingerprint_key = (chapter_run.id, chapter_fingerprint)
            fingerprint_occurrences[fingerprint_key] += 1
            fingerprint_attempt_max[fingerprint_key] = max(fingerprint_attempt_max[fingerprint_key], attempt_count)
            fingerprint_packet_ids[fingerprint_key].append(task.packet_id)

        projected = 0
        chapter_hold_keys_recorded: set[tuple[str, str]] = set()
        for projection in projections:
            task = projection.task
            chapter_run = projection.chapter_run
            work_item = projection.work_item
            result = projection.lane_health
            fingerprint_key = (
                (chapter_run.id, projection.chapter_fingerprint)
                if projection.chapter_fingerprint is not None
                else None
            )
            decision = (
                self._recovery_matrix.evaluate(
                    result.failure_family,
                    signal=result.reason_code or "unknown",
                    attempt_count=projection.attempt_count,
                    fingerprint_occurrences=(
                        fingerprint_occurrences[fingerprint_key]
                        if fingerprint_key is not None
                        else 1
                    ),
                )
                if result.failure_family is not None
                else None
            )
            self._runtime_repo.update_packet_task(
                task.id,
                last_work_item_id=work_item.id if work_item is not None else None,
                attempt_count=projection.attempt_count,
                last_error_class=work_item.error_class if work_item is not None else task.last_error_class,
                runtime_bundle_revision_id=projection.runtime_bundle_revision_id,
            )
            self._runtime_repo.merge_packet_task_conditions(
                task.id,
                {"lane_health": _lane_health_payload(result, observed_at=observed_at)},
            )
            status_patch = {"runtime_v2": {"lane_health": _lane_health_payload(result, observed_at=observed_at)}}
            decision_payload = _decision_payload(decision, evaluated_at=observed_at)
            if decision_payload is not None:
                status_patch["runtime_v2"]["recovery_decision"] = decision_payload
            self._runtime_repo.merge_packet_task_status_detail(task.id, status_patch)
            self._runtime_repo.upsert_checkpoint(
                run_id=run_id,
                scope_type=JobScopeType.PACKET,
                scope_id=task.packet_id,
                checkpoint_key="packet_controller.lane_health",
                checkpoint_json={
                    "packet_task_id": task.id,
                    "chapter_run_id": chapter_run.id,
                    "chapter_id": chapter_run.chapter_id,
                    "lane_health": _lane_health_payload(result, observed_at=observed_at),
                    "recovery_decision": decision_payload,
                },
                generation=int(task.packet_generation or 1),
            )
            if (
                decision is not None
                and result.failure_family == RootCauseLayer.TRANSLATION
                and projection.attempt_count >= max(int(decision.incident_threshold or 0), 1)
                and decision.recommended_action != "chapter_hold"
            ):
                dispatch = self._schedule_packet_runtime_defect_repair(
                    chapter_run=chapter_run,
                    packet_task=task,
                    work_item=work_item,
                    lane_health=result,
                    decision=decision,
                    observed_at=observed_at,
                    attempt_count=projection.attempt_count,
                    fingerprint_occurrences=(
                        fingerprint_occurrences[fingerprint_key]
                        if fingerprint_key is not None
                        else 1
                    ),
                    runtime_bundle_revision_id=projection.runtime_bundle_revision_id,
                )
                self._runtime_repo.merge_packet_task_status_detail(
                    task.id,
                    {"runtime_v2": {"last_runtime_defect_recovery": dict(dispatch.payload)}},
                )
                self._runtime_repo.upsert_checkpoint(
                    run_id=run_id,
                    scope_type=JobScopeType.PACKET,
                    scope_id=task.packet_id,
                    checkpoint_key="packet_controller.runtime_defect_recovery",
                    checkpoint_json={
                        "packet_task_id": task.id,
                        "chapter_run_id": chapter_run.id,
                        "recovery": dict(dispatch.payload),
                        "validation_report": {},
                    },
                    generation=int(task.packet_generation or 1),
                )
                if dispatch.created_new_proposal:
                    self._runtime_repo.append_chapter_recovered_lineage(
                        chapter_run_id=chapter_run.id,
                        lineage_event={
                            "source": "runtime.packet_runtime_defect",
                            "incident_id": dispatch.payload.get("incident_id"),
                            "proposal_id": dispatch.payload.get("proposal_id"),
                            "replay_scope_id": task.packet_id,
                            "repair_work_item_id": dispatch.payload.get("repair_work_item_id"),
                            "status": dispatch.payload.get("status"),
                        },
                    )
            if (
                decision is not None
                and decision.recommended_action == "chapter_hold"
                and fingerprint_key is not None
                and fingerprint_key not in chapter_hold_keys_recorded
            ):
                affected_packet_ids = list(dict.fromkeys(fingerprint_packet_ids[fingerprint_key]))
                self._chapter_controller.record_runtime_chapter_hold(
                    chapter_run_id=chapter_run.id,
                    hold_reason="repair_exhausted",
                    next_action="manual_review",
                    evidence_json={
                        "fingerprint": projection.chapter_fingerprint,
                        "fingerprint_basis": _chapter_fingerprint_basis(
                            result,
                            runtime_bundle_revision_id=projection.runtime_bundle_revision_id,
                        ),
                        "failure_family": result.failure_family.value if result.failure_family is not None else None,
                        "reason_code": result.reason_code,
                        "lane_health_state": result.health_state,
                        "runtime_bundle_revision_id": projection.runtime_bundle_revision_id,
                        "retry_cap": decision.retry_cap,
                        "attempt_count": fingerprint_attempt_max[fingerprint_key],
                        "fingerprint_occurrences": fingerprint_occurrences[fingerprint_key],
                        "replay_scope": decision.replay_scope,
                        "next_boundary": decision.next_boundary,
                        "affected_packet_ids": affected_packet_ids,
                    },
                )
                chapter_hold_keys_recorded.add(fingerprint_key)
            projected += 1

        self._session.flush()
        return projected

    def _schedule_packet_runtime_defect_repair(
        self,
        *,
        chapter_run: ChapterRun,
        packet_task: PacketTask,
        work_item: WorkItem | None,
        lane_health: LaneHealthResult,
        decision: RecoveryDecision,
        observed_at: datetime,
        attempt_count: int,
        fingerprint_occurrences: int,
        runtime_bundle_revision_id: str | None,
    ) -> PacketRuntimeDefectDispatch:
        run = self._session.get(DocumentRun, chapter_run.run_id)
        if run is None:
            raise RuntimeError(f"DocumentRun not found during packet runtime defect repair: {chapter_run.run_id}")
        budget_decision = self._budget_controller.evaluate_auto_patch(
            run_id=run.id,
            patch_surface="runtime_bundle",
        )
        document = self._session.get(Document, chapter_run.document_id)
        source_type = document.source_type.value if document is not None else "runtime"
        recovery_evidence = self._incident_triage.build_runtime_defect_evidence(
            run_id=run.id,
            scope_type=JobScopeType.PACKET,
            scope_id=packet_task.packet_id,
            failure_family=lane_health.failure_family or RootCauseLayer.TRANSLATION,
            reason_code=lane_health.reason_code or "packet_runtime_defect",
            runtime_bundle_revision_id=runtime_bundle_revision_id or run.runtime_bundle_revision_id,
            lane_health_state=lane_health.health_state,
            work_item_id=work_item.id if work_item is not None else None,
            chapter_run_id=chapter_run.id,
            packet_task_id=packet_task.id,
            extra_json={
                "replay_scope": "packet",
                "next_boundary": decision.next_boundary,
                "attempt_count": attempt_count,
                "fingerprint_occurrences": fingerprint_occurrences,
                "chapter_id": chapter_run.chapter_id,
            },
        )
        incident = self._incident_triage.open_or_update_incident(
            self._session,
            run_id=run.id,
            scope_type=JobScopeType.PACKET,
            scope_id=packet_task.packet_id,
            incident_kind=RuntimeIncidentKind.PACKET_RUNTIME_DEFECT,
            source_type=source_type,
            selected_route=f"runtime.{lane_health.reason_code or 'packet_runtime_defect'}",
            runtime_bundle_revision_id=runtime_bundle_revision_id or run.runtime_bundle_revision_id,
            error_code=lane_health.reason_code or "packet_runtime_defect",
            error_message=(
                "Packet runtime defect reached repair threshold: "
                f"{lane_health.reason_code or lane_health.health_state}"
            ),
            route_evidence_json=recovery_evidence,
            latest_error_json={
                "error_code": lane_health.reason_code or "packet_runtime_defect",
                "error_message": (
                    "Packet runtime defect reached repair threshold: "
                    f"{lane_health.reason_code or lane_health.health_state}"
                ),
                "lane_health": _lane_health_payload(lane_health, observed_at=observed_at),
            },
            bundle_json={
                "replay_scope": "packet",
                "chapter_run_id": chapter_run.id,
                "chapter_id": chapter_run.chapter_id,
                "packet_task_id": packet_task.id,
            },
            status_detail_json={
                "budget_decision": _budget_payload(
                    allowed=budget_decision.allowed,
                    reason=budget_decision.reason,
                    allowed_patch_surfaces=budget_decision.allowed_patch_surfaces,
                    current_auto_patch_attempt_count=budget_decision.current_auto_patch_attempt_count,
                ),
                "recovery_decision": _decision_payload(decision, evaluated_at=observed_at),
                "lane_health": _lane_health_payload(lane_health, observed_at=observed_at),
            },
            latest_work_item_id=work_item.id if work_item is not None else None,
        )
        latest_patch = dict((incident.status_detail_json or {}).get("latest_patch_proposal") or {})
        if latest_patch.get("proposal_id"):
            repair_dispatch = dict(latest_patch.get("repair_dispatch") or {})
            repair_blockage = project_runtime_repair_blockage(repair_dispatch, now=observed_at)
            return PacketRuntimeDefectDispatch(
                payload={
                    "incident_id": incident.id,
                    "proposal_id": str(latest_patch.get("proposal_id") or ""),
                    "bundle_revision_id": None,
                    "repair_work_item_id": str(repair_dispatch.get("repair_work_item_id") or ""),
                    "replay_scope_id": packet_task.packet_id,
                    "bound_work_item_ids": [],
                    "reason_code": lane_health.reason_code,
                    "lane_health_state": lane_health.health_state,
                    "status": str(repair_dispatch.get("status") or "scheduled"),
                    "repair_blockage": repair_blockage,
                },
                created_new_proposal=False,
            )
        if not budget_decision.allowed:
            return PacketRuntimeDefectDispatch(
                payload={
                    "incident_id": incident.id,
                    "proposal_id": "",
                    "bundle_revision_id": None,
                    "repair_work_item_id": "",
                    "replay_scope_id": packet_task.packet_id,
                    "bound_work_item_ids": [],
                    "reason_code": lane_health.reason_code,
                    "lane_health_state": lane_health.health_state,
                    "status": "budget_blocked",
                },
                created_new_proposal=False,
            )
        recorded_budget = self._budget_controller.record_auto_patch_attempt(
            run_id=run.id,
            patch_surface="runtime_bundle",
        )
        incident_detail = dict(incident.status_detail_json or {})
        incident_detail["budget_decision"] = _budget_payload(
            allowed=recorded_budget.allowed,
            reason=recorded_budget.reason,
            allowed_patch_surfaces=recorded_budget.allowed_patch_surfaces,
            current_auto_patch_attempt_count=recorded_budget.current_auto_patch_attempt_count,
        )
        incident.status_detail_json = incident_detail
        incident.updated_at = observed_at
        self._session.add(incident)
        self._session.flush()
        dispatch_preferences = _preferred_repair_dispatch(run)
        repair_plan = self._repair_planner.plan_packet_runtime_defect_repair(
            packet_id=packet_task.packet_id,
            chapter_id=chapter_run.chapter_id,
            chapter_run_id=chapter_run.id,
            packet_task_id=packet_task.id,
            reason_code=lane_health.reason_code,
            **dispatch_preferences,
        )
        proposal = self._incident_controller.open_patch_proposal(
            incident_id=incident.id,
            patch_surface=repair_plan.patch_surface,
            diff_manifest_json=repair_plan.diff_manifest_json,
            proposed_by="runtime.packet-controller",
            status_detail_json={"repair_plan": repair_plan.handoff_json},
        )
        proposal = self._runtime_repo.get_runtime_patch_proposal(proposal.id)
        repair_dispatch = dict((proposal.status_detail_json or {}).get("repair_dispatch") or {})
        repair_blockage = project_runtime_repair_blockage(repair_dispatch, now=observed_at)
        return PacketRuntimeDefectDispatch(
            payload={
                "incident_id": incident.id,
                "proposal_id": proposal.id,
                "bundle_revision_id": None,
                "repair_work_item_id": str(repair_dispatch.get("repair_work_item_id") or ""),
                "replay_scope_id": packet_task.packet_id,
                "bound_work_item_ids": [],
                "reason_code": lane_health.reason_code,
                "lane_health_state": lane_health.health_state,
                "status": "scheduled",
                "repair_blockage": repair_blockage,
            },
            created_new_proposal=True,
        )
