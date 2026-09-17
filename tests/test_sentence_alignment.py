"""Pure alignment of retired and re-segmented sentences."""

import unittest

from book_agent.domain.structure.sentence_alignment import align_sentences


def _relations(links):
    return [(link.from_id, link.to_id, link.relation) for link in links]


class SentenceAlignmentTests(unittest.TestCase):
    def test_identical_text_is_same_regardless_of_spacing_and_case(self) -> None:
        links = align_sentences([("o1", "Momentum  matters."), ("o2", "Trends persist.")], [("n1", "momentum matters."), ("n2", "Trends persist.")])
        self.assertEqual(_relations(links), [("o1", "n1", "same"), ("o2", "n2", "same")])

    def test_near_identical_text_is_same_with_a_similarity(self) -> None:
        links = align_sentences([("o1", "The relative strength index measures momentum.")], [("n1", "The relative strength index measure momentum.")])
        self.assertEqual(links[0].relation, "same")
        self.assertGreaterEqual(links[0].similarity, 0.92)

    def test_split_merge_and_removed(self) -> None:
        split = align_sentences([("o1", "Buy low. Sell high.")], [("n1", "Buy low."), ("n2", "Sell high.")])
        self.assertEqual(_relations(split), [("o1", "n1", "split"), ("o1", "n2", "split")])
        merge = align_sentences([("o1", "Buy low"), ("o2", "sell high.")], [("n1", "Buy low, sell high.")])
        self.assertEqual([link.relation for link in merge], ["merge", "merge"])
        removed = align_sentences([("o1", "A footnote that vanished.")], [("n1", "Something else entirely.")])
        self.assertEqual(_relations(removed), [("o1", None, "removed")])

    def test_each_new_sentence_is_used_once(self) -> None:
        links = align_sentences([("o1", "Repeat."), ("o2", "Repeat.")], [("n1", "Repeat.")])
        self.assertEqual(links[0].to_id, "n1")
        self.assertNotEqual(links[1].relation, "same")


if __name__ == "__main__":
    unittest.main()
