from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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
from book_agent.services.bundle_guard import BundleGuardService
from book_agent.services.runtime_bundle import RuntimeBundleRecord, RuntimeBundleService
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
            status_detail_json={},
            created_at=now,
            updated_at=now,
        )
        incident.status = RuntimeIncidentStatus.PATCH_PROPOSED
        incident.updated_at = now
        self._session.add_all([proposal, incident])
        self._session.flush()
        return proposal

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

        self._session.add_all([proposal, incident])
        self._session.flush()
        return bundle_record

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
