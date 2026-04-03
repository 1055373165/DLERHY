from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RuntimeRepairPlan:
    patch_surface: str
    diff_manifest_json: dict[str, Any]
    validation_report_json: dict[str, Any]
    revision_name: str
    manifest_json: dict[str, Any]
    rollout_scope_json: dict[str, Any]
    handoff_json: dict[str, Any]


class RuntimeRepairPlannerService:
    DEFAULT_EXECUTION_MODE = "transport_backed"
    DEFAULT_EXECUTOR_HINT = "python_transport_repair_executor"
    DEFAULT_EXECUTOR_CONTRACT_VERSION = 1
    DEFAULT_TRANSPORT_HINT = "python_subprocess_repair_transport"
    DEFAULT_TRANSPORT_CONTRACT_VERSION = 1

    def plan_review_deadlock_repair(
        self,
        *,
        chapter_id: str,
        chapter_run_id: str,
        review_session_id: str,
        reason_code: str | None,
        patch_surface: str = "runtime_bundle",
        execution_mode: str | None = None,
        executor_hint: str | None = None,
        executor_contract_version: int | None = None,
        transport_hint: str | None = None,
        transport_contract_version: int | None = None,
    ) -> RuntimeRepairPlan:
        normalized_reason = reason_code or "review_deadlock"
        revision_name = f"review-deadlock-fix-{chapter_id[:12]}"
        diff_manifest_json = {
            "files": [
                "src/book_agent/app/runtime/controllers/review_controller.py",
                "src/book_agent/app/runtime/controllers/incident_controller.py",
                "src/book_agent/services/incident_triage.py",
                "src/book_agent/services/run_execution.py",
            ],
            "patch_surface": patch_surface,
            "reason_code": normalized_reason,
            "replay_scope": "review_session",
            "scope_id": chapter_id,
        }
        validation_report_json = {
            "command": (
                "uv run pytest tests/test_incident_triage.py "
                "tests/test_incident_controller.py tests/test_review_sessions.py"
            ),
            "scope": "review_deadlock",
            "review_session_id": review_session_id,
            "chapter_run_id": chapter_run_id,
        }
        manifest_json = {
            "code": {
                "entrypoint": "book_agent.app.runtime.controllers.review_controller",
                "surface": "review_deadlock",
            },
            "config": {
                "recovery": {
                    "review_deadlock": {
                        "enabled": True,
                        "reason_code": reason_code,
                        "scope_id": chapter_id,
                        "review_session_id": review_session_id,
                    }
                }
            },
        }
        rollout_scope_json = {
            "mode": "dev",
            "scope_type": "review",
            "scope_id": chapter_id,
            "replay_scope_id": chapter_id,
        }
        dispatch_contract = self._build_dispatch_contract(
            execution_mode=execution_mode,
            executor_hint=executor_hint,
            executor_contract_version=executor_contract_version,
            transport_hint=transport_hint,
            transport_contract_version=transport_contract_version,
        )
        handoff_json = {
            "incident_kind": "review_deadlock",
            "goal": "Repair repeated review deadlock and replay the minimal chapter review scope.",
            "patch_surface": patch_surface,
            "owned_files": list(diff_manifest_json["files"]),
            "validation": dict(validation_report_json),
            "bundle": {
                "revision_name": revision_name,
                "manifest_json": manifest_json,
                "rollout_scope_json": rollout_scope_json,
            },
            "dispatch": {
                "claim_mode": "runtime_owned",
                "claim_target": "runtime_patch_proposal",
                "lane": "runtime.repair",
                "worker_hint": "review_deadlock_repair_agent",
                "worker_contract_version": 1,
                **dispatch_contract,
            },
            "replay": {
                "scope_type": "chapter",
                "scope_id": chapter_id,
                "boundary": "review_session",
            },
        }
        return RuntimeRepairPlan(
            patch_surface=patch_surface,
            diff_manifest_json=diff_manifest_json,
            validation_report_json=validation_report_json,
            revision_name=revision_name,
            manifest_json=manifest_json,
            rollout_scope_json=rollout_scope_json,
            handoff_json=handoff_json,
        )

    def plan_export_misrouting_repair(
        self,
        *,
        scope_id: str,
        export_type: str | None,
        corrected_route: str,
        route_candidates: list[str],
        route_evidence_json: dict[str, Any],
        patch_surface: str = "runtime_bundle",
        execution_mode: str | None = None,
        executor_hint: str | None = None,
        executor_contract_version: int | None = None,
        transport_hint: str | None = None,
        transport_contract_version: int | None = None,
    ) -> RuntimeRepairPlan:
        revision_suffix = str(route_evidence_json.get("route_fingerprint") or scope_id)[:12]
        revision_name = f"export-routing-fix-{revision_suffix}"
        diff_manifest_json = {
            "files": [
                "src/book_agent/services/export_routing.py",
                "src/book_agent/services/export.py",
                "src/book_agent/services/workflows.py",
            ],
            "patch_surface": patch_surface,
            "export_type": export_type,
            "corrected_route": corrected_route,
            "route_candidates": list(route_candidates),
            "route_evidence_json": dict(route_evidence_json),
        }
        validation_report_json = {
            "command": (
                "uv run pytest tests/test_export_routing.py "
                "tests/test_export_controller.py tests/test_incident_controller.py"
            ),
            "scope": "export_misrouting",
            "route_evidence_json": dict(route_evidence_json),
            "corrected_route": corrected_route,
        }
        manifest_json = {
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
        rollout_scope_json = {
            "mode": "dev",
            "scope_type": "export",
            "scope_id": scope_id,
            "replay_scope_id": scope_id,
        }
        dispatch_contract = self._build_dispatch_contract(
            execution_mode=execution_mode,
            executor_hint=executor_hint,
            executor_contract_version=executor_contract_version,
            transport_hint=transport_hint,
            transport_contract_version=transport_contract_version,
        )
        handoff_json = {
            "incident_kind": "export_misrouting",
            "goal": "Repair export misrouting, publish a corrected runtime bundle, and replay the failed export scope.",
            "patch_surface": patch_surface,
            "owned_files": list(diff_manifest_json["files"]),
            "validation": dict(validation_report_json),
            "bundle": {
                "revision_name": revision_name,
                "manifest_json": manifest_json,
                "rollout_scope_json": rollout_scope_json,
            },
            "dispatch": {
                "claim_mode": "runtime_owned",
                "claim_target": "runtime_patch_proposal",
                "lane": "runtime.repair",
                "worker_hint": "export_routing_repair_agent",
                "worker_contract_version": 1,
                **dispatch_contract,
            },
            "replay": {
                "scope_type": "export",
                "scope_id": scope_id,
                "boundary": "export",
            },
        }
        return RuntimeRepairPlan(
            patch_surface=patch_surface,
            diff_manifest_json=diff_manifest_json,
            validation_report_json=validation_report_json,
            revision_name=revision_name,
            manifest_json=manifest_json,
            rollout_scope_json=rollout_scope_json,
            handoff_json=handoff_json,
        )

    def plan_packet_runtime_defect_repair(
        self,
        *,
        packet_id: str,
        chapter_id: str,
        chapter_run_id: str,
        packet_task_id: str,
        reason_code: str | None,
        patch_surface: str = "runtime_bundle",
        execution_mode: str | None = None,
        executor_hint: str | None = None,
        executor_contract_version: int | None = None,
        transport_hint: str | None = None,
        transport_contract_version: int | None = None,
    ) -> RuntimeRepairPlan:
        normalized_reason = reason_code or "packet_runtime_defect"
        revision_name = f"packet-runtime-fix-{packet_id[:12]}"
        diff_manifest_json = {
            "files": [
                "src/book_agent/app/runtime/controllers/packet_controller.py",
                "src/book_agent/app/runtime/controllers/incident_controller.py",
                "src/book_agent/services/runtime_repair_worker.py",
                "src/book_agent/services/run_execution.py",
            ],
            "patch_surface": patch_surface,
            "reason_code": normalized_reason,
            "replay_scope": "packet",
            "scope_id": packet_id,
        }
        validation_report_json = {
            "command": (
                "uv run pytest tests/test_runtime_lane_health.py "
                "tests/test_runtime_repair_planner.py tests/test_run_execution.py"
            ),
            "scope": "packet_runtime_defect",
            "packet_task_id": packet_task_id,
            "chapter_run_id": chapter_run_id,
            "packet_id": packet_id,
        }
        manifest_json = {
            "code": {
                "entrypoint": "book_agent.app.runtime.controllers.packet_controller",
                "surface": "packet_runtime_defect",
            },
            "config": {
                "recovery": {
                    "packet_runtime_defect": {
                        "enabled": True,
                        "reason_code": normalized_reason,
                        "packet_id": packet_id,
                        "packet_task_id": packet_task_id,
                        "chapter_id": chapter_id,
                        "chapter_run_id": chapter_run_id,
                    }
                }
            },
        }
        rollout_scope_json = {
            "mode": "dev",
            "scope_type": "packet",
            "scope_id": packet_id,
            "replay_scope_id": packet_id,
        }
        dispatch_contract = self._build_dispatch_contract(
            execution_mode=execution_mode,
            executor_hint=executor_hint,
            executor_contract_version=executor_contract_version,
            transport_hint=transport_hint,
            transport_contract_version=transport_contract_version,
        )
        handoff_json = {
            "incident_kind": "packet_runtime_defect",
            "goal": "Repair repeated packet runtime defects and replay only the affected packet scope.",
            "patch_surface": patch_surface,
            "owned_files": list(diff_manifest_json["files"]),
            "validation": dict(validation_report_json),
            "bundle": {
                "revision_name": revision_name,
                "manifest_json": manifest_json,
                "rollout_scope_json": rollout_scope_json,
            },
            "dispatch": {
                "claim_mode": "runtime_owned",
                "claim_target": "runtime_patch_proposal",
                "lane": "runtime.repair",
                "worker_hint": "packet_runtime_defect_repair_agent",
                "worker_contract_version": 1,
                **dispatch_contract,
            },
            "replay": {
                "scope_type": "packet",
                "scope_id": packet_id,
                "boundary": "packet",
            },
        }
        return RuntimeRepairPlan(
            patch_surface=patch_surface,
            diff_manifest_json=diff_manifest_json,
            validation_report_json=validation_report_json,
            revision_name=revision_name,
            manifest_json=manifest_json,
            rollout_scope_json=rollout_scope_json,
            handoff_json=handoff_json,
        )

    def _build_dispatch_contract(
        self,
        *,
        execution_mode: str | None,
        executor_hint: str | None,
        executor_contract_version: int | None,
        transport_hint: str | None,
        transport_contract_version: int | None,
    ) -> dict[str, Any]:
        normalized_execution_mode = str(execution_mode or self.DEFAULT_EXECUTION_MODE).strip()
        normalized_executor_hint = str(executor_hint or self.DEFAULT_EXECUTOR_HINT).strip()
        normalized_executor_contract_version = int(
            executor_contract_version or self.DEFAULT_EXECUTOR_CONTRACT_VERSION
        )
        dispatch = {
            "execution_mode": normalized_execution_mode,
            "executor_hint": normalized_executor_hint,
            "executor_contract_version": normalized_executor_contract_version,
        }
        if normalized_execution_mode != "transport_backed":
            return dispatch
        normalized_hint = str(transport_hint or self.DEFAULT_TRANSPORT_HINT).strip()
        normalized_version = int(
            transport_contract_version or self.DEFAULT_TRANSPORT_CONTRACT_VERSION
        )
        dispatch["transport_hint"] = normalized_hint
        dispatch["transport_contract_version"] = normalized_version
        return dispatch
