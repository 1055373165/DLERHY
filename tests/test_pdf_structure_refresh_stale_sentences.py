"""PDF structure refresh reports blocks whose sentences no longer match their text, and adds the
blocks the new parse finds that no existing block stands for."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from book_agent.domain.enums import BlockType, DocumentStatus, SourceType
from book_agent.domain.models import Document
from book_agent.domain.structure.models import ParsedBlock, ParsedChapter, ParsedDocument
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.services.bootstrap import BootstrapArtifacts, ParseService, SegmentationService
from book_agent.services.pdf_structure_refresh import PdfStructureRefreshService


def _stub_parser(
    text: str | list[tuple[str, str]],
    boxes: dict[str, list[float]] | None = None,
    roles: dict[str, str] | None = None,
):
    """One paragraph, or (anchor, text) blocks in reading order; a "-fig" anchor is a figure, with
    its box from ``boxes``."""
    paragraphs = [("p1-b1", text)] if isinstance(text, str) else text
    boxes = boxes or {}

    def _metadata(anchor: str) -> dict:
        metadata = {"source_page_start": 1, "source_page_end": 1, "pdf_block_role": (roles or {}).get(anchor, "body")}
        if anchor in boxes:
            metadata["source_bbox_json"] = {"regions": [{"page_number": 1, "bbox": boxes[anchor]}]}
        return metadata

    class _StubPdfParser:
        def parse(self, _file_path, profile=None):
            blocks = [
                ParsedBlock(
                    block_type=(BlockType.FIGURE if "-fig" in anchor else BlockType.PARAGRAPH).value,
                    text=paragraph,
                    source_path="pdf://page/1",
                    ordinal=ordinal,
                    anchor=anchor,
                    metadata=_metadata(anchor),
                    parse_confidence=0.95,
                )
                for ordinal, (anchor, paragraph) in enumerate(paragraphs, start=1)
            ]
            chapter = ParsedChapter(
                chapter_id="pdf-chapter-001",
                href="pdf://page/1",
                title="Chapter",
                blocks=blocks,
                metadata={"source_page_start": 1, "source_page_end": 1},
            )
            return ParsedDocument(title="Refresh", author="Author", language="en", chapters=[chapter], metadata={})

    return _StubPdfParser()


class PdfStructureRefreshStaleSentenceTests(unittest.TestCase):
    ORIGINAL: str | list[tuple[str, str]] = "Agents need memory. Context windows are finite."

    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.root = Path(tempdir.name)
        self.engine = build_engine(f"sqlite+pysqlite:///{self.root / 'refresh.db'}")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.pdf_path = self.root / "refresh.pdf"
        self.pdf_path.write_bytes(b"%PDF-1.4\n")
        now = datetime.now(timezone.utc)
        document = Document(
            id="92929292-9292-4292-8292-929292929292",
            source_type=SourceType.PDF_TEXT,
            file_fingerprint="fingerprint-refresh-stale-sentences",
            source_path=str(self.pdf_path),
            status=DocumentStatus.INGESTED,
            metadata_json={"pdf_profile": {"pdf_kind": "text_pdf", "layout_risk": "low"}},
            created_at=now,
            updated_at=now,
        )
        parse_artifacts = ParseService(pdf_parser=_stub_parser(self.ORIGINAL, getattr(self, "BOXES", None))).parse(
            document, str(self.pdf_path)
        )
        segmentation = SegmentationService().segment(
            parse_artifacts.document, parse_artifacts.chapters, parse_artifacts.blocks
        )
        with self.session_factory() as session:
            BootstrapRepository(session).save(
                BootstrapArtifacts(
                    document=parse_artifacts.document,
                    chapters=parse_artifacts.chapters,
                    blocks=parse_artifacts.blocks,
                    sentences=segmentation.sentences,
                    job_runs=[parse_artifacts.job_run, segmentation.job_run],
                )
            )
            session.commit()
        self.document_id = parse_artifacts.document.id

    def _refresh(
        self,
        text: str | list[tuple[str, str]],
        boxes: dict[str, list[float]] | None = None,
        roles: dict[str, str] | None = None,
    ):
        with self.session_factory() as session:
            artifacts = PdfStructureRefreshService(
                session,
                BootstrapRepository(session),
                parse_service=ParseService(pdf_parser=_stub_parser(text, boxes, roles)),
            ).refresh_document(self.document_id)
            session.commit()
            block = BootstrapRepository(session).load_document_bundle(self.document_id).chapters[0].blocks[0]
        return artifacts, block

    def test_changed_text_marks_sentences_stale(self) -> None:
        artifacts, block = self._refresh("Agents need long-term memory. Context windows are finite.")

        self.assertEqual(artifacts.stale_sentence_block_ids, [block.id])
        self.assertTrue(block.source_span_json["refresh_sentences_stale"])

    def test_a_paragraph_read_again_as_a_running_head_is_segmented_again(self) -> None:
        """Art of AI p28: same text, now the page's running head; its sentences were still translatable."""
        artifacts, block = self._refresh(self.ORIGINAL, roles={"p1-b1": "header"})

        self.assertEqual(artifacts.stale_sentence_block_ids, [block.id])
        self.assertTrue(block.source_span_json["refresh_sentences_stale"])

    def test_whitespace_only_change_keeps_sentences_current(self) -> None:
        artifacts, block = self._refresh("Agents need memory.\nContext windows are finite.")

        self.assertEqual(artifacts.stale_sentence_block_ids, [])
        self.assertNotIn("refresh_sentences_stale", block.source_span_json)


class PdfStructureRefreshNewBlockTests(PdfStructureRefreshStaleSentenceTests):
    # The old parse glued the page's first paragraph to its running head.
    ORIGINAL = [
        ("p1-b1", "5 A retrospective Meanwhile, the hype is everywhere. Now what?"),
        ("p1-b3", "After some brainstorming, a couple of ideas emerge."),
    ]

    test_changed_text_marks_sentences_stale = None
    test_whitespace_only_change_keeps_sentences_current = None

    def test_a_paragraph_the_new_parse_finds_is_added_in_reading_order_and_translated_later(self) -> None:
        from book_agent.domain.models import Sentence
        from book_agent.services.parse_revision_fork import ParseRevisionForkService

        artifacts, _block = self._refresh(
            [
                ("p1-b1", "5 A retrospective"),
                ("p1-b2", "Meanwhile, the hype is everywhere. Now what?"),
                ("p1-b3", "After some brainstorming, a couple of ideas emerge."),
            ]
        )
        self.assertEqual(len(artifacts.created_block_ids), 1)
        with self.session_factory() as session:
            blocks = sorted(
                BootstrapRepository(session).load_document_bundle(self.document_id).chapters[0].blocks,
                key=lambda block: block.ordinal,
            )
            self.assertEqual(
                [(block.ordinal, block.source_anchor) for block in blocks],
                [(1, "pdf://page/1#p1-b1"), (2, "pdf://page/1#p1-b2"), (3, "pdf://page/1#p1-b3")],
            )
            created = blocks[1]
            self.assertEqual(created.id, artifacts.created_block_ids[0])
            self.assertTrue(created.source_span_json["refresh_sentences_stale"])

            # The fork after the refresh gives it its sentences.
            fork = ParseRevisionForkService(session)
            self.assertIn(created.id, fork.stale_block_ids(self.document_id))
            fork.resegment_blocks(self.document_id, reason="test")
            sentences = session.query(Sentence).filter(Sentence.block_id == created.id).all()
            self.assertEqual([sentence.source_text for sentence in sentences], ["Meanwhile, the hype is everywhere.", "Now what?"])

    def test_text_an_existing_block_already_holds_is_not_added_twice(self) -> None:
        artifacts, _block = self._refresh(
            [
                ("p1-b1", "5 A retrospective Meanwhile, the hype is everywhere. Now what?"),
                ("p1-b2", "Meanwhile, the hype is everywhere. Now what?"),
                ("p1-b3", "After some brainstorming, a couple of ideas emerge."),
            ]
        )
        self.assertEqual(artifacts.created_block_ids, [])

    def test_an_old_parse_s_stray_copy_is_dropped(self) -> None:
        """Art of AI chapter 2: the old parse had a paragraph twice (b3 and b4); the new parse has it once."""
        from book_agent.domain.enums import ArtifactStatus
        from book_agent.domain.models import Block

        with self.session_factory() as session:
            session.add(
                Block(
                    id="5f0b1e8c-0000-4000-8000-00000000c0b7",
                    chapter_id=session.query(Block).first().chapter_id,
                    ordinal=9,
                    block_type=BlockType.PARAGRAPH,
                    source_text="After some brainstorming, a couple of ideas emerge.",
                    normalized_text="After some brainstorming, a couple of ideas emerge.",
                    source_anchor="pdf://page/1#p1-b4",
                    source_span_json={"source_page_start": 1, "source_page_end": 1},
                    protected_policy=session.query(Block).first().protected_policy,
                    status=ArtifactStatus.ACTIVE,
                )
            )
            session.commit()
        self._refresh(
            [
                ("p1-b1", "5 A retrospective"),
                ("p1-b3", "After some brainstorming, a couple of ideas emerge."),
            ]
        )
        with self.session_factory() as session:
            status = {block.source_anchor.split("#")[-1]: block.status for block in session.query(Block)}
        self.assertEqual(status["p1-b4"], ArtifactStatus.INVALIDATED)
        self.assertEqual(status["p1-b3"], ArtifactStatus.ACTIVE)

    def test_a_stray_copy_run_over_a_page_break_is_dropped(self) -> None:
        """Art of AI p54-55: the copy of a paragraph that continues on the next page stayed."""
        from book_agent.domain.enums import ArtifactStatus
        from book_agent.domain.models import Block

        with self.session_factory() as session:
            first = session.query(Block).first()
            session.add(
                Block(
                    id="5f0b1e8c-0000-4000-8000-00000000c0b8",
                    chapter_id=first.chapter_id,
                    ordinal=9,
                    block_type=BlockType.PARAGRAPH,
                    source_text="After some brainstorming, a couple of ideas emerge.",
                    normalized_text="After some brainstorming, a couple of ideas emerge.",
                    source_anchor="pdf://page/1#p1-b5",
                    source_span_json={"source_page_start": 1, "source_page_end": 2},
                    protected_policy=first.protected_policy,
                    status=ArtifactStatus.ACTIVE,
                )
            )
            session.commit()
        self._refresh([("p1-b1", "5 A retrospective"), ("p1-b3", "After some brainstorming, a couple of ideas emerge.")])
        with self.session_factory() as session:
            status = {block.source_anchor.split("#")[-1]: block.status for block in session.query(Block)}
        self.assertEqual(status["p1-b5"], ArtifactStatus.INVALIDATED)
        self.assertEqual(status["p1-b3"], ArtifactStatus.ACTIVE)

    def test_blocks_renumbered_by_the_new_parse_keep_their_ids(self) -> None:
        """A parser change earlier in the book shifts every anchor after it: the blocks match by text."""
        from book_agent.domain.models import Block

        with self.session_factory() as session:
            before = {block.source_text: block.id for block in session.query(Block)}
        artifacts, _block = self._refresh(
            [
                ("p1-b7", "5 A retrospective Meanwhile, the hype is everywhere. Now what?"),
                ("p1-b9", "After some brainstorming, a couple of ideas emerge."),
            ]
        )
        with self.session_factory() as session:
            after = {block.source_text: (block.id, block.status.value, block.source_anchor) for block in session.query(Block)}
        self.assertEqual(artifacts.created_block_ids, [])
        for text, block_id in before.items():
            self.assertEqual(after[text][:2], (block_id, "active"))
        self.assertEqual(after["After some brainstorming, a couple of ideas emerge."][2], "pdf://page/1#p1-b9")


class PdfStructureRefreshOrderTests(PdfStructureRefreshStaleSentenceTests):
    # An older parse read the pitfalls box before the section it ends (Art of AI chapter 2).
    ORIGINAL = [
        ("p1-b1", "Emotional benefits come first in the section."),
        ("p1-b2", "COMMON PITFALLS Let us characterize some bad candidates."),
        ("p1-b3", "AI can create more human-like interactions on an emotional level."),
    ]

    test_changed_text_marks_sentences_stale = None
    test_whitespace_only_change_keeps_sentences_current = None
    test_a_paragraph_read_again_as_a_running_head_is_segmented_again = None

    def test_blocks_take_the_new_parse_s_order_and_keep_their_ids(self) -> None:
        from book_agent.domain.models import Block

        with self.session_factory() as session:
            before = {block.source_anchor.split("#")[-1]: block.id for block in session.query(Block)}
        artifacts, _block = self._refresh([self.ORIGINAL[0], self.ORIGINAL[2], self.ORIGINAL[1]])
        with self.session_factory() as session:
            ordered = sorted(session.query(Block).all(), key=lambda block: block.ordinal)
        self.assertEqual([block.source_anchor.split("#")[-1] for block in ordered], ["p1-b1", "p1-b3", "p1-b2"])
        self.assertEqual({block.source_anchor.split("#")[-1]: block.id for block in ordered}, before)
        self.assertEqual(len(artifacts.reordered_chapter_ids), 1)
        self.assertEqual(artifacts.stale_sentence_block_ids, [])

        # Read again in the same order, nothing moves.
        artifacts, _block = self._refresh([self.ORIGINAL[0], self.ORIGINAL[2], self.ORIGINAL[1]])
        self.assertEqual(artifacts.reordered_chapter_ids, [])


class PdfStructureRefreshFigureTests(PdfStructureRefreshStaleSentenceTests):
    # The old parse took a sidebar's tinted panel for a figure.
    BOXES = {"p1-fig9": [100, 100, 500, 300], "p1-fig12": [100, 400, 500, 600]}
    ORIGINAL = [
        ("p1-b1", "Case study: collaboration in Miro"),
        ("p1-fig9", "[Figure]"),
        ("p1-fig12", "[Figure]"),
    ]

    test_changed_text_marks_sentences_stale = None
    test_whitespace_only_change_keeps_sentences_current = None

    def test_a_figure_the_new_parse_drops_is_invalidated_and_a_new_one_added(self) -> None:
        from book_agent.domain.enums import ArtifactStatus

        artifacts, _block = self._refresh(
            [
                ("p1-b1", "Case study: collaboration in Miro"),
                ("p1-b2", "Miro uses AI to summarise boards."),
                ("p1-fig12", "[Figure]"),
                ("p1-fig15", "[Figure]"),
            ]
        )
        from book_agent.domain.models import Block

        with self.session_factory() as session:
            status = {block.source_anchor.split("#")[-1]: block.status for block in session.query(Block)}
        self.assertEqual(status["p1-fig9"], ArtifactStatus.INVALIDATED)
        self.assertEqual(status["p1-fig12"], ArtifactStatus.ACTIVE)
        self.assertEqual(status["p1-fig15"], ArtifactStatus.ACTIVE)
        self.assertEqual(status["p1-b2"], ArtifactStatus.ACTIVE)
        self.assertEqual(len(artifacts.created_block_ids), 2)

    def test_a_figure_whose_anchor_moved_keeps_its_block(self) -> None:
        from book_agent.domain.models import Block

        with self.session_factory() as session:
            before = {block.source_anchor.split("#")[-1]: block.id for block in session.query(Block)}
        # The same picture as fig12, now anchored on another of its elements.
        artifacts, _block = self._refresh(
            [("p1-b1", "Case study: collaboration in Miro"), ("p1-fig13", "[Figure]")],
            boxes={"p1-fig13": [102, 398, 500, 610]},
        )
        with self.session_factory() as session:
            after = {block.source_anchor.split("#")[-1]: (block.id, block.status.value) for block in session.query(Block)}
        self.assertEqual(after["p1-fig13"], (before["p1-fig12"], "active"))
        self.assertEqual(after["p1-fig9"], (before["p1-fig9"], "invalidated"))
        self.assertEqual(artifacts.created_block_ids, [])


if __name__ == "__main__":
    unittest.main()
