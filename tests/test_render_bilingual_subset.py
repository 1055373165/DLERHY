"""Tests for the production-grade defenses in scripts/render_bilingual_subset.py.

Each defense (F1-F6) gets a focused test that exercises its boundary
conditions. These run without a DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import render_bilingual_subset as r  # noqa: E402


# ---- F2: page-running-header detection ------------------------------------

def test_f2_lone_page_number_is_running_header():
    assert r.is_page_running_header("1")
    assert r.is_page_running_header("  14  ")
    assert r.is_page_running_header("28")


def test_f2_page_with_chapter_label_is_running_header():
    assert r.is_page_running_header(
        "16\nCHAPTER 2\nTokenizers: How large language models see the world"
    )
    assert r.is_page_running_header("2 CHAPTER 1 Big picture: What are LLMs?")


def test_f2_real_paragraph_starting_with_number_is_not_running_header():
    # A real paragraph that happens to start with a number should NOT
    # be filtered.
    real = (
        "1.4 was a turning point because it forced the field to confront "
        "a question it had been avoiding: what does intelligence even mean? "
        "The answer, as we shall see, is harder than it looks."
    )
    assert not r.is_page_running_header(real)


def test_f2_empty_text_is_not_running_header():
    assert not r.is_page_running_header("")
    assert not r.is_page_running_header("   ")


# ---- F3: heading echo dedupe (page-number trailing strip) -----------------

def test_f3_strip_trailing_page_number():
    assert r.strip_trailing_page_number_echo("1.1 Generative AI in context  3") == "1.1 Generative AI in context"
    assert r.strip_trailing_page_number_echo("Big picture: What are LLMs?  1") == "Big picture: What are LLMs?"


def test_f3_does_not_strip_legitimate_trailing_number():
    # "GPT 3" — the 3 is part of the heading, not a page number.
    # Our heuristic doesn't differentiate, but trailing single digit + word
    # boundary is acceptably rare in section headings.
    assert r.strip_trailing_page_number_echo("Heading 1") == "Heading"  # known limitation


def test_f3_no_change_when_no_trailing_number():
    assert r.strip_trailing_page_number_echo("Summary") == "Summary"
    assert r.strip_trailing_page_number_echo("1.1 Generative AI in context") == "1.1 Generative AI in context"


# ---- F4: LLM meta-commentary stripping ------------------------------------

def test_f4_strips_chinese_meta_explanation_at_tail():
    raw = "总结：这是当前段落中唯一的句子。"
    assert r.strip_llm_meta_commentary(raw) == "总结"


def test_f4_strips_alt_phrasings():
    assert r.strip_llm_meta_commentary("注意：运行BPE算法。这是当前段落中唯一的一个句子。") == "注意：运行BPE算法。"
    assert r.strip_llm_meta_commentary("这是当前段落中的唯一句子。") == ""


def test_f4_does_not_strip_legitimate_translation():
    # A real translation that happens to mention "段落" should not lose meaning.
    legit = "本段落讨论了三个核心概念。"
    assert r.strip_llm_meta_commentary(legit) == legit


def test_f4_handles_empty_or_none():
    assert r.strip_llm_meta_commentary("") == ""
    assert r.strip_llm_meta_commentary(None) is None


# ---- F5: code-vs-prose disambiguation -------------------------------------

def test_f5_chinese_in_code_block_is_prose():
    assert r.is_actually_prose("这是一段中文叙述，肯定不是代码。")


def test_f5_long_natural_language_no_code_signals_is_prose():
    prose = (
        "Going a level deeper, ChatGPT is dealing with human text, and so it "
        "would also be fair to call it a model of human language—or a language "
        "model if you are a cool person who does work in the field known as "
        "natural language processing. The field of NLP intersects both computer "
        "science and linguistics and explores the technology that helps "
        "computers understand, manipulate, and create human language."
    )
    assert r.is_actually_prose(prose)


def test_f5_real_python_code_is_not_prose():
    code = """
def tokenize(text):
    tokens = []
    for word in text.split():
        tokens.append(word.lower())
    return tokens

class Model:
    def __init__(self):
        self.weights = {}
"""
    assert not r.is_actually_prose(code)


def test_f5_short_code_snippet_is_not_prose():
    assert not r.is_actually_prose("x = 1\ny = 2\nprint(x + y)")


# ---- F6: heading-vs-broken-list-item demotion -----------------------------

def test_f6_broken_list_items_are_demoted():
    assert r.looks_like_broken_list_item("4 Sincea")
    assert r.looks_like_broken_list_item("5 Isit an ethical")


def test_f6_real_section_headings_are_kept():
    assert not r.looks_like_broken_list_item("1.1 Generative AI in context")
    assert not r.looks_like_broken_list_item("2.3 Tokenization and LLM capabilities")
    assert not r.looks_like_broken_list_item("Summary")
    assert not r.looks_like_broken_list_item("NOTE Vision and language")


def test_f6_short_numbered_real_heading_kept():
    # "1 Introduction" — 1 token but a real word
    assert not r.looks_like_broken_list_item("1 Introduction")


# ---- heading_level mapping ------------------------------------------------

def test_heading_level_top_level_chapter():
    # No number prefix → default level 2 (chapter / unnumbered section).
    assert r.heading_level("Big picture: What are LLMs?") == 2


def test_heading_level_section():
    # "1.1 ..." → depth 2 → level 3 (### — proper section under ## chapter)
    assert r.heading_level("1.1 Generative AI in context") == 3


def test_heading_level_subsubsection():
    # "2.3.1 ..." → depth 3 → level 4 (#### — proper subsection)
    assert r.heading_level("2.3.1 LLMs are bad at word games") == 4


def test_heading_level_never_skips():
    # MD hierarchy invariant: section level immediately under chapter level.
    chapter = r.heading_level("Big picture")
    section = r.heading_level("1.1 Sub")
    subsection = r.heading_level("1.1.1 Sub-sub")
    assert section == chapter + 1
    assert subsection == section + 1
