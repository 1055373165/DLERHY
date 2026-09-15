"""Book-wide glossary extraction, grounding and the CSV review round trip."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from book_agent.domain.enums import LockLevel, TermType
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.services.glossary_extraction import (
    GlossaryExtractionService,
    _Proposal,
    chunk_texts,
    merge_proposals,
    read_glossary_csv,
    write_glossary_csv,
)
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.translation.contracts import TranslationUsage
from tests.workflow_golden_scenario import write_epub


def _proposal(source: str, target: str, term_type: TermType = TermType.CONCEPT, *, required: bool = False) -> _Proposal:
    return _Proposal(source_term=source, target_term=target, term_type=term_type, note="", required=required)


class MergeProposalsTest(unittest.TestCase):
    SOURCE = [
        "A failure swing appears near the top.",
        "Two failure swings confirmed the divergence.",
        "Wilder described the divergence in 1978.",
        "Divergences are common in trending markets.",
    ]

    def test_grounds_counts_and_picks_the_majority_rendering(self) -> None:
        suggestions, dropped = merge_proposals(
            [
                _proposal("failure swing", "失败摆动"),
                _proposal("Failure Swing", "失败摇摆"),
                _proposal("failure swing", "失败摆动"),
                _proposal("divergence", "背离"),
                _proposal("Wilder", "Wilder", TermType.PERSON),
                _proposal("stochastic crossover", "随机交叉"),
                _proposal("overbought", "超买"),
            ],
            self.SOURCE,
            aligned_targets=[
                ("A failure swing appears near the top.", "顶部附近出现失败摇摆。"),
                ("Two failure swings confirmed the divergence.", "两次失败摆动确认了背离。"),
            ],
        )
        by_term = {item.source_term: item for item in suggestions}

        self.assertEqual(by_term["failure swing"].target_term, "失败摆动")
        self.assertEqual(by_term["failure swing"].alternative_targets, ["失败摇摆"])
        self.assertEqual(by_term["failure swing"].occurrences, 2)  # plural counted
        self.assertEqual(by_term["failure swing"].current_mismatches, 1)
        self.assertEqual(by_term["divergence"].occurrences, 3)
        self.assertIn("Wilder", by_term)  # a name mentioned once is kept
        self.assertEqual(dropped, ["overbought", "stochastic crossover"])  # never in the source
        # Recommended locks (names here) come first, then by frequency.
        self.assertEqual([item.source_term for item in suggestions][:2], ["Wilder", "divergence"])

    def test_mismatches_ignore_spacing_and_sentences_owned_by_longer_terms(self) -> None:
        suggestions, _ = merge_proposals(
            [
                _proposal("RSI divergence", "RSI 背离", required=True),
                _proposal("head & shoulders", "头肩顶"),
                _proposal("inverted head & shoulders", "头肩底"),
            ],
            [
                "An RSI divergence formed.",
                "Another RSI divergence formed.",
                "A head & shoulders top formed.",
                "An inverted head & shoulders bottom formed.",
                "A second head & shoulders top formed.",
            ],
            aligned_targets=[
                ("An RSI divergence formed.", "形成了RSI背离。"),
                ("Another RSI divergence formed.", "又形成了 RSI 背离。"),
                ("A head & shoulders top formed.", "形成了头肩顶。"),
                ("An inverted head & shoulders bottom formed.", "形成了头肩底。"),
                ("A second head & shoulders top formed.", "又形成了头肩形态。"),
            ],
        )
        by_term = {item.source_term: item for item in suggestions}
        self.assertEqual(by_term["RSI divergence"].current_mismatches, 0)
        self.assertEqual(by_term["head & shoulders"].current_mismatches, 1)
        self.assertTrue(by_term["RSI divergence"].recommended_lock)
        self.assertFalse(by_term["head & shoulders"].recommended_lock)

    def test_plural_proposals_merge_into_the_singular_entry(self) -> None:
        suggestions, _ = merge_proposals(
            [_proposal("Divergence", "背离"), _proposal("divergences", "背离"), _proposal("failure swings", "失败摆动")],
            self.SOURCE,
        )
        self.assertEqual(sorted(item.source_term for item in suggestions), ["Divergence", "failure swings"])

    def test_single_mention_concepts_are_not_glossary_entries(self) -> None:
        suggestions, _ = merge_proposals([_proposal("trending market", "趋势市场")], self.SOURCE)
        self.assertEqual(suggestions, [])

    def test_chunk_texts_respects_the_limit(self) -> None:
        self.assertEqual(chunk_texts(["aaaa", "bbbb", "cccc"], max_chars=10), ["aaaa\nbbbb", "cccc"])


class GlossaryCsvRoundTripTest(unittest.TestCase):
    def test_reviewer_edits_and_unmarked_rows(self) -> None:
        suggestions, _ = merge_proposals(
            [
                _proposal("failure swing", "失败摆动", required=True),
                _proposal("divergence", "背离", required=True),
                _proposal("Wilder", "Wilder", TermType.PERSON),
            ],
            MergeProposalsTest.SOURCE,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "glossary.csv"
            write_glossary_csv(path, suggestions)
            # The reviewer edits one rendering, unmarks a row, and marks nothing else.
            text = path.read_text(encoding="utf-8-sig").replace("失败摆动", "失败波动").replace("y,divergence", ",divergence")
            path.write_text(text, encoding="utf-8-sig")
            rows = read_glossary_csv(path)

        self.assertEqual(
            [(row.source_term, row.target_term) for row in rows],
            [("failure swing", "失败波动"), ("Wilder", "Wilder")],
        )


class FakeStructuredClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate_structured_object(self, *, model_name, system_prompt, user_prompt, response_schema, schema_name="x") -> tuple[dict[str, Any], TranslationUsage]:
        self.prompts.append(user_prompt)
        terms = []
        if "Pricing power" in user_prompt:
            terms.append({"source_term": "pricing power", "target_term": "定价权", "term_type": "concept"})
        terms.append({"source_term": "switching cost", "target_term": "转换成本", "term_type": "concept"})
        return {"terms": terms}, TranslationUsage(token_in=10, token_out=5)


class GlossaryExtractionServiceTest(unittest.TestCase):
    def test_extracts_from_a_bootstrapped_book_and_locks_reviewed_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            engine = build_engine(f"sqlite+pysqlite:///{root / 'glossary.db'}")
            try:
                Base.metadata.create_all(engine)
                session_factory = build_session_factory(engine=engine)
                with session_scope(session_factory) as session:
                    document_id = DocumentWorkflowService(session, export_root=str(root / "exports")).bootstrap_document(
                        write_epub(root)
                    ).document_id
                client = FakeStructuredClient()
                with session_scope(session_factory) as session:
                    result = GlossaryExtractionService(session, client, model_name="fake").extract(document_id)
                with session_scope(session_factory) as session:
                    glossary = GlossaryService(session)
                    glossary.lock_term(document_id, "pricing power", "定价权")
                    locked = glossary.get_locked_terms(document_id)
                    entries = glossary.list_document_entries(document_id)
            finally:
                engine.dispose()

        self.assertEqual(result.chunk_count, len(client.prompts))
        self.assertEqual(result.token_in, 10 * result.chunk_count)
        # "switching cost" never occurs in the fixture book, so it is dropped rather than suggested.
        self.assertIn("switching cost", result.dropped_absent_terms)
        self.assertNotIn("switching cost", [item.source_term for item in result.suggestions])
        self.assertEqual(locked, {"pricing power": "定价权"})
        self.assertEqual({entry.lock_level for entry in entries}, {LockLevel.LOCKED})


if __name__ == "__main__":
    unittest.main()
