import unittest

from book_agent.tools.runtime_repair_contract_runner import execute_runtime_repair_contract_runner


class RuntimeRepairContractRunnerTests(unittest.TestCase):
    def test_contract_runner_emits_remote_execution_provenance(self) -> None:
        payload = execute_runtime_repair_contract_runner(
            {
                "executor_descriptor": {
                    "executor_name": "python_contract_agent_backed_repair_executor",
                    "execution_mode": "agent_backed",
                    "executor_hint": "python_contract_agent_repair_executor",
                    "executor_contract_version": 1,
                },
                "transport_descriptor": {
                    "transport_name": "http_contract_runtime_repair_transport",
                    "transport_hint": "http_contract_repair_transport",
                    "transport_contract_version": 1,
                },
                "input_bundle": {
                    "repair_request_contract_version": 1,
                    "proposal_id": "proposal-1",
                    "incident_id": "incident-1",
                    "worker_hint": "export_routing_repair_agent",
                    "worker_contract_version": 1,
                    "validation_json": {"corrected_route": "route-b", "export_type": "rebuilt_pdf"},
                    "bundle_json": {
                        "revision_name": "export-routing-fix",
                        "manifest_json": {"config": {"routing_policy": {"export_routes": {}}}},
                    },
                    "replay_json": {"scope_type": "export", "scope_id": "scope-1", "boundary": "export"},
                    "repair_plan_json": {
                        "incident_kind": "export_misrouting",
                        "goal": "repair export routing",
                    },
                },
            }
        )

        self.assertEqual(payload["repair_result_contract_version"], 1)
        self.assertEqual(payload["repair_agent_execution_status"], "succeeded")
        self.assertTrue(payload["repair_agent_execution_id"])
        self.assertIn("T", payload["repair_agent_execution_started_at"])
        self.assertIn("T", payload["repair_agent_execution_completed_at"])
        self.assertEqual(payload["repair_agent_decision"], "publish_bundle_and_replay")
        self.assertEqual(payload["repair_agent_decision_reason"], "bounded_remote_contract_ready")
        self.assertEqual(
            payload["repair_agent_adapter_name"],
            "export_routing_remote_contract_repair_agent",
        )


if __name__ == "__main__":
    unittest.main()
