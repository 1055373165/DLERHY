"""The review pass's reading-order issue (MISORDERING) blocks only on page-level evidence.

A book-wide "high" layout risk is inherited by every chapter of the book; the RAG book's chapter 1
has no risky page of its own, and its chapter 2 is flagged "multi_column" on two pages whose prose
runs in one column (the columns are a diagram's boxes). Neither is blocking. Prose side by side on
a flagged page (an index, a two-column page) still is.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from book_agent.domain.enums import BlockType, SourceType
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.services.review import ReviewService


def _block(role: str, page: int, bbox: list[float], *, block_type: BlockType = BlockType.PARAGRAPH, **metadata: object):
    return SimpleNamespace(
        id=f"{role}-{page}-{bbox[0]}-{bbox[1]}",
        block_type=block_type,
        source_span_json={
            "pdf_block_role": role,
            "source_page_start": page,
            "source_bbox_json": {"regions": [{"page_number": page, "bbox": bbox}]},
            **metadata,
        },
    )


def _page(number: int, *reasons: str) -> dict[str, object]:
    return {
        "page_number": number,
        "page_layout_risk": "medium" if reasons else "low",
        "page_layout_reasons": list(reasons),
        "layout_suspect": bool(reasons),
        "role_counts": {"body": 3},
        "recovery_flags": [],
    }


def _bundle(blocks: list[object], pages: list[dict[str, object]], *, risk: str = "high"):
    document = SimpleNamespace(
        id="doc",
        source_type=SourceType.PDF_TEXT,
        metadata_json={
            "pdf_profile": {"recovery_lane": None, "layout_risk": risk},
            "pdf_page_evidence": {"pdf_pages": pages},
        },
    )
    chapter = SimpleNamespace(
        id="chapter",
        document_id="doc",
        metadata_json={
            "source_page_start": pages[0]["page_number"],
            "source_page_end": pages[-1]["page_number"],
            "parse_confidence": 0.65,
            "pdf_layout_risk": risk,
        },
    )
    return SimpleNamespace(document=document, chapter=chapter, blocks=blocks, sentences=[])


# Page 45 of the RAG book: one column of prose, a three-box diagram whose labels are text blocks.
_DIAGRAM_PAGE = [
    _block("body", 45, [114.0, 60.8, 464.9, 129.0]),
    _block("heading", 45, [66.0, 215.3, 209.5, 229.8], block_type=BlockType.HEADING),
    _block("body", 45, [102.0, 234.6, 476.6, 326.9]),
    _block("figure", 45, [99.0, 351.9, 471.3, 468.4], block_type=BlockType.FIGURE),
    _block("body", 45, [243.3, 375.9, 326.9, 435.7]),
    _block("body", 45, [115.8, 375.9, 179.3, 444.1]),
    _block("body", 45, [385.7, 375.9, 459.7, 444.1]),
    _block("caption", 45, [102.0, 476.5, 473.8, 496.4], block_type=BlockType.CAPTION),
    _block("list_item", 45, [117.6, 540.2, 476.6, 552.0], block_type=BlockType.LIST_ITEM),
]

# A two-column page whose lines were read across the columns: left, right, left, right.
_TWO_COLUMN_PAGE = [
    _block("body", 250, [60.0, 60.0, 250.0, 300.0]),
    _block("body", 250, [280.0, 60.0, 470.0, 300.0]),
    _block("body", 250, [60.0, 310.0, 250.0, 600.0]),
    _block("body", 250, [280.0, 310.0, 470.0, 600.0]),
]


class PdfLayoutReviewPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ReviewService(ReviewRepository(None))

    def _misordering(self, bundle):
        issues = self.service._pdf_structure_issues(bundle, datetime.now(timezone.utc))
        return [issue for issue in issues if issue.issue_type == "MISORDERING"]

    def test_book_wide_high_risk_without_a_risky_page_in_the_chapter_is_no_issue(self) -> None:
        bundle = _bundle([_block("body", 23, [93.0, 70.0, 467.0, 160.0])], [_page(23), _page(24), _page(25)])

        self.assertEqual(self._misordering(bundle), [])

    def test_high_risk_without_page_evidence_keeps_the_old_blocking_default(self) -> None:
        bundle = _bundle([_block("body", 23, [93.0, 70.0, 467.0, 160.0])], [_page(23)])
        bundle.document.metadata_json.pop("pdf_page_evidence")

        issues = self._misordering(bundle)

        self.assertEqual(len(issues), 1)
        self.assertTrue(issues[0].blocking)
        self.assertEqual(issues[0].evidence_json["review_policy"], "default_blocking_layout_risk")

    def test_multi_column_page_whose_columns_are_a_diagram_is_advisory(self) -> None:
        bundle = _bundle(_DIAGRAM_PAGE, [_page(44), _page(45, "multi_column"), _page(46)])

        issues = self._misordering(bundle)

        self.assertEqual(len(issues), 1)
        self.assertFalse(issues[0].blocking)
        self.assertEqual(issues[0].evidence_json["review_policy"], "multi_column_pages_single_prose_column")

    def test_labels_a_repair_hid_are_not_prose_either(self) -> None:
        blocks = [
            _block("body", 45, [114.0, 60.8, 464.9, 129.0]),
            _block("body", 45, [115.8, 375.9, 179.3, 444.1], repair_hidden_from_export=True),
            _block("body", 45, [385.7, 375.9, 459.7, 444.1], repair_hidden_from_export=True),
        ]
        bundle = _bundle(blocks, [_page(45, "multi_column")])

        self.assertFalse(self._misordering(bundle)[0].blocking)

    def test_prose_side_by_side_on_a_flagged_page_stays_blocking(self) -> None:
        bundle = _bundle([*_DIAGRAM_PAGE, *_TWO_COLUMN_PAGE], [_page(45, "multi_column"), _page(250, "multi_column")])

        issues = self._misordering(bundle)

        self.assertEqual(len(issues), 1)
        self.assertTrue(issues[0].blocking)
        self.assertEqual(issues[0].evidence_json["review_policy"], "default_blocking_layout_risk")

    def test_staggered_column_lines_are_side_by_side_too(self) -> None:
        # Lines of the right column start lower than the left's and run past the gutter.
        blocks = [
            _block("body", 7, [72.0, 108.2, 389.9, 123.3]),
            _block("body", 7, [330.0, 128.2, 595.9, 143.3]),
            _block("body", 7, [72.0, 148.2, 389.9, 163.3]),
        ]
        bundle = _bundle(blocks, [_page(7, "multi_column")], risk="medium")

        self.assertTrue(self._misordering(bundle)[0].blocking)

    def test_a_two_column_page_read_column_by_column_is_in_order(self) -> None:
        """Art of AI's index: two columns side by side, the left one read first; book QA blocked it."""
        index_page = [
            _block("body", 361, [57.0, 52.0, 245.0, 300.0]),
            _block("body", 361, [57.0, 310.0, 250.0, 600.0]),
            _block("body", 361, [270.0, 52.0, 450.0, 300.0]),
            _block("body", 361, [270.0, 310.0, 455.0, 600.0]),
        ]
        bundle = _bundle(index_page, [_page(361, "multi_column")], risk="medium")

        issues = self._misordering(bundle)

        self.assertEqual(len(issues), 1)
        self.assertFalse(issues[0].blocking)

    def test_other_layout_reasons_stay_blocking(self) -> None:
        bundle = _bundle(_DIAGRAM_PAGE, [_page(45, "multi_column", "layout_fragmentation")])

        self.assertTrue(self._misordering(bundle)[0].blocking)

    def test_a_flagged_page_without_prose_to_judge_stays_blocking(self) -> None:
        bundle = _bundle([_block("figure", 45, [99.0, 351.9, 471.3, 468.4], block_type=BlockType.FIGURE)], [_page(45, "multi_column")])

        self.assertTrue(self._misordering(bundle)[0].blocking)


    def test_a_running_head_the_repair_hid_is_no_leak(self) -> None:
        """Art of AI p28: a paragraph read again as the running head, hidden from the reading copy by
        the repair, still had translatable sentences; the rule reported a header leaking into the text."""
        head = _block("header", 28, [57.0, 25.0, 300.0, 36.0], repair_hidden_from_export=True)
        bundle = _bundle([head, _block("body", 28, [93.0, 70.0, 467.0, 160.0])], [_page(28)], risk="low")
        bundle.sentences = [SimpleNamespace(id="s1", block_id=head.id, translatable=True, ordinal_in_block=1)]
        pollution = [
            issue for issue in self.service._pdf_structure_issues(bundle, datetime.now(timezone.utc)) if issue.issue_type == "STRUCTURE_POLLUTION"
        ]
        self.assertEqual(pollution, [])
        head.source_span_json.pop("repair_hidden_from_export")
        pollution = [
            issue for issue in self.service._pdf_structure_issues(bundle, datetime.now(timezone.utc)) if issue.issue_type == "STRUCTURE_POLLUTION"
        ]
        self.assertEqual(len(pollution), 1)

if __name__ == "__main__":
    unittest.main()
