from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import sessionmaker

from book_agent.services.run_execution import ClaimedRunWorkItem
from book_agent.services.runtime_repair_worker import (
    ExportRoutingRepairWorker,
    PacketRuntimeDefectRepairWorker,
    ReviewDeadlockRepairWorker,
    RuntimeRepairWorker,
)


@dataclass(frozen=True, slots=True)
class RuntimeRepairAgentDescriptor:
    adapter_name: str
    execution_mode: str
    worker_hint: str
    worker_contract_version: int


class RuntimeRepairAgentAdapter:
    ADAPTER_NAME = "default_in_process_repair_agent"
    EXECUTION_MODE = "in_process"
    WORKER_HINT = "default_runtime_repair_agent"
    WORKER_CONTRACT_VERSION = 1
    WORKER_CLASS = RuntimeRepairWorker

    def __init__(self, *, session_factory: sessionmaker):
        self._session_factory = session_factory
        self._worker = self.WORKER_CLASS(session_factory=session_factory)

    def descriptor(self) -> RuntimeRepairAgentDescriptor:
        return RuntimeRepairAgentDescriptor(
            adapter_name=self.ADAPTER_NAME,
            execution_mode=self.EXECUTION_MODE,
            worker_hint=self.WORKER_HINT,
            worker_contract_version=self.WORKER_CONTRACT_VERSION,
        )

    def prepare_execution(
        self,
        *,
        claimed: ClaimedRunWorkItem,
        input_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self._worker.prepare_execution(
            claimed=claimed,
            input_bundle=input_bundle,
        )
        descriptor = self.descriptor()
        return {
            **payload,
            "repair_agent_adapter_name": descriptor.adapter_name,
            "repair_agent_execution_mode": descriptor.execution_mode,
            "repair_agent_worker_hint": descriptor.worker_hint,
            "repair_agent_worker_contract_version": descriptor.worker_contract_version,
        }

    def complete_execution(
        self,
        *,
        run_id: str,
        payload: dict[str, Any],
        lease_token: str,
    ) -> None:
        self._worker.complete_execution(
            run_id=run_id,
            payload=payload,
            lease_token=lease_token,
        )


class ReviewDeadlockRepairAgentAdapter(RuntimeRepairAgentAdapter):
    ADAPTER_NAME = "review_deadlock_in_process_repair_agent"
    WORKER_HINT = "review_deadlock_repair_agent"
    WORKER_CLASS = ReviewDeadlockRepairWorker


class ExportRoutingRepairAgentAdapter(RuntimeRepairAgentAdapter):
    ADAPTER_NAME = "export_routing_in_process_repair_agent"
    WORKER_HINT = "export_routing_repair_agent"
    WORKER_CLASS = ExportRoutingRepairWorker


class PacketRuntimeDefectRepairAgentAdapter(RuntimeRepairAgentAdapter):
    ADAPTER_NAME = "packet_runtime_defect_in_process_repair_agent"
    WORKER_HINT = "packet_runtime_defect_repair_agent"
    WORKER_CLASS = PacketRuntimeDefectRepairWorker
