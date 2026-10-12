"""Regression tests for outline-driven chapter detection in pdf.py.

The PDF "How Large Language Models Work" exposed a bug where Manning-
style TOC entries ("1 Big picture: What are LLMs?", "2 Tokenizers...")
were not recognised as primary chapter titles, because the parser
required the literal word "Chapter" before the number. The body of
that book ended up lumped under one "acknowledgments" pseudo-chapter.

These tests pin the fix:
  - "Chapter N Title..." form continues to work (no regression)
  - "N Title..." bare-integer form is now recognised
  - Section-style numbering ("1.1", "2.2.1") is rejected (subsections
    must NOT be promoted to top-level chapters)
  - Frontmatter / appendix / glossary detection is unchanged
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from book_agent.ingestion.pdf.classify import (
    _extract_book_main_chapter_number,
    _looks_like_book_primary_outline_title,
    _should_keep_book_top_level_outline_title,
    _should_start_outlined_book_top_level_chapter,
)


# ---- "Chapter N" form must keep working --------------------------------

def test_chapter_label_form_extracts_number():
    assert _extract_book_main_chapter_number("Chapter 1 Big picture: What are LLMs?") == 1
    assert _extract_book_main_chapter_number("CHAPTER 5: Tokenizers") == 5
    assert _extract_book_main_chapter_number("chapter 12 - Foundations") == 12


def test_chapter_label_form_starts_top_level_chapter():
    assert _should_start_outlined_book_top_level_chapter("Chapter 1 Big picture")


# ---- Bare-integer form (the bug we just fixed) -------------------------

def test_bare_integer_form_extracts_number():
    """The headline fix: Manning-style TOC entries 'N Title...'."""
    assert _extract_book_main_chapter_number("1 Big picture: What are LLMs?") == 1
    assert _extract_book_main_chapter_number("2 Tokenizers: How large language models see the world") == 2
    assert _extract_book_main_chapter_number("3 Transformers: How inputs become outputs") == 3
    assert _extract_book_main_chapter_number("12 Conclusion") == 12


def test_bare_integer_form_starts_top_level_chapter():
    assert _should_start_outlined_book_top_level_chapter("1 Big picture: What are LLMs?")
    assert _should_start_outlined_book_top_level_chapter("2 Tokenizers")


def test_bare_integer_form_marks_as_primary_outline_title():
    assert _looks_like_book_primary_outline_title("1 Big picture: What are LLMs?")
    assert _looks_like_book_primary_outline_title("2 Tokenizers")


# ---- Section-style numbering must NOT be treated as a chapter ----------

def test_dotted_section_numbering_is_rejected():
    """Subsections like '1.1 ...' are not chapters and must remain so."""
    assert _extract_book_main_chapter_number("1.1 Generative AI in context") is None
    assert _extract_book_main_chapter_number("2.2.1 The tokenization process") is None
    assert _extract_book_main_chapter_number("3.4.5.6 Deep nesting") is None


def test_dotted_section_numbering_is_not_primary_outline_title():
    assert not _looks_like_book_primary_outline_title("1.1 Generative AI in context")
    assert not _looks_like_book_primary_outline_title("2.2.1 The tokenization process")


def test_dotted_section_numbering_does_not_start_top_level_chapter():
    assert not _should_start_outlined_book_top_level_chapter("1.1 Generative AI in context")
    assert not _should_start_outlined_book_top_level_chapter("2.2.1 The tokenization process")


# ---- Edge cases that must remain rejected ------------------------------

def test_lowercase_title_after_number_is_rejected():
    """A lowercase first word indicates a frontmatter-ish entry, not a
    primary chapter."""
    assert _extract_book_main_chapter_number("1 introduction") is None
    assert _extract_book_main_chapter_number("2 acknowledgments") is None


def test_number_without_following_title_is_rejected():
    assert _extract_book_main_chapter_number("1.") is None
    assert _extract_book_main_chapter_number("1") is None
    assert _extract_book_main_chapter_number("1 ") is None
    assert _extract_book_main_chapter_number("") is None


def test_ordinal_form_is_rejected():
    """1st, 2nd, 3rd are not chapter prefixes — no whitespace after digits."""
    assert _extract_book_main_chapter_number("1st edition") is None
    assert _extract_book_main_chapter_number("2nd printing") is None


# ---- Frontmatter / appendix / glossary unchanged -----------------------

def test_frontmatter_titles_unchanged():
    """Existing frontmatter detection must not regress."""
    assert _should_start_outlined_book_top_level_chapter("preface")
    assert _should_start_outlined_book_top_level_chapter("acknowledgments")
    assert _should_start_outlined_book_top_level_chapter("foreword")


def test_subtitled_frontmatter_titles_start_chapter():
    """Outline titles like "Introduction: Why RSI is So Magical?" were folded into Front Matter."""
    assert _should_keep_book_top_level_outline_title("Introduction: Why RSI is So Magical?")
    assert _should_start_outlined_book_top_level_chapter("Introduction: Why RSI is So Magical?")
    assert _should_start_outlined_book_top_level_chapter("Preface \u2013 About This Book")
    assert not _should_start_outlined_book_top_level_chapter("Introduction to Machine Learning")


def test_appendix_unchanged():
    assert _should_start_outlined_book_top_level_chapter("Appendix A")
    assert _should_start_outlined_book_top_level_chapter("Appendix B: Definitions")


def test_glossary_unchanged():
    assert _should_start_outlined_book_top_level_chapter("Glossary")


def test_front_matter_pages_and_back_matter_start_their_own_sections():
    """How Large Language Models Work: "about this book", "about the authors" and "about the cover
    illustration" were folded into the acknowledgments, and "References" and "index" into chapter
    9, which ran on to page 200 (parse health: front matter sections, chapters vs bookmarks)."""
    for title in ("about this book", "about the authors", "about the cover illustration", "References", "index"):
        assert _should_start_outlined_book_top_level_chapter(title), title
    assert not _should_start_outlined_book_top_level_chapter("Summary")


def test_back_matter_after_the_last_chapter_is_not_folded_into_it():
    from book_agent.ingestion.pdf.chapters import merge_outlined_book_auxiliary_chapters
    from book_agent.domain.structure.models import ParsedChapter

    def chapter(title: str) -> ParsedChapter:
        return ParsedChapter(chapter_id=title, href="pdf://page/1", title=title, blocks=[], metadata={})

    titles = ["Front Matter", "preface", "acknowledgments", "about this book", "about the authors",
              "1 Big picture: What are LLMs?", "9 Ethics of building and using LLMs", "References", "index", "Summary"]
    merged = merge_outlined_book_auxiliary_chapters([chapter(title) for title in titles])
    assert [item.title for item in merged] == titles[:-1]
    assert merged[-1].metadata["pdf_collapsed_auxiliary_titles"] == ["Summary"]
    # A chapter's own references, before the next chapter, stay in it.
    per_chapter = merge_outlined_book_auxiliary_chapters(
        [chapter(title) for title in ["1 Durable Products", "References", "2 Learning Systems", "References", "index"]]
    )
    assert [item.title for item in per_chapter] == ["1 Durable Products", "2 Learning Systems", "References", "index"]


# ---- Sanity: empty / whitespace-only inputs ----------------------------

def test_empty_inputs_safe():
    assert _extract_book_main_chapter_number("") is None
    assert _extract_book_main_chapter_number("   ") is None
    assert _extract_book_main_chapter_number(None or "") is None
    assert not _looks_like_book_primary_outline_title("")


# ---- Bookmarks decide where a book's chapters start --------------------

def test_section_family_pages_inside_bookmarked_chapters_do_not_split_them():
    """The Art of AI Product Development: an appendix's subsections, a "7.3.2" heading read as
    references and a further-reading list's "Chapter 4" entries split the bookmarked chapters."""
    from book_agent.ingestion.pdf.chapters import section_candidates_outside_outline
    from book_agent.ingestion.pdf.models import _ChapterStartCandidate as Start

    outline = [
        Start(26, "1 Creating value with AI-driven products", "outline"),
        Start(157, "7 Search and retrieval-augmented generation", "outline"),
        Start(324, "Appendix AI development toolbox", "outline"),
        Start(360, "index", "outline"),
    ]
    sections = [
        Start(3, "Front Matter", "section_family", "frontmatter"),
        Start(175, "7.3.2 Evaluating your RAG system", "section_family", "references"),
        Start(324, "appendix AI development toolbox", "section_family", "appendix"),
        Start(324, "A.2.1 Sourcing AI ideas", "section_family", "appendix"),
        Start(358, "Chapter 4", "section_family", "references"),
        Start(368, "Back Matter", "section_family", "backmatter"),
    ]
    # Inside the bookmarks nothing starts; before the first one front matter may; after the index nothing.
    assert [s.title for s in section_candidates_outside_outline(sections, outline)] == ["Front Matter"]
    # Bookmarks ending at the last chapter leave the back matter after it to the section families.
    open_tail = outline[:2]
    assert [s.title for s in section_candidates_outside_outline(sections, open_tail)] == [
        "Front Matter", "7.3.2 Evaluating your RAG system", "appendix AI development toolbox",
        "A.2.1 Sourcing AI ideas", "Chapter 4", "Back Matter",
    ]


# ---- The top of a page holds its running head, not footnotes --------------

def test_a_folio_with_a_section_title_at_the_top_is_a_running_head():
    """The Art of AI Product Development p28: "5 A retrospective" (9pt over a 10pt body) was a
    footnote, and the page's first paragraph went with it into the notes."""
    from book_agent.domain.structure.pdf import _looks_like_running_head

    for head in ("5 A retrospective", "283 Getting buy-in from business stakeholders", "Sourcing AI ideas 19", "25 2.3 Limiting output", "49 3.1 What is RAG?"):
        assert _looks_like_running_head(head), head
    for text in ("1 Problem statement—Clearly define the business challenge AI will address.", "3 Clean and preprocess the data:"):
        assert not _looks_like_running_head(text), text
