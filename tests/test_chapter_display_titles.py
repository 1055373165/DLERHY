"""The interface and the export must name a chapter the same way.

The 逐章审校 page used to print the raw source title, which for a web page is the document's
own <title> — the article's name with the site's name repeated after it. The title now comes
from the backend, decided by the same function the merged export uses.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from book_agent.application.chapter_titles import display_titles
from book_agent.domain.enums import (
    ActorType,
    ArtifactStatus,
    BlockType,
    ChapterStatus,
    DocumentStatus,
    PacketStatus,
    PacketType,
    ProtectedPolicy,
    RelationType,
    RunStatus,
    SegmentType,
    SentenceStatus,
    SourceType,
    TargetSegmentStatus,
)
from book_agent.domain.models.document import Block, Chapter, Document, Sentence
from book_agent.domain.models.translation import (
    AlignmentEdge,
    TargetSegment,
    TranslationPacket,
    TranslationRun,
)
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


class ChapterDisplayTitleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _build(self, *, source_type: SourceType, document_title: str, blocks: list[tuple[BlockType, str, str | None]]) -> tuple[str, str]:
        """A document with one chapter whose blocks are (kind, English, Chinese-or-None)."""
        with self.session_factory() as session:
            document = Document(
                source_type=source_type,
                file_fingerprint=f"fp-{source_type.value}",
                title=document_title,
                title_src=document_title,
                title_tgt=None,
                status=DocumentStatus.ACTIVE,
                created_at=NOW,
                updated_at=NOW,
            )
            session.add(document)
            session.flush()
            chapter = Chapter(
                document_id=document.id,
                ordinal=1,
                title_src=document_title,
                title_tgt=None,
                status=ChapterStatus.TRANSLATED,
                metadata_json={},
                created_at=NOW,
                updated_at=NOW,
            )
            session.add(chapter)
            session.flush()
            for ordinal, (block_type, source_text, target_text) in enumerate(blocks, start=1):
                block = Block(
                    chapter_id=chapter.id,
                    ordinal=ordinal,
                    block_type=block_type,
                    source_text=source_text,
                    normalized_text=source_text,
                    source_span_json={},
                    parse_confidence=0.95,
                    protected_policy=ProtectedPolicy.TRANSLATE,
                    status=ArtifactStatus.ACTIVE,
                    created_at=NOW,
                    updated_at=NOW,
                )
                session.add(block)
                session.flush()
                sentence = Sentence(
                    block_id=block.id,
                    chapter_id=chapter.id,
                    document_id=document.id,
                    ordinal_in_block=1,
                    source_text=source_text,
                    normalized_text=source_text,
                    source_lang="en",
                    translatable=True,
                    source_span_json={},
                    upstream_confidence=0.99,
                    sentence_status=SentenceStatus.TRANSLATED,
                    active_version=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
                session.add(sentence)
                session.flush()
                if target_text is None:
                    continue
                packet = TranslationPacket(
                    chapter_id=chapter.id,
                    block_start_id=block.id,
                    block_end_id=block.id,
                    packet_type=PacketType.TRANSLATE,
                    book_profile_version=1,
                    packet_json={"current_blocks": [{"block_id": block.id, "sentence_ids": [sentence.id]}]},
                    risk_score=0.1,
                    status=PacketStatus.TRANSLATED,
                    created_at=NOW,
                    updated_at=NOW,
                )
                session.add(packet)
                session.flush()
                run = TranslationRun(
                    packet_id=packet.id,
                    model_name="echo-worker",
                    model_config_json={},
                    prompt_version="test",
                    attempt=1,
                    status=RunStatus.SUCCEEDED,
                    output_json={},
                    token_in=1,
                    token_out=1,
                    cost_usd=0,
                    latency_ms=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
                session.add(run)
                session.flush()
                target = TargetSegment(
                    chapter_id=chapter.id,
                    translation_run_id=run.id,
                    ordinal=ordinal,
                    text_zh=target_text,
                    segment_type=SegmentType.SENTENCE,
                    confidence=0.95,
                    final_status=TargetSegmentStatus.DRAFT,
                    created_at=NOW,
                    updated_at=NOW,
                )
                session.add(target)
                session.flush()
                session.add(
                    AlignmentEdge(
                        sentence_id=sentence.id,
                        target_segment_id=target.id,
                        relation_type=RelationType.ONE_TO_ONE,
                        confidence=0.99,
                        created_by=ActorType.SYSTEM,
                        created_at=NOW,
                    )
                )
            session.commit()
            return document.id, chapter.id

    def _title(self, document_id: str, chapter_id: str) -> str | None:
        with self.session_factory() as session:
            document = session.get(Document, document_id)
            chapter = session.get(Chapter, chapter_id)
            return display_titles(session, document, [chapter])[chapter_id]

    def test_a_web_page_is_named_by_its_article_heading_not_the_document_title(self) -> None:
        document_id, chapter_id = self._build(
            source_type=SourceType.WEB,
            # What the page's <title> says, site name and all.
            document_title="Scaling Managed Agents: Decoupling the brain from the hands \\ Anthropic",
            blocks=[
                (BlockType.PARAGRAPH, "By the Anthropic team", "作者：Anthropic 团队"),
                (BlockType.HEADING, "Scaling Managed Agents", "规模化托管代理"),
                (BlockType.PARAGRAPH, "Momentum matters.", "势头很重要。"),
            ],
        )
        self.assertEqual(self._title(document_id, chapter_id), "规模化托管代理")

    def test_a_web_page_with_no_translated_heading_falls_back_to_the_document_title(self) -> None:
        document_id, chapter_id = self._build(
            source_type=SourceType.WEB,
            document_title="Scaling Managed Agents",
            blocks=[(BlockType.PARAGRAPH, "Momentum matters.", "势头很重要。")],
        )
        self.assertEqual(self._title(document_id, chapter_id), "Scaling Managed Agents")

    def test_a_book_chapter_is_named_by_its_own_first_heading(self) -> None:
        document_id, chapter_id = self._build(
            source_type=SourceType.EPUB,
            document_title="Momentum Handbook",
            blocks=[
                (BlockType.HEADING, "Why Momentum Matters", "势头为什么重要"),
                (BlockType.PARAGRAPH, "Momentum is the tendency of a trend to continue.", "势头是趋势延续的倾向。"),
            ],
        )
        self.assertEqual(self._title(document_id, chapter_id), "势头为什么重要")

    def test_an_untranslated_chapter_keeps_the_source_title(self) -> None:
        document_id, chapter_id = self._build(
            source_type=SourceType.EPUB,
            document_title="Momentum Handbook",
            blocks=[(BlockType.HEADING, "Why Momentum Matters", None)],
        )
        self.assertEqual(self._title(document_id, chapter_id), "Momentum Handbook")


if __name__ == "__main__":
    unittest.main()
