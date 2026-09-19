"""Golden snapshots of rendered exports for an EPUB and a PDF fixture.

Guards the P3.2 split of ``services/export.py``: every file the export service
writes (bilingual chapters, review packages, merged HTML / Markdown, rebuilt
and Chinese EPUB entries, image assets) must stay byte-identical after
normalization. After an intentional rendering change, regenerate with
``BOOK_AGENT_UPDATE_GOLDEN=1``.
"""

import difflib
import json
import os
import tempfile
import unittest
from pathlib import Path

from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from tests.export_golden_scenario import FIXTURE_EXPORT_TYPES, render_exports, snapshot_exports

GOLDEN_DIR = Path(__file__).parent / "golden" / "exports"


def _render_snapshot(fixture: str) -> dict:
    with tempfile.TemporaryDirectory() as tempdir:
        root = Path(tempdir)
        engine = build_engine(f"sqlite+pysqlite:///{root / 'golden.db'}")
        try:
            Base.metadata.create_all(engine)
            export_root = render_exports(build_session_factory(engine=engine), root, fixture)
            return json.loads(json.dumps(snapshot_exports(export_root, root), sort_keys=True))
        finally:
            engine.dispose()


def _as_lines(value: object) -> list[str]:
    if isinstance(value, str):
        return value.splitlines()
    return json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True).splitlines()


class ExportGoldenTests(unittest.TestCase):
    maxDiff = None

    def _assert_matches_golden(self, fixture: str) -> None:
        actual = _render_snapshot(fixture)
        golden_path = GOLDEN_DIR / f"{fixture}.json"
        if os.getenv("BOOK_AGENT_UPDATE_GOLDEN") == "1":
            golden_path.write_text(json.dumps(actual, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        expected = json.loads(golden_path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(actual), sorted(expected), "exported file set changed")
        for name in sorted(expected):
            with self.subTest(file=name):
                if actual[name] != expected[name]:
                    diff = "\n".join(
                        difflib.unified_diff(_as_lines(expected[name]), _as_lines(actual[name]), "golden", "actual", lineterm="", n=2)
                    )
                    self.fail(f"{name} differs from golden:\n{diff[:6000]}")

    def test_epub_exports_match_golden(self) -> None:
        self._assert_matches_golden("epub")

    def test_pdf_exports_match_golden(self) -> None:
        self._assert_matches_golden("pdf")

    def test_fixture_export_types_are_covered(self) -> None:
        self.assertEqual(sorted(FIXTURE_EXPORT_TYPES), sorted(path.stem for path in GOLDEN_DIR.glob("*.json")))


if __name__ == "__main__":
    unittest.main()
