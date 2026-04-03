# ruff: noqa: E402

import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import (
    ActorType,
    ChapterStatus,
    DocumentStatus,
    ExportType,
    PacketStatus,
    PacketSentenceRole,
    PacketType,
    RelationType,
    RunStatus,
    SegmentType,
    SentenceStatus,
    TargetSegmentStatus,
)
from book_agent.domain.models import Chapter, Document, Sentence
from book_agent.domain.models.translation import AlignmentEdge, PacketSentenceMap, TargetSegment, TranslationPacket, TranslationRun
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.workflows import DocumentWorkflowService


CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>
"""

CONTENT_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Source Preserving EPUB</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine>
    <itemref idref="chap1" />
  </spine>
</package>
"""

NAV_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="chapter1.xhtml#ch1">Chapter One</a></li>
      </ol>
    </nav>
  </body>
</html>
"""

CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>Chapter One</title>
  </head>
  <body>
    <h1 id="ch1">Chapter One</h1>
    <p id="p1">Source preserving exports should keep the original XHTML structure.</p>
    <p id="p2">See <a id="r1" href="#fn1">1</a> for details.</p>
    <aside id="fn1"><p><a href="#r1">1</a> Footnote text.</p></aside>
  </body>
</html>
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourcePreservingEpubExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _build_sample_epub(self, tempdir: str) -> Path:
        epub_path = Path(tempdir) / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        return epub_path

    def _seed_translation_graph(
        self,
        session,
        chapter_id: str,
        blocks_and_sentences: list[tuple[str, str, str]],
    ) -> None:
        now = _utcnow()
        packet_json = {
            "current_blocks": [
                {"block_id": block_id, "sentence_ids": [sentence_id]}
                for block_id, sentence_id, _target_text in blocks_and_sentences
            ]
        }
        packet = TranslationPacket(
            id="55555555-5555-4555-8555-555555555555",
            chapter_id=chapter_id,
            block_start_id=blocks_and_sentences[0][0],
            block_end_id=blocks_and_sentences[-1][0],
            packet_type=PacketType.TRANSLATE,
            book_profile_version=1,
            chapter_brief_version=None,
            termbase_version=None,
            entity_snapshot_version=None,
            style_snapshot_version=None,
            packet_json=packet_json,
            risk_score=0.1,
            status=PacketStatus.TRANSLATED,
            created_at=now,
            updated_at=now,
        )
        run = TranslationRun(
            id="66666666-6666-4666-8666-666666666666",
            packet_id=packet.id,
            model_name="echo-worker",
            model_config_json={},
            prompt_version="test",
            attempt=1,
            status=RunStatus.SUCCEEDED,
            output_json={},
            token_in=2,
            token_out=2,
            cost_usd=0,
            latency_ms=1,
            created_at=now,
            updated_at=now,
        )
        session.add_all([packet, run])
        session.flush()
        targets = []
        edges = []
        packet_sentence_maps = []
        for ordinal, (block_id, sentence_id, target_text) in enumerate(blocks_and_sentences, start=1):
            target = TargetSegment(
                id=f"77777777-7777-4777-8777-77777777777{ordinal}",
                chapter_id=chapter_id,
                translation_run_id=run.id,
                ordinal=ordinal,
                text_zh=target_text,
                segment_type=SegmentType.SENTENCE,
                confidence=0.98,
                final_status=TargetSegmentStatus.DRAFT,
                created_at=now,
                updated_at=now,
            )
            edge = AlignmentEdge(
                id=f"88888888-8888-4888-8888-88888888888{ordinal}",
                sentence_id=sentence_id,
                target_segment_id=target.id,
                relation_type=RelationType.ONE_TO_ONE,
                confidence=0.99,
                created_by=ActorType.SYSTEM,
                created_at=now,
            )
            packet_sentence_map = PacketSentenceMap(
                packet_id=packet.id,
                sentence_id=sentence_id,
                role=PacketSentenceRole.CURRENT,
            )
            targets.append(target)
            edges.append(edge)
            packet_sentence_maps.append(packet_sentence_map)
        session.add_all(targets)
        session.flush()
        session.add_all([*edges, *packet_sentence_maps])

    def test_exports_source_preserving_zh_epub_with_translated_xhtml(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            epub_path = self._build_sample_epub(tempdir)
            artifacts = BootstrapOrchestrator().bootstrap_epub(epub_path)

            with self.session_factory() as session:
                BootstrapRepository(session).save(artifacts)
                chapter = session.get(Chapter, artifacts.chapters[0].id)
                document = session.get(Document, artifacts.document.id)
                block_p1 = next(
                    item for item in artifacts.blocks if str((item.source_span_json or {}).get("anchor") or "") == "p1"
                )
                block_p2 = next(
                    item for item in artifacts.blocks if str((item.source_span_json or {}).get("anchor") or "") == "p2"
                )
                block_heading = next(
                    item for item in artifacts.blocks if str((item.source_span_json or {}).get("anchor") or "") == "ch1"
                )
                block_fn1 = next(
                    item for item in artifacts.blocks if str((item.source_span_json or {}).get("anchor") or "") == "fn1"
                )
                sentence_heading = next(item for item in artifacts.sentences if item.block_id == block_heading.id)
                sentence_fn1 = next(item for item in artifacts.sentences if item.block_id == block_fn1.id)
                sentence_p1 = next(item for item in artifacts.sentences if item.block_id == block_p1.id)
                sentence_p2 = next(item for item in artifacts.sentences if item.block_id == block_p2.id)
                assert chapter is not None
                assert document is not None
                self._seed_translation_graph(
                    session,
                    chapter.id,
                    [
                        (
                            block_heading.id,
                            sentence_heading.id,
                            "章节标题应保持结构并进入中文导出。",
                        ),
                        (
                            block_p1.id,
                            sentence_p1.id,
                            "结构化导出的 EPUB 应该保留原始 XHTML。",
                        ),
                        (
                            block_p2.id,
                            sentence_p2.id,
                            "参见脚注 1 获取更多细节，并保留内联脚注链接。",
                        ),
                        (
                            block_fn1.id,
                            sentence_fn1.id,
                            "脚注文本应被保留并在源结构中完成翻译。",
                        ),
                    ],
                )
                chapter.status = ChapterStatus.QA_CHECKED
                document.status = DocumentStatus.ACTIVE
                sentence_heading.sentence_status = SentenceStatus.FINALIZED
                sentence_fn1.sentence_status = SentenceStatus.FINALIZED
                sentence_p1.sentence_status = SentenceStatus.FINALIZED
                sentence_p2.sentence_status = SentenceStatus.FINALIZED
                session.commit()

            with self.session_factory() as session:
                workflow = DocumentWorkflowService(session, export_root=Path(tempdir) / "exports")
                result = workflow.export_document(artifacts.document.id, ExportType.ZH_EPUB)

            exported_epub = Path(result.file_path)
            self.assertTrue(exported_epub.is_file())
            self.assertEqual(result.export_type, ExportType.ZH_EPUB.value)

            with zipfile.ZipFile(exported_epub) as archive:
                names = set(archive.namelist())
                self.assertIn("META-INF/container.xml", names)
                self.assertIn("OEBPS/nav.xhtml", names)
                self.assertIn("OEBPS/chapter1.xhtml", names)

                chapter_xhtml = archive.read("OEBPS/chapter1.xhtml").decode("utf-8")
                nav_xhtml = archive.read("OEBPS/nav.xhtml").decode("utf-8")

            self.assertIn("结构化导出的 EPUB 应该保留原始 XHTML。", chapter_xhtml)
            self.assertIn('id="ch1"', chapter_xhtml)
            self.assertIn('id="fn1"', chapter_xhtml)
            self.assertIn('href="#fn1"', chapter_xhtml)
            self.assertIn("参见脚注 1 获取更多细节，并保留内联脚注链接。", chapter_xhtml)
            self.assertIn('href="chapter1.xhtml#ch1"', nav_xhtml)
            self.assertIn("Chapter One", nav_xhtml)
            self.assertNotIn("Source preserving exports should keep the original XHTML structure.", chapter_xhtml)
