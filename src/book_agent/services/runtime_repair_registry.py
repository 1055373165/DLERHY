from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.orm import sessionmaker

from book_agent.services.runtime_repair_agent_adapter import (
    ExportRoutingRepairAgentAdapter,
    PacketRuntimeDefectRepairAgentAdapter,
    ReviewDeadlockRepairAgentAdapter,
    RuntimeRepairAgentAdapter,
)

RuntimeRepairAgentAdapterFactory = Callable[[sessionmaker], RuntimeRepairAgentAdapter]


class UnsupportedRuntimeRepairWorkerError(RuntimeError):
    """Raised when a repair work item requests an unknown or unsupported worker contract."""


class RuntimeRepairWorkerRegistry:
    DEFAULT_WORKER_HINT = "default_runtime_repair_agent"
    DEFAULT_WORKER_CONTRACT_VERSION = 1

    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        registrations: Mapping[tuple[str, int], RuntimeRepairAgentAdapterFactory] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._registrations = dict(registrations or self._default_registrations())

    def resolve_for_input_bundle(self, input_bundle: dict[str, Any]) -> RuntimeRepairAgentAdapter:
        worker_hint = str(input_bundle.get("worker_hint") or self.DEFAULT_WORKER_HINT).strip()
        worker_contract_version = int(
            input_bundle.get("worker_contract_version") or self.DEFAULT_WORKER_CONTRACT_VERSION
        )
        return self.resolve(
            worker_hint=worker_hint,
            worker_contract_version=worker_contract_version,
        )

    def resolve(
        self,
        *,
        worker_hint: str,
        worker_contract_version: int,
    ) -> RuntimeRepairAgentAdapter:
        normalized_hint = str(worker_hint or self.DEFAULT_WORKER_HINT).strip()
        normalized_version = int(
            worker_contract_version or self.DEFAULT_WORKER_CONTRACT_VERSION
        )
        factory = self._registrations.get((normalized_hint, normalized_version))
        if factory is not None:
            return factory(self._session_factory)

        supported_versions = sorted(
            version
            for hint, version in self._registrations
            if hint == normalized_hint
        )
        if supported_versions:
            supported_text = ", ".join(str(version) for version in supported_versions)
            raise UnsupportedRuntimeRepairWorkerError(
                "Unsupported repair worker contract version "
                f"{normalized_version} for worker_hint={normalized_hint!r}. "
                f"Supported versions: {supported_text}."
            )

        supported_hints = sorted({hint for hint, _version in self._registrations})
        raise UnsupportedRuntimeRepairWorkerError(
            "Unknown repair worker hint "
            f"{normalized_hint!r}. Supported hints: {', '.join(supported_hints)}."
        )

    @classmethod
    def _default_registrations(cls) -> dict[tuple[str, int], RuntimeRepairAgentAdapterFactory]:
        return {
            (cls.DEFAULT_WORKER_HINT, cls.DEFAULT_WORKER_CONTRACT_VERSION): cls._default_repair_agent_adapter_factory,
            ("review_deadlock_repair_agent", 1): cls._review_deadlock_repair_agent_adapter_factory,
            ("export_routing_repair_agent", 1): cls._export_routing_repair_agent_adapter_factory,
            ("packet_runtime_defect_repair_agent", 1): cls._packet_runtime_defect_repair_agent_adapter_factory,
        }

    @staticmethod
    def _default_repair_agent_adapter_factory(session_factory: sessionmaker) -> RuntimeRepairAgentAdapter:
        return RuntimeRepairAgentAdapter(session_factory=session_factory)

    @staticmethod
    def _review_deadlock_repair_agent_adapter_factory(session_factory: sessionmaker) -> RuntimeRepairAgentAdapter:
        return ReviewDeadlockRepairAgentAdapter(session_factory=session_factory)

    @staticmethod
    def _export_routing_repair_agent_adapter_factory(session_factory: sessionmaker) -> RuntimeRepairAgentAdapter:
        return ExportRoutingRepairAgentAdapter(session_factory=session_factory)

    @staticmethod
    def _packet_runtime_defect_repair_agent_adapter_factory(
        session_factory: sessionmaker,
    ) -> RuntimeRepairAgentAdapter:
        return PacketRuntimeDefectRepairAgentAdapter(session_factory=session_factory)
