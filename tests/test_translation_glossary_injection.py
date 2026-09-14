# ruff: noqa: E402
"""Tests for M2.7b prompt injection — the document glossary feeds compiled context terms.

`MemoryService.load_compiled_context` resolves active document glossary
entries and the context compiler merges them with the packet's termbase terms
and chapter concepts, applying the same relevance filter to all of them. The
prompt builder (`workers.translator._sorted_term_lines`) renders the result.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import DocumentStatus, SourceType
from book_agent.domain.models import Document
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.chapter_memory import ChapterTranslationMemoryRepository
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.context_compile import ChapterContextCompiler
from book_agent.services.memory_service import MemoryService
from book_agent.translation.contracts import (
    CompiledTranslationContext,
    ContextPacket,
    PacketBlock,
    RelevantTerm,
)
from book_agent.workers.translator import _sorted_term_lines


def _enable_sqlite_fk(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _mk_context_packet(
    document_id: str,
    *,
    relevant_terms: list[RelevantTerm] | None = None,
) -> ContextPacket:
    return ContextPacket(
        packet_id="packet-1",
        document_id=document_id,
        chapter_id="chap-1",
        packet_type="translate",
        book_profile_version=1,
        current_blocks=[
            PacketBlock(
                block_id="b1",
                block_type="paragraph",
                sentence_ids=["s1"],
                text="The Agent calls the Transformer.",
            )
        ],
        relevant_terms=relevant_terms or [],
    )


class GlossaryPromptInjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        sqlite_path = Path(self.tempdir.name) / "book.db"
        self.engine = build_engine(
            f"sqlite+pysqlite:///{sqlite_path}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        event.listen(self.engine, "connect", _enable_sqlite_fk)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.doc_id = str(uuid4())
        with self.session_factory() as session:
            session.add(
                Document(
                    id=self.doc_id,
                    source_type=SourceType.PDF_TEXT,
                    file_fingerprint=f"fp-{self.doc_id[:8]}",
                    source_path="x.pdf",
                    title="T", author="A", src_lang="en", tgt_lang="zh",
                    status=DocumentStatus.ACTIVE,
                    parser_version=1, segmentation_version=1,
                )
            )
            session.commit()

    def _compile(self, session, *, relevant_terms: list[RelevantTerm] | None = None) -> CompiledTranslationContext:
        packet = _mk_context_packet(self.doc_id, relevant_terms=relevant_terms)
        return MemoryService(
            chapter_memory_repository=ChapterTranslationMemoryRepository(session),
            context_compiler=ChapterContextCompiler(),
        ).load_compiled_context(packet=packet).context

    def test_empty_glossary_adds_no_terms(self) -> None:
        with self.session_factory() as session:
            self.assertEqual(self._compile(session).relevant_terms, [])

    def test_relevant_locked_terms_are_merged_into_relevant_terms(self) -> None:
        with self.session_factory() as session:
            gs = GlossaryService(session)
            gs.lock_term(self.doc_id, "Agent", "智能体")
            gs.lock_term(self.doc_id, "Transformer", "变换器")
            session.commit()
        with self.session_factory() as session:
            term_map = {t.source_term: t for t in self._compile(session).relevant_terms}
        self.assertEqual(term_map["Agent"].target_term, "智能体")
        self.assertEqual(term_map["Agent"].lock_level, "locked")
        self.assertEqual(term_map["Transformer"].target_term, "变换器")

    def test_glossary_terms_absent_from_the_packet_are_filtered_out(self) -> None:
        # The glossary used to be appended wholesale; it now goes through the
        # same relevance filter as the packet's own terms.
        with self.session_factory() as session:
            gs = GlossaryService(session)
            gs.lock_term(self.doc_id, "Agent", "智能体")
            gs.lock_term(self.doc_id, "Retrieval", "检索")
            session.commit()
        with self.session_factory() as session:
            source_terms = [t.source_term for t in self._compile(session).relevant_terms]
        self.assertEqual(source_terms, ["Agent"])

    def test_packet_terms_win_over_glossary_on_conflict(self) -> None:
        with self.session_factory() as session:
            GlossaryService(session).lock_term(self.doc_id, "Agent", "智能体")
            session.commit()
        with self.session_factory() as session:
            preexisting = [RelevantTerm(source_term="Agent", target_term="代理人", lock_level="preferred")]
            agent_terms = [t for t in self._compile(session, relevant_terms=preexisting).relevant_terms if t.source_term == "Agent"]
        self.assertEqual([(t.target_term, t.lock_level) for t in agent_terms], [("代理人", "preferred")])

    def test_suggested_entries_with_empty_target_are_skipped(self) -> None:
        with self.session_factory() as session:
            from book_agent.services.terminology_miner import TermCandidate

            cand = TermCandidate(
                term="Agent",
                frequency=3,
                weight=4.5,
                first_seen_chapter_id="ch1",
                first_seen_block_anchor="b1",
                first_seen_block_ordinal=1,
                is_proper_noun=True,
                is_acronym=False,
                definition_boost=False,
            )
            GlossaryService(session).upsert_candidates(self.doc_id, [cand])
            session.commit()
        with self.session_factory() as session:
            self.assertEqual(self._compile(session).relevant_terms, [])

    def test_glossary_terms_appear_in_rendered_prompt_locked_first(self) -> None:
        with self.session_factory() as session:
            GlossaryService(session).lock_term(self.doc_id, "Transformer", "变换器")
            session.commit()
        with self.session_factory() as session:
            preexisting = [RelevantTerm(source_term="Agent", target_term="代理", lock_level="suggested")]
            lines = _sorted_term_lines(self._compile(session, relevant_terms=preexisting))
        self.assertEqual(len(lines), 2)
        self.assertIn("Transformer => 变换器", lines[0])
        self.assertIn("(locked)", lines[0])


if __name__ == "__main__":
    unittest.main()
