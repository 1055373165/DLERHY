"""Export history and atomic writes: re-exports keep created_at, bump version, record digests; failed writes leave the old file."""

from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from book_agent.app.main import create_app
from book_agent.domain.enums import ExportType
from book_agent.domain.models.review import Export, ExportVersion
from book_agent.export.atomic import atomic_path, atomic_write_text, export_lock
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.export import EXPORT_VERSION_RETENTION
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.review import ReviewService
from book_agent.services.translation import TranslationService
from book_agent.services.workflows import DocumentWorkflowService
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML


class AtomicWriteTests(unittest.TestCase):
    def test_failed_write_keeps_the_previous_file_and_leaves_no_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "artifact.html"
            atomic_write_text(target, "v1")
            with self.assertRaises(RuntimeError):
                with atomic_path(target) as temporary:
                    temporary.write_text("half", encoding="utf-8")
                    raise RuntimeError("renderer crashed")
            self.assertEqual(target.read_text(encoding="utf-8"), "v1")
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["artifact.html"])

    def test_writer_that_produces_nothing_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                with atomic_path(Path(tmp) / "empty.pdf"):
                    pass

    def test_lock_serialises_writers_of_one_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events: list[str] = []

            def writer(name: str) -> None:
                with export_lock(Path(tmp), "merged_html"):
                    events.append(f"{name}:start")
                    threading.Event().wait(0.05)
                    events.append(f"{name}:end")

            threads = [threading.Thread(target=writer, args=(name,)) for name in ("a", "b")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual([event.split(":")[1] for event in events], ["start", "end", "start", "end"])


class ExportVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / ".test-tmp")
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{root / 'exports.db'}", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        self.export_root = root / "exports"
        epub_path = root / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        artifacts = BootstrapOrchestrator().bootstrap_epub(epub_path)
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            service = TranslationService(TranslationRepository(session))
            for packet in artifacts.translation_packets:
                service.execute_packet(packet.id)
            for chapter in artifacts.chapters:
                ReviewService(ReviewRepository(session)).review_chapter(chapter.id)
            session.commit()
        self.document_id = artifacts.document.id

    def _export(self, session) -> Export:
        DocumentWorkflowService(session, export_root=self.export_root).export_document(self.document_id, ExportType.MERGED_HTML)
        session.commit()
        return session.scalars(select(Export).where(Export.export_type == ExportType.MERGED_HTML)).one()

    def test_reexport_bumps_version_keeps_first_created_at_and_records_history(self) -> None:
        with self.session_factory() as session:
            first = self._export(session)
            first_created = first.created_at
            self.assertEqual(first.version, 1)
            second = self._export(session)
            self.assertEqual(second.version, 2)
            self.assertEqual(second.created_at, first_created)
            versions = session.scalars(
                select(ExportVersion).where(ExportVersion.export_id == second.id).order_by(ExportVersion.version)
            ).all()
            self.assertEqual([row.version for row in versions], [1, 2])
            self.assertTrue(all(row.content_sha256 and row.byte_count for row in versions))
            self.assertTrue(versions[1].manifest_path.endswith(".json"))
            self.assertEqual(sorted(p.name for p in Path(second.file_path).parent.glob(".*.tmp")), [])

        app = create_app()
        app.state.session_factory = self.session_factory
        app.state.export_root = str(self.export_root)
        client = TestClient(app)
        self.addCleanup(client.close)
        response = client.get(f"/v1/documents/{self.document_id}/exports/{second.id}/versions")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["current_version"], [v["version"] for v in body["versions"]]), (2, [2, 1]))
        self.assertEqual(client.get(f"/v1/documents/{self.document_id}/exports/00000000-0000-0000-0000-000000000000/versions").status_code, 404)

    def test_a_direct_re_render_updates_the_digest_that_downloads_follow(self) -> None:
        from book_agent.domain.models import Chapter
        from book_agent.services.export import ExportService
        from book_agent.infra.repositories.export import ExportRepository

        with self.session_factory() as session:
            chapter_id = session.scalar(select(Chapter.id))
            service = ExportService(ExportRepository(session), output_root=self.export_root)
            service.export_bilingual_html(chapter_id, enforce_gate=False)
            session.commit()
            export = session.scalars(select(Export).where(Export.export_type == ExportType.BILINGUAL_HTML)).one()
            first_digest = export.content_sha256
            from book_agent.domain.models.translation import TargetSegment

            segment = session.scalars(select(TargetSegment)).first()
            segment.text_zh = "重新渲染后的译文。"
            session.flush()
            # A render outside the export use case (no separate digest stamping afterwards).
            service.export_bilingual_html(chapter_id, enforce_gate=False)
            session.commit()
            session.refresh(export)
            self.assertNotEqual(export.content_sha256, first_digest)
            digest = hashlib.sha256(Path(export.file_path).read_bytes()).hexdigest()
            self.assertEqual(export.content_sha256, digest)

    def test_history_is_capped(self) -> None:
        self.assertGreaterEqual(EXPORT_VERSION_RETENTION, 5)
        with patch("book_agent.infra.repositories.export.EXPORT_VERSION_RETENTION", 2), self.session_factory() as session:
            for _ in range(3):
                export = self._export(session)
            versions = session.scalars(select(ExportVersion.version).where(ExportVersion.export_id == export.id)).all()
            self.assertEqual(sorted(versions), [2, 3])


if __name__ == "__main__":
    unittest.main()
