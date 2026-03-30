from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from book_agent.domain.enums import JobScopeType, RuntimeIncidentKind
from book_agent.domain.models.ops import DocumentRun, RuntimeIncident, RuntimePatchProposal
from book_agent.app.runtime.controllers.budget_controller import BudgetController
from book_agent.app.runtime.controllers.incident_controller import IncidentController
from book_agent.services.incident_triage import IncidentTriageService
from book_agent.services.runtime_repair_planner import RuntimeRepairPlannerService


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ExportMisroutingRecoveryResult:
    run_id: str
    incident_id: str
    proposal_id: str
    bundle_revision_id: str
    route_evidence_json: dict[str, Any]
    corrected_route: str
    replay_scope_id: str
    bound_work_item_ids: list[str]
    validation_report_json: dict[str, Any]


class ExportController:
    def __init__(self, *, session: Session):
        self._session = session
        self._budget_controller = BudgetController(session=session)
        self._incident_controller = IncidentController(session=session)
        self._incident_triage = IncidentTriageService()
        self._repair_planner = RuntimeRepairPlannerService()

    def recover_export_misrouting(
        self,
        *,
        run_id: str,
        work_item_id: str | None,
        scope_id: str,
        source_type: str,
        selected_route: str,
        runtime_bundle_revision_id: str | None,
        route_candidates: list[str],
        route_evidence_json: dict[str, Any],
        error_message: str,
        export_type: str | None = None,
    ) -> ExportMisroutingRecoveryResult:
        budget_decision = self._budget_controller.evaluate_auto_patch(
            run_id=run_id,
            patch_surface="runtime_bundle",
        )
        if not budget_decision.allowed:
            raise RuntimeError(
                "Export misrouting recovery is blocked by runtime budget guardrails: "
                f"{budget_decision.reason}"
            )
        self._budget_controller.record_auto_patch_attempt(
            run_id=run_id,
            patch_surface="runtime_bundle",
        )

        incident = self._incident_triage.open_or_update_incident(
            self._session,
            run_id=run_id,
            scope_type=JobScopeType.DOCUMENT,
            scope_id=scope_id,
            incident_kind=RuntimeIncidentKind.EXPORT_MISROUTING,
            source_type=source_type,
            selected_route=selected_route,
            runtime_bundle_revision_id=runtime_bundle_revision_id,
            error_code="export_misrouting",
            error_message=error_message,
            route_evidence_json=route_evidence_json,
            latest_error_json={
                "error_code": "export_misrouting",
                "error_message": error_message,
                "route_evidence_json": route_evidence_json,
            },
            bundle_json={
                "export_type": export_type,
                "route_candidates": list(route_candidates),
            },
            status_detail_json={
                "budget_decision": {
                    "allowed": budget_decision.allowed,
                    "reason": budget_decision.reason,
                    "allowed_patch_surfaces": budget_decision.allowed_patch_surfaces,
                    "current_auto_patch_attempt_count": budget_decision.current_auto_patch_attempt_count,
                },
                "route_candidates": list(route_candidates),
                "selected_route": selected_route,
            },
            latest_work_item_id=work_item_id,
        )

        corrected_route = route_candidates[0] if route_candidates else selected_route
        repair_plan = self._repair_planner.plan_export_misrouting_repair(
            scope_id=scope_id,
            export_type=export_type,
            corrected_route=corrected_route,
            route_candidates=route_candidates,
            route_evidence_json=route_evidence_json,
        )
        proposal = self._incident_controller.open_patch_proposal(
            incident_id=incident.id,
            patch_surface=repair_plan.patch_surface,
            diff_manifest_json=repair_plan.diff_manifest_json,
            proposed_by="runtime.export-controller",
            status_detail_json={"repair_plan": repair_plan.handoff_json},
        )
        validation_result = self._incident_controller.validate_patch_proposal(
            proposal_id=proposal.id,
            passed=True,
            report_json=repair_plan.validation_report_json,
        )

        bundle_record = self._incident_controller.publish_validated_patch(
            proposal_id=proposal.id,
            revision_name=repair_plan.revision_name,
            manifest_json=repair_plan.manifest_json,
            rollout_scope_json=repair_plan.rollout_scope_json,
        )

        proposal = self._session.get(RuntimePatchProposal, proposal.id)
        incident = self._session.get(RuntimeIncident, incident.id)
        run = self._session.get(DocumentRun, run_id)
        if proposal is None or incident is None or run is None:
            raise RuntimeError("Runtime misrouting recovery state vanished during publish.")
        proposal_detail = dict(proposal.status_detail_json or {})
        bound_work_item_ids = [
            str(work_item_id)
            for work_item_id in proposal_detail.get("bound_work_item_ids", [])
            if str(work_item_id).strip()
        ]
        runtime_v2 = dict((run.status_detail_json or {}).get("runtime_v2") or {})
        runtime_v2["last_export_route_evidence"] = route_evidence_json
        runtime_v2["last_export_route_recovery"] = {
            "incident_id": incident.id,
            "proposal_id": proposal.id,
            "bundle_revision_id": bundle_record.revision.id,
            "selected_route": selected_route,
            "corrected_route": corrected_route,
            "route_candidates": list(route_candidates),
            "export_type": export_type,
        }
        status_detail = dict(run.status_detail_json or {})
        status_detail["runtime_v2"] = runtime_v2
        run.status_detail_json = status_detail
        run.runtime_bundle_revision_id = bundle_record.revision.id
        run.updated_at = _utcnow()
        self._session.add(run)
        self._session.flush()

        return ExportMisroutingRecoveryResult(
            run_id=run_id,
            incident_id=incident.id,
            proposal_id=proposal.id,
            bundle_revision_id=bundle_record.revision.id,
            route_evidence_json=route_evidence_json,
            corrected_route=corrected_route,
            replay_scope_id=scope_id,
            bound_work_item_ids=bound_work_item_ids,
            validation_report_json=validation_result.report_json,
        )

    def _build_patch_manifest(
        self,
        *,
        export_type: str | None,
        corrected_route: str,
        route_candidates: list[str],
        route_evidence_json: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "files": [
                "src/book_agent/services/export_routing.py",
                "src/book_agent/services/export.py",
                "src/book_agent/services/workflows.py",
            ],
            "patch_surface": "runtime_bundle",
            "export_type": export_type,
            "corrected_route": corrected_route,
            "route_candidates": list(route_candidates),
            "route_evidence_json": dict(route_evidence_json),
        }

    def _build_runtime_bundle_manifest(
        self,
        *,
        export_type: str | None,
        corrected_route: str,
        route_candidates: list[str],
        route_evidence_json: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "code": {
                "entrypoint": "book_agent.app.runtime.document_run_executor",
                "surface": "export_routing",
            },
            "config": {
                "routing_policy": {
                    "export_routes": {
                        str(export_type or route_evidence_json.get("export_type") or "rebuilt_pdf"): {
                            "selected_route": corrected_route,
                            "allowed_routes": list(route_candidates) or [corrected_route],
                            "route_candidates": list(route_candidates) or [corrected_route],
                            "source_types": [route_evidence_json.get("source_type")],
                        }
                    }
                }
            },
        }
