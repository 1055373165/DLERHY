"""Book-specific translation heuristics come from data packs selectable per document."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import book_agent.translation.heuristics as heuristics
from book_agent.services.style_drift import source_aware_literalism_guardrail_lines
from book_agent.services.term_normalization import normalize_term_rendering


class HeuristicsPackTests(unittest.TestCase):
    def test_default_pack_carries_the_tuned_rules(self) -> None:
        pack = heuristics.load_heuristics_pack()

        self.assertEqual(pack.name, "tech-book-default")
        self.assertIn("context_engineering_literal", [rule.pattern_id for rule in pack.style_drift_rules])
        self.assertIn("agentic", pack.concept_modifiers)
        self.assertEqual(normalize_term_rendering("Agentic AI", "智能体式 AI"), "智能体AI")
        self.assertIn("Prefer: 上下文工程", source_aware_literalism_guardrail_lines("Context engineering matters."))

    def test_document_metadata_selects_the_pack(self) -> None:
        custom = {"name": "empty-pack", "style_drift_rules": []}
        with tempfile.TemporaryDirectory() as tempdir:
            pack_file = Path(tempdir) / "empty-pack.json"
            pack_file.write_text(json.dumps(custom), encoding="utf-8")

            def _files(_package):
                return Path(tempdir)

            heuristics.load_heuristics_pack.cache_clear()
            self.addCleanup(heuristics.load_heuristics_pack.cache_clear)
            with patch.object(heuristics, "files", side_effect=_files):
                document = SimpleNamespace(metadata_json={heuristics.DOCUMENT_METADATA_KEY: "empty-pack"})
                pack = heuristics.heuristics_pack_for_document(document)

        self.assertEqual(pack.name, "empty-pack")
        self.assertEqual(pack.style_drift_rules, ())

    def test_unknown_pack_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown translation heuristics pack"):
            heuristics.load_heuristics_pack("does-not-exist")

    def test_documents_without_a_selection_use_the_default_pack(self) -> None:
        self.assertEqual(heuristics.heuristics_pack_for_document(SimpleNamespace(metadata_json={})).name, "tech-book-default")


if __name__ == "__main__":
    unittest.main()
