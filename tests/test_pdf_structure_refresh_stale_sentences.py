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


def _stub_parser(text: str | list[tuple[str, str]], boxes: dict[str, list[float]] | None = None):
    """One paragraph, or (anchor, text) blocks in reading order; a "-fig" anchor is a figure, with
    its box from ``boxes``."""
    paragraphs = [("p1-b1", text)] if isinstance(text, str) else text
    boxes = boxes or {}

    def _metadata(anchor: str) -> dict:
        metadata = {"source_page_start": 1, "source_page_end": 1, "pdf_block_role": "body"}
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

    def _refresh(self, text: str | list[tuple[str, str]], boxes: dict[str, list[float]] | None = None):
        with self.session_factory() as session:
            artifacts = PdfStructureRefreshService(
                session,
                BootstrapRepository(session),
                parse_service=ParseService(pdf_parser=_stub_parser(text, boxes)),
            ).refresh_document(self.document_id)
            session.commit()
            block = BootstrapRepository(session).load_document_bundle(self.document_id).chapters[0].blocks[0]
        return artifacts, block

    def test_changed_text_marks_sentences_stale(self) -> None:
        artifacts, block = self._refresh("Agents need long-term memory. Context windows are finite.")

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
