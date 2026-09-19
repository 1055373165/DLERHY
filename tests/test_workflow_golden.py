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
from book_agent.schemas.workflow import (
    ChapterMemoryProposalDecisionResponse,
    ChapterWorklistAssignmentResponse,
    DocumentChapterWorklistDetailResponse,
    DocumentChapterWorklistResponse,
    DocumentExportDashboardResponse,
    DocumentHistoryPageResponse,
    DocumentSummaryResponse,
    ExportDetailResponse,
)
from book_agent.infra.db.session import build_engine, build_session_factory
from tests.workflow_golden_scenario import normalize, run_scenario

GOLDEN_PATH = Path(__file__).parent / "golden" / "workflow_scenario.json"


# API response models built from the captured read models with from_attributes.
RESPONSE_MODELS = {
    "summary": DocumentSummaryResponse,
    "history": DocumentHistoryPageResponse,
    "export_dashboard": DocumentExportDashboardResponse,
    "export_dashboard_filtered": DocumentExportDashboardResponse,
    "export_detail": ExportDetailResponse,
    "worklist_before_assignment": DocumentChapterWorklistResponse,
    "worklist_filtered": DocumentChapterWorklistResponse,
    "worklist_final": DocumentChapterWorklistResponse,
    "worklist_detail_blocked": DocumentChapterWorklistDetailResponse,
    "assignment": ChapterWorklistAssignmentResponse,
    "proposal_approval": ChapterMemoryProposalDecisionResponse,
    "proposal_rejection": ChapterMemoryProposalDecisionResponse,
}


class WorkflowGoldenTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        tempdir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        engine = build_engine(f"sqlite+pysqlite:///{root / 'golden.db'}")
        try:
            Base.metadata.create_all(engine)
            cls.captured = run_scenario(build_session_factory(engine=engine), root)
        finally:
            engine.dispose()
        cls.root = root

    def test_read_models_validate_against_api_response_models(self) -> None:
        for key, response_model in RESPONSE_MODELS.items():
            with self.subTest(section=key):
                response_model.model_validate(self.captured[key], from_attributes=True)
        for detail in self.captured["worklist_details_final"]:
            DocumentChapterWorklistDetailResponse.model_validate(detail, from_attributes=True)

    def test_workflow_scenario_matches_golden_snapshot(self) -> None:
        actual = json.loads(json.dumps(normalize(self.captured, root=self.root), sort_keys=True))

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
