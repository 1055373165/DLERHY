import unittest
from unittest import mock

from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.runtime_repair_agent_adapter import ReviewDeadlockRepairAgentAdapter
from book_agent.services.run_execution import ClaimedRunWorkItem
from book_agent.services.runtime_repair_contract import InvalidRuntimeRepairResultContractError
from book_agent.services.runtime_repair_executor import (
    AgentBackedSubprocessRuntimeRepairExecutor,
    ContractBackedSubprocessRuntimeRepairExecutor,
    ContractTransportBackedRuntimeRepairExecutor,
    InProcessRuntimeRepairExecutor,
    RuntimeRepairExecutorRegistry,
    RuntimeRepairExecutorInvocationError,
    TransportBackedRuntimeRepairExecutor,
    UnsupportedRuntimeRepairExecutorError,
)


class RuntimeRepairExecutorRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.session_factory = build_session_factory(engine=engine)
        self.repair_agent = ReviewDeadlockRepairAgentAdapter(session_factory=self.session_factory)

    def test_resolves_in_process_executor_for_bundle(self) -> None:
        registry = RuntimeRepairExecutorRegistry(session_factory=self.session_factory)

        executor = registry.resolve_for_input_bundle(
            input_bundle={
                "execution_mode": "in_process",
                "executor_hint": "python_repair_executor",
                "executor_contract_version": 1,
            },
            repair_agent=self.repair_agent,
        )

        self.assertIsInstance(executor, InProcessRuntimeRepairExecutor)
        self.assertEqual(executor.descriptor().execution_mode, "in_process")
        self.assertEqual(executor.descriptor().executor_hint, "python_repair_executor")

    def test_resolves_agent_backed_subprocess_executor_for_bundle(self) -> None:
        registry = RuntimeRepairExecutorRegistry(session_factory=self.session_factory)

        executor = registry.resolve_for_input_bundle(
            input_bundle={
                "execution_mode": "agent_backed",
                "executor_hint": "python_subprocess_repair_executor",
                "executor_contract_version": 1,
            },
            repair_agent=self.repair_agent,
        )

        self.assertIsInstance(executor, AgentBackedSubprocessRuntimeRepairExecutor)
        self.assertEqual(executor.descriptor().execution_mode, "agent_backed")
        self.assertEqual(executor.descriptor().executor_hint, "python_subprocess_repair_executor")

    def test_resolves_contract_backed_subprocess_executor_for_bundle(self) -> None:
        registry = RuntimeRepairExecutorRegistry(session_factory=self.session_factory)

        executor = registry.resolve_for_input_bundle(
            input_bundle={
                "execution_mode": "agent_backed",
                "executor_hint": "python_contract_agent_repair_executor",
                "executor_contract_version": 1,
            },
            repair_agent=self.repair_agent,
        )

        self.assertIsInstance(executor, ContractBackedSubprocessRuntimeRepairExecutor)
        self.assertEqual(executor.descriptor().execution_mode, "agent_backed")
        self.assertEqual(executor.descriptor().executor_hint, "python_contract_agent_repair_executor")

    def test_resolves_transport_backed_executor_for_bundle(self) -> None:
        registry = RuntimeRepairExecutorRegistry(session_factory=self.session_factory)

        executor = registry.resolve_for_input_bundle(
            input_bundle={
                "execution_mode": "transport_backed",
                "executor_hint": "python_transport_repair_executor",
                "executor_contract_version": 1,
                "transport_hint": "python_subprocess_repair_transport",
                "transport_contract_version": 1,
            },
            repair_agent=self.repair_agent,
        )

        self.assertIsInstance(executor, TransportBackedRuntimeRepairExecutor)
        self.assertEqual(executor.descriptor().execution_mode, "transport_backed")
        self.assertEqual(executor.descriptor().executor_hint, "python_transport_repair_executor")

    def test_resolves_contract_transport_backed_executor_for_bundle(self) -> None:
        registry = RuntimeRepairExecutorRegistry(session_factory=self.session_factory)

        executor = registry.resolve_for_input_bundle(
            input_bundle={
                "execution_mode": "transport_backed",
                "executor_hint": "python_contract_transport_repair_executor",
                "executor_contract_version": 1,
                "transport_hint": "http_contract_repair_transport",
                "transport_contract_version": 1,
            },
            repair_agent=self.repair_agent,
        )

        self.assertIsInstance(executor, ContractTransportBackedRuntimeRepairExecutor)
        self.assertEqual(executor.descriptor().execution_mode, "transport_backed")
        self.assertEqual(executor.descriptor().executor_hint, "python_contract_transport_repair_executor")

    def test_rejects_unknown_executor_hint(self) -> None:
        registry = RuntimeRepairExecutorRegistry(session_factory=self.session_factory)

        with self.assertRaises(UnsupportedRuntimeRepairExecutorError) as exc_info:
            registry.resolve_for_input_bundle(
                input_bundle={
                    "execution_mode": "in_process",
                    "executor_hint": "unknown_repair_executor",
                    "executor_contract_version": 1,
                },
                repair_agent=self.repair_agent,
            )

        self.assertIn("Unknown repair executor hint", str(exc_info.exception))
        self.assertIn("python_repair_executor", str(exc_info.exception))

    def test_rejects_unsupported_executor_contract_version(self) -> None:
        registry = RuntimeRepairExecutorRegistry(session_factory=self.session_factory)

        with self.assertRaises(UnsupportedRuntimeRepairExecutorError) as exc_info:
            registry.resolve_for_input_bundle(
                input_bundle={
                    "execution_mode": "in_process",
                    "executor_hint": "python_repair_executor",
                    "executor_contract_version": 2,
                },
                repair_agent=self.repair_agent,
            )

        self.assertIn("Unsupported repair executor contract version", str(exc_info.exception))
        self.assertIn("Supported versions: 1", str(exc_info.exception))

    def test_transport_backed_executor_rejects_invalid_remote_result_contract(self) -> None:
        executor = TransportBackedRuntimeRepairExecutor(
            session_factory=self.session_factory,
            repair_agent=self.repair_agent,
        )
        claimed = ClaimedRunWorkItem(
            run_id="run-1",
            work_item_id="work-item-1",
            stage="REPAIR",
            scope_type="ISSUE_ACTION",
            scope_id="proposal-1",
            attempt=1,
            priority=40,
            lease_token="lease-1",
            worker_name="repair-worker",
            worker_instance_id="repair-worker-1",
            lease_expires_at="2026-03-31T00:00:00+00:00",
        )
        fake_transport = mock.Mock()
        fake_transport.dispatch.return_value = {"repair_runner_status": "succeeded"}

        with mock.patch.object(
            executor._transport_registry,
            "resolve_for_input_bundle",
            return_value=fake_transport,
        ):
            with self.assertRaises(InvalidRuntimeRepairResultContractError) as exc_info:
                executor.prepare_execution(
                    claimed=claimed,
                    input_bundle={
                        "execution_mode": "transport_backed",
                        "executor_hint": "python_transport_repair_executor",
                        "executor_contract_version": 1,
                        "transport_hint": "http_repair_transport",
                        "transport_contract_version": 1,
                    },
                )

        self.assertIn("invalid result contract version", str(exc_info.exception))

    def test_contract_backed_executor_rejects_invalid_remote_result_contract(self) -> None:
        executor = ContractBackedSubprocessRuntimeRepairExecutor(
            session_factory=self.session_factory,
            repair_agent=self.repair_agent,
        )
        claimed = ClaimedRunWorkItem(
            run_id="run-1",
            work_item_id="work-item-1",
            stage="REPAIR",
            scope_type="ISSUE_ACTION",
            scope_id="proposal-1",
            attempt=1,
            priority=40,
            lease_token="lease-1",
            worker_name="repair-worker",
            worker_instance_id="repair-worker-1",
            lease_expires_at="2026-03-31T00:00:00+00:00",
        )
        with mock.patch(
            "book_agent.services.runtime_repair_executor.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout='{"repair_runner_status":"succeeded"}', stderr=""),
        ), mock.patch.object(executor, "_begin_dispatch_execution", return_value=None):
            with self.assertRaises(InvalidRuntimeRepairResultContractError) as exc_info:
                executor.prepare_execution(
                    claimed=claimed,
                    input_bundle={
                        "repair_request_contract_version": 1,
                        "proposal_id": "proposal-1",
                        "incident_id": "incident-1",
                        "worker_hint": "review_deadlock_repair_agent",
                        "worker_contract_version": 1,
                    },
                )

        self.assertIn("invalid result contract version", str(exc_info.exception))

    def test_contract_backed_executor_rejects_non_json_remote_output(self) -> None:
        executor = ContractBackedSubprocessRuntimeRepairExecutor(
            session_factory=self.session_factory,
            repair_agent=self.repair_agent,
        )
        claimed = ClaimedRunWorkItem(
            run_id="run-1",
            work_item_id="work-item-1",
            stage="REPAIR",
            scope_type="ISSUE_ACTION",
            scope_id="proposal-1",
            attempt=1,
            priority=40,
            lease_token="lease-1",
            worker_name="repair-worker",
            worker_instance_id="repair-worker-1",
            lease_expires_at="2026-03-31T00:00:00+00:00",
        )
        with mock.patch(
            "book_agent.services.runtime_repair_executor.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout="not-json", stderr=""),
        ), mock.patch.object(executor, "_begin_dispatch_execution", return_value=None):
            with self.assertRaises(RuntimeRepairExecutorInvocationError) as exc_info:
                executor.prepare_execution(
                    claimed=claimed,
                    input_bundle={
                        "repair_request_contract_version": 1,
                        "proposal_id": "proposal-1",
                        "incident_id": "incident-1",
                        "worker_hint": "review_deadlock_repair_agent",
                        "worker_contract_version": 1,
                    },
                )

        self.assertIn("invalid JSON", str(exc_info.exception))


if __name__ == "__main__":
    unittest.main()
