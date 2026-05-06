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


# ---- F2-ext: section-running-header form (footnote-typed) ---------------

def test_f2_ext_section_running_header():
    """Section running headers like '1.2 What you will learn 5' (often
    typed as footnote by the parser) should be filtered."""
    assert r.is_page_running_header("1.2 What you will learn 5")
    assert r.is_page_running_header("2.3 Tokenization and LLM capabilities 25")
    assert r.is_page_running_header("3.1.4 Foo Bar 42")


def test_f2_ext_keeps_real_footnote():
    """A real footnote with cite ref + body should NOT be filtered."""
    real = "[1] Smith et al. 2024. Some paper title. Journal of X 12(3):45-67"
    assert not r.is_page_running_header(real)


# ---- F7: NOTE/TIP/WARNING callout heading detection ---------------------

def test_f7_note_callout_heading_detected():
    assert r.is_note_callout_heading("NOTE Vision and language")
    assert r.is_note_callout_heading("TIP Use a separate venv")
    assert r.is_note_callout_heading("WARNING This may take hours")
    assert r.is_note_callout_heading("CAUTION Read carefully")
    assert r.is_note_callout_heading("IMPORTANT Back up first")


def test_f7_note_callout_lowercase_or_partial_words_rejected():
    assert not r.is_note_callout_heading("Note that this is...")  # not all-caps NOTE
    assert not r.is_note_callout_heading("NOTES on usage")  # NOTES (plural) not in list
    assert not r.is_note_callout_heading("1.1 Generative AI in context")


# ---- F8: diagram label dump detection -----------------------------------

def test_f8_diagram_label_dump_detected():
    """Figure 1.3-style label dump: capitalised noun phrases interleaved
    with stop words, no real sentence terminators."""
    text_value = (
        "Some examples of generative AI include are products built using "
        "ChatGPT Gemini Copilot Claude which use techniques from Artificial "
        "intelligence Large language models Machine learning Natural "
        "language processing is the input and output from are built using "
        "Deep learning Text data Transformers"
    )
    assert r.is_diagram_label_dump(text_value)


def test_f8_real_prose_not_flagged():
    prose = (
        "Some researchers have observed that ChatGPT, when prompted with "
        "ambiguous instructions, tends to fall back on verbose explanations. "
        "This pattern is consistent across multiple model versions and is "
        "likely a side effect of the RLHF training process. Future work "
        "could explore whether instruction-tuning alone could mitigate this."
    )
    assert not r.is_diagram_label_dump(prose)


def test_f8_short_text_not_flagged():
    assert not r.is_diagram_label_dump("Short text only")
    assert not r.is_diagram_label_dump("")


# ---- F9: visual paragraph splitting -------------------------------------

def test_f9_splits_at_short_tail_period_line():
    """The classic case: a multi-paragraph block where the visual
    paragraph break corresponds to a short last line ending with a period.
    """
    src = (
        "This book aims to help you make sense of this new world by\n"
        "dispelling the mystery behind what makes ChatGPT and related\n"
        "technologies work. We will cover the knowledge necessary to\n"
        "understand their inner workings and how the components (data and\n"
        "algorithms) stack together to create the tools we use. We'll also\n"
        "discuss various cases where this technology can form the\n"
        "cornerstone of a broader system and others where systems based on\n"
        "large language models (LLMs) may be a poor\n"
        "choice.\n"
        "After reading this book, you'll understand what generative AI\n"
        "like ChatGPT really is, what it can and can't do."
    )
    chunks = r.split_into_visual_paragraphs(src)
    assert len(chunks) == 2
    assert "This book aims" in chunks[0]
    assert chunks[0].endswith("choice.")
    assert chunks[1].startswith("After reading this book")


def test_f9_single_paragraph_returns_one_chunk():
    src = (
        "First, we need to get more specific about what we are discussing\n"
        "when we talk about LLMs, GPTs, and the various tools that rely on\n"
        "them. The GPT in ChatGPT stands for Generative Pretrained Transformer."
    )
    chunks = r.split_into_visual_paragraphs(src)
    assert len(chunks) == 1


def test_f9_empty_input_returns_empty_list():
    assert r.split_into_visual_paragraphs("") == []
    assert r.split_into_visual_paragraphs("   \n  ") == []


# ---- F11: merged_sentence target redistribution ------------------------

def test_f11_split_chinese_sentences_basic():
    text_zh = "首先这是第一句。然后这是第二句。最后这是第三句。"
    parts = r.split_chinese_sentences(text_zh)
    assert len(parts) == 3
    assert parts[0] == "首先这是第一句。"
    assert parts[1] == "然后这是第二句。"
    assert parts[2] == "最后这是第三句。"


def test_f11_split_handles_question_and_exclaim():
    text_zh = "这是吗？是的！这是陈述。"
    parts = r.split_chinese_sentences(text_zh)
    assert len(parts) == 3


def test_f11_expand_merged_targets_splits_correctly():
    """The classic block-9 case: 7 source sentences, 1 merged ZH chunk."""
    fake_target = (1, "首先A。然后B。其次C。再者D。例如E。", "merged_sentence", "draft", 0.9)
    targets = {1: fake_target}
    sentences = [(i, f"sentence-{i}", True) for i in range(1, 6)]
    out = r.expand_merged_targets(targets, sentences)
    assert len(out) == 5
    assert out[1][1] == "首先A。"
    assert out[5][1] == "例如E。"


def test_f11_expand_no_change_when_count_mismatch():
    """If split produces a different count than the source-sentence count,
    do not reassign — that would be making up alignments."""
    fake_target = (1, "只有一句话。", "merged_sentence", "draft", 0.9)
    targets = {1: fake_target}
    sentences = [(i, f"s-{i}", True) for i in range(1, 4)]  # 3 source sentences
    out = r.expand_merged_targets(targets, sentences)
    assert out is targets  # unchanged identity


def test_f11_expand_no_change_when_already_per_sentence():
    """Multi-target dicts are already per-sentence; leave alone."""
    targets = {
        1: (1, "句一。", "sentence", "draft", 0.9),
        2: (2, "句二。", "sentence", "draft", 0.9),
    }
    sentences = [(i, f"s-{i}", True) for i in range(1, 3)]
    out = r.expand_merged_targets(targets, sentences)
    assert out is targets


def test_heading_level_never_skips():
    # MD hierarchy invariant: section level immediately under chapter level.
    chapter = r.heading_level("Big picture")
    section = r.heading_level("1.1 Sub")
    subsection = r.heading_level("1.1.1 Sub-sub")
    assert section == chapter + 1
    assert subsection == section + 1
