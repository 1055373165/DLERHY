# ruff: noqa: E402
"""Tests for M2.7 worker integration — glossary post-validation events.

Drives `TranslationService._emit_glossary_violations` against a fake
bundle / artifacts pair and verifies that:

  1. With NO locked glossary, zero events are emitted.
  2. With a locked glossary that the target obeys, zero events.
  3. With a locked glossary the target violates, exactly one
     GLOSSARY_VIOLATION event per breached rule, payload carrying
     source_term, expected_target, severity_hint, etc.
  4. Per-document scoping — locks for OTHER documents do not fire.
"""

import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from types import SimpleNamespace

from sqlalchemy import event, select
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ.setdefault("BOOK_AGENT_TRANSLATION_BACKEND", "echo")
os.environ.setdefault("BOOK_AGENT_TRANSLATION_MODEL", "echo-worker")
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from book_agent.domain.enums import DocumentStatus, SourceType
from book_agent.domain.event_kinds import GLOSSARY_VIOLATION
from book_agent.domain.models import Document
from book_agent.domain.models.ops import Event
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.translation import (
    TranslationPacketBundle,
    TranslationRepository,
)
from book_agent.services.glossary_service import GlossaryService
from book_agent.services.translation import (
    TranslationExecutionArtifacts,
    TranslationService,
)


def _enable_sqlite_fk(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@dataclass
class _FakeSentence:
    source_text: str
    id: str = ""


@dataclass
class _FakePacket:
    id: str
    chapter_id: str


@dataclass
class _FakeContextPacket:
    document_id: str
    chapter_id: str


class _FakeBundle:
    def __init__(
        self,
        *,
        document_id: str,
        chapter_id: str,
        packet_id: str,
        sources: list[str],
    ) -> None:
        self.context_packet = _FakeContextPacket(
            document_id=document_id, chapter_id=chapter_id
        )
        self.packet = _FakePacket(id=packet_id, chapter_id=chapter_id)
        self.current_sentences = [_FakeSentence(source_text=s) for s in sources]


def _mk_artifacts(*, run_id: str, targets: list[str]) -> TranslationExecutionArtifacts:
    """Build a minimal artifacts shape — only fields read by the
    post-validator are populated. Avoids constructing real ORM rows."""
    target_segments = [
        SimpleNamespace(text_zh=text) for text in targets
    ]
    run = SimpleNamespace(id=run_id)
    return TranslationExecutionArtifacts(
        translation_run=run,
        target_segments=target_segments,
        alignment_edges=[],
        updated_sentences=[],
    )


class TranslationGlossaryPostValidationTests(unittest.TestCase):
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
        self.other_doc_id = str(uuid4())
        with self.session_factory() as session:
            for did in (self.doc_id, self.other_doc_id):
                session.add(
                    Document(
                        id=did,
                        source_type=SourceType.PDF_TEXT,
                        file_fingerprint=f"fp-{did[:8]}",
                        source_path="x.pdf",
                        title="T", author="A", src_lang="en", tgt_lang="zh",
                        status=DocumentStatus.ACTIVE,
                        parser_version=1, segmentation_version=1,
                    )
                )
            session.commit()

    def _make_service(self, session) -> TranslationService:
        # All dependencies default — we only exercise _emit_glossary_violations
        # which uses repository.session and our injected GlossaryService.
        return TranslationService(repository=TranslationRepository(session))

    def _read_glossary_events(self, session) -> list[Event]:
        return list(
            session.scalars(
                select(Event).where(Event.kind == GLOSSARY_VIOLATION)
            ).all()
        )

    def test_no_locked_glossary_emits_no_events(self) -> None:
        with self.session_factory() as session:
            svc = self._make_service(session)
            bundle = _FakeBundle(
                document_id=self.doc_id,
                chapter_id="chap-1",
                packet_id="packet-1",
                sources=["The Agent coordinates tools."],
            )
            artifacts = _mk_artifacts(run_id=str(uuid4()), targets=["这个代理协调工具。"])
            svc._emit_glossary_violations(
                bundle=bundle, artifacts=artifacts, run_id="run-1"
            )
            session.flush()
            self.assertEqual(self._read_glossary_events(session), [])

    def test_obeyed_locked_glossary_emits_no_events(self) -> None:
        with self.session_factory() as session:
            GlossaryService(session).lock_term(self.doc_id, "Agent", "智能体")
            session.commit()
        with self.session_factory() as session:
            svc = self._make_service(session)
            bundle = _FakeBundle(
                document_id=self.doc_id,
                chapter_id="chap-1",
                packet_id="packet-1",
                sources=["The Agent coordinates tools."],
            )
            artifacts = _mk_artifacts(run_id=str(uuid4()), targets=["智能体协调工具。"])
            svc._emit_glossary_violations(
                bundle=bundle, artifacts=artifacts, run_id="run-1"
            )
            session.flush()
            self.assertEqual(self._read_glossary_events(session), [])

    def test_violation_emits_glossary_violation_event(self) -> None:
        with self.session_factory() as session:
            GlossaryService(session).lock_term(self.doc_id, "Agent", "智能体")
            session.commit()
        run_id = str(uuid4())
        with self.session_factory() as session:
            svc = self._make_service(session)
            bundle = _FakeBundle(
                document_id=self.doc_id,
                chapter_id="chap-1",
                packet_id="packet-1",
                sources=["The Agent coordinates tools."],
            )
            artifacts = _mk_artifacts(run_id=run_id, targets=["这个代理协调工具。"])
            svc._emit_glossary_violations(
                bundle=bundle, artifacts=artifacts, run_id="run-1"
            )
            session.flush()
            events = self._read_glossary_events(session)
            self.assertEqual(len(events), 1)
            payload = events[0].payload
            self.assertEqual(payload["source_term"], "Agent")
            self.assertEqual(payload["expected_target"], "智能体")
            self.assertEqual(payload["severity_hint"], "hard")
            self.assertEqual(payload["source_match_count"], 1)
            self.assertEqual(payload["target_match_count"], 0)
            self.assertEqual(payload["document_id"], self.doc_id)

    def test_other_documents_locks_do_not_fire(self) -> None:
        with self.session_factory() as session:
            GlossaryService(session).lock_term(self.other_doc_id, "Agent", "智能体")
            session.commit()
        with self.session_factory() as session:
            svc = self._make_service(session)
            # The bundle is for self.doc_id, but the lock lives on other_doc_id.
            bundle = _FakeBundle(
                document_id=self.doc_id,
                chapter_id="chap-1",
                packet_id="packet-1",
                sources=["The Agent coordinates tools."],
            )
            artifacts = _mk_artifacts(run_id=str(uuid4()), targets=["代理协调工具。"])
            svc._emit_glossary_violations(
                bundle=bundle, artifacts=artifacts, run_id="run-1"
            )
            session.flush()
            self.assertEqual(self._read_glossary_events(session), [])

    def test_multiple_violations_emit_one_event_each(self) -> None:
        with self.session_factory() as session:
            gs = GlossaryService(session)
            gs.lock_term(self.doc_id, "Agent", "智能体")
            gs.lock_term(self.doc_id, "Transformer", "变换器")
            session.commit()
        with self.session_factory() as session:
            svc = self._make_service(session)
            bundle = _FakeBundle(
                document_id=self.doc_id,
                chapter_id="chap-1",
                packet_id="packet-1",
                sources=["The Agent uses a Transformer."],
            )
            # Both terms missing in target — two violations.
            artifacts = _mk_artifacts(
                run_id=str(uuid4()),
                targets=["这个代理使用了一个变换器模型。"],  # contains 变换器
            )
            svc._emit_glossary_violations(
                bundle=bundle, artifacts=artifacts, run_id="run-1"
            )
            session.flush()
            events = self._read_glossary_events(session)
            # Only Agent violated (Transformer is honoured).
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].payload["source_term"], "Agent")


if __name__ == "__main__":
    unittest.main()
