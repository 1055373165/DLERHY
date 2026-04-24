# ruff: noqa: E402
"""Tests for M2.5 document-level terminology mining."""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.structure.models import (
    TRANSLATE_ALL,
    TRANSLATE_NONE,
    ParsedBlock,
    ParsedChapter,
    ParsedDocument,
)
from book_agent.services.terminology_miner import TermCandidate, mine_terms


def _mk_block(
    *,
    ordinal: int,
    text: str,
    block_type: str = "paragraph",
    translatability: str = TRANSLATE_ALL,
    anchor: str | None = None,
) -> ParsedBlock:
    return ParsedBlock(
        block_type=block_type,
        text=text,
        source_path="pdf://page/1",
        ordinal=ordinal,
        anchor=anchor or f"b-{ordinal}",
        translatability=translatability,
    )


def _mk_doc(blocks: list[ParsedBlock], *, chapter_id: str = "ch1") -> ParsedDocument:
    return ParsedDocument(
        title="T",
        author="A",
        language="en",
        chapters=[
            ParsedChapter(
                chapter_id=chapter_id,
                href="pdf://page/1",
                title="Chapter 1",
                blocks=blocks,
            )
        ],
    )


class TerminologyMinerTests(unittest.TestCase):
    def test_repeated_bigram_is_mined(self) -> None:
        doc = _mk_doc(
            [
                _mk_block(
                    ordinal=1,
                    text=(
                        "The agent system coordinates tool calls. "
                        "An agent system can also route queries. "
                        "Each agent system reports back to the planner."
                    ),
                ),
            ]
        )
        terms = mine_terms(doc, min_frequency=2)
        surface = {c.term.lower() for c in terms}
        self.assertIn("agent system", surface)

    def test_stopword_framed_ngrams_rejected(self) -> None:
        doc = _mk_doc(
            [
                _mk_block(
                    ordinal=1,
                    text="the agent of the system and the tool with the memory " * 4,
                ),
            ]
        )
        terms = mine_terms(doc)
        # No candidate should begin or end with a stopword.
        for c in terms:
            tokens = c.term.lower().split()
            self.assertNotIn(tokens[0], {"the", "of", "and", "with"})
            self.assertNotIn(tokens[-1], {"the", "of", "and", "with"})

    def test_code_block_tokens_do_not_leak(self) -> None:
        doc = _mk_doc(
            [
                _mk_block(
                    ordinal=1,
                    text=(
                        "import numpy as np\n"
                        "def compute_gradient(values):\n"
                        "    return np.mean(values)"
                    ),
                    block_type="code",
                    translatability=TRANSLATE_NONE,
                ),
                _mk_block(
                    ordinal=2,
                    text=(
                        "The learning rate controls how aggressively parameters update. "
                        "A small learning rate gives stable but slow convergence."
                    ),
                ),
            ]
        )
        terms = mine_terms(doc)
        surface = {c.term.lower() for c in terms}
        # Code identifiers must not surface.
        self.assertNotIn("compute_gradient", surface)
        self.assertNotIn("np.mean", surface)
        # Prose term should be present.
        self.assertIn("learning rate", surface)

    def test_acronym_is_boosted(self) -> None:
        doc = _mk_doc(
            [
                _mk_block(
                    ordinal=1,
                    text=(
                        "The RAG pipeline retrieves documents. "
                        "After the RAG pipeline finishes, the LLM answers. "
                        "RAG has become standard practice in AI Agents."
                    ),
                ),
            ]
        )
        terms = mine_terms(doc, min_frequency=2)
        rag = next((c for c in terms if c.term == "RAG"), None)
        self.assertIsNotNone(rag, f"RAG not mined: {[c.term for c in terms]}")
        self.assertTrue(rag.is_acronym)
        # RAG (unigram acronym with 3 occurrences) must outrank a normal
        # prose bigram of the same frequency.
        self.assertGreater(rag.weight, 5.0)

    def test_definition_pattern_surfaces_even_rare_terms(self) -> None:
        doc = _mk_doc(
            [
                _mk_block(
                    ordinal=1,
                    text="An Action Plan is defined as a structured list of steps.",
                ),
                _mk_block(
                    ordinal=2,
                    text="The user chooses an action. The system executes the action.",
                ),
            ]
        )
        terms = mine_terms(doc, min_frequency=2)
        defined = [c for c in terms if c.definition_boost]
        self.assertTrue(defined, "no definition-boosted candidates found")
        # "Action Plan" only appears ONCE, so frequency filter would
        # normally drop it — definition boost must override.
        action_plan = next(
            (c for c in terms if c.term.lower() == "action plan"), None
        )
        self.assertIsNotNone(action_plan)
        self.assertTrue(action_plan.definition_boost)

    def test_proper_noun_bigram_outranks_common_bigram_at_same_frequency(self) -> None:
        doc = _mk_doc(
            [
                _mk_block(
                    ordinal=1,
                    text=(
                        "Vector Store holds embeddings. Vector Store enables search. "
                        "search engine runs queries. search engine indexes documents."
                    ),
                ),
            ]
        )
        terms = mine_terms(doc, min_frequency=2)
        by_term = {c.term.lower(): c for c in terms}
        vs = by_term.get("vector store")
        se = by_term.get("search engine")
        self.assertIsNotNone(vs)
        self.assertIsNotNone(se)
        self.assertEqual(vs.frequency, se.frequency)
        self.assertGreater(
            vs.weight,
            se.weight,
            "proper-noun bigram should outweigh lowercase bigram at equal frequency",
        )

    def test_top_k_respected(self) -> None:
        large_text = " ".join(f"concept{i} term" for i in range(50)) * 2
        doc = _mk_doc([_mk_block(ordinal=1, text=large_text)])
        terms = mine_terms(doc, top_k=5)
        self.assertLessEqual(len(terms), 5)

    def test_first_seen_provenance_tracked(self) -> None:
        doc = ParsedDocument(
            title=None, author=None, language="en",
            chapters=[
                ParsedChapter(
                    chapter_id="ch1", href="h1", title=None,
                    blocks=[_mk_block(ordinal=1, text="No match here.")],
                ),
                ParsedChapter(
                    chapter_id="ch2", href="h2", title=None,
                    blocks=[
                        _mk_block(
                            ordinal=5,
                            anchor="first-occurrence",
                            text="The attention mechanism is central. The attention mechanism scales.",
                        ),
                        _mk_block(
                            ordinal=6,
                            anchor="later",
                            text="The attention mechanism computes weighted sums.",
                        ),
                    ],
                ),
            ],
        )
        terms = mine_terms(doc, min_frequency=2)
        attn = next((c for c in terms if c.term.lower() == "attention mechanism"), None)
        self.assertIsNotNone(attn)
        self.assertEqual(attn.first_seen_chapter_id, "ch2")
        self.assertEqual(attn.first_seen_block_anchor, "first-occurrence")
        self.assertEqual(attn.first_seen_block_ordinal, 5)


if __name__ == "__main__":
    unittest.main()
