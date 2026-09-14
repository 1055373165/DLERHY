"""Characterization snapshots of recovered PDF structure for every fixture PDF.

Regenerate with ``BOOK_AGENT_UPDATE_GOLDEN=1`` after an intentional change to
PDF structure recovery.
"""

import difflib
import json
import os
import tempfile
import unittest
from pathlib import Path

from tests.pdf_structure_scenario import fixture_writers, normalize, parse_fixture

GOLDEN_DIR = Path(__file__).parent / "golden" / "pdf_structure"


class PdfStructureGoldenTests(unittest.TestCase):
    maxDiff = None

    def test_every_fixture_pdf_matches_its_structure_snapshot(self) -> None:
        writers = fixture_writers()
        update = os.getenv("BOOK_AGENT_UPDATE_GOLDEN") == "1"
        if update:
            GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        else:
            self.assertEqual(sorted(writers), sorted(path.stem for path in GOLDEN_DIR.glob("*.json")))
        for name, writer in writers.items():
            with self.subTest(fixture=name), tempfile.TemporaryDirectory() as tempdir:
                root = Path(tempdir)
                actual = json.dumps(normalize(parse_fixture(writer, root), root), indent=1, ensure_ascii=False, sort_keys=True)
                golden_path = GOLDEN_DIR / f"{name}.json"
                if update:
                    golden_path.write_text(actual + "\n", encoding="utf-8")
                expected = golden_path.read_text(encoding="utf-8").rstrip("\n")
                if actual != expected:
                    diff = "\n".join(
                        difflib.unified_diff(expected.splitlines(), actual.splitlines(), "golden", "actual", lineterm="", n=2)
                    )
                    self.fail(f"{name} structure changed:\n{diff[:6000]}")


if __name__ == "__main__":
    unittest.main()
