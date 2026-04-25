# ruff: noqa: E402
"""Tests for M2.7b prompt injection — glossary feeds into ContextPacket.

Verifies `TranslationService._inject_locked_glossary` correctly merges
document-level locked glossary entries into `compiled_context_packet
.relevant_terms`. The existing prompt builder
(`workers.translator._sorted_term_lines`) already renders these in the
system prompt, so we additionally end-to-end-check that the glossary
text appears in the rendered prompt string.
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
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.translation import TranslationService
from book_agent.workers.contracts import (
    CompiledTranslationContext,
    PacketBlock,
    RelevantTerm,
)
from book_agent.workers.translator import _sorted_term_lines


def _enable_sqlite_fk(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _mk_compiled_context(
    document_id: str,
    *,
    relevant_terms: list[RelevantTerm] | None = None,
) -> CompiledTranslationContext:
    return CompiledTranslationContext(
        packet_id="packet-1",
        source_packet_id="packet-1",
        document_id=document_id,
        chapter_id="chap-1",
        packet_type="translate",
        book_profile_version=1,
        context_compile_version="v1",
        memory_version_used=None,
        compile_metadata={},
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

    def _service(self, session) -> TranslationService:
        return TranslationService(repository=TranslationRepository(session))

    def test_empty_glossary_leaves_context_unchanged(self) -> None:
        with self.session_factory() as session:
            svc = self._service(session)
            ctx = _mk_compiled_context(self.doc_id)
            result = svc._inject_locked_glossary(ctx)
            self.assertIs(result, ctx)
            self.assertEqual(result.relevant_terms, [])

    def test_locked_terms_are_merged_into_relevant_terms(self) -> None:
        with self.session_factory() as session:
            gs = GlossaryService(session)
            gs.lock_term(self.doc_id, "Agent", "智能体")
            gs.lock_term(self.doc_id, "Transformer", "变换器")
            session.commit()
        with self.session_factory() as session:
            svc = self._service(session)
            ctx = _mk_compiled_context(self.doc_id)
            result = svc._inject_locked_glossary(ctx)
            term_map = {t.source_term: t for t in result.relevant_terms}
            self.assertIn("Agent", term_map)
            self.assertIn("Transformer", term_map)
            self.assertEqual(term_map["Agent"].target_term, "智能体")
            self.assertEqual(term_map["Agent"].lock_level, "locked")
            self.assertEqual(term_map["Transformer"].target_term, "变换器")

    def test_existing_relevant_terms_win_on_conflict(self) -> None:
        # Context compiler may have already chosen a chapter-scope target
        # for a term; the document-level injection must NOT override it.
        with self.session_factory() as session:
            GlossaryService(session).lock_term(self.doc_id, "Agent", "智能体")
            session.commit()
        with self.session_factory() as session:
            svc = self._service(session)
            preexisting = [
                RelevantTerm(source_term="Agent", target_term="代理人", lock_level="preferred")
            ]
            ctx = _mk_compiled_context(self.doc_id, relevant_terms=preexisting)
            result = svc._inject_locked_glossary(ctx)
            agent_terms = [t for t in result.relevant_terms if t.source_term == "Agent"]
            self.assertEqual(len(agent_terms), 1)
            self.assertEqual(agent_terms[0].target_term, "代理人")
            self.assertEqual(agent_terms[0].lock_level, "preferred")

    def test_suggested_entries_with_empty_target_are_skipped(self) -> None:
        # SUGGESTED rows from upsert_candidates have empty target_term.
        # We must not surface "Agent => " to the LLM.
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
            svc = self._service(session)
            ctx = _mk_compiled_context(self.doc_id)
            result = svc._inject_locked_glossary(ctx)
            self.assertEqual(result.relevant_terms, [])

    def test_injected_terms_appear_in_rendered_prompt(self) -> None:
        with self.session_factory() as session:
            GlossaryService(session).lock_term(self.doc_id, "Agent", "智能体")
            session.commit()
        with self.session_factory() as session:
            svc = self._service(session)
            ctx = _mk_compiled_context(self.doc_id)
            merged = svc._inject_locked_glossary(ctx)
            # Use the worker's existing renderer to verify the prompt text.
            lines = _sorted_term_lines(merged)
            joined = "\n".join(lines)
            self.assertIn("Agent => 智能体", joined)
            self.assertIn("(locked)", joined)

    def test_locked_terms_sort_before_suggested_in_prompt(self) -> None:
        # Mix of locked + preferred + suggested; renderer must put
        # locked first so the LLM sees them at top.
        with self.session_factory() as session:
            gs = GlossaryService(session)
            gs.lock_term(self.doc_id, "RAG", "检索增强生成")  # locked
            session.commit()
        with self.session_factory() as session:
            svc = self._service(session)
            preexisting = [
                RelevantTerm(source_term="Apple", target_term="苹果", lock_level="suggested"),
                RelevantTerm(source_term="Banana", target_term="香蕉", lock_level="preferred"),
            ]
            ctx = _mk_compiled_context(self.doc_id, relevant_terms=preexisting)
            merged = svc._inject_locked_glossary(ctx)
            lines = _sorted_term_lines(merged)
            self.assertEqual(len(lines), 3)
            # First line should be the locked term (lowest sort key).
            self.assertIn("(locked)", lines[0])
            self.assertIn("RAG", lines[0])


if __name__ == "__main__":
    unittest.main()
