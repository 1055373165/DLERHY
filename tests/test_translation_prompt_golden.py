"""Golden snapshots of translation context packets and prompts for every profile.

Regenerate with ``BOOK_AGENT_UPDATE_GOLDEN=1`` after an intentional prompt or
context change (for example the P3.4 term unification).
"""

import difflib
import json
import os
import tempfile
import unittest
from pathlib import Path

from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from tests.translation_prompt_scenario import intern_long_strings, normalize, record_prompts

GOLDEN_DIR = Path(__file__).parent / "golden" / "translation_prompts"


def _snapshot(fixture: str) -> dict:
    with tempfile.TemporaryDirectory() as tempdir:
        root = Path(tempdir)
        engine = build_engine(f"sqlite+pysqlite:///{root / 'prompts.db'}")
        try:
            Base.metadata.create_all(engine)
            records = record_prompts(build_session_factory(engine=engine), root, fixture)
        finally:
            engine.dispose()
        return json.loads(json.dumps(intern_long_strings(normalize(records, root)), sort_keys=True))


class TranslationPromptGoldenTests(unittest.TestCase):
    maxDiff = None

    def _assert_matches_golden(self, fixture: str) -> None:
        actual = _snapshot(fixture)
        golden_path = GOLDEN_DIR / f"{fixture}.json"
        if os.getenv("BOOK_AGENT_UPDATE_GOLDEN") == "1":
            GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(json.dumps(actual, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        expected = json.loads(golden_path.read_text(encoding="utf-8"))
        if actual == expected:
            return
        expected_text = json.dumps(_expand(expected), indent=1, ensure_ascii=False, sort_keys=True).splitlines()
        actual_text = json.dumps(_expand(actual), indent=1, ensure_ascii=False, sort_keys=True).splitlines()
        diff = "\n".join(difflib.unified_diff(expected_text, actual_text, "golden", "actual", lineterm="", n=2))
        self.fail(f"{fixture} translation prompts changed:\n{diff[:8000]}")

    def test_epub_translation_prompts_match_golden(self) -> None:
        self._assert_matches_golden("epub")

    def test_pdf_translation_prompts_match_golden(self) -> None:
        self._assert_matches_golden("pdf")


def _expand(snapshot: dict) -> object:
    texts = snapshot["texts"]

    def walk(item):
        if isinstance(item, dict):
            return {key: walk(val) for key, val in item.items()}
        if isinstance(item, list):
            return [walk(val) for val in item]
        if isinstance(item, str) and item.startswith("<text:") and item.endswith(">"):
            return texts[item[len("<text:") : -1]]
        return item

    return walk(snapshot["records"])


if __name__ == "__main__":
    unittest.main()
