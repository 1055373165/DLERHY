"""Commit policy of the typed chapter translation memory."""

import unittest

from book_agent.translation.chapter_memory import RECENT_TRANSLATION_LIMIT, ChapterMemory


class ChapterMemoryTests(unittest.TestCase):
    def test_round_trips_payload_and_tolerates_bad_lists(self) -> None:
        memory = ChapterMemory.from_content(
            {"chapter_id": "c1", "active_concepts": "oops", "recent_accepted_translations": [1, {"packet_id": "p"}], "legacy": 7}
        )

        content = memory.to_content()
        self.assertEqual(content["active_concepts"], [])
        self.assertEqual(content["recent_accepted_translations"], [{"packet_id": "p"}])
        self.assertEqual(content["legacy"], 7)
        self.assertEqual(content["schema_version"], 1)

    def test_newer_or_equal_brief_wins_and_missing_brief_is_filled(self) -> None:
        memory = ChapterMemory(chapter_id="c1", chapter_brief="old", chapter_brief_version=3, heading_path=["A"])

        self.assertEqual(memory.with_brief("older", version=2, heading_path=["B"]).chapter_brief, "old")
        adopted = memory.with_brief("new", version=3, heading_path=["B"])
        self.assertEqual((adopted.chapter_brief, adopted.chapter_brief_version, adopted.heading_path), ("new", 3, ["B"]))
        empty = ChapterMemory(chapter_id="c1", chapter_brief_version=5)
        self.assertEqual(empty.with_brief("first", version=1, heading_path=[]).chapter_brief, "first")

    def test_recent_translations_are_keyed_by_packet_and_capped(self) -> None:
        memory = ChapterMemory(chapter_id="c1")
        for index in range(RECENT_TRANSLATION_LIMIT + 2):
            memory = memory.with_recent_translation({"packet_id": f"p{index}", "target_excerpt": "x"})
        memory = memory.with_recent_translation({"packet_id": "p5", "target_excerpt": "updated"})

        packet_ids = [item["packet_id"] for item in memory.recent_accepted_translations]
        self.assertEqual(len(packet_ids), RECENT_TRANSLATION_LIMIT)
        self.assertEqual(packet_ids[-1], "p5")
        self.assertEqual(memory.recent_translation_for_packet("p5")["target_excerpt"], "updated")

    def test_approved_proposal_merges_concepts_translation_and_run_ids(self) -> None:
        base = ChapterMemory(
            chapter_id="c1",
            active_concepts=[{"source_term": "Agent", "times_seen": 4, "canonical_zh": "智能体"}],
            recent_accepted_translations=[{"packet_id": "p1"}],
        )
        proposal = ChapterMemory(
            chapter_id="c1",
            chapter_title="Chapter",
            active_concepts=[{"source_term": "agent", "times_seen": 2, "canonical_zh": None}, {"source_term": "Memory"}],
            recent_accepted_translations=[{"packet_id": "p2", "target_excerpt": "译文"}],
        )

        merged = base.merge_approved_proposal(proposal, packet_id="p2", translation_run_id="run-2")

        self.assertEqual(merged.chapter_title, "Chapter")
        self.assertEqual([item["packet_id"] for item in merged.recent_accepted_translations], ["p1", "p2"])
        self.assertEqual(merged.active_concepts[0], {"source_term": "agent", "times_seen": 4, "canonical_zh": "智能体"})
        self.assertEqual(merged.active_concepts[1]["source_term"], "Memory")
        self.assertEqual((merged.last_packet_id, merged.last_translation_run_id), ("p2", "run-2"))


if __name__ == "__main__":
    unittest.main()
