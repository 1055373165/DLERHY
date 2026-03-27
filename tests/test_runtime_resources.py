import unittest
from datetime import datetime, timezone
from uuid import uuid4

from book_agent.app.runtime.controllers.review_controller import ReviewController
from book_agent.domain.enums import (
    ChapterRunPhase,
    ChapterRunStatus,
    ChapterStatus,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    JobScopeType,
    PacketStatus,
    PacketType,
    ReviewSessionStatus,
    ReviewTerminalityState,
    SourceType,
)
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import ChapterRun, DocumentRun, ReviewSession
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository
from book_agent.services.run_control import RunControlService


class RuntimeResourcesPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def test_run_summary_includes_runtime_v2_resource_counts(self) -> None:
        with self.session_factory() as session:
            doc = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"runtime-summary-{uuid4()}",
                source_path="/tmp/runtime.epub",
                title="Runtime Summary",
                author="Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(doc)
            session.flush()

            chapter = Chapter(
                document_id=doc.id,
                ordinal=1,
                title_src="Chapter 1",
                status=ChapterStatus.PACKET_BUILT,
                metadata_json={},
            )
            session.add(chapter)
            session.flush()

            packet = TranslationPacket(
                chapter_id=chapter.id,
                block_start_id=None,
                block_end_id=None,
                packet_type=PacketType.TRANSLATE,
                book_profile_version=1,
                chapter_brief_version=1,
                termbase_version=1,
                entity_snapshot_version=1,
                style_snapshot_version=1,
                packet_json={"packet_ordinal": 1, "input_version_bundle": {"chapter_id": chapter.id}},
                risk_score=0.1,
                status=PacketStatus.BUILT,
            )
            session.add(packet)
            session.flush()

            run = DocumentRun(
                document_id=doc.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="test",
                priority=100,
                status_detail_json={},
            )
            session.add(run)
            session.commit()

        with self.session_factory() as session:
            runtime_repo = RuntimeResourcesRepository(session)
            chapter_run = runtime_repo.ensure_chapter_run(
                run_id=run.id,
                document_id=doc.id,
                chapter_id=chapter.id,
            )
            runtime_repo.ensure_packet_task(
                chapter_run_id=chapter_run.id,
                packet_id=packet.id,
                packet_generation=1,
            )
            runtime_repo.upsert_checkpoint(
                run_id=run.id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=chapter.id,
                checkpoint_key="controller_runner.v1",
                checkpoint_json={"cursor": 1},
                generation=1,
            )
            session.commit()

        with self.session_factory() as session:
            service = RunControlService(RunControlRepository(session))
            summary = service.get_run_summary(run.id)
            v2 = summary.status_detail_json["runtime_v2"]
            self.assertIsNone(v2["runtime_bundle_revision_id"])
            self.assertEqual(v2["chapter_run_count"], 1)
            self.assertEqual(v2["packet_task_count"], 1)
            self.assertEqual(v2["review_session_count"], 0)
            self.assertEqual(v2["runtime_checkpoint_count"], 1)

    def test_checkpoint_upsert_updates_in_place(self) -> None:
        with self.session_factory() as session:
            doc = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"runtime-checkpoint-{uuid4()}",
                source_path="/tmp/runtime.epub",
                title="Runtime Checkpoint",
                author="Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(doc)
            session.flush()

            chapter = Chapter(
                document_id=doc.id,
                ordinal=1,
                title_src="Chapter 1",
                status=ChapterStatus.PACKET_BUILT,
                metadata_json={},
            )
            session.add(chapter)
            session.flush()

            run = DocumentRun(
                document_id=doc.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="test",
                priority=100,
                status_detail_json={},
            )
            session.add(run)
            session.commit()

        with self.session_factory() as session:
            repo = RuntimeResourcesRepository(session)
            first = repo.upsert_checkpoint(
                run_id=run.id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=chapter.id,
                checkpoint_key="controller_runner.v1",
                checkpoint_json={"cursor": 1},
                generation=1,
            )
            second = repo.upsert_checkpoint(
                run_id=run.id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=chapter.id,
                checkpoint_key="controller_runner.v1",
                checkpoint_json={"cursor": 2},
                generation=2,
            )
            self.assertEqual(first.id, second.id)
            self.assertEqual(second.generation, 2)
            self.assertEqual(second.checkpoint_json["cursor"], 2)


class ReviewControllerPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed_review_context(self) -> tuple[str, str, str, str]:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"review-controller-{uuid4()}",
                source_path="/tmp/review-controller.epub",
                title="Review Controller",
                author="Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()

            chapter = Chapter(
                document_id=document.id,
                ordinal=1,
                title_src="Chapter 1",
                status=ChapterStatus.PACKET_BUILT,
                metadata_json={},
            )
            session.add(chapter)
            session.flush()

            run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                runtime_bundle_revision_id=str(uuid4()),
                requested_by="test",
                priority=100,
                status_detail_json={},
            )
            session.add(run)
            session.flush()

            chapter_run = ChapterRun(
                run_id=run.id,
                document_id=document.id,
                chapter_id=chapter.id,
                desired_phase=ChapterRunPhase.REVIEW,
                observed_phase=ChapterRunPhase.REVIEW,
                status=ChapterRunStatus.ACTIVE,
                generation=3,
                observed_generation=3,
                conditions_json={},
                status_detail_json={},
            )
            session.add(chapter_run)
            session.commit()
            return run.id, document.id, chapter.id, chapter_run.id

    def test_review_controller_reconcile_is_mirror_only_for_existing_session(self) -> None:
        run_id, document_id, chapter_id, chapter_run_id = self._seed_review_context()
        terminal_at = datetime(2026, 3, 1, tzinfo=timezone.utc)

        with self.session_factory() as session:
            repo = RuntimeResourcesRepository(session)
            chapter_run = repo.get_chapter_run(chapter_run_id)
            created = repo.ensure_review_session(
                chapter_run_id=chapter_run.id,
                desired_generation=chapter_run.generation,
                observed_generation=chapter_run.observed_generation - 1,
                scope_json={"legacy_scope": True},
                runtime_bundle_revision_id=None,
            )
            repo.update_review_session(
                created.id,
                status=ReviewSessionStatus.PAUSED,
                terminality_state=ReviewTerminalityState.BLOCKED,
                conditions_json={"manual_hold": {"reason": "awaiting-human"}},
                status_detail_json={"issue_count": 2},
                last_terminal_at=terminal_at,
            )

            run = session.get(DocumentRun, run_id)
            assert run is not None
            run.runtime_bundle_revision_id = str(uuid4())
            chapter_run.observed_generation = chapter_run.generation + 1
            session.add(run)
            session.add(chapter_run)
            session.commit()

            expected_review_session_id = created.id
            expected_observed_generation = chapter_run.observed_generation
            expected_bundle_revision_id = run.runtime_bundle_revision_id

        with self.session_factory() as session:
            controller = ReviewController(session=session)
            result = controller.reconcile_review_session(chapter_run_id=chapter_run_id)
            session.commit()

            self.assertFalse(result.created)
            self.assertEqual(result.review_session_id, expected_review_session_id)

        with self.session_factory() as session:
            review_sessions = session.query(ReviewSession).all()
            self.assertEqual(len(review_sessions), 1)
            review_session = review_sessions[0]
            self.assertEqual(review_session.id, expected_review_session_id)
            self.assertEqual(review_session.status, ReviewSessionStatus.PAUSED)
            self.assertEqual(review_session.terminality_state, ReviewTerminalityState.BLOCKED)
            self.assertEqual(review_session.conditions_json, {"manual_hold": {"reason": "awaiting-human"}})
            self.assertEqual(review_session.status_detail_json, {"issue_count": 2})
            self.assertEqual(review_session.observed_generation, expected_observed_generation)
            self.assertEqual(
                review_session.scope_json,
                {"run_id": run_id, "document_id": document_id, "chapter_id": chapter_id},
            )
            self.assertEqual(review_session.runtime_bundle_revision_id, expected_bundle_revision_id)
            self.assertEqual(review_session.last_terminal_at.replace(tzinfo=timezone.utc), terminal_at)
            self.assertIsNotNone(review_session.last_reconciled_at)

    def test_review_controller_reconcile_raises_for_missing_chapter_run(self) -> None:
        with self.session_factory() as session:
            controller = ReviewController(session=session)
            with self.assertRaises(ValueError):
                controller.reconcile_review_session(chapter_run_id="missing")
