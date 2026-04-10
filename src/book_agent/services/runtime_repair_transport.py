from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import httpx
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from typing import Any

from sqlalchemy.orm import sessionmaker

from book_agent.core.config import get_settings
from book_agent.services.run_execution import ClaimedRunWorkItem

RuntimeRepairTransportFactory = Callable[[sessionmaker], "RuntimeRepairTransport"]


@dataclass(frozen=True, slots=True)
class RuntimeRepairTransportDescriptor:
    transport_name: str
    transport_hint: str
    transport_contract_version: int


class UnsupportedRuntimeRepairTransportError(RuntimeError):
    """Raised when a repair work item requests an unknown or unsupported transport contract."""


class RuntimeRepairTransportInvocationError(RuntimeError):
    """Raised when a repair transport cannot successfully invoke its underlying transport backend."""


class RuntimeRepairTransport:
    TRANSPORT_NAME = "default_subprocess_repair_transport"
    TRANSPORT_HINT = "python_subprocess_repair_transport"
    TRANSPORT_CONTRACT_VERSION = 1

    def __init__(self, *, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def descriptor(self) -> RuntimeRepairTransportDescriptor:
        return RuntimeRepairTransportDescriptor(
            transport_name=self.TRANSPORT_NAME,
            transport_hint=self.TRANSPORT_HINT,
            transport_contract_version=self.TRANSPORT_CONTRACT_VERSION,
        )

    def dispatch(
        self,
        *,
        run_id: str,
        lease_token: str,
        claimed: ClaimedRunWorkItem,
        input_bundle: dict[str, Any],
        executor_descriptor: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError


class SubprocessRuntimeRepairTransport(RuntimeRepairTransport):
    TRANSPORT_NAME = "python_subprocess_runtime_repair_transport"
    TRANSPORT_HINT = "python_subprocess_repair_transport"
    TRANSPORT_CONTRACT_VERSION = 1

    def dispatch(
        self,
        *,
        run_id: str,
        lease_token: str,
        claimed: ClaimedRunWorkItem,
        input_bundle: dict[str, Any],
        executor_descriptor: dict[str, Any],
    ) -> dict[str, Any]:
        descriptor = self.descriptor()
        runner_payload = self._build_runner_payload(
            run_id=run_id,
            lease_token=lease_token,
            claimed=claimed,
            input_bundle=input_bundle,
            executor_descriptor=executor_descriptor,
            transport_descriptor=descriptor,
        )
        payload_path = self._write_runner_payload(runner_payload)
        try:
            command = self._build_command(payload_path)
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            payload_path.unlink(missing_ok=True)
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            detail = stderr or stdout or "repair runner exited without output"
            raise RuntimeRepairTransportInvocationError(
                "Repair transport failed to invoke runtime repair runner: "
                f"{detail}"
            )
        stdout = completed.stdout.strip()
        if not stdout:
            raise RuntimeRepairTransportInvocationError(
                "Repair transport received no output from runtime repair runner."
            )
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeRepairTransportInvocationError(
                "Repair transport received invalid JSON from runtime repair runner."
            ) from exc

    def _build_runner_payload(
        self,
        *,
        run_id: str,
        lease_token: str,
        claimed: ClaimedRunWorkItem,
        input_bundle: dict[str, Any],
        executor_descriptor: dict[str, Any],
        transport_descriptor: RuntimeRepairTransportDescriptor,
    ) -> dict[str, Any]:
        return {
            "database_url": self._resolve_database_url(),
            "run_id": run_id,
            "lease_token": lease_token,
            "executor_descriptor": dict(executor_descriptor),
            "transport_descriptor": {
                "transport_name": transport_descriptor.transport_name,
                "transport_hint": transport_descriptor.transport_hint,
                "transport_contract_version": transport_descriptor.transport_contract_version,
            },
            "claimed": {
                "run_id": claimed.run_id,
                "work_item_id": claimed.work_item_id,
                "stage": claimed.stage,
                "scope_type": claimed.scope_type,
                "scope_id": claimed.scope_id,
                "attempt": claimed.attempt,
                "priority": claimed.priority,
                "lease_token": claimed.lease_token,
                "worker_name": claimed.worker_name,
                "worker_instance_id": claimed.worker_instance_id,
                "lease_expires_at": claimed.lease_expires_at,
            },
            "input_bundle": dict(input_bundle),
        }

    def _build_command(self, payload_path: Path) -> list[str]:
        return [
            sys.executable,
            "-m",
            "book_agent.tools.runtime_repair_runner",
            "--payload-file",
            str(payload_path),
        ]

    def _resolve_database_url(self) -> str:
        bind = self._session_factory.kw.get("bind")
        if bind is None or getattr(bind, "url", None) is None:
            raise RuntimeRepairTransportInvocationError(
                "Subprocess repair transport requires a bound database URL."
            )
        database_url = str(bind.url)
        if database_url.endswith("/:memory:") or database_url.endswith("://"):
            raise RuntimeRepairTransportInvocationError(
                "Subprocess repair transport requires a valid database URL; "
                "in-memory databases cannot be shared with a subprocess repair agent."
            )
        return database_url

    @staticmethod
    def _write_runner_payload(payload: dict[str, Any]) -> Path:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".runtime-repair.json",
            delete=False,
        ) as handle:
            json.dump(payload, handle)
            handle.flush()
            return Path(handle.name)


class ConfiguredCommandRuntimeRepairTransport(SubprocessRuntimeRepairTransport):
    TRANSPORT_NAME = "configured_command_runtime_repair_transport"
    TRANSPORT_HINT = "configured_command_repair_transport"
    TRANSPORT_CONTRACT_VERSION = 1

    def _build_command(self, payload_path: Path) -> list[str]:
        configured_command = str(get_settings().runtime_repair_transport_command or "").strip()
        if not configured_command:
            raise RuntimeRepairTransportInvocationError(
                "Configured command repair transport requires "
                "BOOK_AGENT_RUNTIME_REPAIR_TRANSPORT_COMMAND."
            )
        return [
            *shlex.split(configured_command),
            "--payload-file",
            str(payload_path),
        ]


class HttpRuntimeRepairTransport(SubprocessRuntimeRepairTransport):
    TRANSPORT_NAME = "http_runtime_repair_transport"
    TRANSPORT_HINT = "http_repair_transport"
    TRANSPORT_CONTRACT_VERSION = 1

    def dispatch(
        self,
        *,
        run_id: str,
        lease_token: str,
        claimed: ClaimedRunWorkItem,
        input_bundle: dict[str, Any],
        executor_descriptor: dict[str, Any],
    ) -> dict[str, Any]:
        settings = get_settings()
        endpoint = str(settings.runtime_repair_transport_http_url or "").strip()
        if not endpoint:
            raise RuntimeRepairTransportInvocationError(
                "HTTP repair transport requires BOOK_AGENT_RUNTIME_REPAIR_TRANSPORT_HTTP_URL."
            )
        descriptor = self.descriptor()
        runner_payload = self._build_runner_payload(
            run_id=run_id,
            lease_token=lease_token,
            claimed=claimed,
            input_bundle=input_bundle,
            executor_descriptor=executor_descriptor,
            transport_descriptor=descriptor,
        )
        headers = {"content-type": "application/json"}
        bearer_token = str(settings.runtime_repair_transport_http_bearer_token or "").strip()
        if bearer_token:
            headers["authorization"] = f"Bearer {bearer_token}"
        try:
            response = httpx.post(
                endpoint,
                json=runner_payload,
                headers=headers,
                timeout=max(1, int(settings.runtime_repair_transport_http_timeout_seconds)),
            )
        except httpx.HTTPError as exc:
            raise RuntimeRepairTransportInvocationError(
                f"HTTP repair transport failed to invoke remote executor: {exc}"
            ) from exc
        if response.status_code >= 400:
            detail = response.text.strip() or f"HTTP {response.status_code}"
            raise RuntimeRepairTransportInvocationError(
                "HTTP repair transport received a failing response: "
                f"{detail}"
            )
        try:
            payload = dict(response.json())
        except (ValueError, TypeError) as exc:
            raise RuntimeRepairTransportInvocationError(
                "HTTP repair transport received invalid JSON from remote executor."
            ) from exc
        payload.setdefault("repair_transport_endpoint", endpoint)
        return payload


class HttpContractRuntimeRepairTransport(RuntimeRepairTransport):
    TRANSPORT_NAME = "http_contract_runtime_repair_transport"
    TRANSPORT_HINT = "http_contract_repair_transport"
    TRANSPORT_CONTRACT_VERSION = 1

    def dispatch(
        self,
        *,
        run_id: str,
        lease_token: str,
        claimed: ClaimedRunWorkItem,
        input_bundle: dict[str, Any],
        executor_descriptor: dict[str, Any],
    ) -> dict[str, Any]:
        settings = get_settings()
        endpoint = str(settings.runtime_repair_transport_http_url or "").strip()
        if not endpoint:
            raise RuntimeRepairTransportInvocationError(
                "HTTP repair transport requires BOOK_AGENT_RUNTIME_REPAIR_TRANSPORT_HTTP_URL."
            )
        descriptor = self.descriptor()
        payload = {
            "executor_descriptor": dict(executor_descriptor),
            "transport_descriptor": {
                "transport_name": descriptor.transport_name,
                "transport_hint": descriptor.transport_hint,
                "transport_contract_version": descriptor.transport_contract_version,
            },
            "input_bundle": dict(input_bundle),
        }
        headers = {"content-type": "application/json"}
        bearer_token = str(settings.runtime_repair_transport_http_bearer_token or "").strip()
        if bearer_token:
            headers["authorization"] = f"Bearer {bearer_token}"
        try:
            response = httpx.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=max(1, int(settings.runtime_repair_transport_http_timeout_seconds)),
            )
        except httpx.HTTPError as exc:
            raise RuntimeRepairTransportInvocationError(
                f"HTTP repair transport failed to invoke remote executor: {exc}"
            ) from exc
        if response.status_code >= 400:
            detail = response.text.strip() or f"HTTP {response.status_code}"
            raise RuntimeRepairTransportInvocationError(
                "HTTP repair transport received a failing response: "
                f"{detail}"
            )
        try:
            payload = dict(response.json())
        except (ValueError, TypeError) as exc:
            raise RuntimeRepairTransportInvocationError(
                "HTTP repair transport received invalid JSON from remote executor."
            ) from exc
        payload.setdefault("repair_transport_endpoint", endpoint)
        return payload


class RuntimeRepairTransportRegistry:
    DEFAULT_TRANSPORT_HINT = "python_subprocess_repair_transport"
    DEFAULT_TRANSPORT_CONTRACT_VERSION = 1

    def __init__(
        self,
        *,
        session_factory: sessionmaker,
        registrations: Mapping[tuple[str, int], RuntimeRepairTransportFactory] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._registrations = dict(registrations or self._default_registrations())

    def resolve_for_input_bundle(self, input_bundle: dict[str, Any]) -> RuntimeRepairTransport:
        transport_hint = str(input_bundle.get("transport_hint") or self.DEFAULT_TRANSPORT_HINT).strip()
        transport_contract_version = int(
            input_bundle.get("transport_contract_version") or self.DEFAULT_TRANSPORT_CONTRACT_VERSION
        )
        return self.resolve(
            transport_hint=transport_hint,
            transport_contract_version=transport_contract_version,
        )

    def resolve(
        self,
        *,
        transport_hint: str,
        transport_contract_version: int,
    ) -> RuntimeRepairTransport:
        normalized_hint = str(transport_hint or self.DEFAULT_TRANSPORT_HINT).strip()
        normalized_version = int(
            transport_contract_version or self.DEFAULT_TRANSPORT_CONTRACT_VERSION
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
            raise UnsupportedRuntimeRepairTransportError(
                "Unsupported repair transport contract version "
                f"{normalized_version} for transport_hint={normalized_hint!r}. "
                f"Supported versions: {supported_text}."
            )

        supported_hints = sorted({hint for hint, _version in self._registrations})
        raise UnsupportedRuntimeRepairTransportError(
            "Unknown repair transport hint "
            f"{normalized_hint!r}. Supported hints: {', '.join(supported_hints)}."
        )

    @classmethod
    def _default_registrations(cls) -> dict[tuple[str, int], RuntimeRepairTransportFactory]:
        return {
            (
                cls.DEFAULT_TRANSPORT_HINT,
                cls.DEFAULT_TRANSPORT_CONTRACT_VERSION,
            ): cls._subprocess_transport_factory,
            (
                "configured_command_repair_transport",
                1,
            ): cls._configured_command_transport_factory,
            (
                "http_repair_transport",
                1,
            ): cls._http_transport_factory,
            (
                "http_contract_repair_transport",
                1,
            ): cls._http_contract_transport_factory,
        }

    @staticmethod
    def _subprocess_transport_factory(session_factory: sessionmaker) -> RuntimeRepairTransport:
        return SubprocessRuntimeRepairTransport(session_factory=session_factory)

    @staticmethod
    def _configured_command_transport_factory(session_factory: sessionmaker) -> RuntimeRepairTransport:
        return ConfiguredCommandRuntimeRepairTransport(session_factory=session_factory)

    @staticmethod
    def _http_transport_factory(session_factory: sessionmaker) -> RuntimeRepairTransport:
        return HttpRuntimeRepairTransport(session_factory=session_factory)

    @staticmethod
    def _http_contract_transport_factory(session_factory: sessionmaker) -> RuntimeRepairTransport:
        return HttpContractRuntimeRepairTransport(session_factory=session_factory)
