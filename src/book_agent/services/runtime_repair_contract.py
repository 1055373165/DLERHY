from __future__ import annotations

from typing import Any

RUNTIME_REPAIR_REQUEST_CONTRACT_VERSION = 1
RUNTIME_REPAIR_RESULT_CONTRACT_VERSION = 1


class InvalidRuntimeRepairRequestContractError(RuntimeError):
    """Raised when a remote repair agent request does not satisfy the explicit request contract."""


class InvalidRuntimeRepairResultContractError(RuntimeError):
    """Raised when a remote or transport-backed repair result does not satisfy the explicit contract."""


def build_runtime_repair_request_input_bundle(
    *,
    proposal_id: str,
    incident_id: str,
    repair_dispatch_json: dict[str, Any],
    repair_plan_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repair_dispatch = dict(repair_dispatch_json or {})
    repair_plan = dict(repair_plan_json or {})
    replay_json = dict(repair_plan.get("replay") or repair_dispatch.get("replay") or {})
    bundle_json = dict(repair_plan.get("bundle") or {})
    validation_json = dict(repair_plan.get("validation") or {})
    owned_files = list(repair_dispatch.get("owned_files") or repair_plan.get("owned_files") or [])

    return {
        "repair_request_contract_version": RUNTIME_REPAIR_REQUEST_CONTRACT_VERSION,
        "proposal_id": proposal_id,
        "incident_id": incident_id,
        "repair_dispatch_id": repair_dispatch.get("dispatch_id"),
        "patch_surface": repair_dispatch.get("patch_surface"),
        "target_scope_type": replay_json.get("scope_type"),
        "target_scope_id": replay_json.get("scope_id"),
        "replay_boundary": replay_json.get("boundary"),
        "validation_command": repair_dispatch.get("validation_command"),
        "bundle_revision_name": repair_dispatch.get("bundle_revision_name"),
        "rollout_scope_json": dict(repair_dispatch.get("rollout_scope_json") or {}),
        "claim_mode": repair_dispatch.get("claim_mode"),
        "claim_target": repair_dispatch.get("claim_target"),
        "dispatch_lane": repair_dispatch.get("lane"),
        "worker_hint": repair_dispatch.get("worker_hint"),
        "worker_contract_version": int(repair_dispatch.get("worker_contract_version") or 1),
        "execution_mode": repair_dispatch.get("execution_mode"),
        "executor_hint": repair_dispatch.get("executor_hint"),
        "executor_contract_version": int(repair_dispatch.get("executor_contract_version") or 1),
        "transport_hint": repair_dispatch.get("transport_hint"),
        "transport_contract_version": int(repair_dispatch.get("transport_contract_version") or 1),
        "owned_files": owned_files,
        "repair_goal": repair_plan.get("goal"),
        "validation_json": validation_json,
        "bundle_json": bundle_json,
        "replay_json": replay_json,
        "repair_dispatch_json": repair_dispatch,
        "repair_plan_json": repair_plan,
    }


def build_runtime_repair_result_payload(
    *,
    prepared_payload: dict[str, Any],
    executor_descriptor: dict[str, Any],
    transport_descriptor: dict[str, Any],
    repair_runner_status: str = "succeeded",
    repair_runner_pid: int | None = None,
    repair_agent_execution_id: str | None = None,
    repair_agent_execution_status: str | None = None,
    repair_agent_execution_started_at: str | None = None,
    repair_agent_execution_completed_at: str | None = None,
    repair_transport_endpoint: str | None = None,
) -> dict[str, Any]:
    payload = {
        **dict(prepared_payload),
        "repair_result_contract_version": RUNTIME_REPAIR_RESULT_CONTRACT_VERSION,
        "repair_executor_name": executor_descriptor.get("executor_name"),
        "repair_executor_execution_mode": executor_descriptor.get("execution_mode"),
        "repair_executor_hint": executor_descriptor.get("executor_hint"),
        "repair_executor_contract_version": executor_descriptor.get("executor_contract_version"),
        "repair_transport_name": transport_descriptor.get("transport_name"),
        "repair_transport_hint": transport_descriptor.get("transport_hint"),
        "repair_transport_contract_version": transport_descriptor.get("transport_contract_version"),
        "repair_runner_status": repair_runner_status,
    }
    if repair_runner_pid is not None:
        payload["repair_runner_pid"] = repair_runner_pid
    if repair_agent_execution_id is not None:
        payload["repair_agent_execution_id"] = repair_agent_execution_id
    if repair_agent_execution_status is not None:
        payload["repair_agent_execution_status"] = repair_agent_execution_status
    if repair_agent_execution_started_at is not None:
        payload["repair_agent_execution_started_at"] = repair_agent_execution_started_at
    if repair_agent_execution_completed_at is not None:
        payload["repair_agent_execution_completed_at"] = repair_agent_execution_completed_at
    if repair_transport_endpoint is not None:
        payload["repair_transport_endpoint"] = repair_transport_endpoint
    return payload


def validate_runtime_repair_request_input_bundle(input_bundle: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(input_bundle or {})
    version = normalized.get("repair_request_contract_version")
    if int(version or 0) != RUNTIME_REPAIR_REQUEST_CONTRACT_VERSION:
        raise InvalidRuntimeRepairRequestContractError(
            "Repair executor received an invalid request contract version: "
            f"{version!r}. Expected {RUNTIME_REPAIR_REQUEST_CONTRACT_VERSION}."
        )
    return normalized


def validate_runtime_repair_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload or {})
    version = normalized.get("repair_result_contract_version")
    if int(version or 0) != RUNTIME_REPAIR_RESULT_CONTRACT_VERSION:
        raise InvalidRuntimeRepairResultContractError(
            "Repair executor returned an invalid result contract version: "
            f"{version!r}. Expected {RUNTIME_REPAIR_RESULT_CONTRACT_VERSION}."
        )
    return normalized
