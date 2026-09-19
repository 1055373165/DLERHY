"""The run executor must not hold a database connection across the LLM call.

Previously a translate work item kept one session (and its pooled connection)
open for the whole provider round-trip, so parallel workers exhausted the
pool; the LLM_CALL_FAILED event was also rolled back with the failed work.
"""

import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import select

from book_agent.app.runtime.document_run_executor import DocumentRunExecutor
from book_agent.domain.enums import PacketStatus, RunStatus
from book_agent.domain.event_kinds import LLM_CALL_FAILED, PACKET_TRANSLATED
from book_agent.domain.models.ops import Event
from book_agent.domain.models.translation import TranslationPacket, TranslationRun
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory, session_scope
from book_agent.services.workflows import DocumentWorkflowService
from book_agent.workers.translator import EchoTranslationWorker

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" /></rootfiles>
</container>
"""
CONTENT_OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Transactions</dc:title><dc:language>en</dc:language></metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="chap1" href="chapter1.xhtml" media-type="application/xhtml+xml" />
  </manifest>
  <spine><itemref idref="chap1" /></spine>
</package>
"""
NAV_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body><nav epub:type="toc"><ol><li><a href="chapter1.xhtml">Chapter One</a></li></ol></nav></body>
</html>
"""
CHAPTER_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body><h1>Chapter One</h1><p>Agents need memory to stay coherent. Context windows are finite.</p></body>
</html>
"""


class _PoolProbeWorker(EchoTranslationWorker):
    def __init__(self, engine, *, fail: bool = False) -> None:
        super().__init__()
        self._engine = engine
        self._fail = fail
        self.checked_out_during_call: list[int] = []

    def translate(self, task):
        self.checked_out_during_call.append(self._engine.pool.checkedout())
        if self._fail:
            raise RuntimeError("provider exploded")
        return super().translate(task)


class _DropLastAlignmentWorker(EchoTranslationWorker):
    def translate(self, task):
        result = super().translate(task)
        result.output.alignment_suggestions = result.output.alignment_suggestions[:-1]
        return result


class ExecutorTranslationTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.root = Path(tempdir.name)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{self.root / 'tx.db'}",
            connect_args={"check_same_thread": False},
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        epub_path = self.root / "book.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        with session_scope(self.session_factory) as session:
            DocumentWorkflowService(session, export_root=str(self.root / "exports")).bootstrap_document(epub_path)
        with self.session_factory() as session:
            self.packet_id = session.scalars(select(TranslationPacket.id)).first()

    def _executor(self, worker) -> DocumentRunExecutor:
        return DocumentRunExecutor(
            session_factory=self.session_factory,
            export_root=str(self.root / "exports"),
            translation_worker=worker,
        )

    def test_worker_runs_without_a_checked_out_connection(self) -> None:
        worker = _PoolProbeWorker(self.engine)

        result = self._executor(worker)._translate_single_packet(self.packet_id)

        self.assertEqual(worker.checked_out_during_call, [0])
        self.assertNotEqual(result["translation_run_id"], "already-translated")
        with self.session_factory() as session:
            self.assertEqual(session.get(TranslationPacket, self.packet_id).status, PacketStatus.TRANSLATED)

    def test_worker_failure_event_is_persisted(self) -> None:
        worker = _PoolProbeWorker(self.engine, fail=True)

        with self.assertRaises(RuntimeError):
            self._executor(worker)._translate_single_packet(self.packet_id)

        with self.session_factory() as session:
            failed_events = session.scalars(select(Event).where(Event.kind == LLM_CALL_FAILED)).all()
            packet = session.get(TranslationPacket, self.packet_id)
        self.assertEqual(len(failed_events), 1)
        self.assertEqual(failed_events[0].payload["error_message"], "provider exploded")
        self.assertEqual(failed_events[0].payload["error_code"], "unclassified")
        self.assertEqual(packet.status, PacketStatus.BUILT)

    def test_failed_attempt_is_recorded_as_a_failed_run_and_next_attempt_follows(self) -> None:
        with self.assertRaises(RuntimeError):
            self._executor(_PoolProbeWorker(self.engine, fail=True))._translate_single_packet(self.packet_id)
        self._executor(_PoolProbeWorker(self.engine))._translate_single_packet(self.packet_id)

        with self.session_factory() as session:
            runs = session.scalars(
                select(TranslationRun).where(TranslationRun.packet_id == self.packet_id).order_by(TranslationRun.attempt)
            ).all()
        self.assertEqual([(run.attempt, run.status, run.error_code) for run in runs], [
            (1, RunStatus.FAILED, "unclassified"),
            (2, RunStatus.SUCCEEDED, None),
        ])

    def test_incomplete_sentence_coverage_is_recorded_on_the_run(self) -> None:
        self._executor(_DropLastAlignmentWorker())._translate_single_packet(self.packet_id)

        with self.session_factory() as session:
            run = session.scalars(select(TranslationRun).where(TranslationRun.packet_id == self.packet_id)).one()
            translated_event = session.scalars(select(Event).where(Event.kind == PACKET_TRANSLATED)).one()
        self.assertEqual(run.status, RunStatus.SUCCEEDED)
        self.assertEqual(run.error_code, "output_sentence_coverage_incomplete")
        self.assertEqual(len(translated_event.payload["output_validation"]["uncovered_sentence_ids"]), 1)


if __name__ == "__main__":
    unittest.main()
