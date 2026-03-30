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
        self.assertEqual(plan.validation_report_json["scope"], "export_misrouting")
        self.assertIn("export_routing.py", " ".join(plan.handoff_json["owned_files"]))


if __name__ == "__main__":
    unittest.main()
