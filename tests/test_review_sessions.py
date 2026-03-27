import unittest
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, StatementError

from book_agent.domain.enums import (
    ChapterRunPhase,
    ChapterRunStatus,
    ChapterStatus,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    ReviewSessionStatus,
    ReviewTerminalityState,
    SourceType,
)
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import ChapterRun, DocumentRun, ReviewSession
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository


class ReviewSessionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _seed_chapter_run(self) -> tuple[str, str, str, int, int]:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"review-session-{uuid4()}",
                source_path="/tmp/review-session.epub",
                title="Review Session",
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
                requested_by="tester",
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
                generation=2,
                observed_generation=2,
                conditions_json={},
                status_detail_json={},
            )
            session.add(chapter_run)
            session.commit()
            return document.id, chapter.id, chapter_run.id, chapter_run.generation, chapter_run.observed_generation

    def test_review_session_persists_with_runtime_defaults(self) -> None:
        document_id, chapter_id, chapter_run_id, desired_generation, observed_generation = self._seed_chapter_run()

        with self.session_factory() as session:
            session.add(
                ReviewSession(
                    chapter_run_id=chapter_run_id,
                    desired_generation=desired_generation,
                    observed_generation=observed_generation,
                    scope_json={"document_id": document_id, "chapter_id": chapter_id},
                )
            )
            session.commit()

            persisted = session.query(ReviewSession).one()
            self.assertEqual(persisted.status, ReviewSessionStatus.ACTIVE)
            self.assertEqual(persisted.terminality_state, ReviewTerminalityState.OPEN)
            self.assertEqual(persisted.scope_json["document_id"], document_id)
            self.assertEqual(persisted.scope_json["chapter_id"], chapter_id)
            self.assertEqual(persisted.conditions_json, {})
            self.assertEqual(persisted.status_detail_json, {})

    def test_review_session_generation_identity_is_unique_per_chapter_run(self) -> None:
        _document_id, _chapter_id, chapter_run_id, desired_generation, observed_generation = self._seed_chapter_run()

        with self.session_factory() as session:
            session.add(
                ReviewSession(
                    chapter_run_id=chapter_run_id,
                    desired_generation=desired_generation,
                    observed_generation=observed_generation,
                    scope_json={},
                )
            )
            session.commit()

            session.add(
                ReviewSession(
                    chapter_run_id=chapter_run_id,
                    desired_generation=desired_generation,
                    observed_generation=observed_generation,
                    scope_json={},
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()

    def test_review_session_rejects_invalid_status_value(self) -> None:
        _document_id, _chapter_id, chapter_run_id, desired_generation, observed_generation = self._seed_chapter_run()

        with self.session_factory() as session:
            session.add(
                ReviewSession(
                    chapter_run_id=chapter_run_id,
                    desired_generation=desired_generation,
                    observed_generation=observed_generation,
                    status="not-a-status",
                    scope_json={},
                )
            )
            with self.assertRaises(StatementError):
                session.flush()

    def test_runtime_repository_binds_review_sessions_to_each_generation(self) -> None:
        document_id, chapter_id, chapter_run_id, desired_generation, observed_generation = self._seed_chapter_run()

        with self.session_factory() as session:
            repo = RuntimeResourcesRepository(session)
            first = repo.ensure_review_session(
                chapter_run_id=chapter_run_id,
                desired_generation=desired_generation,
                observed_generation=observed_generation,
                scope_json={"document_id": document_id, "chapter_id": chapter_id, "generation": desired_generation},
            )

            chapter_run = repo.get_chapter_run(chapter_run_id)
            chapter_run.generation = desired_generation + 1
            chapter_run.observed_generation = observed_generation + 2
            session.add(chapter_run)
            session.flush()

            second = repo.ensure_review_session(
                chapter_run_id=chapter_run_id,
                desired_generation=chapter_run.generation,
                observed_generation=chapter_run.observed_generation,
                scope_json={"document_id": document_id, "chapter_id": chapter_id, "generation": chapter_run.generation},
            )

            listed = repo.list_review_sessions_for_chapter_run(chapter_run_id=chapter_run_id)

            self.assertNotEqual(first.id, second.id)
            self.assertEqual(
                [review_session.desired_generation for review_session in listed],
                [desired_generation, desired_generation + 1],
            )
