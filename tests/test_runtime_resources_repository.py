import unittest
from datetime import datetime, timezone
from uuid import uuid4

from book_agent.domain.enums import (
    ChapterRunPhase,
    ChapterStatus,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    JobScopeType,
    PacketStatus,
    PacketType,
    PacketTaskAction,
    ReviewSessionStatus,
    ReviewTerminalityState,
    SourceType,
)
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import DocumentRun
from book_agent.domain.models.translation import TranslationPacket
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository


class RuntimeResourcesRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def test_ensure_chapter_run_is_idempotent(self) -> None:
        with self.session_factory() as session:
            doc = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"runtime-repo-{uuid4()}",
                source_path="/tmp/repo.epub",
                title="Repo Test",
                author="Repo Tester",
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
            first = repo.ensure_chapter_run(
                run_id=run.id,
                document_id=doc.id,
                chapter_id=chapter.id,
                desired_phase=ChapterRunPhase.PACKETIZE,
            )
            second = repo.ensure_chapter_run(
                run_id=run.id,
                document_id=doc.id,
                chapter_id=chapter.id,
                desired_phase=ChapterRunPhase.TRANSLATE,
            )
            self.assertEqual(first.id, second.id)

    def test_ensure_packet_task_is_idempotent_and_checkpoint_upsert_updates(self) -> None:
        with self.session_factory() as session:
            doc = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"runtime-repo2-{uuid4()}",
                source_path="/tmp/repo2.epub",
                title="Repo Test 2",
                author="Repo Tester",
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
            repo = RuntimeResourcesRepository(session)
            chapter_run = repo.ensure_chapter_run(
                run_id=run.id,
                document_id=doc.id,
                chapter_id=chapter.id,
            )
            first_task = repo.ensure_packet_task(
                chapter_run_id=chapter_run.id,
                packet_id=packet.id,
                packet_generation=1,
                desired_action=PacketTaskAction.TRANSLATE,
            )
            second_task = repo.ensure_packet_task(
                chapter_run_id=chapter_run.id,
                packet_id=packet.id,
                packet_generation=1,
                desired_action=PacketTaskAction.RETRANSLATE,
            )
            self.assertEqual(first_task.id, second_task.id)

            created = repo.upsert_checkpoint(
                run_id=run.id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=chapter.id,
                checkpoint_key="controller_runner.v1",
                checkpoint_json={"cursor": 1},
                generation=1,
            )
            updated = repo.upsert_checkpoint(
                run_id=run.id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=chapter.id,
                checkpoint_key="controller_runner.v1",
                checkpoint_json={"cursor": 2},
                generation=2,
            )
            self.assertEqual(created.id, updated.id)
            self.assertEqual(updated.checkpoint_json["cursor"], 2)
            self.assertEqual(updated.generation, 2)

    def test_getters_raise_for_missing_ids(self) -> None:
        with self.session_factory() as session:
            repo = RuntimeResourcesRepository(session)
            with self.assertRaises(ValueError):
                repo.get_chapter_run("missing")
            with self.assertRaises(ValueError):
                repo.get_packet_task("missing")
            with self.assertRaises(ValueError):
                repo.get_review_session("missing")

    def test_review_session_repository_contract(self) -> None:
        with self.session_factory() as session:
            doc = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"runtime-review-repo-{uuid4()}",
                source_path="/tmp/review-repo.epub",
                title="Review Repo Test",
                author="Repo Tester",
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

        reconciled_at = datetime.now(timezone.utc)
        terminal_at = datetime.now(timezone.utc)

        with self.session_factory() as session:
            repo = RuntimeResourcesRepository(session)
            chapter_run = repo.ensure_chapter_run(
                run_id=run.id,
                document_id=doc.id,
                chapter_id=chapter.id,
                desired_phase=ChapterRunPhase.REVIEW,
            )
            created = repo.ensure_review_session(
                chapter_run_id=chapter_run.id,
                desired_generation=chapter_run.generation,
                observed_generation=chapter_run.observed_generation,
                scope_json={"document_id": doc.id, "chapter_id": chapter.id},
                runtime_bundle_revision_id=str(uuid4()),
            )
            updated = repo.update_review_session(
                created.id,
                status=ReviewSessionStatus.SUCCEEDED,
                terminality_state=ReviewTerminalityState.APPROVED,
                observed_generation=chapter_run.observed_generation + 1,
                status_detail_json={"issue_count": 0},
                last_terminal_at=terminal_at,
                last_reconciled_at=reconciled_at,
            )

            fetched = repo.get_review_session(created.id)
            self.assertEqual(created.id, updated.id)
            self.assertEqual(fetched.status, ReviewSessionStatus.SUCCEEDED)
            self.assertEqual(fetched.terminality_state, ReviewTerminalityState.APPROVED)
            self.assertEqual(fetched.observed_generation, chapter_run.observed_generation + 1)
            self.assertEqual(fetched.status_detail_json["issue_count"], 0)
            self.assertEqual(fetched.last_terminal_at, terminal_at)
            self.assertEqual(fetched.last_reconciled_at, reconciled_at)
            self.assertEqual(
                repo.get_review_session_by_identity(
                    chapter_run_id=chapter_run.id,
                    desired_generation=chapter_run.generation,
                ).id,
                created.id,
            )
            self.assertIsNone(repo.get_active_review_session_for_chapter_run(chapter_run_id=chapter_run.id))
            self.assertEqual(len(repo.list_review_sessions_for_chapter_run(chapter_run_id=chapter_run.id)), 1)
