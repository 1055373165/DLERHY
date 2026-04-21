# ruff: noqa: E402
"""Regression pins for the TranslateFrontierPlan DECIDE contract (P0.1c).

Two things these tests guarantee:

1. ``TranslateFrontierPlan`` is frozen — a caller cannot mutate the
   planner's decision after the fact. Any code that wants to change
   the seed list has to construct a new plan, not poke attributes.
2. ``is_empty`` reflects ``packet_ids`` so the executor's
   short-circuit (``if plan.is_empty: return []``) stays correct.

The actual frontier-picking logic is exercised end-to-end by
``tests/test_run_execution.py::test_seedable_translate_packet_ids_only_advance_frontier_for_unblocked_chapter`` —
this file is intentionally narrow: it pins the data contract, not
the decision rules.
"""

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.orchestrator.frontier_plan import TranslateFrontierPlan


class TranslateFrontierPlanTests(unittest.TestCase):
    def test_plan_is_frozen(self) -> None:
        plan = TranslateFrontierPlan(packet_ids=["p1"])
        with self.assertRaises(FrozenInstanceError):
            plan.packet_ids = ["p2"]  # type: ignore[misc]

    def test_is_empty_tracks_packet_ids(self) -> None:
        self.assertTrue(TranslateFrontierPlan(packet_ids=[]).is_empty)
        self.assertFalse(TranslateFrontierPlan(packet_ids=["p1"]).is_empty)

    def test_default_provenance_fields_are_empty_frozensets(self) -> None:
        plan = TranslateFrontierPlan(packet_ids=["p1"])
        self.assertEqual(plan.blocked_chapter_ids, frozenset())
        self.assertEqual(plan.represented_packet_ids, frozenset())

    def test_carries_planner_reasoning(self) -> None:
        plan = TranslateFrontierPlan(
            packet_ids=["p2"],
            blocked_chapter_ids=frozenset({"ch-1"}),
            represented_packet_ids=frozenset({"p1"}),
        )
        self.assertIn("ch-1", plan.blocked_chapter_ids)
        self.assertIn("p1", plan.represented_packet_ids)


if __name__ == "__main__":
    unittest.main()
