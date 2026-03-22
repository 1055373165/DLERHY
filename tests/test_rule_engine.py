# ruff: noqa: E402

import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import ActionActorType, ActionStatus, ActionType, Detector, IssueStatus, JobScopeType, RootCauseLayer, Severity
from book_agent.domain.models.review import IssueAction, ReviewIssue
from book_agent.orchestrator.rerun import build_rerun_plan
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

    def test_alignment_failure_routes_to_rerun_when_realign_would_be_lossy(self) -> None:
        action = resolve_action(
            IssueRoutingContext(
                issue_type="ALIGNMENT_FAILURE",
                root_cause_layer=RootCauseLayer.ALIGNMENT,
                translation_content_ok=True,
                requires_packet_rerun=True,
            )
        )
        self.assertEqual(action, ActionType.RERUN_PACKET)

    def test_build_rerun_plan_includes_locked_term_override_for_term_conflict(self) -> None:
        now = datetime.now(timezone.utc)
        issue = ReviewIssue(
            id="issue-1",
            document_id="doc-1",
            chapter_id="chap-1",
            block_id=None,
            sentence_id="sent-1",
            packet_id="pkt-1",
            issue_type="TERM_CONFLICT",
            root_cause_layer=RootCauseLayer.MEMORY,
            severity=Severity.HIGH,
            blocking=True,
            detector=Detector.RULE,
            confidence=1.0,
            evidence_json={
                "source_term": "agentic AI",
                "expected_target_term": "智能体式AI",
                "actual_target_text": "智能体AI通过吸收反馈持续改进。",
            },
            status=IssueStatus.OPEN,
            created_at=now,
            updated_at=now,
        )
        action = IssueAction(
            id="action-1",
            issue_id=issue.id,
            action_type=ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED,
            scope_type=JobScopeType.CHAPTER,
            scope_id="chap-1",
            status=ActionStatus.PLANNED,
            reason_json={},
            created_by=ActionActorType.SYSTEM,
            created_at=now,
            updated_at=now,
        )

        plan = build_rerun_plan(issue, action)

        self.assertEqual(len(plan.concept_overrides), 1)
        self.assertEqual(plan.concept_overrides[0].source_term, "agentic AI")
        self.assertEqual(plan.concept_overrides[0].canonical_zh, "智能体AI")
        self.assertEqual(plan.concept_overrides[0].status, "locked")

    def test_build_rerun_plan_includes_style_hint_for_style_drift(self) -> None:
        now = datetime.now(timezone.utc)
        issue = ReviewIssue(
            id="issue-2",
            document_id="doc-1",
            chapter_id="chap-1",
            block_id=None,
            sentence_id="sent-2",
            packet_id="pkt-2",
            issue_type="STYLE_DRIFT",
            root_cause_layer=RootCauseLayer.PACKET,
            severity=Severity.MEDIUM,
            blocking=False,
            detector=Detector.RULE,
            confidence=1.0,
            evidence_json={
                "style_rule": "contextually_accurate_outputs_literal",
                "preferred_hint": "更符合上下文的输出",
                "matched_target_excerpt": "上下文更准确的输出",
                "prompt_guidance": (
                    "Prefer '更符合上下文的输出' or an equally natural Chinese expression, "
                    "not literal forms like '上下文更准确的输出'."
                ),
            },
            status=IssueStatus.OPEN,
            created_at=now,
            updated_at=now,
        )
        action = IssueAction(
            id="action-2",
            issue_id=issue.id,
            action_type=ActionType.RERUN_PACKET,
            scope_type=JobScopeType.PACKET,
            scope_id="pkt-2",
            status=ActionStatus.PLANNED,
            reason_json={},
            created_by=ActionActorType.SYSTEM,
            created_at=now,
            updated_at=now,
        )

        plan = build_rerun_plan(issue, action)

        self.assertEqual(plan.concept_overrides, ())
        self.assertEqual(len(plan.style_hints), 3)
        self.assertTrue(any("更符合上下文的输出" in hint for hint in plan.style_hints))
        self.assertTrue(any("contextually_accurate_outputs_literal" in hint for hint in plan.style_hints))
        self.assertTrue(any("上下文更准确的输出" in hint for hint in plan.style_hints))

    def test_build_rerun_plan_projects_stale_chapter_brief_to_packet_scope_when_packet_ids_present(self) -> None:
        now = datetime.now(timezone.utc)
        issue = ReviewIssue(
            id="issue-stale-1",
            document_id="doc-1",
            chapter_id="chap-1",
            block_id=None,
            sentence_id="sent-1",
            packet_id=None,
            issue_type="STALE_CHAPTER_BRIEF",
            root_cause_layer=RootCauseLayer.MEMORY,
            severity=Severity.LOW,
            blocking=False,
            detector=Detector.RULE,
            confidence=1.0,
            evidence_json={
                "missing_concepts": ["adaptive agent"],
                "packet_ids_seen": ["pkt-2", "pkt-4"],
                "chapter_brief_summary": "A recipe book offers instructions for many meals.",
            },
            status=IssueStatus.OPEN,
            created_at=now,
            updated_at=now,
        )
        action = IssueAction(
            id="action-stale-1",
            issue_id=issue.id,
            action_type=ActionType.REBUILD_CHAPTER_BRIEF,
            scope_type=JobScopeType.CHAPTER,
            scope_id="chap-1",
            status=ActionStatus.PLANNED,
            reason_json={},
            created_by=ActionActorType.SYSTEM,
            created_at=now,
            updated_at=now,
        )

        plan = build_rerun_plan(issue, action)

        self.assertEqual(plan.scope_type, JobScopeType.PACKET)
        self.assertEqual(plan.scope_ids, ["pkt-2", "pkt-4"])


if __name__ == "__main__":
    unittest.main()
