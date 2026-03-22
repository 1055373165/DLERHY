import unittest
from types import SimpleNamespace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import BlockType, SourceType
from book_agent.infra.repositories.export import ChapterExportBundle
from book_agent.services.export import MergedRenderBlock
from book_agent.services.layout_validate import LayoutValidationService


class LayoutValidationServiceTests(unittest.TestCase):
    def _bundle(self) -> ChapterExportBundle:
        chapter = SimpleNamespace(id="chapter-1", document_id="document-1", metadata_json={})
        document = SimpleNamespace(id="document-1", source_type=SourceType.PDF_TEXT, metadata_json={})
        return ChapterExportBundle(
            chapter=chapter,
            document=document,
            book_profile=None,
            blocks=[],
            document_images=[],
            sentences=[],
            packets=[],
            translation_runs=[],
            target_segments=[],
            alignment_edges=[],
            review_issues=[],
            quality_summary=None,
            active_snapshots=[],
            audit_events=[],
        )

    def _render_block(
        self,
        *,
        block_id: str,
        block_type: str,
        render_mode: str = "zh_primary_with_optional_source",
        artifact_kind: str | None = None,
        source_text: str = "source text",
        target_text: str | None = "target text",
        source_metadata: dict[str, object] | None = None,
    ) -> MergedRenderBlock:
        return MergedRenderBlock(
            block_id=block_id,
            chapter_id="chapter-1",
            block_type=block_type,
            render_mode=render_mode,
            artifact_kind=artifact_kind,
            title=None,
            source_text=source_text,
            target_text=target_text,
            source_metadata=dict(source_metadata or {}),
            source_sentence_ids=[],
            target_segment_ids=[],
            is_expected_source_only=False,
            notice=None,
        )

    def test_validate_chapter_flags_empty_heading_and_heading_level_skip(self) -> None:
        blocks = [
            self._render_block(
                block_id="heading-1",
                block_type=BlockType.HEADING.value,
                source_text="Introduction",
                target_text="介绍",
                source_metadata={"heading_level": 1, "tag": "h1"},
            ),
            self._render_block(
                block_id="heading-2",
                block_type=BlockType.HEADING.value,
                source_text="Deep Dive",
                target_text="深入讨论",
                source_metadata={"heading_level": 3, "tag": "h3"},
            ),
            self._render_block(
                block_id="heading-3",
                block_type=BlockType.HEADING.value,
                source_text="   ",
                target_text=None,
                source_metadata={"heading_level": 4, "tag": "h4"},
            ),
        ]

        result = LayoutValidationService().validate_chapter(self._bundle(), blocks)

        self.assertEqual(result.chapter_id, "chapter-1")
        self.assertTrue(result.has_blocking_issues)
        self.assertEqual(
            {issue.issue_code for issue in result.issues},
            {"HEADING_LEVEL_SKIP", "HEADING_EMPTY"},
        )

    def test_validate_chapter_accepts_figure_with_crop_region(self) -> None:
        blocks = [
            self._render_block(
                block_id="figure-1",
                block_type=BlockType.CAPTION.value,
                render_mode="image_anchor_with_translated_caption",
                artifact_kind="image",
                source_text="Figure 1. Agent workflow.",
                target_text="图 1. 智能体工作流。",
                source_metadata={
                    "source_bbox_json": {
                        "regions": [{"page_number": 12, "bbox": [120.0, 180.0, 420.0, 360.0]}]
                    }
                },
            )
        ]

        result = LayoutValidationService().validate_chapter(self._bundle(), blocks)

        self.assertEqual(result.issue_count, 0)
        self.assertFalse(result.has_blocking_issues)

    def test_validate_chapter_flags_missing_figure_asset_and_orphaned_footnote(self) -> None:
        blocks = [
            self._render_block(
                block_id="figure-2",
                block_type=BlockType.CAPTION.value,
                render_mode="image_anchor_with_translated_caption",
                artifact_kind="figure",
                source_text="Figure 2. Missing image asset.",
                target_text="图 2. 缺少图片资源。",
            ),
            self._render_block(
                block_id="footnote-1",
                block_type=BlockType.FOOTNOTE.value,
                source_text="2 Supporting explanation.",
                target_text="2 补充说明。",
                source_metadata={"footnote_anchor_matched": False, "footnote_anchor_label": "2"},
            ),
        ]

        result = LayoutValidationService().validate_chapter(self._bundle(), blocks)

        self.assertEqual(
            {issue.issue_code for issue in result.issues},
            {"FIGURE_ASSET_MISSING", "FOOTNOTE_ANCHOR_ORPHANED"},
        )
        self.assertEqual(result.blocking_issue_count, 2)

    def test_validate_chapter_flags_empty_footnote_text(self) -> None:
        blocks = [
            self._render_block(
                block_id="footnote-2",
                block_type=BlockType.FOOTNOTE.value,
                source_text=" ",
                target_text=None,
            )
        ]

        result = LayoutValidationService().validate_chapter(self._bundle(), blocks)

        self.assertEqual([issue.issue_code for issue in result.issues], ["FOOTNOTE_EMPTY"])

    def test_validate_chapter_flags_unrenderable_table_structure(self) -> None:
        blocks = [
            self._render_block(
                block_id="table-1",
                block_type=BlockType.TABLE.value,
                render_mode="translated_wrapper_with_preserved_artifact",
                artifact_kind="table",
                source_text="Latency notes only",
                target_text="延迟说明",
            )
        ]

        result = LayoutValidationService().validate_chapter(self._bundle(), blocks)

        self.assertEqual([issue.issue_code for issue in result.issues], ["TABLE_STRUCTURE_UNRENDERABLE"])

    def test_validate_chapter_accepts_structured_pipe_table(self) -> None:
        blocks = [
            self._render_block(
                block_id="table-2",
                block_type=BlockType.TABLE.value,
                render_mode="translated_wrapper_with_preserved_artifact",
                artifact_kind="table",
                source_text="Tier | Latency\nBasic | Slow",
                target_text="层级与延迟",
            )
        ]

        result = LayoutValidationService().validate_chapter(self._bundle(), blocks)

        self.assertEqual(result.issue_count, 0)


if __name__ == "__main__":
    unittest.main()
