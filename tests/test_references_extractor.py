# ruff: noqa: E402
"""Tests for M3.1 references modality."""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.structure.models import (
    TRANSLATE_ALL,
    TRANSLATE_NONE,
    ParsedBlock,
    ParsedChapter,
    ParsedDocument,
)
from book_agent.services.references_extractor import (
    is_reference_heading,
    is_terminating_heading,
    iter_reference_entries,
    parse_reference_entry,
    protect_references_section,
)


def _block(
    *,
    text: str,
    block_type: str = "paragraph",
    ordinal: int = 1,
    anchor: str | None = None,
    translatability: str = TRANSLATE_ALL,
) -> ParsedBlock:
    return ParsedBlock(
        block_type=block_type,
        text=text,
        source_path="x",
        ordinal=ordinal,
        anchor=anchor or f"b{ordinal}",
        translatability=translatability,
    )


def _doc(blocks_per_chapter: list[tuple[str, list[ParsedBlock]]]) -> ParsedDocument:
    chapters = [
        ParsedChapter(
            chapter_id=f"ch{i+1}",
            href=f"h{i+1}",
            title=title,
            blocks=blocks,
        )
        for i, (title, blocks) in enumerate(blocks_per_chapter)
    ]
    return ParsedDocument(
        title="T", author="A", language="en", chapters=chapters
    )


class HeadingDetectionTests(unittest.TestCase):
    def test_canonical_titles_recognized(self) -> None:
        for title in ["References", "Bibliography", "WORKS CITED", "参考文献", "引用"]:
            self.assertTrue(is_reference_heading(title), title)

    def test_non_reference_titles_rejected(self) -> None:
        for title in ["Chapter 1", "Introduction", "Methods", "Discussion"]:
            self.assertFalse(is_reference_heading(title))

    def test_terminating_headings_recognized(self) -> None:
        for title in ["Index", "Appendix", "Glossary", "索引", "附录"]:
            self.assertTrue(is_terminating_heading(title))


class EntryParserTests(unittest.TestCase):
    def test_apa_style_entry_parsed(self) -> None:
        entry = parse_reference_entry(
            "Vaswani, A., Shazeer, N., & Parmar, N. (2017). Attention Is All You Need. NeurIPS."
        )
        self.assertEqual(entry.year, 2017)
        self.assertIsNotNone(entry.authors_chunk)
        self.assertIn("Vaswani", entry.authors_chunk)
        self.assertEqual(entry.title, "Attention Is All You Need")
        self.assertEqual(entry.venue, "NeurIPS")

    def test_doi_extracted(self) -> None:
        entry = parse_reference_entry(
            "LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. "
            "Nature, 521(7553), 436-444. doi:10.1038/nature14539"
        )
        self.assertEqual(entry.doi, "10.1038/nature14539")
        self.assertEqual(entry.year, 2015)

    def test_arxiv_id_extracted(self) -> None:
        entry = parse_reference_entry(
            "Devlin, J., et al. (2019). BERT. arXiv:1810.04805"
        )
        self.assertEqual(entry.arxiv_id, "1810.04805")

    def test_url_extracted(self) -> None:
        entry = parse_reference_entry(
            "Brown, T., Mann, B., et al. (2020). GPT-3. https://arxiv.org/abs/2005.14165"
        )
        self.assertEqual(entry.urls, ("https://arxiv.org/abs/2005.14165",))

    def test_numbered_prefix_stripped(self) -> None:
        entry = parse_reference_entry(
            "[3] Smith, J. (2018). Example. Conference."
        )
        self.assertEqual(entry.year, 2018)

    def test_empty_input_returns_blank(self) -> None:
        entry = parse_reference_entry("   ")
        self.assertEqual(entry.raw, "")
        self.assertIsNone(entry.year)


class ProtectReferencesSectionTests(unittest.TestCase):
    def test_section_started_by_heading_block(self) -> None:
        doc = _doc(
            [
                (
                    "Chapter 1",
                    [
                        _block(text="Body of chapter one.", ordinal=1),
                        _block(
                            text="References",
                            block_type="heading",
                            ordinal=2,
                            anchor="ref-head",
                        ),
                        _block(
                            text="Smith, J. (2018). Foo. Bar.",
                            ordinal=3,
                            anchor="ref-1",
                        ),
                        _block(
                            text="Brown, T. (2020). Baz. Qux.",
                            ordinal=4,
                            anchor="ref-2",
                        ),
                    ],
                ),
            ]
        )
        rewritten, result = protect_references_section(doc)
        all_blocks = list(rewritten.chapters[0].blocks)
        self.assertEqual(all_blocks[0].translatability, TRANSLATE_ALL)
        self.assertEqual(all_blocks[1].translatability, TRANSLATE_NONE)  # heading
        self.assertEqual(all_blocks[2].translatability, TRANSLATE_NONE)
        self.assertEqual(all_blocks[3].translatability, TRANSLATE_NONE)
        self.assertEqual(result.block_count, 2)
        self.assertEqual(len(result.parsed_entries), 2)
        self.assertEqual(result.section_start_block_anchor, "ref-head")

    def test_section_started_by_chapter_title(self) -> None:
        doc = _doc(
            [
                ("Chapter 1", [_block(text="Body.", ordinal=1)]),
                (
                    "References",
                    [
                        _block(text="Smith, J. (2018). Foo. Bar.", ordinal=1, anchor="r1"),
                    ],
                ),
            ]
        )
        rewritten, result = protect_references_section(doc)
        self.assertEqual(rewritten.chapters[0].blocks[0].translatability, TRANSLATE_ALL)
        self.assertEqual(rewritten.chapters[1].blocks[0].translatability, TRANSLATE_NONE)
        self.assertEqual(result.section_chapter_id, "ch2")

    def test_section_terminated_by_appendix_heading(self) -> None:
        doc = _doc(
            [
                (
                    "Chapter 1",
                    [
                        _block(text="References", block_type="heading", ordinal=1),
                        _block(text="Smith, J. (2018). Foo.", ordinal=2),
                        _block(text="Appendix", block_type="heading", ordinal=3),
                        _block(text="Appendix body content.", ordinal=4),
                    ],
                ),
            ]
        )
        rewritten, _ = protect_references_section(doc)
        blocks = list(rewritten.chapters[0].blocks)
        # heading + first ref entry → translate_none
        self.assertEqual(blocks[0].translatability, TRANSLATE_NONE)
        self.assertEqual(blocks[1].translatability, TRANSLATE_NONE)
        # appendix heading + body → unchanged
        self.assertEqual(blocks[2].translatability, TRANSLATE_ALL)
        self.assertEqual(blocks[3].translatability, TRANSLATE_ALL)

    def test_no_references_section_leaves_doc_unchanged(self) -> None:
        doc = _doc(
            [
                (
                    "Chapter 1",
                    [
                        _block(text="Just regular prose.", ordinal=1),
                    ],
                )
            ]
        )
        rewritten, result = protect_references_section(doc)
        self.assertEqual(result.block_count, 0)
        self.assertEqual(result.parsed_entries, [])
        self.assertEqual(rewritten.chapters[0].blocks[0].translatability, TRANSLATE_ALL)

    def test_iter_reference_entries_yields_only_section_entries(self) -> None:
        doc = _doc(
            [
                (
                    "Chapter 1",
                    [
                        _block(text="Some prose.", ordinal=1),
                        _block(
                            text="References",
                            block_type="heading",
                            ordinal=2,
                        ),
                        _block(text="Smith, J. (2018). Foo. Bar.", ordinal=3),
                        _block(text="Jones, K. (2019). Baz. Qux.", ordinal=4),
                    ],
                )
            ]
        )
        entries = list(iter_reference_entries(doc))
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].year, 2018)
        self.assertEqual(entries[1].year, 2019)


if __name__ == "__main__":
    unittest.main()
