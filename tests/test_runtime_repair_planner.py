import unittest

from book_agent.services.runtime_repair_planner import RuntimeRepairPlannerService


class RuntimeRepairPlannerTests(unittest.TestCase):
    def test_plans_review_deadlock_repair_with_minimal_replay_handoff(self) -> None:
        service = RuntimeRepairPlannerService()

        plan = service.plan_review_deadlock_repair(
            chapter_id="chapter-1234567890ab",
            chapter_run_id="chapter-run-1",
            review_session_id="review-session-1",
            reason_code="review_failed_without_terminality",
        )

        self.assertEqual(plan.patch_surface, "runtime_bundle")
        self.assertEqual(plan.revision_name, "review-deadlock-fix-chapter-1234")
        self.assertEqual(plan.handoff_json["incident_kind"], "review_deadlock")
        self.assertEqual(plan.handoff_json["replay"]["scope_type"], "chapter")
        self.assertEqual(plan.handoff_json["replay"]["boundary"], "review_session")
        self.assertEqual(plan.handoff_json["dispatch"]["claim_mode"], "runtime_owned")
        self.assertEqual(plan.handoff_json["dispatch"]["claim_target"], "runtime_patch_proposal")
        self.assertEqual(plan.handoff_json["dispatch"]["worker_contract_version"], 1)
        self.assertEqual(plan.handoff_json["dispatch"]["execution_mode"], "transport_backed")
        self.assertEqual(plan.handoff_json["dispatch"]["executor_hint"], "python_transport_repair_executor")
        self.assertEqual(plan.handoff_json["dispatch"]["executor_contract_version"], 1)
        self.assertEqual(plan.handoff_json["dispatch"]["transport_hint"], "python_subprocess_repair_transport")
        self.assertEqual(plan.handoff_json["dispatch"]["transport_contract_version"], 1)
        self.assertEqual(plan.validation_report_json["scope"], "review_deadlock")
        self.assertIn("review_controller.py", " ".join(plan.handoff_json["owned_files"]))

    def test_plans_export_misrouting_repair_with_export_replay_handoff(self) -> None:
        service = RuntimeRepairPlannerService()

        plan = service.plan_export_misrouting_repair(
            scope_id="export-scope-1",
            export_type="rebuilt_pdf",
            corrected_route="epub.rebuilt_pdf_via_html",
            route_candidates=["epub.rebuilt_pdf_via_html", "pdf.direct"],
            route_evidence_json={"route_fingerprint": "abc123def456", "source_type": "epub"},
        )

        self.assertEqual(plan.patch_surface, "runtime_bundle")
        self.assertEqual(plan.revision_name, "export-routing-fix-abc123def456")
        self.assertEqual(plan.handoff_json["incident_kind"], "export_misrouting")
        self.assertEqual(plan.handoff_json["replay"]["scope_type"], "export")
        self.assertEqual(plan.handoff_json["replay"]["scope_id"], "export-scope-1")
        self.assertEqual(plan.handoff_json["dispatch"]["lane"], "runtime.repair")
        self.assertEqual(plan.handoff_json["dispatch"]["worker_hint"], "export_routing_repair_agent")
        self.assertEqual(plan.handoff_json["dispatch"]["worker_contract_version"], 1)
        self.assertEqual(plan.handoff_json["dispatch"]["execution_mode"], "transport_backed")
        self.assertEqual(plan.handoff_json["dispatch"]["executor_hint"], "python_transport_repair_executor")
        self.assertEqual(plan.handoff_json["dispatch"]["executor_contract_version"], 1)
        self.assertEqual(plan.handoff_json["dispatch"]["transport_hint"], "python_subprocess_repair_transport")
        self.assertEqual(plan.handoff_json["dispatch"]["transport_contract_version"], 1)
        self.assertEqual(plan.validation_report_json["scope"], "export_misrouting")
        self.assertIn("export_routing.py", " ".join(plan.handoff_json["owned_files"]))

    def test_plans_review_deadlock_repair_with_transport_override(self) -> None:
        service = RuntimeRepairPlannerService()

        plan = service.plan_review_deadlock_repair(
            chapter_id="chapter-1234567890ab",
            chapter_run_id="chapter-run-1",
            review_session_id="review-session-1",
            reason_code="review_failed_without_terminality",
            transport_hint="configured_command_repair_transport",
            transport_contract_version=1,
        )

        self.assertEqual(
            plan.handoff_json["dispatch"]["transport_hint"],
            "configured_command_repair_transport",
        )
        self.assertEqual(plan.handoff_json["dispatch"]["transport_contract_version"], 1)

    def test_plans_review_deadlock_repair_with_executor_override(self) -> None:
        service = RuntimeRepairPlannerService()

        plan = service.plan_review_deadlock_repair(
            chapter_id="chapter-1234567890ab",
            chapter_run_id="chapter-run-1",
            review_session_id="review-session-1",
            reason_code="review_failed_without_terminality",
            execution_mode="agent_backed",
            executor_hint="python_subprocess_repair_executor",
            executor_contract_version=1,
        )

        self.assertEqual(plan.handoff_json["dispatch"]["execution_mode"], "agent_backed")
        self.assertEqual(
            plan.handoff_json["dispatch"]["executor_hint"],
            "python_subprocess_repair_executor",
        )
        self.assertEqual(plan.handoff_json["dispatch"]["executor_contract_version"], 1)
        self.assertNotIn("transport_hint", plan.handoff_json["dispatch"])

    def test_plans_export_misrouting_repair_with_executor_override(self) -> None:
        service = RuntimeRepairPlannerService()

        plan = service.plan_export_misrouting_repair(
            scope_id="export-scope-1",
            export_type="rebuilt_pdf",
            corrected_route="epub.rebuilt_pdf_via_html",
            route_candidates=["epub.rebuilt_pdf_via_html"],
            route_evidence_json={"route_fingerprint": "abc123def456", "source_type": "epub"},
            execution_mode="agent_backed",
            executor_hint="python_subprocess_repair_executor",
            executor_contract_version=1,
        )

        self.assertEqual(plan.handoff_json["dispatch"]["execution_mode"], "agent_backed")
        self.assertEqual(
            plan.handoff_json["dispatch"]["executor_hint"],
            "python_subprocess_repair_executor",
        )
        self.assertEqual(plan.handoff_json["dispatch"]["executor_contract_version"], 1)
        self.assertNotIn("transport_hint", plan.handoff_json["dispatch"])

    def test_plans_packet_runtime_defect_repair_with_packet_replay_handoff(self) -> None:
        service = RuntimeRepairPlannerService()

        plan = service.plan_packet_runtime_defect_repair(
            packet_id="packet-1234567890ab",
            chapter_id="chapter-1",
            chapter_run_id="chapter-run-1",
            packet_task_id="packet-task-1",
            reason_code="work_item_terminal_failed",
        )

        self.assertEqual(plan.patch_surface, "runtime_bundle")
        self.assertEqual(plan.revision_name, "packet-runtime-fix-packet-12345")
        self.assertEqual(plan.handoff_json["incident_kind"], "packet_runtime_defect")
        self.assertEqual(plan.handoff_json["replay"]["scope_type"], "packet")
        self.assertEqual(plan.handoff_json["replay"]["scope_id"], "packet-1234567890ab")
        self.assertEqual(plan.handoff_json["replay"]["boundary"], "packet")
        self.assertEqual(plan.handoff_json["dispatch"]["worker_hint"], "packet_runtime_defect_repair_agent")
        self.assertEqual(plan.handoff_json["dispatch"]["execution_mode"], "transport_backed")
        self.assertEqual(plan.validation_report_json["scope"], "packet_runtime_defect")
        self.assertIn("packet_controller.py", " ".join(plan.handoff_json["owned_files"]))

    def test_plans_packet_runtime_defect_repair_with_transport_override(self) -> None:
        service = RuntimeRepairPlannerService()

        plan = service.plan_packet_runtime_defect_repair(
            packet_id="packet-1234567890ab",
            chapter_id="chapter-1",
            chapter_run_id="chapter-run-1",
            packet_task_id="packet-task-1",
            reason_code="work_item_terminal_failed",
            transport_hint="configured_command_repair_transport",
            transport_contract_version=1,
        )

        self.assertEqual(
            plan.handoff_json["dispatch"]["transport_hint"],
            "configured_command_repair_transport",
        )
        self.assertEqual(plan.handoff_json["dispatch"]["transport_contract_version"], 1)

    def test_plans_packet_runtime_defect_repair_with_executor_override(self) -> None:
        service = RuntimeRepairPlannerService()

        plan = service.plan_packet_runtime_defect_repair(
            packet_id="packet-1234567890ab",
            chapter_id="chapter-1",
            chapter_run_id="chapter-run-1",
            packet_task_id="packet-task-1",
            reason_code="work_item_terminal_failed",
            execution_mode="agent_backed",
            executor_hint="python_subprocess_repair_executor",
            executor_contract_version=1,
        )

        self.assertEqual(plan.handoff_json["dispatch"]["execution_mode"], "agent_backed")
        self.assertEqual(
            plan.handoff_json["dispatch"]["executor_hint"],
            "python_subprocess_repair_executor",
        )
        self.assertEqual(plan.handoff_json["dispatch"]["executor_contract_version"], 1)
        self.assertNotIn("transport_hint", plan.handoff_json["dispatch"])


if __name__ == "__main__":
    unittest.main()
