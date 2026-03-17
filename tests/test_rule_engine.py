import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import ActionType, RootCauseLayer
from book_agent.orchestrator.rule_engine import IssueRoutingContext, resolve_action


class RuleEngineTests(unittest.TestCase):
    def test_context_failure_routes_to_rebuild_chapter_brief(self) -> None:
        action = resolve_action(
            IssueRoutingContext(
                issue_type="CONTEXT_FAILURE",
                root_cause_layer=RootCauseLayer.MEMORY,
            )
        )
        self.assertEqual(action, ActionType.REBUILD_CHAPTER_BRIEF)

    def test_packet_context_failure_routes_to_packet_rebuild(self) -> None:
        action = resolve_action(
            IssueRoutingContext(
                issue_type="CONTEXT_FAILURE",
                root_cause_layer=RootCauseLayer.PACKET,
            )
        )
        self.assertEqual(action, ActionType.REBUILD_PACKET_THEN_RERUN)

    def test_style_drift_routes_to_rebuild_chapter_brief(self) -> None:
        action = resolve_action(
            IssueRoutingContext(
                issue_type="STYLE_DRIFT",
                root_cause_layer=RootCauseLayer.MEMORY,
            )
        )
        self.assertEqual(action, ActionType.REBUILD_CHAPTER_BRIEF)

    def test_packet_style_drift_routes_to_rerun_packet(self) -> None:
        action = resolve_action(
            IssueRoutingContext(
                issue_type="STYLE_DRIFT",
                root_cause_layer=RootCauseLayer.PACKET,
            )
        )
        self.assertEqual(action, ActionType.RERUN_PACKET)

    def test_stale_chapter_brief_routes_to_rebuild_chapter_brief(self) -> None:
        action = resolve_action(
            IssueRoutingContext(
                issue_type="STALE_CHAPTER_BRIEF",
                root_cause_layer=RootCauseLayer.MEMORY,
            )
        )
        self.assertEqual(action, ActionType.REBUILD_CHAPTER_BRIEF)

    def test_reference_mistranslation_stays_packet_scoped(self) -> None:
        action = resolve_action(
            IssueRoutingContext(
                issue_type="MISTRANSLATION_REFERENCE",
                root_cause_layer=RootCauseLayer.TRANSLATION,
            )
        )
        self.assertEqual(action, ActionType.REBUILD_PACKET_THEN_RERUN)

    def test_packet_duplication_routes_to_packet_rebuild(self) -> None:
        action = resolve_action(
            IssueRoutingContext(
                issue_type="DUPLICATION",
                root_cause_layer=RootCauseLayer.PACKET,
            )
        )
        self.assertEqual(action, ActionType.REBUILD_PACKET_THEN_RERUN)

    def test_export_duplication_routes_to_reexport_only(self) -> None:
        action = resolve_action(
            IssueRoutingContext(
                issue_type="DUPLICATION",
                root_cause_layer=RootCauseLayer.EXPORT,
            )
        )
        self.assertEqual(action, ActionType.REEXPORT_ONLY)

    def test_alignment_failure_routes_to_realign_only(self) -> None:
        action = resolve_action(
            IssueRoutingContext(
                issue_type="ALIGNMENT_FAILURE",
                root_cause_layer=RootCauseLayer.ALIGNMENT,
                translation_content_ok=True,
            )
        )
        self.assertEqual(action, ActionType.REALIGN_ONLY)


if __name__ == "__main__":
    unittest.main()
