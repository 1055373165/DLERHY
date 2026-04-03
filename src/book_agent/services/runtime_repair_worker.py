from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from book_agent.app.runtime.controllers.incident_controller import IncidentController
from book_agent.domain.enums import JobScopeType, RuntimeIncidentKind, WorkItemScopeType, WorkItemStage
from book_agent.domain.models.ops import DocumentRun, RuntimeIncident, RuntimePatchProposal
from book_agent.infra.db.session import session_scope
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository
from book_agent.services.run_execution import ClaimedRunWorkItem, RunExecutionService
from book_agent.services.runtime_repair_blockage import project_runtime_repair_blockage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _append_recovered_lineage(
    runtime_v2: dict[str, Any],
    *,
    lineage_entry: dict[str, Any],
) -> None:
    existing_entries = [
        dict(entry)
        for entry in (runtime_v2.get("recovered_lineage") or [])
        if isinstance(entry, dict)
    ]
    proposal_id = lineage_entry.get("proposal_id")
    if proposal_id:
        existing_entries = [
            entry
            for entry in existing_entries
            if entry.get("proposal_id") != proposal_id
        ]
    existing_entries.append(lineage_entry)
    runtime_v2["recovered_lineage"] = existing_entries


class UnsupportedRuntimeRepairIncidentError(RuntimeError):
    """Raised when a repair worker is asked to execute an unsupported incident kind."""


class RuntimeRepairDecisionError(RuntimeError):
    """Raised when a repair worker needs deterministic non-default decision handling."""

    RETRYABLE = False

    def __init__(
        self,
        *,
        decision: str,
        decision_reason: str | None = None,
        result_json: dict[str, Any] | None = None,
        message: str,
    ) -> None:
        super().__init__(message)
        self.decision = str(decision or "").strip()
        self.decision_reason = str(decision_reason or "").strip()
        self.result_json = dict(result_json or {})

    @property
    def retryable(self) -> bool:
        return bool(self.RETRYABLE)


class UnsupportedRuntimeRepairDecisionError(RuntimeRepairDecisionError):
    """Raised when a repair worker receives an unsupported repair decision."""


class RuntimeRepairManualEscalationRequired(RuntimeRepairDecisionError):
    """Raised when a repair must deterministically stop and escalate to manual handling."""


class RuntimeRepairRetryLater(RuntimeRepairDecisionError):
    """Raised when a repair should deterministically retry later."""

    RETRYABLE = True


@dataclass(slots=True)
class RuntimeRepairDispatchContract:
    proposal_id: str
    incident_id: str
    repair_dispatch_id: str
    patch_surface: str
    target_scope_type: str
    target_scope_id: str
    replay_boundary: str
    validation_command: str
    bundle_revision_name: str
    claim_mode: str
    claim_target: str
    dispatch_lane: str
    worker_hint: str
    worker_contract_version: int

    @classmethod
    def from_input_bundle(
        cls,
        *,
        input_bundle: dict[str, Any],
        claimed: ClaimedRunWorkItem,
    ) -> "RuntimeRepairDispatchContract":
        proposal_id = str(input_bundle.get("proposal_id") or claimed.scope_id or "").strip()
        incident_id = str(input_bundle.get("incident_id") or "").strip()
        if not proposal_id:
            raise ValueError("Repair dispatch input bundle is missing proposal_id.")
        if not incident_id:
            raise ValueError(f"Repair dispatch input bundle is missing incident_id for proposal {proposal_id}.")
        return cls(
            proposal_id=proposal_id,
            incident_id=incident_id,
            repair_dispatch_id=str(input_bundle.get("repair_dispatch_id") or "").strip(),
            patch_surface=str(input_bundle.get("patch_surface") or "").strip(),
            target_scope_type=str(input_bundle.get("target_scope_type") or "").strip(),
            target_scope_id=str(input_bundle.get("target_scope_id") or "").strip(),
            replay_boundary=str(input_bundle.get("replay_boundary") or "").strip(),
            validation_command=str(input_bundle.get("validation_command") or "").strip(),
            bundle_revision_name=str(input_bundle.get("bundle_revision_name") or "").strip(),
            claim_mode=str(input_bundle.get("claim_mode") or "runtime_owned").strip(),
            claim_target=str(input_bundle.get("claim_target") or "runtime_patch_proposal").strip(),
            dispatch_lane=str(input_bundle.get("dispatch_lane") or "runtime.repair").strip(),
            worker_hint=str(input_bundle.get("worker_hint") or "").strip(),
            worker_contract_version=int(input_bundle.get("worker_contract_version") or 1),
        )

    @classmethod
    def from_request_input_bundle(
        cls,
        input_bundle: dict[str, Any],
    ) -> "RuntimeRepairDispatchContract":
        proposal_id = str(input_bundle.get("proposal_id") or "").strip()
        incident_id = str(input_bundle.get("incident_id") or "").strip()
        if not proposal_id:
            raise ValueError("Repair request contract is missing proposal_id.")
        if not incident_id:
            raise ValueError(f"Repair request contract is missing incident_id for proposal {proposal_id}.")
        return cls(
            proposal_id=proposal_id,
            incident_id=incident_id,
            repair_dispatch_id=str(input_bundle.get("repair_dispatch_id") or "").strip(),
            patch_surface=str(input_bundle.get("patch_surface") or "").strip(),
            target_scope_type=str(input_bundle.get("target_scope_type") or "").strip(),
            target_scope_id=str(input_bundle.get("target_scope_id") or "").strip(),
            replay_boundary=str(input_bundle.get("replay_boundary") or "").strip(),
            validation_command=str(input_bundle.get("validation_command") or "").strip(),
            bundle_revision_name=str(input_bundle.get("bundle_revision_name") or "").strip(),
            claim_mode=str(input_bundle.get("claim_mode") or "runtime_owned").strip(),
            claim_target=str(input_bundle.get("claim_target") or "runtime_patch_proposal").strip(),
            dispatch_lane=str(input_bundle.get("dispatch_lane") or "runtime.repair").strip(),
            worker_hint=str(input_bundle.get("worker_hint") or "").strip(),
            worker_contract_version=int(input_bundle.get("worker_contract_version") or 1),
        )

    def as_payload_json(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "incident_id": self.incident_id,
            "repair_dispatch_id": self.repair_dispatch_id,
            "patch_surface": self.patch_surface,
            "target_scope_type": self.target_scope_type,
            "target_scope_id": self.target_scope_id,
            "replay_boundary": self.replay_boundary,
            "validation_command": self.validation_command,
            "bundle_revision_name": self.bundle_revision_name,
            "claim_mode": self.claim_mode,
            "claim_target": self.claim_target,
            "dispatch_lane": self.dispatch_lane,
            "worker_hint": self.worker_hint,
            "worker_contract_version": self.worker_contract_version,
        }


class RuntimeRepairWorker:
    SUPPORTED_INCIDENT_KINDS: frozenset[RuntimeIncidentKind] | None = None

    def __init__(self, *, session_factory: sessionmaker):
        self._session_factory = session_factory

    def prepare_execution(
        self,
        *,
        claimed: ClaimedRunWorkItem,
        input_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        contract = RuntimeRepairDispatchContract.from_input_bundle(
            input_bundle=input_bundle,
            claimed=claimed,
        )
        with session_scope(self._session_factory) as session:
            controller = IncidentController(session=session)
            controller.begin_repair_dispatch_execution(
                proposal_id=contract.proposal_id,
                worker_name=claimed.worker_name,
                worker_instance_id=claimed.worker_instance_id,
                work_item_id=claimed.work_item_id,
                lease_token=claimed.lease_token,
            )
            proposal = RuntimeResourcesRepository(session).get_runtime_patch_proposal(contract.proposal_id)
            incident = RuntimeResourcesRepository(session).get_runtime_incident(proposal.incident_id)
            self._assert_supported_incident_kind(incident.incident_kind)
            repair_plan = dict((proposal.status_detail_json or {}).get("repair_plan") or {})
            corrected_route = str(
                (repair_plan.get("validation") or {}).get("corrected_route")
                or (repair_plan.get("bundle") or {})
                .get("manifest_json", {})
                .get("config", {})
                .get("routing_policy", {})
                .get("export_routes", {})
                .get((incident.bundle_json or {}).get("export_type") or "", {})
                .get("selected_route")
                or ""
            )
            return {
                **contract.as_payload_json(),
                "incident_kind": repair_plan.get("incident_kind"),
                "changed_files": list(repair_plan.get("owned_files") or []),
                "replay_scope_type": (repair_plan.get("replay") or {}).get("scope_type"),
                "replay_scope_id": (repair_plan.get("replay") or {}).get("scope_id"),
                "bundle_revision_name": (repair_plan.get("bundle") or {}).get("revision_name"),
                "corrected_route": corrected_route,
                "repair_agent_decision": "publish_bundle_and_replay",
                "repair_agent_decision_reason": "bounded_repair_plan_ready",
            }

    def complete_execution(
        self,
        *,
        run_id: str,
        payload: dict[str, Any],
        lease_token: str,
    ) -> None:
        contract = RuntimeRepairDispatchContract(
            proposal_id=str(payload["proposal_id"]),
            incident_id=str(payload["incident_id"]),
            repair_dispatch_id=str(payload.get("repair_dispatch_id") or ""),
            patch_surface=str(payload.get("patch_surface") or ""),
            target_scope_type=str(payload.get("target_scope_type") or payload.get("replay_scope_type") or ""),
            target_scope_id=str(payload.get("target_scope_id") or payload.get("replay_scope_id") or ""),
            replay_boundary=str(payload.get("replay_boundary") or ""),
            validation_command=str(payload.get("validation_command") or ""),
            bundle_revision_name=str(payload.get("bundle_revision_name") or ""),
            claim_mode=str(payload.get("claim_mode") or "runtime_owned"),
            claim_target=str(payload.get("claim_target") or "runtime_patch_proposal"),
            dispatch_lane=str(payload.get("dispatch_lane") or "runtime.repair"),
            worker_hint=str(payload.get("worker_hint") or ""),
            worker_contract_version=int(payload.get("worker_contract_version") or 1),
        )
        decision = str(payload.get("repair_agent_decision") or "publish_bundle_and_replay").strip()
        decision_reason = str(payload.get("repair_agent_decision_reason") or "").strip()
        if decision == "manual_escalation_required":
            raise RuntimeRepairManualEscalationRequired(
                decision=decision,
                decision_reason=decision_reason,
                result_json=dict(payload),
                message=(
                    "Runtime repair worker requires manual escalation before continuing repair "
                    f"execution. decision={decision!r}."
                ),
            )
        if decision == "retry_later":
            raise RuntimeRepairRetryLater(
                decision=decision,
                decision_reason=decision_reason,
                result_json=dict(payload),
                message=(
                    "Runtime repair worker requested a bounded retry-later outcome for this "
                    f"repair lane. decision={decision!r}."
                ),
            )
        if decision != "publish_bundle_and_replay":
            raise UnsupportedRuntimeRepairDecisionError(
                decision=decision,
                decision_reason=decision_reason,
                result_json=dict(payload),
                message=(
                    "Runtime repair worker only supports 'publish_bundle_and_replay', "
                    "'manual_escalation_required', or 'retry_later' decisions. "
                    f"Received {decision!r}."
                ),
            )
        with session_scope(self._session_factory) as session:
            controller = IncidentController(session=session)
            execution = RunExecutionService(RunControlRepository(session))
            runtime_repo = RuntimeResourcesRepository(session)
            proposal = runtime_repo.get_runtime_patch_proposal(contract.proposal_id)
            incident = runtime_repo.get_runtime_incident(proposal.incident_id)
            self._assert_supported_incident_kind(incident.incident_kind)
            repair_plan = dict((proposal.status_detail_json or {}).get("repair_plan") or {})
            validation = controller.validate_patch_proposal(
                proposal_id=contract.proposal_id,
                passed=True,
                report_json=dict((repair_plan.get("validation") or {})),
            )
            bundle_record = controller.publish_validated_patch(
                proposal_id=contract.proposal_id,
                revision_name=str((repair_plan.get("bundle") or {}).get("revision_name") or ""),
                manifest_json=dict((repair_plan.get("bundle") or {}).get("manifest_json") or {}),
                rollout_scope_json=dict((repair_plan.get("bundle") or {}).get("rollout_scope_json") or {}),
            )

            if incident.incident_kind == RuntimeIncidentKind.REVIEW_DEADLOCK:
                self._finalize_review_deadlock_repair(
                    session=session,
                    run_id=run_id,
                    incident=incident,
                    proposal=runtime_repo.get_runtime_patch_proposal(contract.proposal_id),
                    bundle_revision_id=bundle_record.revision.id,
                    validation_report_json=validation.report_json,
                )
            elif incident.incident_kind == RuntimeIncidentKind.EXPORT_MISROUTING:
                self._finalize_export_route_repair(
                    session=session,
                    run_id=run_id,
                    incident=incident,
                    proposal=runtime_repo.get_runtime_patch_proposal(contract.proposal_id),
                    bundle_revision_id=bundle_record.revision.id,
                    corrected_route=str(payload.get("corrected_route") or ""),
                )
            elif incident.incident_kind == RuntimeIncidentKind.PACKET_RUNTIME_DEFECT:
                self._finalize_packet_runtime_defect_repair(
                    session=session,
                    run_id=run_id,
                    incident=incident,
                    proposal=runtime_repo.get_runtime_patch_proposal(contract.proposal_id),
                    bundle_revision_id=bundle_record.revision.id,
                    validation_report_json=validation.report_json,
                )

            proposal = runtime_repo.get_runtime_patch_proposal(contract.proposal_id)
            incident = runtime_repo.get_runtime_incident(proposal.incident_id)
            proposal_detail = dict(proposal.status_detail_json or {})
            result_payload = {
                **payload,
                "published_bundle_revision_id": bundle_record.revision.id,
                "active_bundle_revision_id": (
                    (proposal_detail.get("bundle_guard") or {}).get("effective_revision_id")
                    or bundle_record.revision.id
                ),
                "bound_work_item_ids": list(proposal_detail.get("bound_work_item_ids") or []),
            }
            controller.record_repair_dispatch_execution(
                proposal_id=contract.proposal_id,
                succeeded=True,
                result_json=result_payload,
                manage_work_item_lifecycle=False,
            )
            execution.complete_work_item_success(
                lease_token=lease_token,
                output_artifact_refs_json={
                    "proposal_id": contract.proposal_id,
                    "incident_id": incident.id,
                    "published_bundle_revision_id": bundle_record.revision.id,
                },
                payload_json={
                    **contract.as_payload_json(),
                    "repair_dispatch_id": payload.get("repair_dispatch_id"),
                    "patch_surface": payload.get("patch_surface"),
                    "target_scope_type": payload.get("replay_scope_type"),
                    "target_scope_id": payload.get("replay_scope_id"),
                    **result_payload,
                },
            )

    def _assert_supported_incident_kind(self, incident_kind: RuntimeIncidentKind) -> None:
        supported = self.SUPPORTED_INCIDENT_KINDS
        if supported is None or incident_kind in supported:
            return
        supported_names = ", ".join(kind.value for kind in sorted(supported, key=lambda item: item.value))
        raise UnsupportedRuntimeRepairIncidentError(
            f"{self.__class__.__name__} does not support incident kind {incident_kind.value!r}. "
            f"Supported kinds: {supported_names}."
        )

    def _finalize_export_route_repair(
        self,
        *,
        session: Session,
        run_id: str,
        incident: RuntimeIncident,
        proposal: RuntimePatchProposal,
        bundle_revision_id: str,
        corrected_route: str | None = None,
    ) -> None:
        run = session.get(DocumentRun, run_id)
        if run is None:
            return
        proposal_detail = dict(proposal.status_detail_json or {})
        bundle_guard = dict(proposal_detail.get("bundle_guard") or {})
        route_candidates = list((incident.bundle_json or {}).get("route_candidates") or [])
        export_type = (incident.bundle_json or {}).get("export_type")
        route_evidence_json = dict(incident.route_evidence_json or {})
        published_bundle_revision_id = proposal.published_bundle_revision_id or bundle_revision_id
        active_bundle_revision_id = str(
            bundle_guard.get("effective_revision_id")
            or run.runtime_bundle_revision_id
            or published_bundle_revision_id
        )
        rollback_target_revision_id = bundle_guard.get("rollback_target_revision_id")
        rollback_performed = bool(bundle_guard.get("rollback_performed"))
        bound_work_item_ids = [
            str(work_item_id)
            for work_item_id in list(proposal_detail.get("bound_work_item_ids") or [])
            if str(work_item_id).strip()
        ]
        repair_dispatch = dict(proposal_detail.get("repair_dispatch") or {})
        replay_scope_id = str((repair_dispatch.get("replay") or {}).get("scope_id") or incident.scope_id)
        replay_work_item_id = bound_work_item_ids[0] if bound_work_item_ids else ""
        repair_blockage = project_runtime_repair_blockage(repair_dispatch)
        corrected_route = str(
            corrected_route
            or (repair_dispatch.get("last_result") or {}).get("result_json", {}).get("corrected_route")
            or route_evidence_json.get("corrected_route")
            or ""
        )
        lineage_entry = {
            "incident_id": incident.id,
            "proposal_id": proposal.id,
            "published_bundle_revision_id": published_bundle_revision_id,
            "active_bundle_revision_id": active_bundle_revision_id,
            "rollback_performed": rollback_performed,
            "rollback_target_revision_id": rollback_target_revision_id,
            "replay_scope_id": replay_scope_id,
            "replay_work_item_id": replay_work_item_id,
            "bound_work_item_ids": bound_work_item_ids,
            "recorded_at": _utcnow().isoformat(),
        }
        status_detail = dict(run.status_detail_json or {})
        runtime_v2 = dict(status_detail.get("runtime_v2") or {})
        runtime_v2["last_export_route_recovery"] = {
            "incident_id": incident.id,
            "proposal_id": proposal.id,
            "bundle_revision_id": published_bundle_revision_id,
            "published_bundle_revision_id": published_bundle_revision_id,
            "active_bundle_revision_id": active_bundle_revision_id,
            "selected_route": incident.selected_route,
            "rollback_performed": rollback_performed,
            "rollback_target_revision_id": rollback_target_revision_id,
            "corrected_route": corrected_route,
            "route_candidates": route_candidates,
            "export_type": export_type,
            "replay_scope_id": replay_scope_id,
            "replay_work_item_id": replay_work_item_id,
            "bound_work_item_ids": bound_work_item_ids,
            "repair_blockage": repair_blockage,
        }
        runtime_v2["active_runtime_bundle_revision_id"] = active_bundle_revision_id
        runtime_v2["runtime_bundle_revision_id"] = active_bundle_revision_id
        runtime_v2.pop("pending_export_route_repair", None)
        runtime_v2["last_export_route_evidence"] = route_evidence_json
        _append_recovered_lineage(runtime_v2, lineage_entry=lineage_entry)
        status_detail["runtime_v2"] = runtime_v2
        run.status_detail_json = status_detail
        run.runtime_bundle_revision_id = active_bundle_revision_id
        run.updated_at = _utcnow()
        session.add(run)
        session.flush()

    def _finalize_review_deadlock_repair(
        self,
        *,
        session: Session,
        run_id: str,
        incident: RuntimeIncident,
        proposal: RuntimePatchProposal,
        bundle_revision_id: str,
        validation_report_json: dict[str, Any],
    ) -> None:
        runtime_repo = RuntimeResourcesRepository(session)
        route_evidence = dict(incident.route_evidence_json or {})
        chapter_run_id = str(
            route_evidence.get("chapter_run_id")
            or (incident.bundle_json or {}).get("chapter_run_id")
            or ""
        )
        review_session_id = str(
            route_evidence.get("review_session_id")
            or (incident.bundle_json or {}).get("review_session_id")
            or ""
        )
        chapter_id = str(
            (proposal.status_detail_json or {}).get("repair_plan", {}).get("replay", {}).get("scope_id")
            or incident.scope_id
        )
        if not chapter_run_id or not review_session_id or not chapter_id:
            return
        review_session = runtime_repo.get_review_session(review_session_id)
        chapter_run = runtime_repo.get_chapter_run(chapter_run_id)
        replay_work_item_ids = RunExecutionService(RunControlRepository(session)).ensure_scope_replay_work_items(
            run_id=run_id,
            stage=WorkItemStage.REVIEW,
            scope_type=WorkItemScopeType.CHAPTER,
            scope_ids=[chapter_id],
            input_version_bundle_by_scope_id={
                chapter_id: {
                    "document_id": chapter_run.document_id,
                    "chapter_id": chapter_id,
                    "chapter_run_id": chapter_run.id,
                    "review_session_id": review_session.id,
                }
            },
        )
        proposal_detail = dict(proposal.status_detail_json or {})
        bound_work_item_ids = [
            str(work_item_id)
            for work_item_id in list(proposal_detail.get("bound_work_item_ids") or [])
            if str(work_item_id).strip()
        ]
        recovery_payload = {
            "incident_id": incident.id,
            "proposal_id": proposal.id,
            "bundle_revision_id": bundle_revision_id,
            "repair_work_item_id": str((proposal_detail.get("repair_dispatch") or {}).get("repair_work_item_id") or ""),
            "replay_scope_id": chapter_id,
            "replay_work_item_ids": replay_work_item_ids,
            "bound_work_item_ids": bound_work_item_ids,
            "reason_code": route_evidence.get("reason_code"),
            "lane_health_state": route_evidence.get("lane_health_state"),
            "status": "published",
            "repair_blockage": project_runtime_repair_blockage(proposal_detail.get("repair_dispatch") or {}),
        }
        runtime_repo.merge_review_session_status_detail(
            review_session.id,
            {"runtime_v2": {"last_deadlock_recovery": recovery_payload}},
        )
        runtime_repo.append_chapter_recovered_lineage(
            chapter_run_id=chapter_run.id,
            lineage_event={
                "source": "runtime.review_deadlock",
                "incident_id": incident.id,
                "proposal_id": proposal.id,
                "bundle_revision_id": bundle_revision_id,
                "replay_scope_id": chapter_id,
                "repair_work_item_id": str((proposal_detail.get("repair_dispatch") or {}).get("repair_work_item_id") or ""),
                "status": "published",
            },
        )
        runtime_repo.upsert_checkpoint(
            run_id=run_id,
            scope_type=JobScopeType.CHAPTER,
            scope_id=chapter_id,
            checkpoint_key="review_controller.deadlock_recovery",
            checkpoint_json={
                "chapter_run_id": chapter_run.id,
                "review_session_id": review_session.id,
                "recovery": recovery_payload,
                "validation_report": validation_report_json,
            },
            generation=int(chapter_run.generation or 1),
        )

    def _finalize_packet_runtime_defect_repair(
        self,
        *,
        session: Session,
        run_id: str,
        incident: RuntimeIncident,
        proposal: RuntimePatchProposal,
        bundle_revision_id: str,
        validation_report_json: dict[str, Any],
    ) -> None:
        runtime_repo = RuntimeResourcesRepository(session)
        route_evidence = dict(incident.route_evidence_json or {})
        packet_task_id = str(
            route_evidence.get("packet_task_id")
            or (incident.bundle_json or {}).get("packet_task_id")
            or ""
        )
        chapter_run_id = str(
            route_evidence.get("chapter_run_id")
            or (incident.bundle_json or {}).get("chapter_run_id")
            or ""
        )
        packet_id = str(
            (proposal.status_detail_json or {}).get("repair_plan", {}).get("replay", {}).get("scope_id")
            or incident.scope_id
        )
        if not packet_task_id or not chapter_run_id or not packet_id:
            return
        packet_task = runtime_repo.get_packet_task(packet_task_id)
        chapter_run = runtime_repo.get_chapter_run(chapter_run_id)
        replay_work_item_ids = RunExecutionService(RunControlRepository(session)).ensure_scope_replay_work_items(
            run_id=run_id,
            stage=WorkItemStage.TRANSLATE,
            scope_type=WorkItemScopeType.PACKET,
            scope_ids=[packet_id],
            input_version_bundle_by_scope_id={
                packet_id: {
                    "packet_id": packet_id,
                    "chapter_id": chapter_run.chapter_id,
                }
            },
        )
        proposal_detail = dict(proposal.status_detail_json or {})
        bound_work_item_ids = [
            str(work_item_id)
            for work_item_id in list(proposal_detail.get("bound_work_item_ids") or [])
            if str(work_item_id).strip()
        ]
        recovery_payload = {
            "incident_id": incident.id,
            "proposal_id": proposal.id,
            "bundle_revision_id": bundle_revision_id,
            "repair_work_item_id": str((proposal_detail.get("repair_dispatch") or {}).get("repair_work_item_id") or ""),
            "replay_scope_id": packet_id,
            "replay_work_item_ids": replay_work_item_ids,
            "bound_work_item_ids": bound_work_item_ids,
            "reason_code": route_evidence.get("reason_code"),
            "lane_health_state": route_evidence.get("lane_health_state"),
            "status": "published",
            "repair_blockage": project_runtime_repair_blockage(proposal_detail.get("repair_dispatch") or {}),
        }
        runtime_repo.merge_packet_task_status_detail(
            packet_task.id,
            {"runtime_v2": {"last_runtime_defect_recovery": recovery_payload}},
        )
        runtime_repo.append_chapter_recovered_lineage(
            chapter_run_id=chapter_run.id,
            lineage_event={
                "source": "runtime.packet_runtime_defect",
                "incident_id": incident.id,
                "proposal_id": proposal.id,
                "bundle_revision_id": bundle_revision_id,
                "replay_scope_id": packet_id,
                "repair_work_item_id": str((proposal_detail.get("repair_dispatch") or {}).get("repair_work_item_id") or ""),
                "status": "published",
            },
        )
        runtime_repo.upsert_checkpoint(
            run_id=run_id,
            scope_type=JobScopeType.PACKET,
            scope_id=packet_id,
            checkpoint_key="packet_controller.runtime_defect_recovery",
            checkpoint_json={
                "packet_task_id": packet_task.id,
                "chapter_run_id": chapter_run.id,
                "recovery": recovery_payload,
                "validation_report": validation_report_json,
            },
            generation=int(packet_task.packet_generation or 1),
        )


class ReviewDeadlockRepairWorker(RuntimeRepairWorker):
    SUPPORTED_INCIDENT_KINDS = frozenset({RuntimeIncidentKind.REVIEW_DEADLOCK})


class ExportRoutingRepairWorker(RuntimeRepairWorker):
    SUPPORTED_INCIDENT_KINDS = frozenset({RuntimeIncidentKind.EXPORT_MISROUTING})


class PacketRuntimeDefectRepairWorker(RuntimeRepairWorker):
    SUPPORTED_INCIDENT_KINDS = frozenset({RuntimeIncidentKind.PACKET_RUNTIME_DEFECT})
