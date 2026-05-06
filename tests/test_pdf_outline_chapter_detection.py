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

from book_agent.domain.structure.pdf import (  # noqa: E402
    _extract_book_main_chapter_number,
    _looks_like_book_primary_outline_title,
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


def test_appendix_unchanged():
    assert _should_start_outlined_book_top_level_chapter("Appendix A")
    assert _should_start_outlined_book_top_level_chapter("Appendix B: Definitions")


def test_glossary_unchanged():
    assert _should_start_outlined_book_top_level_chapter("Glossary")


def test_about_the_authors_does_not_start_chapter():
    """'about the authors' is content-frontmatter, not a top-level chapter
    in the outline-driven splitting model."""
    assert not _should_start_outlined_book_top_level_chapter("about the authors")


# ---- Sanity: empty / whitespace-only inputs ----------------------------

def test_empty_inputs_safe():
    assert _extract_book_main_chapter_number("") is None
    assert _extract_book_main_chapter_number("   ") is None
    assert _extract_book_main_chapter_number(None or "") is None
    assert not _looks_like_book_primary_outline_title("")
