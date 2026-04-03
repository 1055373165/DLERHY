from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from sqlalchemy.orm import sessionmaker

from book_agent.app.runtime.controllers.incident_controller import IncidentController
from book_agent.infra.db.session import session_scope
from book_agent.services.run_execution import ClaimedRunWorkItem
from book_agent.services.runtime_repair_agent_adapter import RuntimeRepairAgentAdapter
from book_agent.services.runtime_repair_contract import (
    build_runtime_repair_result_payload,
    validate_runtime_repair_result_payload,
)
from book_agent.services.runtime_repair_transport import RuntimeRepairTransportRegistry

RuntimeRepairExecutorFactory = Callable[[sessionmaker, RuntimeRepairAgentAdapter], "RuntimeRepairExecutor"]


@dataclass(frozen=True, slots=True)
class RuntimeRepairExecutorDescriptor:
    executor_name: str
    execution_mode: str
    executor_hint: str
    executor_contract_version: int


class UnsupportedRuntimeRepairExecutorError(RuntimeError):
    """Raised when a repair work item requests an unknown or unsupported repair executor contract."""


class RuntimeRepairExecutorInvocationError(RuntimeError):
    """Raised when a repair executor cannot successfully invoke its delegated execution body."""


class RuntimeRepairExecutor:
    EXECUTOR_NAME = "default_in_process_repair_executor"
    EXECUTION_MODE = "in_process"
    EXECUTOR_HINT = "python_repair_executor"
    EXECUTOR_CONTRACT_VERSION = 1

    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        repair_agent: RuntimeRepairAgentAdapter,
    ) -> None:
        self._session_factory = session_factory
        self._repair_agent = repair_agent

    def descriptor(self) -> RuntimeRepairExecutorDescriptor:
        return RuntimeRepairExecutorDescriptor(
            executor_name=self.EXECUTOR_NAME,
            execution_mode=self.EXECUTION_MODE,
            executor_hint=self.EXECUTOR_HINT,
            executor_contract_version=self.EXECUTOR_CONTRACT_VERSION,
        )

    def prepare_execution(
        self,
        *,
        claimed: ClaimedRunWorkItem,
        input_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self._repair_agent.prepare_execution(
            claimed=claimed,
            input_bundle=input_bundle,
        )
        descriptor = self.descriptor()
        return build_runtime_repair_result_payload(
            prepared_payload=payload,
            executor_descriptor={
                "executor_name": descriptor.executor_name,
                "execution_mode": descriptor.execution_mode,
                "executor_hint": descriptor.executor_hint,
                "executor_contract_version": descriptor.executor_contract_version,
            },
            transport_descriptor={},
            repair_runner_status="in_process",
            repair_runner_pid=None,
        )

    def complete_execution(
        self,
        *,
        run_id: str,
        payload: dict[str, Any],
        lease_token: str,
    ) -> None:
        self._repair_agent.complete_execution(
            run_id=run_id,
            payload=payload,
            lease_token=lease_token,
        )

    def _begin_dispatch_execution(self, *, claimed: ClaimedRunWorkItem, proposal_id: str) -> None:
        with session_scope(self._session_factory) as session:
            IncidentController(session=session).begin_repair_dispatch_execution(
                proposal_id=proposal_id,
                worker_name=claimed.worker_name,
                worker_instance_id=claimed.worker_instance_id,
                work_item_id=claimed.work_item_id,
                lease_token=claimed.lease_token,
            )


class InProcessRuntimeRepairExecutor(RuntimeRepairExecutor):
    EXECUTOR_NAME = "python_in_process_repair_executor"
    EXECUTION_MODE = "in_process"
    EXECUTOR_HINT = "python_repair_executor"
    EXECUTOR_CONTRACT_VERSION = 1


class AgentBackedSubprocessRuntimeRepairExecutor(RuntimeRepairExecutor):
    EXECUTOR_NAME = "python_agent_backed_subprocess_repair_executor"
    EXECUTION_MODE = "agent_backed"
    EXECUTOR_HINT = "python_subprocess_repair_executor"
    EXECUTOR_CONTRACT_VERSION = 1
    DEFAULT_TRANSPORT_HINT = "python_subprocess_repair_transport"
    DEFAULT_TRANSPORT_CONTRACT_VERSION = 1

    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        repair_agent: RuntimeRepairAgentAdapter,
    ) -> None:
        super().__init__(session_factory=session_factory, repair_agent=repair_agent)
        self._transport_registry = RuntimeRepairTransportRegistry(session_factory=session_factory)

    def prepare_execution(
        self,
        *,
        claimed: ClaimedRunWorkItem,
        input_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        descriptor = self.descriptor()
        transport = self._transport_registry.resolve(
            transport_hint=self.DEFAULT_TRANSPORT_HINT,
            transport_contract_version=self.DEFAULT_TRANSPORT_CONTRACT_VERSION,
        )
        return validate_runtime_repair_result_payload(
            transport.dispatch(
                run_id=claimed.run_id,
                lease_token=claimed.lease_token,
                claimed=claimed,
                input_bundle=input_bundle,
                executor_descriptor={
                    "executor_name": descriptor.executor_name,
                    "execution_mode": descriptor.execution_mode,
                    "executor_hint": descriptor.executor_hint,
                    "executor_contract_version": descriptor.executor_contract_version,
                },
            )
        )

    def complete_execution(
        self,
        *,
        run_id: str,
        payload: dict[str, Any],
        lease_token: str,
    ) -> None:
        # The subprocess runner owns the success-path DB mutation and completes the work item.
        return None


class ContractBackedSubprocessRuntimeRepairExecutor(RuntimeRepairExecutor):
    EXECUTOR_NAME = "python_contract_agent_backed_repair_executor"
    EXECUTION_MODE = "agent_backed"
    EXECUTOR_HINT = "python_contract_agent_repair_executor"
    EXECUTOR_CONTRACT_VERSION = 1

    def prepare_execution(
        self,
        *,
        claimed: ClaimedRunWorkItem,
        input_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        proposal_id = str(input_bundle.get("proposal_id") or claimed.scope_id or "").strip()
        if proposal_id:
            self._begin_dispatch_execution(claimed=claimed, proposal_id=proposal_id)
        descriptor = self.descriptor()
        payload_path = self._write_runner_payload(
            {
                "executor_descriptor": {
                    "executor_name": descriptor.executor_name,
                    "execution_mode": descriptor.execution_mode,
                    "executor_hint": descriptor.executor_hint,
                    "executor_contract_version": descriptor.executor_contract_version,
                },
                "transport_descriptor": {},
                "input_bundle": dict(input_bundle),
            }
        )
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "book_agent.tools.runtime_repair_contract_runner",
                    "--payload-file",
                    str(payload_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            payload_path.unlink(missing_ok=True)
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            detail = stderr or stdout or "repair contract runner exited without output"
            raise RuntimeRepairExecutorInvocationError(
                "Contract-backed repair executor failed to invoke remote repair agent: "
                f"{detail}"
            )
        stdout = completed.stdout.strip()
        if not stdout:
            raise RuntimeRepairExecutorInvocationError(
                "Contract-backed repair executor received no output from remote repair agent."
            )
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeRepairExecutorInvocationError(
                "Contract-backed repair executor received invalid JSON from remote repair agent."
            ) from exc
        return validate_runtime_repair_result_payload(dict(result))

    @staticmethod
    def _write_runner_payload(payload: dict[str, Any]) -> Path:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".runtime-repair-contract.json",
            delete=False,
        ) as handle:
            json.dump(payload, handle)
            handle.flush()
            return Path(handle.name)


class TransportBackedRuntimeRepairExecutor(RuntimeRepairExecutor):
    EXECUTOR_NAME = "python_transport_backed_repair_executor"
    EXECUTION_MODE = "transport_backed"
    EXECUTOR_HINT = "python_transport_repair_executor"
    EXECUTOR_CONTRACT_VERSION = 1

    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        repair_agent: RuntimeRepairAgentAdapter,
    ) -> None:
        super().__init__(session_factory=session_factory, repair_agent=repair_agent)
        self._transport_registry = RuntimeRepairTransportRegistry(session_factory=session_factory)

    def prepare_execution(
        self,
        *,
        claimed: ClaimedRunWorkItem,
        input_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        descriptor = self.descriptor()
        transport = self._transport_registry.resolve_for_input_bundle(input_bundle)
        return validate_runtime_repair_result_payload(
            transport.dispatch(
                run_id=claimed.run_id,
                lease_token=claimed.lease_token,
                claimed=claimed,
                input_bundle=input_bundle,
                executor_descriptor={
                    "executor_name": descriptor.executor_name,
                    "execution_mode": descriptor.execution_mode,
                    "executor_hint": descriptor.executor_hint,
                    "executor_contract_version": descriptor.executor_contract_version,
                },
            )
        )

    def complete_execution(
        self,
        *,
        run_id: str,
        payload: dict[str, Any],
        lease_token: str,
    ) -> None:
        return None


class ContractTransportBackedRuntimeRepairExecutor(RuntimeRepairExecutor):
    EXECUTOR_NAME = "python_contract_transport_backed_repair_executor"
    EXECUTION_MODE = "transport_backed"
    EXECUTOR_HINT = "python_contract_transport_repair_executor"
    EXECUTOR_CONTRACT_VERSION = 1

    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        repair_agent: RuntimeRepairAgentAdapter,
    ) -> None:
        super().__init__(session_factory=session_factory, repair_agent=repair_agent)
        self._transport_registry = RuntimeRepairTransportRegistry(session_factory=session_factory)

    def prepare_execution(
        self,
        *,
        claimed: ClaimedRunWorkItem,
        input_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        proposal_id = str(input_bundle.get("proposal_id") or claimed.scope_id or "").strip()
        if proposal_id:
            self._begin_dispatch_execution(claimed=claimed, proposal_id=proposal_id)
        descriptor = self.descriptor()
        transport = self._transport_registry.resolve_for_input_bundle(input_bundle)
        return validate_runtime_repair_result_payload(
            transport.dispatch(
                run_id=claimed.run_id,
                lease_token=claimed.lease_token,
                claimed=claimed,
                input_bundle=input_bundle,
                executor_descriptor={
                    "executor_name": descriptor.executor_name,
                    "execution_mode": descriptor.execution_mode,
                    "executor_hint": descriptor.executor_hint,
                    "executor_contract_version": descriptor.executor_contract_version,
                },
            )
        )


class RuntimeRepairExecutorRegistry:
    DEFAULT_EXECUTION_MODE = "in_process"
    DEFAULT_EXECUTOR_HINT = "python_repair_executor"
    DEFAULT_EXECUTOR_CONTRACT_VERSION = 1

    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        registrations: Mapping[tuple[str, str, int], RuntimeRepairExecutorFactory] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._registrations = dict(registrations or self._default_registrations())

    def resolve_for_input_bundle(
        self,
        *,
        input_bundle: dict[str, Any],
        repair_agent: RuntimeRepairAgentAdapter,
    ) -> RuntimeRepairExecutor:
        execution_mode = str(input_bundle.get("execution_mode") or self.DEFAULT_EXECUTION_MODE).strip()
        executor_hint = str(input_bundle.get("executor_hint") or self.DEFAULT_EXECUTOR_HINT).strip()
        executor_contract_version = int(
            input_bundle.get("executor_contract_version") or self.DEFAULT_EXECUTOR_CONTRACT_VERSION
        )
        return self.resolve(
            execution_mode=execution_mode,
            executor_hint=executor_hint,
            executor_contract_version=executor_contract_version,
            repair_agent=repair_agent,
        )

    def resolve(
        self,
        *,
        execution_mode: str,
        executor_hint: str,
        executor_contract_version: int,
        repair_agent: RuntimeRepairAgentAdapter,
    ) -> RuntimeRepairExecutor:
        normalized_mode = str(execution_mode or self.DEFAULT_EXECUTION_MODE).strip()
        normalized_hint = str(executor_hint or self.DEFAULT_EXECUTOR_HINT).strip()
        normalized_version = int(
            executor_contract_version or self.DEFAULT_EXECUTOR_CONTRACT_VERSION
        )
        factory = self._registrations.get((normalized_mode, normalized_hint, normalized_version))
        if factory is not None:
            return factory(self._session_factory, repair_agent)

        supported_versions = sorted(
            version
            for mode, hint, version in self._registrations
            if mode == normalized_mode and hint == normalized_hint
        )
        if supported_versions:
            supported_text = ", ".join(str(version) for version in supported_versions)
            raise UnsupportedRuntimeRepairExecutorError(
                "Unsupported repair executor contract version "
                f"{normalized_version} for execution_mode={normalized_mode!r}, "
                f"executor_hint={normalized_hint!r}. Supported versions: {supported_text}."
            )

        supported_hints = sorted(
            hint
            for mode, hint, _version in self._registrations
            if mode == normalized_mode
        )
        if supported_hints:
            raise UnsupportedRuntimeRepairExecutorError(
                "Unknown repair executor hint "
                f"{normalized_hint!r} for execution_mode={normalized_mode!r}. "
                f"Supported hints: {', '.join(supported_hints)}."
            )

        supported_modes = sorted({mode for mode, _hint, _version in self._registrations})
        raise UnsupportedRuntimeRepairExecutorError(
            "Unknown repair executor mode "
            f"{normalized_mode!r}. Supported modes: {', '.join(supported_modes)}."
        )

    @classmethod
    def _default_registrations(cls) -> dict[tuple[str, str, int], RuntimeRepairExecutorFactory]:
        return {
            (
                cls.DEFAULT_EXECUTION_MODE,
                cls.DEFAULT_EXECUTOR_HINT,
                cls.DEFAULT_EXECUTOR_CONTRACT_VERSION,
            ): cls._in_process_executor_factory,
            (
                "agent_backed",
                "python_subprocess_repair_executor",
                1,
            ): cls._agent_backed_subprocess_executor_factory,
            (
                "agent_backed",
                "python_contract_agent_repair_executor",
                1,
            ): cls._contract_backed_subprocess_executor_factory,
            (
                "transport_backed",
                "python_transport_repair_executor",
                1,
            ): cls._transport_backed_executor_factory,
            (
                "transport_backed",
                "python_contract_transport_repair_executor",
                1,
            ): cls._contract_transport_backed_executor_factory,
        }

    @staticmethod
    def _in_process_executor_factory(
        session_factory: sessionmaker,
        repair_agent: RuntimeRepairAgentAdapter,
    ) -> RuntimeRepairExecutor:
        return InProcessRuntimeRepairExecutor(
            session_factory=session_factory,
            repair_agent=repair_agent,
        )

    @staticmethod
    def _agent_backed_subprocess_executor_factory(
        session_factory: sessionmaker,
        repair_agent: RuntimeRepairAgentAdapter,
    ) -> RuntimeRepairExecutor:
        return AgentBackedSubprocessRuntimeRepairExecutor(
            session_factory=session_factory,
            repair_agent=repair_agent,
        )

    @staticmethod
    def _contract_backed_subprocess_executor_factory(
        session_factory: sessionmaker,
        repair_agent: RuntimeRepairAgentAdapter,
    ) -> RuntimeRepairExecutor:
        return ContractBackedSubprocessRuntimeRepairExecutor(
            session_factory=session_factory,
            repair_agent=repair_agent,
        )

    @staticmethod
    def _transport_backed_executor_factory(
        session_factory: sessionmaker,
        repair_agent: RuntimeRepairAgentAdapter,
    ) -> RuntimeRepairExecutor:
        return TransportBackedRuntimeRepairExecutor(
            session_factory=session_factory,
            repair_agent=repair_agent,
        )

    @staticmethod
    def _contract_transport_backed_executor_factory(
        session_factory: sessionmaker,
        repair_agent: RuntimeRepairAgentAdapter,
    ) -> RuntimeRepairExecutor:
        return ContractTransportBackedRuntimeRepairExecutor(
            session_factory=session_factory,
            repair_agent=repair_agent,
        )
