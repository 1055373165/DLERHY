import unittest

from book_agent.services.runtime_repair_contract import (
    InvalidRuntimeRepairRequestContractError,
    InvalidRuntimeRepairResultContractError,
    RUNTIME_REPAIR_RESULT_CONTRACT_VERSION,
    RUNTIME_REPAIR_REQUEST_CONTRACT_VERSION,
    build_runtime_repair_result_payload,
    build_runtime_repair_request_input_bundle,
    validate_runtime_repair_request_input_bundle,
    validate_runtime_repair_result_payload,
)


class RuntimeRepairContractTests(unittest.TestCase):
    def test_builds_explicit_repair_request_input_bundle(self) -> None:
        bundle = build_runtime_repair_request_input_bundle(
            proposal_id="proposal-1",
            incident_id="incident-1",
            repair_dispatch_json={
                "dispatch_id": "dispatch-1",
                "patch_surface": "runtime_bundle",
                "claim_mode": "runtime_owned",
                "claim_target": "runtime_patch_proposal",
                "lane": "runtime.repair",
                "worker_hint": "packet_runtime_defect_repair_agent",
                "worker_contract_version": 1,
                "execution_mode": "transport_backed",
                "executor_hint": "python_transport_repair_executor",
                "executor_contract_version": 1,
                "transport_hint": "http_repair_transport",
                "transport_contract_version": 1,
                "validation_command": "uv run pytest tests/test_packet_runtime_repair.py",
                "bundle_revision_name": "packet-runtime-fix-packet-123",
                "rollout_scope_json": {"mode": "dev", "scope_type": "packet"},
                "owned_files": ["src/book_agent/app/runtime/controllers/packet_controller.py"],
                "replay": {
                    "scope_type": "packet",
                    "scope_id": "packet-1",
                    "boundary": "packet",
                },
            },
            repair_plan_json={
                "goal": "Repair repeated packet runtime defects and replay only the affected packet scope.",
                "owned_files": ["src/book_agent/app/runtime/controllers/packet_controller.py"],
                "validation": {
                    "command": "uv run pytest tests/test_packet_runtime_repair.py",
                    "scope": "packet_runtime_defect",
                },
                "bundle": {
                    "revision_name": "packet-runtime-fix-packet-123",
                    "manifest_json": {"code": {"surface": "packet_runtime_defect"}},
                    "rollout_scope_json": {"mode": "dev", "scope_type": "packet"},
                },
                "replay": {
                    "scope_type": "packet",
                    "scope_id": "packet-1",
                    "boundary": "packet",
                },
            },
        )

        self.assertEqual(bundle["repair_request_contract_version"], RUNTIME_REPAIR_REQUEST_CONTRACT_VERSION)
        self.assertEqual(bundle["proposal_id"], "proposal-1")
        self.assertEqual(bundle["incident_id"], "incident-1")
        self.assertEqual(bundle["worker_hint"], "packet_runtime_defect_repair_agent")
        self.assertEqual(bundle["transport_hint"], "http_repair_transport")
        self.assertEqual(bundle["repair_goal"], "Repair repeated packet runtime defects and replay only the affected packet scope.")
        self.assertEqual(bundle["validation_json"]["scope"], "packet_runtime_defect")
        self.assertEqual(bundle["bundle_json"]["revision_name"], "packet-runtime-fix-packet-123")
        self.assertEqual(bundle["replay_json"]["scope_id"], "packet-1")
        self.assertEqual(
            bundle["repair_dispatch_json"]["worker_hint"],
            "packet_runtime_defect_repair_agent",
        )
        self.assertEqual(
            bundle["repair_plan_json"]["bundle"]["manifest_json"]["code"]["surface"],
            "packet_runtime_defect",
        )

    def test_builds_explicit_repair_result_payload(self) -> None:
        payload = build_runtime_repair_result_payload(
            prepared_payload={"proposal_id": "proposal-1", "repair_status": "published"},
            executor_descriptor={
                "executor_name": "python_transport_backed_repair_executor",
                "execution_mode": "transport_backed",
                "executor_hint": "python_transport_repair_executor",
                "executor_contract_version": 1,
            },
            transport_descriptor={
                "transport_name": "http_runtime_repair_transport",
                "transport_hint": "http_repair_transport",
                "transport_contract_version": 1,
            },
            repair_runner_status="succeeded",
            repair_runner_pid=4321,
            repair_agent_execution_id="exec-1",
            repair_agent_execution_status="succeeded",
            repair_agent_execution_started_at="2026-04-01T00:00:00+00:00",
            repair_agent_execution_completed_at="2026-04-01T00:00:10+00:00",
            repair_transport_endpoint="https://repair-agent.example/execute",
        )

        self.assertEqual(payload["repair_result_contract_version"], RUNTIME_REPAIR_RESULT_CONTRACT_VERSION)
        self.assertEqual(payload["repair_status"], "published")
        self.assertEqual(payload["repair_executor_execution_mode"], "transport_backed")
        self.assertEqual(payload["repair_transport_hint"], "http_repair_transport")
        self.assertEqual(payload["repair_runner_status"], "succeeded")
        self.assertEqual(payload["repair_runner_pid"], 4321)
        self.assertEqual(payload["repair_agent_execution_id"], "exec-1")
        self.assertEqual(payload["repair_agent_execution_status"], "succeeded")
        self.assertEqual(payload["repair_agent_execution_started_at"], "2026-04-01T00:00:00+00:00")
        self.assertEqual(payload["repair_agent_execution_completed_at"], "2026-04-01T00:00:10+00:00")
        self.assertEqual(payload["repair_transport_endpoint"], "https://repair-agent.example/execute")

    def test_rejects_invalid_repair_result_contract_version(self) -> None:
        with self.assertRaises(InvalidRuntimeRepairResultContractError) as exc_info:
            validate_runtime_repair_result_payload(
                {
                    "repair_result_contract_version": 99,
                    "repair_runner_status": "succeeded",
                }
            )

        self.assertIn("invalid result contract version", str(exc_info.exception))

    def test_rejects_invalid_repair_request_contract_version(self) -> None:
        with self.assertRaises(InvalidRuntimeRepairRequestContractError) as exc_info:
            validate_runtime_repair_request_input_bundle(
                {
                    "repair_request_contract_version": 99,
                    "proposal_id": "proposal-1",
                    "incident_id": "incident-1",
                }
            )

        self.assertIn("invalid request contract version", str(exc_info.exception))


if __name__ == "__main__":
    unittest.main()
