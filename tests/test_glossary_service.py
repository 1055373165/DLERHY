# ruff: noqa: E402
"""Tests for M2.6 document-level GlossaryService."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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

from book_agent.domain.enums import (
    DocumentStatus,
    LockLevel,
    SourceType,
    TermStatus,
    TermType,
)
from book_agent.domain.models import Document
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.terminology_miner import TermCandidate


def _enable_sqlite_fk(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _candidate(term: str, frequency: int = 3, is_proper_noun: bool = False) -> TermCandidate:
    return TermCandidate(
        term=term,
        frequency=frequency,
        weight=float(frequency),
        first_seen_chapter_id="ch1",
        first_seen_block_anchor="b1",
        first_seen_block_ordinal=1,
        is_proper_noun=is_proper_noun,
        is_acronym=False,
        definition_boost=False,
    )


class GlossaryServiceTests(unittest.TestCase):
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
                    file_fingerprint="fp",
                    source_path="x.pdf",
                    title="T", author="A", src_lang="en", tgt_lang="zh",
                    status=DocumentStatus.ACTIVE,
                    parser_version=1, segmentation_version=1,
                )
            )
            session.commit()

    # --- upsert_candidates ---

    def test_upsert_candidates_creates_suggested_rows(self) -> None:
        with self.session_factory() as session:
            svc = GlossaryService(session)
            result = svc.upsert_candidates(
                self.doc_id,
                [_candidate("agent system"), _candidate("vector store")],
            )
            session.commit()
            self.assertEqual(result.inserted, 2)
            self.assertEqual(result.skipped, 0)
        with self.session_factory() as session:
            svc = GlossaryService(session)
            entries = svc.list_document_entries(self.doc_id)
            self.assertEqual(len(entries), 2)
            for e in entries:
                self.assertEqual(e.lock_level, LockLevel.SUGGESTED)
                self.assertEqual(e.status, TermStatus.ACTIVE)
                self.assertEqual(e.target_term, "")

    def test_upsert_is_idempotent(self) -> None:
        cands = [_candidate("agent system")]
        with self.session_factory() as session:
            svc = GlossaryService(session)
            r1 = svc.upsert_candidates(self.doc_id, cands)
            session.commit()
            r2 = svc.upsert_candidates(self.doc_id, cands)
            session.commit()
            self.assertEqual(r1.inserted, 1)
            self.assertEqual(r2.inserted, 0)
            self.assertEqual(r2.skipped, 1)

    def test_upsert_skips_already_locked_terms(self) -> None:
        with self.session_factory() as session:
            svc = GlossaryService(session)
            svc.lock_term(self.doc_id, "Transformer", "变换器")
            session.commit()
        with self.session_factory() as session:
            svc = GlossaryService(session)
            result = svc.upsert_candidates(
                self.doc_id,
                [_candidate("Transformer"), _candidate("Agent")],
            )
            session.commit()
            self.assertEqual(result.inserted, 1)
            self.assertEqual(result.skipped, 1)
            locked = svc.get_locked_terms(self.doc_id)
            self.assertEqual(locked, {"Transformer": "变换器"})

    # --- lock_term ---

    def test_lock_term_creates_active_locked_entry(self) -> None:
        with self.session_factory() as session:
            svc = GlossaryService(session)
            entry = svc.lock_term(self.doc_id, "Agent", "智能体")
            session.commit()
            self.assertEqual(entry.lock_level, LockLevel.LOCKED)
            self.assertEqual(entry.status, TermStatus.ACTIVE)
            self.assertEqual(entry.version, 1)
        with self.session_factory() as session:
            svc = GlossaryService(session)
            self.assertEqual(svc.get_locked_terms(self.doc_id), {"Agent": "智能体"})

    def test_lock_term_upgrades_suggested_entry(self) -> None:
        with self.session_factory() as session:
            svc = GlossaryService(session)
            svc.upsert_candidates(self.doc_id, [_candidate("Agent")])
            session.commit()
        with self.session_factory() as session:
            svc = GlossaryService(session)
            entry = svc.lock_term(self.doc_id, "Agent", "智能体")
            session.commit()
            self.assertEqual(entry.lock_level, LockLevel.LOCKED)
            self.assertEqual(entry.version, 2)
        with self.session_factory() as session:
            svc = GlossaryService(session)
            active = svc.list_document_entries(self.doc_id)
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].target_term, "智能体")
            # The suggested row should have been superseded.
            all_rows = svc.list_document_entries(self.doc_id, include_superseded=True)
            self.assertEqual(len(all_rows), 2)
            superseded = [r for r in all_rows if r.status == TermStatus.SUPERSEDED]
            self.assertEqual(len(superseded), 1)
            self.assertEqual(superseded[0].lock_level, LockLevel.SUGGESTED)

    def test_lock_term_idempotent_with_same_target(self) -> None:
        with self.session_factory() as session:
            svc = GlossaryService(session)
            svc.lock_term(self.doc_id, "Agent", "智能体")
            session.commit()
        with self.session_factory() as session:
            svc = GlossaryService(session)
            svc.lock_term(self.doc_id, "Agent", "智能体")
            session.commit()
            entries = svc.list_document_entries(self.doc_id, include_superseded=True)
            # No new version created — idempotent.
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].version, 1)

    def test_lock_term_supersedes_on_target_change(self) -> None:
        with self.session_factory() as session:
            svc = GlossaryService(session)
            svc.lock_term(self.doc_id, "Agent", "代理")
            session.commit()
        with self.session_factory() as session:
            svc = GlossaryService(session)
            svc.lock_term(self.doc_id, "Agent", "智能体")
            session.commit()
        with self.session_factory() as session:
            svc = GlossaryService(session)
            self.assertEqual(svc.get_locked_terms(self.doc_id), {"Agent": "智能体"})
            all_rows = svc.list_document_entries(self.doc_id, include_superseded=True)
            self.assertEqual(len(all_rows), 2)

    def test_lock_term_rejects_empty_inputs(self) -> None:
        with self.session_factory() as session:
            svc = GlossaryService(session)
            with self.assertRaises(ValueError):
                svc.lock_term(self.doc_id, "  ", "智能体")
            with self.assertRaises(ValueError):
                svc.lock_term(self.doc_id, "Agent", "  ")

    # --- unlock_term ---

    def test_unlock_term_demotes_locked_entry_to_suggested(self) -> None:
        with self.session_factory() as session:
            svc = GlossaryService(session)
            svc.lock_term(self.doc_id, "Agent", "智能体")
            session.commit()
        with self.session_factory() as session:
            svc = GlossaryService(session)
            demoted = svc.unlock_term(self.doc_id, "Agent")
            session.commit()
            self.assertIsNotNone(demoted)
            self.assertEqual(demoted.lock_level, LockLevel.SUGGESTED)
            self.assertEqual(demoted.target_term, "智能体")  # preserved
        with self.session_factory() as session:
            svc = GlossaryService(session)
            self.assertEqual(svc.get_locked_terms(self.doc_id), {})

    def test_unlock_unknown_term_is_noop(self) -> None:
        with self.session_factory() as session:
            svc = GlossaryService(session)
            self.assertIsNone(svc.unlock_term(self.doc_id, "Nonexistent"))

    # --- get_locked_terms filtering ---

    def test_get_locked_terms_excludes_suggested_and_superseded(self) -> None:
        with self.session_factory() as session:
            svc = GlossaryService(session)
            svc.upsert_candidates(self.doc_id, [_candidate("suggested_term")])
            svc.lock_term(self.doc_id, "locked_term", "锁定的")
            svc.lock_term(self.doc_id, "locked_term", "改了")  # supersedes v1
            session.commit()
        with self.session_factory() as session:
            svc = GlossaryService(session)
            locked = svc.get_locked_terms(self.doc_id)
            self.assertEqual(locked, {"locked_term": "改了"})


if __name__ == "__main__":
    unittest.main()
