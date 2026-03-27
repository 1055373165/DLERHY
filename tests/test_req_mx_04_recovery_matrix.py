import unittest

from book_agent.domain.enums import RootCauseLayer
from book_agent.services.recovery_matrix import RecoveryMatrixService


class ReqMx04RecoveryMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = RecoveryMatrixService()

    def test_req_mx_04_representative_failure_families_have_bounded_decisions(self) -> None:
        cases = [
            (
                "translate_content",
                RootCauseLayer.TRANSLATION,
                "mistranslation_content",
                1,
                1,
                {
                    "recommended_action": "rerun_packet",
                    "replay_scope": "packet",
                    "next_boundary": "packet",
                    "should_retry": True,
                    "open_incident": False,
                    "escalate_scope": False,
                },
            ),
            (
                "review_content_verdict",
                RootCauseLayer.REVIEW,
                "blocking_verdict",
                1,
                1,
                {
                    "recommended_action": "replay_review_session",
                    "replay_scope": "review_session",
                    "next_boundary": "review_session",
                    "should_retry": True,
                    "open_incident": False,
                    "escalate_scope": False,
                },
            ),
            (
                "review_runtime_deadlock",
                RootCauseLayer.REVIEW,
                "non_terminal_closure",
                2,
                2,
                {
                    "recommended_action": "chapter_hold",
                    "replay_scope": "review_session",
                    "next_boundary": "chapter",
                    "should_retry": False,
                    "open_incident": True,
                    "escalate_scope": True,
                },
            ),
            (
                "export_routing_defect",
                RootCauseLayer.EXPORT,
                "routing_misroute",
                1,
                1,
                {
                    "recommended_action": "reexport_scope",
                    "replay_scope": "export_scope",
                    "next_boundary": "export_scope",
                    "should_retry": True,
                    "open_incident": True,
                    "escalate_scope": False,
                },
            ),
            (
                "ops_worker_crash",
                RootCauseLayer.OPS,
                "worker_crash",
                1,
                1,
                {
                    "recommended_action": "freeze_and_rollback_bundle",
                    "replay_scope": "failed_scope",
                    "next_boundary": "runtime_bundle",
                    "should_retry": False,
                    "open_incident": True,
                    "escalate_scope": True,
                },
            ),
        ]

        for label, family, signal, attempt_count, fingerprint_occurrences, expected in cases:
            with self.subTest(case=label):
                decision = self.service.evaluate(
                    family,
                    signal=signal,
                    attempt_count=attempt_count,
                    fingerprint_occurrences=fingerprint_occurrences,
                )
                self.assertEqual(decision.failure_family, family)
                self.assertEqual(decision.source_signal, signal)
                self.assertEqual(decision.recommended_action, expected["recommended_action"])
                self.assertEqual(decision.replay_scope, expected["replay_scope"])
                self.assertEqual(decision.next_boundary, expected["next_boundary"])
                self.assertEqual(decision.should_retry, expected["should_retry"])
                self.assertEqual(decision.open_incident, expected["open_incident"])
                self.assertEqual(decision.escalate_scope, expected["escalate_scope"])
                self.assertGreaterEqual(decision.retry_cap, 1)
                self.assertGreaterEqual(decision.incident_threshold, 1)
                self.assertTrue(decision.escalation_boundary)

    def test_req_mx_04_unknown_family_is_rejected_explicitly(self) -> None:
        with self.assertRaises(ValueError):
            self.service.evaluate(
                "unknown_family",
                signal="mystery_failure",
                attempt_count=1,
                fingerprint_occurrences=1,
            )
