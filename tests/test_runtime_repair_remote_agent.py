import unittest

from book_agent.services.runtime_repair_remote_agent import (
    ExportRoutingRemoteRepairAgent,
    ReviewDeadlockRemoteRepairAgent,
    RuntimeRemoteRepairAgentRegistry,
    UnsupportedRuntimeRemoteRepairAgentError,
)
from book_agent.services.runtime_repair_worker import UnsupportedRuntimeRepairIncidentError


class RuntimeRemoteRepairAgentRegistryTests(unittest.TestCase):
    def test_resolves_registered_remote_worker_hints(self) -> None:
        registry = RuntimeRemoteRepairAgentRegistry()

        review_agent = registry.resolve(
            worker_hint="review_deadlock_repair_agent",
            worker_contract_version=1,
        )
        export_agent = registry.resolve(
            worker_hint="export_routing_repair_agent",
            worker_contract_version=1,
        )
        default_agent = registry.resolve_for_input_bundle({})

        self.assertIsInstance(review_agent, ReviewDeadlockRemoteRepairAgent)
        self.assertIsInstance(export_agent, ExportRoutingRemoteRepairAgent)
        self.assertEqual(default_agent.descriptor().worker_hint, "default_runtime_repair_agent")

    def test_rejects_unknown_remote_worker_hint(self) -> None:
        registry = RuntimeRemoteRepairAgentRegistry()

        with self.assertRaises(UnsupportedRuntimeRemoteRepairAgentError) as exc_info:
            registry.resolve(
                worker_hint="unknown_remote_repair_agent",
                worker_contract_version=1,
            )

        self.assertIn("Unknown remote repair worker hint", str(exc_info.exception))

    def test_remote_agent_prepares_payload_from_request_contract(self) -> None:
        agent = ExportRoutingRemoteRepairAgent()

        payload = agent.prepare_execution_from_request_contract(
            {
                "repair_request_contract_version": 1,
                "proposal_id": "proposal-1",
                "incident_id": "incident-1",
                "repair_dispatch_id": "dispatch-1",
                "patch_surface": "runtime_bundle",
                "target_scope_type": "export",
                "target_scope_id": "scope-1",
                "replay_boundary": "export",
                "validation_command": "uv run pytest tests/test_export_controller.py",
                "bundle_revision_name": "export-routing-fix-scope-1",
                "claim_mode": "runtime_owned",
                "claim_target": "runtime_patch_proposal",
                "dispatch_lane": "runtime.repair",
                "worker_hint": "export_routing_repair_agent",
                "worker_contract_version": 1,
                "owned_files": ["src/book_agent/services/export_routing.py"],
                "validation_json": {"corrected_route": "route-b", "export_type": "rebuilt_pdf"},
                "bundle_json": {
                    "revision_name": "export-routing-fix-scope-1",
                    "manifest_json": {"code": {"surface": "export_routing"}},
                },
                "replay_json": {"scope_type": "export", "scope_id": "scope-1", "boundary": "export"},
                "repair_plan_json": {
                    "incident_kind": "export_misrouting",
                    "goal": "repair export routing",
                    "owned_files": ["src/book_agent/services/export_routing.py"],
                },
            }
        )

        self.assertEqual(payload["proposal_id"], "proposal-1")
        self.assertEqual(payload["incident_kind"], "export_misrouting")
        self.assertEqual(payload["corrected_route"], "route-b")
        self.assertEqual(payload["repair_agent_decision"], "publish_bundle_and_replay")
        self.assertEqual(payload["repair_agent_decision_reason"], "bounded_remote_contract_ready")
        self.assertEqual(payload["repair_agent_adapter_name"], "export_routing_remote_contract_repair_agent")

    def test_remote_agent_rejects_unsupported_incident_kind(self) -> None:
        agent = ReviewDeadlockRemoteRepairAgent()

        with self.assertRaises(UnsupportedRuntimeRepairIncidentError) as exc_info:
            agent.prepare_execution_from_request_contract(
                {
                    "repair_request_contract_version": 1,
                    "proposal_id": "proposal-1",
                    "incident_id": "incident-1",
                    "worker_hint": "review_deadlock_repair_agent",
                    "worker_contract_version": 1,
                    "repair_plan_json": {"incident_kind": "export_misrouting"},
                }
            )

        self.assertIn("does not support incident kind", str(exc_info.exception))


if __name__ == "__main__":
    unittest.main()
