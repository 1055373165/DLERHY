from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from book_agent.domain.enums import RuntimeIncidentKind
from book_agent.services.runtime_repair_contract import (
    validate_runtime_repair_request_input_bundle,
)
from book_agent.services.runtime_repair_worker import (
    RuntimeRepairDispatchContract,
    UnsupportedRuntimeRepairIncidentError,
)

RuntimeRemoteRepairAgentFactory = Callable[[], "RuntimeRemoteRepairAgent"]


@dataclass(frozen=True, slots=True)
class RuntimeRemoteRepairAgentDescriptor:
    agent_name: str
    worker_hint: str
    worker_contract_version: int


class UnsupportedRuntimeRemoteRepairAgentError(RuntimeError):
    """Raised when a remote repair request targets an unknown or unsupported worker contract."""


class RuntimeRemoteRepairAgent:
    AGENT_NAME = "default_remote_contract_repair_agent"
    WORKER_HINT = "default_runtime_repair_agent"
    WORKER_CONTRACT_VERSION = 1
    SUPPORTED_INCIDENT_KINDS: frozenset[RuntimeIncidentKind] | None = None

    def descriptor(self) -> RuntimeRemoteRepairAgentDescriptor:
        return RuntimeRemoteRepairAgentDescriptor(
            agent_name=self.AGENT_NAME,
            worker_hint=self.WORKER_HINT,
            worker_contract_version=self.WORKER_CONTRACT_VERSION,
        )

    def prepare_execution_from_request_contract(self, input_bundle: dict[str, Any]) -> dict[str, Any]:
        normalized = validate_runtime_repair_request_input_bundle(input_bundle)
        contract = RuntimeRepairDispatchContract.from_request_input_bundle(normalized)
        repair_plan = dict(normalized.get("repair_plan_json") or {})
        incident_kind = self._normalize_incident_kind(repair_plan.get("incident_kind"))
        self._assert_supported_incident_kind(incident_kind)
        descriptor = self.descriptor()
        validation_json = dict(normalized.get("validation_json") or repair_plan.get("validation") or {})
        bundle_json = dict(normalized.get("bundle_json") or repair_plan.get("bundle") or {})
        replay_json = dict(normalized.get("replay_json") or repair_plan.get("replay") or {})
        corrected_route = str(
            validation_json.get("corrected_route")
            or bundle_json.get("manifest_json", {})
            .get("config", {})
            .get("routing_policy", {})
            .get("export_routes", {})
            .get(str(validation_json.get("export_type") or ""), {})
            .get("selected_route")
            or ""
        )
        payload = {
            **contract.as_payload_json(),
            "incident_kind": incident_kind.value,
            "changed_files": list(normalized.get("owned_files") or repair_plan.get("owned_files") or []),
            "replay_scope_type": str(replay_json.get("scope_type") or contract.target_scope_type or ""),
            "replay_scope_id": str(replay_json.get("scope_id") or contract.target_scope_id or ""),
            "bundle_revision_name": str(bundle_json.get("revision_name") or contract.bundle_revision_name or ""),
            "corrected_route": corrected_route,
            "repair_agent_decision": "publish_bundle_and_replay",
            "repair_agent_decision_reason": "bounded_remote_contract_ready",
            "repair_agent_adapter_name": descriptor.agent_name,
            "repair_agent_execution_mode": "remote_contract",
            "repair_agent_worker_hint": descriptor.worker_hint,
            "repair_agent_worker_contract_version": descriptor.worker_contract_version,
        }
        return payload

    def _normalize_incident_kind(self, value: Any) -> RuntimeIncidentKind:
        try:
            return RuntimeIncidentKind(str(value or "").strip())
        except ValueError as exc:
            raise UnsupportedRuntimeRepairIncidentError(
                f"Remote repair agent received unsupported incident kind {value!r}."
            ) from exc

    def _assert_supported_incident_kind(self, incident_kind: RuntimeIncidentKind) -> None:
        supported = self.SUPPORTED_INCIDENT_KINDS
        if supported is None or incident_kind in supported:
            return
        supported_names = ", ".join(kind.value for kind in sorted(supported, key=lambda item: item.value))
        raise UnsupportedRuntimeRepairIncidentError(
            f"{self.__class__.__name__} does not support incident kind {incident_kind.value!r}. "
            f"Supported kinds: {supported_names}."
        )


class ReviewDeadlockRemoteRepairAgent(RuntimeRemoteRepairAgent):
    AGENT_NAME = "review_deadlock_remote_contract_repair_agent"
    WORKER_HINT = "review_deadlock_repair_agent"
    SUPPORTED_INCIDENT_KINDS = frozenset({RuntimeIncidentKind.REVIEW_DEADLOCK})


class ExportRoutingRemoteRepairAgent(RuntimeRemoteRepairAgent):
    AGENT_NAME = "export_routing_remote_contract_repair_agent"
    WORKER_HINT = "export_routing_repair_agent"
    SUPPORTED_INCIDENT_KINDS = frozenset({RuntimeIncidentKind.EXPORT_MISROUTING})


class PacketRuntimeDefectRemoteRepairAgent(RuntimeRemoteRepairAgent):
    AGENT_NAME = "packet_runtime_defect_remote_contract_repair_agent"
    WORKER_HINT = "packet_runtime_defect_repair_agent"
    SUPPORTED_INCIDENT_KINDS = frozenset({RuntimeIncidentKind.PACKET_RUNTIME_DEFECT})


class RuntimeRemoteRepairAgentRegistry:
    DEFAULT_WORKER_HINT = "default_runtime_repair_agent"
    DEFAULT_WORKER_CONTRACT_VERSION = 1

    def __init__(
        self,
        *,
        registrations: Mapping[tuple[str, int], RuntimeRemoteRepairAgentFactory] | None = None,
    ) -> None:
        self._registrations = dict(registrations or self._default_registrations())

    def resolve_for_input_bundle(self, input_bundle: dict[str, Any]) -> RuntimeRemoteRepairAgent:
        worker_hint = str(input_bundle.get("worker_hint") or self.DEFAULT_WORKER_HINT).strip()
        worker_contract_version = int(
            input_bundle.get("worker_contract_version") or self.DEFAULT_WORKER_CONTRACT_VERSION
        )
        return self.resolve(
            worker_hint=worker_hint,
            worker_contract_version=worker_contract_version,
        )

    def resolve(self, *, worker_hint: str, worker_contract_version: int) -> RuntimeRemoteRepairAgent:
        normalized_hint = str(worker_hint or self.DEFAULT_WORKER_HINT).strip()
        normalized_version = int(worker_contract_version or self.DEFAULT_WORKER_CONTRACT_VERSION)
        factory = self._registrations.get((normalized_hint, normalized_version))
        if factory is not None:
            return factory()

        supported_versions = sorted(
            version
            for hint, version in self._registrations
            if hint == normalized_hint
        )
        if supported_versions:
            supported_text = ", ".join(str(version) for version in supported_versions)
            raise UnsupportedRuntimeRemoteRepairAgentError(
                "Unsupported remote repair worker contract version "
                f"{normalized_version} for worker_hint={normalized_hint!r}. "
                f"Supported versions: {supported_text}."
            )

        supported_hints = sorted({hint for hint, _version in self._registrations})
        raise UnsupportedRuntimeRemoteRepairAgentError(
            "Unknown remote repair worker hint "
            f"{normalized_hint!r}. Supported hints: {', '.join(supported_hints)}."
        )

    @classmethod
    def _default_registrations(cls) -> dict[tuple[str, int], RuntimeRemoteRepairAgentFactory]:
        return {
            (cls.DEFAULT_WORKER_HINT, cls.DEFAULT_WORKER_CONTRACT_VERSION): RuntimeRemoteRepairAgent,
            ("review_deadlock_repair_agent", 1): ReviewDeadlockRemoteRepairAgent,
            ("export_routing_repair_agent", 1): ExportRoutingRemoteRepairAgent,
            ("packet_runtime_defect_repair_agent", 1): PacketRuntimeDefectRemoteRepairAgent,
        }
