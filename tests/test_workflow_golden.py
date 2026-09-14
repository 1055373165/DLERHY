"""Golden snapshot of the document workflow read models.

Guards the P3 split of ``services/workflows.py``: the scenario in
``tests/workflow_golden_scenario.py`` must keep producing the same normalized
results. After an intentional behavior change, regenerate with
``BOOK_AGENT_UPDATE_GOLDEN=1``.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from tests.workflow_golden_scenario import normalize, run_scenario

GOLDEN_PATH = Path(__file__).parent / "golden" / "workflow_scenario.json"


class WorkflowGoldenTests(unittest.TestCase):
    maxDiff = None

    def test_workflow_scenario_matches_golden_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            engine = build_engine(f"sqlite+pysqlite:///{root / 'golden.db'}")
            try:
                Base.metadata.create_all(engine)
                captured = run_scenario(build_session_factory(engine=engine), root)
            finally:
                engine.dispose()
            actual = json.loads(json.dumps(normalize(captured, root=root), sort_keys=True))

        if os.getenv("BOOK_AGENT_UPDATE_GOLDEN") == "1":
            GOLDEN_PATH.write_text(
                json.dumps(actual, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
            )
        expected = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        for key in sorted(set(expected) | set(actual)):
            with self.subTest(section=key):
                self.assertEqual(actual.get(key), expected.get(key))


if __name__ == "__main__":
    unittest.main()
