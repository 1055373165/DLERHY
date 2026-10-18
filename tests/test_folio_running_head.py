"""A section's running head with its folio is page furniture, not text.

How Large Language Models Work sets "PREFACE" and "xi" on one baseline at the top of the page; the
text layer hands them over as two lines, which the one-line running-head rule refused. The block
stayed body text: the reading copy showed "前言 xi……" in the middle of the preface, and the
paragraph the page break cut was never joined again.
"""

from __future__ import annotations

import unittest

from book_agent.domain.enums import BlockType
from types import SimpleNamespace

from book_agent.domain.structure.pdf import _FOLIO_RUNNING_HEAD, PdfStructureRecoveryService, _is_section_running_head
from book_agent.ingestion.pdf.models import _RecoveredBlock


def _matches(text: str) -> bool:
    return bool(_FOLIO_RUNNING_HEAD.fullmatch(" ".join(text.split())))


class FolioRunningHeadTests(unittest.TestCase):
    def test_a_section_name_beside_its_folio_is_a_running_head(self) -> None:
        for text in ("PREFACE\nxi", "xviii ABOUT THE AUTHORS", "164 REFERENCES", "ACKNOWLEDGMENTS xiii", "CONTENTS vii"):
            self.assertTrue(_matches(text), text)

    def test_chapter_labels_and_title_case_headings_are_not(self) -> None:
        for text in ("CHAPTER 3", "PART 2", "Preface", "4.4 If LLMs cannot extrapolate well", "Figure 6.2", "LLM 2"):
            self.assertFalse(_matches(text), text)



def _body(text: str, page: int) -> _RecoveredBlock:
    return _RecoveredBlock(
        role="body", block_type=BlockType.PARAGRAPH, text=text, page_start=page, page_end=page, bbox_regions=[],
        reading_order_index=page, parse_confidence=1.0, flags=[], font_size_avg=10.0, source_path="book.pdf", anchor=f"p{page}",
    )


class NameListAcrossPagesTests(unittest.TestCase):
    """The acknowledgments' reviewer list broke between "Florian" and "Braun," at the page break."""

    def _merges(self, previous: str, current: str) -> bool:
        return PdfStructureRecoveryService()._should_merge_cross_page_prose_continuation(_body(previous, 14), _body(current, 15), [])

    def test_a_list_of_names_runs_on_across_the_page(self) -> None:
        self.assertTrue(self._merges("To all the reviewers: Doug Puenner, Doyle Turner, Emanuele Piccinelli, Federico Grasso, Florian", "Braun, Georg Sommer, George Onofrei, thank you."))
        self.assertTrue(self._merges("We thank Doug Puenner, Doyle Turner and,", "Federico Grasso for their help."))

    def test_a_page_ending_on_an_article_runs_on_into_a_capital(self) -> None:
        # "How this book is organized" broke at "the process of training an" / "LLM on incredible amounts".
        self.assertTrue(self._merges("Chapter 4 discusses how all of this is possible, the process of training an", "LLM on incredible amounts of text."))

    def test_a_quotation_opening_the_next_page_goes_on(self) -> None:
        self.assertTrue(self._merges("training on older data, and the model begins to", "“forget” that older information."))
        self.assertTrue(self._merges("always interesting and full of caveats such as", "“How many examples are in the training data?”"))

    def test_a_sidebar_continued_label_is_no_new_paragraph(self) -> None:
        self.assertTrue(self._merges("the likely answer is that such certainty", "(continued)\ndoes not yet exist for LLMs."))

    def test_a_new_sentence_after_a_cut_still_stands_alone(self) -> None:
        self.assertFalse(self._merges("The model reads the prompt and the context window", "Transformers, however, read it whole."))



class SectionRunningHeadTests(unittest.TestCase):
    """"1.7 Why LLMs perform so well" / "11" atop a page with a figure: a second 1.7 heading and a stray "11"."""

    def _is_head(self, text: str, size: float = 9.0, median: float = 10.0) -> bool:
        return _is_section_running_head(SimpleNamespace(font_size_avg=size), text, median)

    def test_a_section_title_with_its_folio_is_a_running_head(self) -> None:
        self.assertTrue(self._is_head("1.7 Why LLMs perform so well\n11", size=9.0, median=7.0))
        self.assertTrue(self._is_head("70 5.1 Why do we want to constrain behavior?"))

    def test_the_section_heading_itself_is_not(self) -> None:
        self.assertFalse(self._is_head("1.7\nWhy LLMs perform so well", size=11.0))
        self.assertFalse(self._is_head("2.3 Top 10 tips", size=11.0))


if __name__ == "__main__":
    unittest.main()
