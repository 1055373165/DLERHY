"""PDF structure refresh reports blocks whose sentences no longer match their text."""

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


def _stub_parser(text: str):
    class _StubPdfParser:
        def parse(self, _file_path, profile=None):
            block = ParsedBlock(
                block_type=BlockType.PARAGRAPH.value,
                text=text,
                source_path="pdf://page/1",
                ordinal=1,
                anchor="p1-b1",
                metadata={"source_page_start": 1, "source_page_end": 1, "pdf_block_role": "body"},
                parse_confidence=0.95,
            )
            chapter = ParsedChapter(
                chapter_id="pdf-chapter-001",
                href="pdf://page/1",
                title="Chapter",
                blocks=[block],
                metadata={"source_page_start": 1, "source_page_end": 1},
            )
            return ParsedDocument(title="Refresh", author="Author", language="en", chapters=[chapter], metadata={})

    return _StubPdfParser()


class PdfStructureRefreshStaleSentenceTests(unittest.TestCase):
    ORIGINAL = "Agents need memory. Context windows are finite."

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
        parse_artifacts = ParseService(pdf_parser=_stub_parser(self.ORIGINAL)).parse(document, str(self.pdf_path))
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

    def _refresh(self, text: str):
        with self.session_factory() as session:
            artifacts = PdfStructureRefreshService(
                session,
                BootstrapRepository(session),
                parse_service=ParseService(pdf_parser=_stub_parser(text)),
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


if __name__ == "__main__":
    unittest.main()
