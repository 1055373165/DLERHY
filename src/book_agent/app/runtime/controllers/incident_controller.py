from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from book_agent.domain.enums import (
    JobScopeType,
    RuntimeIncidentKind,
    RuntimeIncidentStatus,
    RuntimePatchProposalStatus,
    WorkItemScopeType,
)
from book_agent.domain.models.ops import DocumentRun, RuntimePatchProposal
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.services.run_execution import RunExecutionService
from book_agent.services.bundle_guard import BundleGuardService
from book_agent.services.runtime_bundle import RuntimeBundleRecord, RuntimeBundleService
from book_agent.services.runtime_repair_blockage import project_runtime_repair_blockage
from book_agent.services.runtime_repair_contract import build_runtime_repair_request_input_bundle
from book_agent.services.runtime_patch_validation import (
    RuntimePatchValidationResult,
    RuntimePatchValidationService,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IncidentController:
    def __init__(
        self,
        *,
        session: Session,
        bundle_service: RuntimeBundleService | None = None,
        bundle_guard_service: BundleGuardService | None = None,
        validation_service: RuntimePatchValidationService | None = None,
    ):
        self._session = session
        self._runtime_repo = RuntimeResourcesRepository(session)
        self._run_execution = RunExecutionService(RunControlRepository(session))
        self._bundle_service = bundle_service or RuntimeBundleService(session)
        self._bundle_guard_service = bundle_guard_service or BundleGuardService(
            session,
            bundle_service=self._bundle_service,
        )
        self._validation_service = validation_service or RuntimePatchValidationService(session)

    def open_patch_proposal(
        self,
        *,
        incident_id: str,
        patch_surface: str,
        diff_manifest_json: dict[str, Any],
        proposed_by: str = "runtime.incident-controller",
        status_detail_json: dict[str, Any] | None = None,
    ) -> RuntimePatchProposal:
        incident = self._runtime_repo.get_runtime_incident(incident_id)
        now = _utcnow()
        proposal = RuntimePatchProposal(
            incident_id=incident.id,
            status=RuntimePatchProposalStatus.PROPOSED,
            proposed_by=proposed_by,
            patch_surface=patch_surface,
            diff_manifest_json=dict(diff_manifest_json),
            validation_report_json={},
            status_detail_json=dict(status_detail_json or {}),
            created_at=now,
            updated_at=now,
        )
        self._session.add(proposal)
        self._session.flush()
        repair_plan = dict((status_detail_json or {}).get("repair_plan") or {})
        repair_dispatch = (
            self._seed_repair_dispatch(
                run_id=incident.run_id,
                incident_id=incident.id,
                proposal_id=proposal.id,
                patch_surface=patch_surface,
                repair_plan=repair_plan,
            )
            if repair_plan
            else None
        )
        proposal.status_detail_json = {
            **dict(proposal.status_detail_json or {}),
            **({"repair_dispatch": repair_dispatch} if repair_dispatch is not None else {}),
        }
        incident.status = RuntimeIncidentStatus.PATCH_PROPOSED
        incident.status_detail_json = {
            **dict(incident.status_detail_json or {}),
            "latest_patch_proposal": {
                "proposal_id": proposal.id,
                "patch_surface": patch_surface,
                "proposed_by": proposed_by,
                "repair_plan": repair_plan,
                "repair_dispatch": dict(repair_dispatch or {}),
            },
            **({"repair_dispatch": dict(repair_dispatch)} if repair_dispatch is not None else {}),
        }
        incident.updated_at = now
        if repair_dispatch is not None:
            self._persist_repair_dispatch(proposal=proposal, incident=incident, dispatch=repair_dispatch, now=now)
        else:
            self._session.add_all([proposal, incident])
            self._session.flush()
        return proposal

    def claim_repair_dispatch(
        self,
        *,
        proposal_id: str,
        worker_name: str,
        worker_instance_id: str,
    ) -> dict[str, Any]:
        proposal = self._runtime_repo.get_runtime_patch_proposal(proposal_id)
        dispatch = self._get_repair_dispatch(proposal)
        work_item_id = str(dispatch.get("repair_work_item_id") or "")
        if not work_item_id:
            raise ValueError(f"Repair dispatch is missing repair work item id: {proposal_id}")
        claimed_work_item = self._run_execution.claim_repair_dispatch_work_item(
            work_item_id=work_item_id,
            worker_name=worker_name,
            worker_instance_id=worker_instance_id,
            lease_seconds=300,
        )
        if claimed_work_item is None:
            raise ValueError(f"Repair dispatch work item is not claimable: {proposal_id}")
        claimed_work_item = self._run_execution.start_work_item(
            lease_token=claimed_work_item.lease_token,
            lease_seconds=300,
        )
        return self.begin_repair_dispatch_execution(
            proposal_id=proposal_id,
            worker_name=worker_name,
            worker_instance_id=worker_instance_id,
            work_item_id=claimed_work_item.work_item_id,
            lease_token=claimed_work_item.lease_token,
        )

    def begin_repair_dispatch_execution(
        self,
        *,
        proposal_id: str,
        worker_name: str,
        worker_instance_id: str,
        work_item_id: str,
        lease_token: str,
    ) -> dict[str, Any]:
        proposal = self._runtime_repo.get_runtime_patch_proposal(proposal_id)
        incident = self._runtime_repo.get_runtime_incident(proposal.incident_id)
        dispatch = self._get_repair_dispatch(proposal)
        expected_work_item_id = str(dispatch.get("repair_work_item_id") or "")
        if expected_work_item_id and expected_work_item_id != work_item_id:
            raise ValueError(
                f"Repair dispatch work item mismatch for proposal {proposal_id}: "
                f"expected {expected_work_item_id}, got {work_item_id}"
            )
        now = _utcnow()
        execution = {
            "execution_id": str(uuid4()),
            "worker_name": worker_name,
            "worker_instance_id": worker_instance_id,
            "work_item_id": work_item_id,
            "lease_token": lease_token,
            "claimed_at": now.isoformat(),
            "status": "running",
        }
        dispatch["status"] = "claimed"
        dispatch["attempt_count"] = int(dispatch.get("attempt_count", 0) or 0) + 1
        dispatch["claimed_by"] = {
            "worker_name": worker_name,
            "worker_instance_id": worker_instance_id,
        }
        dispatch["last_claimed_at"] = now.isoformat()
        dispatch["current_execution"] = execution
        self._persist_repair_dispatch(proposal=proposal, incident=incident, dispatch=dispatch, now=now)
        return dict(dispatch)

    def resume_repair_dispatch(
        self,
        *,
        proposal_id: str,
        resumed_by: str,
        note: str | None = None,
        dispatch_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        proposal = self._runtime_repo.get_runtime_patch_proposal(proposal_id)
        incident = self._runtime_repo.get_runtime_incident(proposal.incident_id)
        dispatch = self._get_repair_dispatch(proposal)
        work_item_id = str(dispatch.get("repair_work_item_id") or "")
        if not work_item_id:
            raise ValueError(f"Repair dispatch is missing repair work item id: {proposal_id}")
        normalized_overrides = self._apply_repair_dispatch_overrides(
            dispatch=dispatch,
            overrides=dispatch_overrides,
        )
        self._refresh_repair_dispatch_work_item_input(
            proposal=proposal,
            incident_id=incident.id,
            dispatch=dispatch,
        )
        work_item = self._run_execution.resume_repair_dispatch_work_item(
            work_item_id=work_item_id,
            actor_id=resumed_by,
            note=note,
        )
        now = _utcnow()
        resume_entry = {
            "resumed_at": now.isoformat(),
            "resumed_by": resumed_by,
            "note": note or "",
            "previous_status": str(dispatch.get("status") or ""),
            "previous_decision": str(dispatch.get("decision") or ""),
            "next_action": "claim_repair_lane",
            "work_item_attempt": int(work_item.attempt or 0),
            "dispatch_overrides": dict(normalized_overrides),
        }
        resume_history = [
            dict(entry)
            for entry in list(dispatch.get("resume_history") or [])
            if isinstance(entry, dict)
        ]
        resume_history.append(resume_entry)
        dispatch["status"] = "pending"
        dispatch["next_action"] = "claim_repair_lane"
        dispatch["retryable"] = False
        dispatch["current_execution"] = None
        dispatch["resume_history"] = resume_history
        dispatch["resume_count"] = len(resume_history)
        dispatch["last_resumed_at"] = now.isoformat()
        dispatch["last_resumed_by"] = resumed_by
        dispatch["last_resume_overrides"] = dict(normalized_overrides)
        dispatch.pop("decision", None)
        dispatch.pop("decision_reason", None)
        dispatch.pop("retry_after_seconds", None)
        dispatch.pop("next_retry_after", None)
        self._persist_repair_dispatch(proposal=proposal, incident=incident, dispatch=dispatch, now=now)
        return dict(dispatch)

    def record_repair_dispatch_execution(
        self,
        *,
        proposal_id: str,
        succeeded: bool,
        result_json: dict[str, Any] | None = None,
        manage_work_item_lifecycle: bool = True,
    ) -> dict[str, Any]:
        proposal = self._runtime_repo.get_runtime_patch_proposal(proposal_id)
        incident = self._runtime_repo.get_runtime_incident(proposal.incident_id)
        dispatch = self._get_repair_dispatch(proposal)
        current_execution = dict(dispatch.get("current_execution") or {})
        if not current_execution:
            raise ValueError(f"Repair dispatch has not been claimed: {proposal_id}")
        lease_token = str(current_execution.get("lease_token") or "")
        if not lease_token:
            raise ValueError(f"Repair dispatch is missing lease token: {proposal_id}")

        now = _utcnow()
        execution_record = {
            **current_execution,
            "completed_at": now.isoformat(),
            "status": "succeeded" if succeeded else "failed",
            "result_json": dict(result_json or {}),
        }
        if manage_work_item_lifecycle:
            if succeeded:
                self._run_execution.complete_work_item_success(
                    lease_token=lease_token,
                    output_artifact_refs_json={
                        "proposal_id": proposal.id,
                        "incident_id": incident.id,
                    },
                    payload_json={
                        "repair_dispatch_id": dispatch.get("dispatch_id"),
                        "patch_surface": dispatch.get("patch_surface"),
                        "target_scope_type": dispatch.get("replay", {}).get("scope_type"),
                        "target_scope_id": dispatch.get("replay", {}).get("scope_id"),
                        **dict(result_json or {}),
                    },
                )
            else:
                self._run_execution.complete_work_item_failure(
                    lease_token=lease_token,
                    error_class="repair_dispatch_failed",
                    error_detail_json=dict(result_json or {}),
                    retryable=False,
                )
        history = [
            dict(entry)
            for entry in list(dispatch.get("execution_history") or [])
            if isinstance(entry, dict)
        ]
        history.append(execution_record)
        decision = str((result_json or {}).get("repair_agent_decision") or "").strip()
        decision_reason = str((result_json or {}).get("repair_agent_decision_reason") or "").strip()
        retry_after_seconds = int((result_json or {}).get("repair_agent_retry_after_seconds") or 0)
        dispatch["decision"] = decision or ("publish_bundle_and_replay" if succeeded else "failed")
        if decision_reason:
            dispatch["decision_reason"] = decision_reason
        if succeeded:
            dispatch["status"] = "executed"
            dispatch["next_action"] = "replay_repaired_scope"
            dispatch["retryable"] = False
            dispatch.pop("retry_after_seconds", None)
            dispatch.pop("next_retry_after", None)
        elif decision in {"manual_escalation_required", "retry_later"}:
            dispatch["status"] = decision
            dispatch["next_action"] = (
                "manual_escalation"
                if decision == "manual_escalation_required"
                else "retry_repair_lane"
            )
            dispatch["retryable"] = decision == "retry_later"
            if decision == "retry_later" and retry_after_seconds > 0:
                dispatch["retry_after_seconds"] = retry_after_seconds
                dispatch["next_retry_after"] = (now + timedelta(seconds=retry_after_seconds)).replace(
                    microsecond=0
                ).isoformat()
            else:
                dispatch.pop("retry_after_seconds", None)
                dispatch.pop("next_retry_after", None)
        else:
            dispatch["status"] = "failed"
            dispatch["next_action"] = "inspect_repair_failure"
            dispatch["retryable"] = False
            dispatch.pop("retry_after_seconds", None)
            dispatch.pop("next_retry_after", None)
        dispatch["current_execution"] = None
        dispatch["execution_history"] = history
        dispatch["last_result"] = execution_record
        dispatch["last_completed_at"] = now.isoformat()
        self._persist_repair_dispatch(proposal=proposal, incident=incident, dispatch=dispatch, now=now)
        return dict(dispatch)

    def validate_patch_proposal(
        self,
        *,
        proposal_id: str,
        passed: bool,
        report_json: dict[str, Any] | None = None,
    ) -> RuntimePatchValidationResult:
        proposal = self._runtime_repo.get_runtime_patch_proposal(proposal_id)
        incident = self._runtime_repo.get_runtime_incident(proposal.incident_id)
        incident.status = RuntimeIncidentStatus.VALIDATING
        incident.updated_at = _utcnow()
        self._session.add(incident)
        self._session.flush()

        self._validation_service.begin_validation(proposal_id=proposal_id)
        result = self._validation_service.record_validation_result(
            proposal_id=proposal_id,
            passed=passed,
            report_json=report_json,
        )

        incident.status = RuntimeIncidentStatus.VALIDATING if passed else RuntimeIncidentStatus.FAILED
        incident.status_detail_json = {
            **dict(incident.status_detail_json or {}),
            "validation": result.report_json,
        }
        dispatch = dict((proposal.status_detail_json or {}).get("repair_dispatch") or {})
        if dispatch:
            dispatch["validation"] = {
                "status": "passed" if passed else "failed",
                "report_json": result.report_json,
            }
            self._persist_repair_dispatch(proposal=proposal, incident=incident, dispatch=dispatch, now=_utcnow())
        incident.updated_at = _utcnow()
        self._session.add(incident)
        self._session.flush()
        return result

    def publish_validated_patch(
        self,
        *,
        proposal_id: str,
        revision_name: str,
        manifest_json: dict[str, Any],
        rollout_scope_json: dict[str, Any] | None = None,
        canary_report_json: dict[str, Any] | None = None,
    ) -> RuntimeBundleRecord:
        proposal = self._runtime_repo.get_runtime_patch_proposal(proposal_id)
        if proposal.status != RuntimePatchProposalStatus.VALIDATED:
            raise ValueError(f"RuntimePatchProposal is not validated: {proposal_id}")

        incident = self._runtime_repo.get_runtime_incident(proposal.incident_id)
        bundle_record = self._bundle_service.publish_bundle(
            revision_name=revision_name,
            manifest_json=manifest_json,
            parent_bundle_revision_id=incident.runtime_bundle_revision_id,
            rollout_scope_json=rollout_scope_json or {"mode": "dev"},
        )
        self._bundle_service.activate_bundle(bundle_record.revision.id)
        bundle_guard = self._bundle_guard_service.evaluate_canary_and_maybe_rollback(
            revision_id=bundle_record.revision.id,
            report_json=canary_report_json or proposal.validation_report_json,
            rollout_scope_json=rollout_scope_json or {"mode": "dev"},
        )
        effective_bundle_record = self._bundle_service.lookup_bundle(bundle_guard.effective_revision_id)

        now = _utcnow()
        proposal.status = (
            RuntimePatchProposalStatus.ROLLED_BACK
            if bundle_guard.rollback_performed
            else RuntimePatchProposalStatus.PUBLISHED
        )
        proposal.published_bundle_revision_id = bundle_record.revision.id
        proposal.status_detail_json = {
            **dict(proposal.status_detail_json or {}),
            "published_revision_id": bundle_record.revision.id,
            "bundle_guard": {
                "canary_verdict": bundle_guard.canary_verdict,
                "rollback_performed": bundle_guard.rollback_performed,
                "effective_revision_id": bundle_guard.effective_revision_id,
                "rollback_target_revision_id": bundle_guard.rollback_target_revision_id,
                "freeze_reason": bundle_guard.freeze_reason,
            },
        }
        proposal.updated_at = now

        incident.status = RuntimeIncidentStatus.FROZEN if bundle_guard.rollback_performed else RuntimeIncidentStatus.PUBLISHED
        incident.runtime_bundle_revision_id = effective_bundle_record.revision.id
        incident.bundle_json = {
            **dict(incident.bundle_json or {}),
            "published_bundle_revision_id": bundle_record.revision.id,
            "active_bundle_revision_id": effective_bundle_record.revision.id,
            "rollback_target_revision_id": bundle_guard.rollback_target_revision_id,
            "revision_name": revision_name,
        }
        incident.updated_at = now

        run = self._session.get(DocumentRun, incident.run_id)
        if run is not None:
            run.runtime_bundle_revision_id = effective_bundle_record.revision.id
            runtime_v2 = dict((run.status_detail_json or {}).get("runtime_v2") or {})
            runtime_v2["active_runtime_bundle_revision_id"] = effective_bundle_record.revision.id
            status_detail = dict(run.status_detail_json or {})
            status_detail["runtime_v2"] = runtime_v2
            run.status_detail_json = status_detail
            run.updated_at = now
            self._session.add(run)

        bound_work_items = self._bind_retryable_scope_work_items(
            run_id=incident.run_id,
            scope_type=incident.scope_type,
            scope_id=incident.scope_id,
            revision_id=effective_bundle_record.revision.id,
            work_item_scope_type_override=(
                WorkItemScopeType.EXPORT
                if incident.incident_kind == RuntimeIncidentKind.EXPORT_MISROUTING
                else None
            ),
        )
        proposal.status_detail_json = {
            **dict(proposal.status_detail_json or {}),
            "bound_work_item_ids": bound_work_items,
        }
        dispatch = dict((proposal.status_detail_json or {}).get("repair_dispatch") or {})
        if dispatch:
            dispatch["bundle_publication"] = {
                "published_revision_id": bundle_record.revision.id,
                "effective_revision_id": effective_bundle_record.revision.id,
                "rollback_performed": bundle_guard.rollback_performed,
                "bound_work_item_ids": bound_work_items,
            }
            self._persist_repair_dispatch(proposal=proposal, incident=incident, dispatch=dispatch, now=now)

        self._session.add_all([proposal, incident])
        self._session.flush()
        return bundle_record

    def _seed_repair_dispatch(
        self,
        *,
        run_id: str,
        incident_id: str,
        proposal_id: str,
        patch_surface: str,
        repair_plan: dict[str, Any],
    ) -> dict[str, Any]:
        dispatch = {
            "dispatch_id": str(uuid4()),
            "status": "pending",
            "claim_mode": str((repair_plan.get("dispatch") or {}).get("claim_mode") or "runtime_owned"),
            "claim_target": str((repair_plan.get("dispatch") or {}).get("claim_target") or "runtime_patch_proposal"),
            "lane": str((repair_plan.get("dispatch") or {}).get("lane") or "runtime.repair"),
            "worker_hint": str((repair_plan.get("dispatch") or {}).get("worker_hint") or ""),
            "worker_contract_version": int((repair_plan.get("dispatch") or {}).get("worker_contract_version") or 1),
            "execution_mode": str((repair_plan.get("dispatch") or {}).get("execution_mode") or "in_process"),
            "executor_hint": str((repair_plan.get("dispatch") or {}).get("executor_hint") or "python_repair_executor"),
            "executor_contract_version": int(
                (repair_plan.get("dispatch") or {}).get("executor_contract_version") or 1
            ),
            "transport_hint": str((repair_plan.get("dispatch") or {}).get("transport_hint") or ""),
            "transport_contract_version": int(
                (repair_plan.get("dispatch") or {}).get("transport_contract_version") or 1
            ),
            "proposal_id": proposal_id,
            "incident_id": incident_id,
            "patch_surface": patch_surface,
            "attempt_count": 0,
            "owned_files": list(repair_plan.get("owned_files") or []),
            "validation_command": str((repair_plan.get("validation") or {}).get("command") or ""),
            "bundle_revision_name": str((repair_plan.get("bundle") or {}).get("revision_name") or ""),
            "rollout_scope_json": dict((repair_plan.get("bundle") or {}).get("rollout_scope_json") or {}),
            "replay": dict(repair_plan.get("replay") or {}),
            "current_execution": None,
            "execution_history": [],
        }
        dispatch["repair_work_item_id"] = self._run_execution.ensure_repair_dispatch_work_item(
            run_id=run_id,
            proposal_id=proposal_id,
            incident_id=incident_id,
            repair_dispatch_json=dispatch,
            repair_plan_json=repair_plan,
        )
        return dispatch

    def _get_repair_dispatch(self, proposal: RuntimePatchProposal) -> dict[str, Any]:
        dispatch = dict((proposal.status_detail_json or {}).get("repair_dispatch") or {})
        if not dispatch:
            raise ValueError(f"Repair dispatch is not available for proposal: {proposal.id}")
        return dispatch

    def _persist_repair_dispatch(
        self,
        *,
        proposal: RuntimePatchProposal,
        incident,
        dispatch: dict[str, Any],
        now: datetime,
    ) -> None:
        dispatch["repair_blockage"] = project_runtime_repair_blockage(dispatch, now=now)
        proposal.status_detail_json = {
            **dict(proposal.status_detail_json or {}),
            "repair_dispatch": dict(dispatch),
        }
        proposal.updated_at = now

        incident_detail = dict(incident.status_detail_json or {})
        latest_patch = dict(incident_detail.get("latest_patch_proposal") or {})
        latest_patch["repair_dispatch"] = dict(dispatch)
        incident_detail["latest_patch_proposal"] = latest_patch
        incident_detail["repair_dispatch"] = dict(dispatch)
        incident.status_detail_json = incident_detail
        incident.updated_at = now

        self._session.add_all([proposal, incident])
        self._session.flush()
        self._project_bounded_lane_repair_control_plane(
            proposal=proposal,
            incident=incident,
            dispatch=dispatch,
            now=now,
        )

    def _refresh_repair_dispatch_work_item_input(
        self,
        *,
        proposal: RuntimePatchProposal,
        incident_id: str,
        dispatch: dict[str, Any],
    ) -> None:
        work_item_id = str(dispatch.get("repair_work_item_id") or "")
        if not work_item_id:
            return
        work_item = RunControlRepository(self._session).get_work_item(work_item_id)
        repair_plan = dict((proposal.status_detail_json or {}).get("repair_plan") or {})
        work_item.input_version_bundle_json = build_runtime_repair_request_input_bundle(
            proposal_id=proposal.id,
            incident_id=incident_id,
            repair_dispatch_json=dispatch,
            repair_plan_json=repair_plan,
        )
        work_item.updated_at = _utcnow()
        self._session.add(work_item)
        self._session.flush()

    @staticmethod
    def _apply_repair_dispatch_overrides(
        *,
        dispatch: dict[str, Any],
        overrides: dict[str, Any] | None,
    ) -> dict[str, Any]:
        normalized = dict(overrides or {})
        if not normalized:
            return {}
        if "execution_mode" in normalized:
            dispatch["execution_mode"] = str(normalized["execution_mode"] or "").strip()
        if "worker_hint" in normalized:
            dispatch["worker_hint"] = str(normalized["worker_hint"] or "").strip()
        if "worker_contract_version" in normalized:
            dispatch["worker_contract_version"] = int(normalized["worker_contract_version"] or 1)
        if "executor_hint" in normalized:
            dispatch["executor_hint"] = str(normalized["executor_hint"] or "").strip()
        if "executor_contract_version" in normalized:
            dispatch["executor_contract_version"] = int(normalized["executor_contract_version"] or 1)
        if "transport_hint" in normalized:
            dispatch["transport_hint"] = str(normalized["transport_hint"] or "").strip()
        if "transport_contract_version" in normalized:
            dispatch["transport_contract_version"] = int(normalized["transport_contract_version"] or 1)
        if "validation_command" in normalized:
            dispatch["validation_command"] = str(normalized["validation_command"] or "").strip()
        if "bundle_revision_name" in normalized:
            dispatch["bundle_revision_name"] = str(normalized["bundle_revision_name"] or "").strip()
        return {
            key: dispatch[key]
            for key in (
                "execution_mode",
                "worker_hint",
                "worker_contract_version",
                "executor_hint",
                "executor_contract_version",
                "transport_hint",
                "transport_contract_version",
                "validation_command",
                "bundle_revision_name",
            )
            if key in normalized
        }

    def _project_bounded_lane_repair_control_plane(
        self,
        *,
        proposal: RuntimePatchProposal,
        incident,
        dispatch: dict[str, Any],
        now: datetime,
    ) -> None:
        if incident.incident_kind == RuntimeIncidentKind.REVIEW_DEADLOCK:
            self._project_review_deadlock_repair_control_plane(
                proposal=proposal,
                incident=incident,
                dispatch=dispatch,
                now=now,
            )
            return
        if incident.incident_kind == RuntimeIncidentKind.PACKET_RUNTIME_DEFECT:
            self._project_packet_runtime_defect_repair_control_plane(
                proposal=proposal,
                incident=incident,
                dispatch=dispatch,
                now=now,
            )
            return
        if incident.incident_kind == RuntimeIncidentKind.EXPORT_MISROUTING:
            self._project_export_misrouting_repair_control_plane(
                proposal=proposal,
                incident=incident,
                dispatch=dispatch,
                now=now,
            )

    def _project_review_deadlock_repair_control_plane(
        self,
        *,
        proposal: RuntimePatchProposal,
        incident,
        dispatch: dict[str, Any],
        now: datetime,
    ) -> None:
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
        replay_scope_id = str(
            (proposal.status_detail_json or {}).get("repair_plan", {}).get("replay", {}).get("scope_id")
            or incident.scope_id
        )
        if not chapter_run_id or not review_session_id or not replay_scope_id:
            return
        try:
            review_session = self._runtime_repo.get_review_session(review_session_id)
            chapter_run = self._runtime_repo.get_chapter_run(chapter_run_id)
        except ValueError:
            return
        existing_runtime_v2 = dict((review_session.status_detail_json or {}).get("runtime_v2") or {})
        existing_recovery = dict(existing_runtime_v2.get("last_deadlock_recovery") or {})
        recovery_payload = self._build_bounded_lane_recovery_payload(
            proposal=proposal,
            incident=incident,
            dispatch=dispatch,
            existing_recovery=existing_recovery,
            replay_scope_id=replay_scope_id,
        )
        self._runtime_repo.merge_review_session_status_detail(
            review_session.id,
            {"runtime_v2": {"last_deadlock_recovery": recovery_payload}},
        )
        self._runtime_repo.upsert_checkpoint(
            run_id=incident.run_id,
            scope_type=JobScopeType.CHAPTER,
            scope_id=replay_scope_id,
            checkpoint_key="review_controller.deadlock_recovery",
            checkpoint_json={
                "chapter_run_id": chapter_run.id,
                "review_session_id": review_session.id,
                "recovery": recovery_payload,
                "validation_report": dict((dispatch.get("validation") or {}).get("report_json") or {}),
            },
            generation=int(chapter_run.generation or 1),
        )

    def _project_packet_runtime_defect_repair_control_plane(
        self,
        *,
        proposal: RuntimePatchProposal,
        incident,
        dispatch: dict[str, Any],
        now: datetime,
    ) -> None:
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
        replay_scope_id = str(
            (proposal.status_detail_json or {}).get("repair_plan", {}).get("replay", {}).get("scope_id")
            or incident.scope_id
        )
        if not packet_task_id or not chapter_run_id or not replay_scope_id:
            return
        try:
            packet_task = self._runtime_repo.get_packet_task(packet_task_id)
            chapter_run = self._runtime_repo.get_chapter_run(chapter_run_id)
        except ValueError:
            return
        existing_runtime_v2 = dict((packet_task.status_detail_json or {}).get("runtime_v2") or {})
        existing_recovery = dict(existing_runtime_v2.get("last_runtime_defect_recovery") or {})
        recovery_payload = self._build_bounded_lane_recovery_payload(
            proposal=proposal,
            incident=incident,
            dispatch=dispatch,
            existing_recovery=existing_recovery,
            replay_scope_id=replay_scope_id,
        )
        self._runtime_repo.merge_packet_task_status_detail(
            packet_task.id,
            {"runtime_v2": {"last_runtime_defect_recovery": recovery_payload}},
        )
        self._runtime_repo.upsert_checkpoint(
            run_id=incident.run_id,
            scope_type=JobScopeType.PACKET,
            scope_id=replay_scope_id,
            checkpoint_key="packet_controller.runtime_defect_recovery",
            checkpoint_json={
                "packet_task_id": packet_task.id,
                "chapter_run_id": chapter_run.id,
                "recovery": recovery_payload,
                "validation_report": dict((dispatch.get("validation") or {}).get("report_json") or {}),
            },
            generation=int(packet_task.packet_generation or 1),
        )

    def _project_export_misrouting_repair_control_plane(
        self,
        *,
        proposal: RuntimePatchProposal,
        incident,
        dispatch: dict[str, Any],
        now: datetime,
    ) -> None:
        run = self._session.get(DocumentRun, incident.run_id)
        if run is None:
            return
        status_detail = dict(run.status_detail_json or {})
        runtime_v2 = dict(status_detail.get("runtime_v2") or {})
        existing_pending = dict(runtime_v2.get("pending_export_route_repair") or {})
        repair_plan = dict((proposal.status_detail_json or {}).get("repair_plan") or {})
        runtime_v2["pending_export_route_repair"] = {
            **existing_pending,
            "incident_id": incident.id,
            "proposal_id": proposal.id,
            "bundle_revision_id": proposal.published_bundle_revision_id,
            "repair_work_item_id": str(dispatch.get("repair_work_item_id") or ""),
            "replay_scope_id": str((repair_plan.get("replay") or {}).get("scope_id") or incident.scope_id),
            "bound_work_item_ids": [
                str(work_item_id)
                for work_item_id in list((proposal.status_detail_json or {}).get("bound_work_item_ids") or [])
                if str(work_item_id).strip()
            ],
            "status": str(dispatch.get("status") or existing_pending.get("status") or "pending"),
            "repair_blockage": dict(dispatch.get("repair_blockage") or {}),
            "updated_at": now.isoformat(),
        }
        status_detail["runtime_v2"] = runtime_v2
        run.status_detail_json = status_detail
        run.updated_at = now
        self._session.add(run)
        self._session.flush()

    @staticmethod
    def _build_bounded_lane_recovery_payload(
        *,
        proposal: RuntimePatchProposal,
        incident,
        dispatch: dict[str, Any],
        existing_recovery: dict[str, Any],
        replay_scope_id: str,
    ) -> dict[str, Any]:
        route_evidence = dict(incident.route_evidence_json or {})
        persisted_status = str(existing_recovery.get("status") or "").strip()
        dispatch_status = str(dispatch.get("status") or "").strip()
        return {
            **existing_recovery,
            "incident_id": incident.id,
            "proposal_id": proposal.id,
            "bundle_revision_id": proposal.published_bundle_revision_id or existing_recovery.get("bundle_revision_id"),
            "repair_work_item_id": str(dispatch.get("repair_work_item_id") or ""),
            "replay_scope_id": replay_scope_id,
            "bound_work_item_ids": [
                str(work_item_id)
                for work_item_id in list((proposal.status_detail_json or {}).get("bound_work_item_ids") or [])
                if str(work_item_id).strip()
            ],
            "reason_code": route_evidence.get("reason_code") or existing_recovery.get("reason_code"),
            "lane_health_state": route_evidence.get("lane_health_state") or existing_recovery.get("lane_health_state"),
            "status": (
                persisted_status
                if persisted_status == "published"
                else (dispatch_status or persisted_status or "pending")
            ),
            "repair_blockage": dict(dispatch.get("repair_blockage") or {}),
        }

    def _bind_retryable_scope_work_items(
        self,
        *,
        run_id: str,
        scope_type: JobScopeType,
        scope_id: str,
        revision_id: str,
        work_item_scope_type_override: WorkItemScopeType | None = None,
    ) -> list[str]:
        work_item_scope_type = work_item_scope_type_override or _work_item_scope_type_for_job_scope(scope_type)
        if work_item_scope_type is None:
            return []
        work_items = self._runtime_repo.list_retryable_work_items_for_scope(
            run_id=run_id,
            scope_type=work_item_scope_type,
            scope_id=scope_id,
        )
        now = _utcnow()
        bound_ids: list[str] = []
        for work_item in work_items:
            work_item.runtime_bundle_revision_id = revision_id
            work_item.updated_at = now
            self._session.add(work_item)
            bound_ids.append(work_item.id)
        self._session.flush()
        return bound_ids


def _work_item_scope_type_for_job_scope(scope_type: JobScopeType) -> WorkItemScopeType | None:
    if scope_type == JobScopeType.DOCUMENT:
        return WorkItemScopeType.DOCUMENT
    if scope_type == JobScopeType.CHAPTER:
        return WorkItemScopeType.CHAPTER
    if scope_type == JobScopeType.PACKET:
        return WorkItemScopeType.PACKET
    return None
