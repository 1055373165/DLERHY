"""A section's running head with its folio is page furniture, not text.

How Large Language Models Work sets "PREFACE" and "xi" on one baseline at the top of the page; the
text layer hands them over as two lines, which the one-line running-head rule refused. The block
stayed body text: the reading copy showed "前言 xi……" in the middle of the preface, and the
paragraph the page break cut was never joined again.
"""

from __future__ import annotations

import unittest

from book_agent.domain.structure.pdf import _FOLIO_RUNNING_HEAD


def _matches(text: str) -> bool:
    return bool(_FOLIO_RUNNING_HEAD.fullmatch(" ".join(text.split())))


class FolioRunningHeadTests(unittest.TestCase):
    def test_a_section_name_beside_its_folio_is_a_running_head(self) -> None:
        for text in ("PREFACE\nxi", "xviii ABOUT THE AUTHORS", "164 REFERENCES", "ACKNOWLEDGMENTS xiii", "CONTENTS vii"):
            self.assertTrue(_matches(text), text)

    def test_chapter_labels_and_title_case_headings_are_not(self) -> None:
        for text in ("CHAPTER 3", "PART 2", "Preface", "4.4 If LLMs cannot extrapolate well", "Figure 6.2", "LLM 2"):
            self.assertFalse(_matches(text), text)


if __name__ == "__main__":
    unittest.main()
