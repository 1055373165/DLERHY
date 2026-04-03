import json
import subprocess
import tempfile
import unittest
from unittest import mock

from book_agent.core.config import Settings
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.run_execution import ClaimedRunWorkItem
from book_agent.services.runtime_repair_transport import (
    ConfiguredCommandRuntimeRepairTransport,
    HttpContractRuntimeRepairTransport,
    HttpRuntimeRepairTransport,
    RuntimeRepairTransportRegistry,
    RuntimeRepairTransportInvocationError,
    SubprocessRuntimeRepairTransport,
    UnsupportedRuntimeRepairTransportError,
)


class RuntimeRepairTransportRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.session_factory = build_session_factory(engine=engine)

    def test_resolves_subprocess_transport_for_bundle(self) -> None:
        registry = RuntimeRepairTransportRegistry(session_factory=self.session_factory)

        transport = registry.resolve_for_input_bundle(
            {
                "transport_hint": "python_subprocess_repair_transport",
                "transport_contract_version": 1,
            }
        )

        self.assertIsInstance(transport, SubprocessRuntimeRepairTransport)
        self.assertEqual(transport.descriptor().transport_hint, "python_subprocess_repair_transport")

    def test_resolves_configured_command_transport_for_bundle(self) -> None:
        registry = RuntimeRepairTransportRegistry(session_factory=self.session_factory)

        transport = registry.resolve_for_input_bundle(
            {
                "transport_hint": "configured_command_repair_transport",
                "transport_contract_version": 1,
            }
        )

        self.assertIsInstance(transport, ConfiguredCommandRuntimeRepairTransport)
        self.assertEqual(transport.descriptor().transport_hint, "configured_command_repair_transport")

    def test_rejects_unknown_transport_hint(self) -> None:
        registry = RuntimeRepairTransportRegistry(session_factory=self.session_factory)

        with self.assertRaises(UnsupportedRuntimeRepairTransportError) as exc_info:
            registry.resolve(
                transport_hint="unknown_repair_transport",
                transport_contract_version=1,
            )

        self.assertIn("Unknown repair transport hint", str(exc_info.exception))
        self.assertIn("python_subprocess_repair_transport", str(exc_info.exception))

    def test_rejects_unsupported_transport_contract_version(self) -> None:
        registry = RuntimeRepairTransportRegistry(session_factory=self.session_factory)

        with self.assertRaises(UnsupportedRuntimeRepairTransportError) as exc_info:
            registry.resolve(
                transport_hint="python_subprocess_repair_transport",
                transport_contract_version=2,
            )

        self.assertIn("Unsupported repair transport contract version", str(exc_info.exception))
        self.assertIn("Supported versions: 1", str(exc_info.exception))

    def test_resolves_http_transport_for_bundle(self) -> None:
        registry = RuntimeRepairTransportRegistry(session_factory=self.session_factory)

        transport = registry.resolve_for_input_bundle(
            {
                "transport_hint": "http_repair_transport",
                "transport_contract_version": 1,
            }
        )

        self.assertIsInstance(transport, HttpRuntimeRepairTransport)
        self.assertEqual(transport.descriptor().transport_hint, "http_repair_transport")

    def test_resolves_http_contract_transport_for_bundle(self) -> None:
        registry = RuntimeRepairTransportRegistry(session_factory=self.session_factory)

        transport = registry.resolve_for_input_bundle(
            {
                "transport_hint": "http_contract_repair_transport",
                "transport_contract_version": 1,
            }
        )

        self.assertIsInstance(transport, HttpContractRuntimeRepairTransport)
        self.assertEqual(transport.descriptor().transport_hint, "http_contract_repair_transport")

    def test_configured_command_transport_dispatches_runner_with_configured_command(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            database_url = f"sqlite+pysqlite:///{tempdir}/repair-transport.sqlite"
            engine = build_engine(database_url)
            Base.metadata.create_all(engine)
            session_factory = build_session_factory(engine=engine)
            transport = ConfiguredCommandRuntimeRepairTransport(session_factory=session_factory)
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

            with (
                mock.patch(
                    "book_agent.services.runtime_repair_transport.get_settings",
                    return_value=Settings(
                        database_url=database_url,
                        runtime_repair_transport_command="python -m book_agent.tools.runtime_repair_runner",
                    ),
                ),
                mock.patch(
                    "book_agent.services.runtime_repair_transport.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        args=[],
                        returncode=0,
                        stdout=json.dumps({"status": "executed"}),
                        stderr="",
                    ),
                ) as run_mock,
            ):
                result = transport.dispatch(
                    run_id="run-1",
                    lease_token="lease-1",
                    claimed=claimed,
                    input_bundle={"transport_hint": "configured_command_repair_transport"},
                    executor_descriptor={"executor_hint": "python_transport_repair_executor"},
                )

        self.assertEqual(result["status"], "executed")
        command = run_mock.call_args.args[0]
        self.assertEqual(command[:3], ["python", "-m", "book_agent.tools.runtime_repair_runner"])
        self.assertEqual(command[-2], "--payload-file")

    def test_configured_command_transport_requires_configured_command(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            database_url = f"sqlite+pysqlite:///{tempdir}/repair-transport.sqlite"
            engine = build_engine(database_url)
            Base.metadata.create_all(engine)
            session_factory = build_session_factory(engine=engine)
            transport = ConfiguredCommandRuntimeRepairTransport(session_factory=session_factory)
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
                "book_agent.services.runtime_repair_transport.get_settings",
                return_value=Settings(database_url=database_url),
            ):
                with self.assertRaises(RuntimeRepairTransportInvocationError) as exc_info:
                    transport.dispatch(
                        run_id="run-1",
                        lease_token="lease-1",
                        claimed=claimed,
                        input_bundle={"transport_hint": "configured_command_repair_transport"},
                        executor_descriptor={"executor_hint": "python_transport_repair_executor"},
                    )

        self.assertIn("BOOK_AGENT_RUNTIME_REPAIR_TRANSPORT_COMMAND", str(exc_info.exception))

    def test_http_transport_dispatches_runner_payload_to_remote_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            database_url = f"sqlite+pysqlite:///{tempdir}/repair-transport.sqlite"
            engine = build_engine(database_url)
            Base.metadata.create_all(engine)
            session_factory = build_session_factory(engine=engine)
            transport = HttpRuntimeRepairTransport(session_factory=session_factory)
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
            response = mock.Mock()
            response.status_code = 200
            response.json.return_value = {"status": "executed"}
            response.text = '{"status":"executed"}'

            with (
                mock.patch(
                    "book_agent.services.runtime_repair_transport.get_settings",
                    return_value=Settings(
                        database_url=database_url,
                        runtime_repair_transport_http_url="https://repair-agent.example/execute",
                        runtime_repair_transport_http_timeout_seconds=45,
                        runtime_repair_transport_http_bearer_token="secret-token",
                    ),
                ),
                mock.patch(
                    "book_agent.services.runtime_repair_transport.httpx.post",
                    return_value=response,
                ) as post_mock,
            ):
                result = transport.dispatch(
                    run_id="run-1",
                    lease_token="lease-1",
                    claimed=claimed,
                    input_bundle={
                        "transport_hint": "http_repair_transport",
                        "repair_request_contract_version": 1,
                        "repair_plan_json": {"goal": "repair export routing"},
                    },
                    executor_descriptor={"executor_hint": "python_transport_repair_executor"},
                )

        self.assertEqual(result["status"], "executed")
        self.assertEqual(post_mock.call_args.args[0], "https://repair-agent.example/execute")
        self.assertEqual(post_mock.call_args.kwargs["timeout"], 45)
        self.assertEqual(
            post_mock.call_args.kwargs["headers"]["authorization"],
            "Bearer secret-token",
        )
        self.assertEqual(
            post_mock.call_args.kwargs["json"]["transport_descriptor"]["transport_hint"],
            "http_repair_transport",
        )
        self.assertEqual(
            post_mock.call_args.kwargs["json"]["input_bundle"]["repair_request_contract_version"],
            1,
        )
        self.assertEqual(result["repair_transport_endpoint"], "https://repair-agent.example/execute")
        self.assertEqual(
            post_mock.call_args.kwargs["json"]["input_bundle"]["repair_plan_json"]["goal"],
            "repair export routing",
        )

    def test_http_transport_requires_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            database_url = f"sqlite+pysqlite:///{tempdir}/repair-transport.sqlite"
            engine = build_engine(database_url)
            Base.metadata.create_all(engine)
            session_factory = build_session_factory(engine=engine)
            transport = HttpRuntimeRepairTransport(session_factory=session_factory)
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
                "book_agent.services.runtime_repair_transport.get_settings",
                return_value=Settings(database_url=database_url),
            ):
                with self.assertRaises(RuntimeRepairTransportInvocationError) as exc_info:
                    transport.dispatch(
                        run_id="run-1",
                        lease_token="lease-1",
                        claimed=claimed,
                        input_bundle={"transport_hint": "http_repair_transport"},
                        executor_descriptor={"executor_hint": "python_transport_repair_executor"},
                    )

        self.assertIn("BOOK_AGENT_RUNTIME_REPAIR_TRANSPORT_HTTP_URL", str(exc_info.exception))

    def test_http_contract_transport_dispatches_contract_payload_to_remote_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            database_url = f"sqlite+pysqlite:///{tempdir}/repair-transport.sqlite"
            engine = build_engine(database_url)
            Base.metadata.create_all(engine)
            session_factory = build_session_factory(engine=engine)
            transport = HttpContractRuntimeRepairTransport(session_factory=session_factory)
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
            response = mock.Mock()
            response.status_code = 200
            response.json.return_value = {"status": "executed"}
            response.text = '{"status":"executed"}'

            with (
                mock.patch(
                    "book_agent.services.runtime_repair_transport.get_settings",
                    return_value=Settings(
                        database_url=database_url,
                        runtime_repair_transport_http_url="https://repair-agent.example/execute",
                        runtime_repair_transport_http_timeout_seconds=45,
                        runtime_repair_transport_http_bearer_token="secret-token",
                    ),
                ),
                mock.patch(
                    "book_agent.services.runtime_repair_transport.httpx.post",
                    return_value=response,
                ) as post_mock,
            ):
                result = transport.dispatch(
                    run_id="run-1",
                    lease_token="lease-1",
                    claimed=claimed,
                    input_bundle={
                        "transport_hint": "http_contract_repair_transport",
                        "repair_request_contract_version": 1,
                        "worker_hint": "export_routing_repair_agent",
                    },
                    executor_descriptor={"executor_hint": "python_transport_repair_executor"},
                )

        self.assertEqual(result["status"], "executed")
        self.assertEqual(post_mock.call_args.args[0], "https://repair-agent.example/execute")
        self.assertNotIn("database_url", post_mock.call_args.kwargs["json"])
        self.assertNotIn("claimed", post_mock.call_args.kwargs["json"])
        self.assertEqual(
            post_mock.call_args.kwargs["json"]["transport_descriptor"]["transport_hint"],
            "http_contract_repair_transport",
        )
        self.assertEqual(
            post_mock.call_args.kwargs["json"]["input_bundle"]["repair_request_contract_version"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
