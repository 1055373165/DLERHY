"""A pilot's export QA judges the pilot's chapters, not the whole book.

The Agent and RAG books' pilots filed EXPORT_QA_FAILURE issues on their two-chapter reading
copies: "Q3 untranslated_ratio 83.6%", chapters 3–10 "folded into a neighbour", "R6
image_render_coverage expected≥82": each counted against the whole book. An export scoped to
some chapters is now audited on those chapters; and inline icons, which the reading copy leaves
out on purpose, are not counted as missing pictures.
"""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from sqlalchemy import select

from book_agent.domain.enums import ArtifactStatus, BlockType, ExportType, ProtectedPolicy
from book_agent.domain.models import Block
from book_agent.services.export_qa import ExportQaService
from tests.pilot_repair_fixture import PilotBench, block_with_text


class PilotExportQaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bench = PilotBench()
        self.addCleanup(self.bench.close)
        self.chapter_ids = self.bench.translate_pilot()
        with self.bench.session_factory() as session:
            result = self.bench.workflow(session).export_document(
                self.bench.document_id, ExportType.MERGED_HTML, chapter_ids=self.chapter_ids, despite_gate=True
            )
            session.commit()
            self.html_path = Path(result.file_path)

    def _failed(self, **kwargs) -> dict[str, dict]:
        with self.bench.session_factory() as session:
            audit = ExportQaService(session).audit(self.bench.document_id, ExportType.MERGED_HTML, [(None, self.html_path)], **kwargs)
            session.rollback()
        return {check["name"]: check for check in audit.failed_checks}

    def test_the_whole_book_view_is_what_filed_the_false_issues(self) -> None:
        failed = self._failed()
        self.assertIn("Q3 untranslated_ratio", failed)
        self.assertIn("Q4 chapter_sections", failed)
        self.assertIn("3 Running it in production", failed["Q4 chapter_sections"]["data"]["missing"])

    def test_a_pilot_export_is_judged_on_its_chapters(self) -> None:
        self.assertEqual(self._failed(chapter_ids=self.chapter_ids), {})

    def test_an_inline_icon_the_reading_copy_leaves_out_is_not_a_missing_picture(self) -> None:
        with self.bench.session_factory() as session:
            figure = block_with_text(session, self.bench.document_id, "[Figure]")
            span = dict(figure.source_span_json)
            icon_span = {**span, "source_bbox_json": {"regions": [{"page_number": 2, "bbox": [93.0, 490.0, 108.8, 505.0]}]}}
            last = session.scalars(select(Block).where(Block.chapter_id == figure.chapter_id).order_by(Block.ordinal.desc())).first()
            session.add(
                Block(
                    id=str(uuid.uuid4()),
                    chapter_id=figure.chapter_id,
                    ordinal=last.ordinal + 1,
                    block_type=BlockType.IMAGE,
                    source_text="[Image]",
                    normalized_text="[Image]",
                    source_anchor="icon",
                    source_span_json=icon_span,
                    parse_confidence=0.9,
                    protected_policy=ProtectedPolicy.PROTECT,
                    status=ArtifactStatus.ACTIVE,
                )
            )
            session.commit()
        self.assertNotIn("R6 image_render_coverage", self._failed(chapter_ids=self.chapter_ids))

    def test_the_pilot_repair_audits_its_re_export_on_the_pilot_chapters(self) -> None:
        from book_agent.domain.models.review import ReviewIssue
        from book_agent.services.pilot_repair import PilotRepairService
        from tests.pilot_repair_fixture import edit_translation, sentence_ids

        # A whole-book audit of the pilot's copy filed the false issues (the old stage).
        with self.bench.session_factory() as session:
            ExportQaService(session).audit(self.bench.document_id, ExportType.MERGED_HTML, [(None, self.html_path)])
            (sentence,) = sentence_ids(session, self.bench.document_id, "The first technique reads")
            edit_translation(session, sentence, lambda text: "图片锚点保留" + text)
            session.commit()
        with self.bench.session_factory() as session:
            PilotRepairService(session, workflow_factory=self.bench.workflow).run(self.bench.document_id)
            open_issues = session.scalars(
                select(ReviewIssue).where(ReviewIssue.issue_type == "EXPORT_QA_FAILURE", ReviewIssue.status == "open")
            ).all()
        self.assertEqual([issue.evidence_json["check"] for issue in open_issues], [])


if __name__ == "__main__":
    unittest.main()
