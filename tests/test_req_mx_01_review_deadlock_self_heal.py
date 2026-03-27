import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from book_agent.app.runtime.controllers.incident_controller import IncidentController
from book_agent.app.runtime.controllers.review_controller import ReviewController
from book_agent.core.config import Settings
from book_agent.domain.enums import (
    ChapterRunPhase,
    ChapterRunStatus,
    ChapterStatus,
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    JobScopeType,
    ReviewSessionStatus,
    ReviewTerminalityState,
    RuntimeIncidentKind,
    RuntimeIncidentStatus,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Chapter, Document
from book_agent.domain.models.ops import (
    ChapterRun,
    DocumentRun,
    RunBudget,
    RuntimeCheckpoint,
    RuntimeIncident,
    RuntimePatchProposal,
    WorkItem,
)
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.runtime_resources import RuntimeResourcesRepository
from book_agent.services.runtime_bundle import RuntimeBundleService
from book_agent.services.runtime_patch_validation import RuntimePatchValidationService


class ReqMx01ReviewDeadlockSelfHealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.bundle_root = Path(self.tempdir.name) / "runtime-bundles"
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _settings(self) -> Settings:
        return Settings(
            database_url="sqlite+pysqlite:///:memory:",
            runtime_bundle_root=self.bundle_root,
        )

    def test_req_mx_01_review_deadlock_self_heal_stays_bounded_to_review_scope(self) -> None:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"req-mx-01-{uuid4()}",
                source_path="/tmp/req-mx-01.epub",
                title="REQ-MX-01",
                author="Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
            session.flush()

            target_chapter = Chapter(
                document_id=document.id,
                ordinal=1,
                title_src="Target Chapter",
                status=ChapterStatus.PACKET_BUILT,
                metadata_json={},
            )
            untouched_chapter = Chapter(
                document_id=document.id,
                ordinal=2,
                title_src="Untouched Chapter",
                status=ChapterStatus.PACKET_BUILT,
                metadata_json={},
            )
            session.add_all([target_chapter, untouched_chapter])
            session.flush()

            run = DocumentRun(
                document_id=document.id,
                run_type=DocumentRunType.TRANSLATE_FULL,
                status=DocumentRunStatus.RUNNING,
                requested_by="tester",
                priority=100,
                status_detail_json={
                    "runtime_v2": {
                        "allowed_patch_surfaces": ["runtime_bundle"],
                        "enable_review_deadlock_auto_repair": True,
                    }
                },
            )
            session.add(run)
            session.flush()

            target_chapter_run = ChapterRun(
                run_id=run.id,
                document_id=document.id,
                chapter_id=target_chapter.id,
                desired_phase=ChapterRunPhase.REVIEW,
                observed_phase=ChapterRunPhase.REVIEW,
                status=ChapterRunStatus.ACTIVE,
                generation=1,
                observed_generation=1,
                conditions_json={},
                status_detail_json={},
            )
            untouched_chapter_run = ChapterRun(
                run_id=run.id,
                document_id=document.id,
                chapter_id=untouched_chapter.id,
                desired_phase=ChapterRunPhase.REVIEW,
                observed_phase=ChapterRunPhase.REVIEW,
                status=ChapterRunStatus.ACTIVE,
                generation=1,
                observed_generation=1,
                conditions_json={},
                status_detail_json={},
            )
            session.add_all([target_chapter_run, untouched_chapter_run])
            session.flush()

            runtime_repo = RuntimeResourcesRepository(session)
            bundle_revision_id = str(uuid4())
            target_review_session = runtime_repo.ensure_review_session(
                chapter_run_id=target_chapter_run.id,
                desired_generation=1,
                observed_generation=1,
                scope_json={
                    "run_id": run.id,
                    "document_id": document.id,
                    "chapter_id": target_chapter.id,
                },
                runtime_bundle_revision_id=bundle_revision_id,
            )
            runtime_repo.ensure_review_session(
                chapter_run_id=untouched_chapter_run.id,
                desired_generation=1,
                observed_generation=1,
                scope_json={
                    "run_id": run.id,
                    "document_id": document.id,
                    "chapter_id": untouched_chapter.id,
                },
            )
            runtime_repo.update_review_session(
                target_review_session.id,
                status=ReviewSessionStatus.ACTIVE,
                terminality_state=ReviewTerminalityState.OPEN,
                last_reconciled_at=datetime.now(timezone.utc) - timedelta(minutes=40),
            )
            target_review_session.updated_at = datetime.now(timezone.utc) - timedelta(minutes=40)
            session.add(target_review_session)

            work_item = WorkItem(
                run_id=run.id,
                stage=WorkItemStage.REVIEW,
                scope_type=WorkItemScopeType.CHAPTER,
                scope_id=target_chapter.id,
                attempt=2,
                priority=100,
                status=WorkItemStatus.RETRYABLE_FAILED,
                runtime_bundle_revision_id=bundle_revision_id,
                lease_owner=None,
                started_at=datetime.now(timezone.utc) - timedelta(minutes=45),
                updated_at=datetime.now(timezone.utc) - timedelta(minutes=40),
                finished_at=datetime.now(timezone.utc) - timedelta(minutes=40),
                input_version_bundle_json={
                    "document_id": document.id,
                    "chapter_id": target_chapter.id,
                    "chapter_run_id": target_chapter_run.id,
                    "review_session_id": target_review_session.id,
                },
                output_artifact_refs_json={},
                error_detail_json={},
            )
            session.add(work_item)
            session.add(
                RunBudget(
                    run_id=run.id,
                    max_auto_followup_attempts=2,
                )
            )
            session.flush()

            controller = ReviewController(session=session)
            controller._incident_controller = IncidentController(
                session=session,
                bundle_service=RuntimeBundleService(session, settings=self._settings()),
                validation_service=RuntimePatchValidationService(session),
            )

            controller.reconcile_review_session(chapter_run_id=target_chapter_run.id)
            controller.reconcile_review_session(chapter_run_id=target_chapter_run.id)
            session.commit()

            incident = session.query(RuntimeIncident).filter(RuntimeIncident.run_id == run.id).one()
            proposal = session.query(RuntimePatchProposal).filter(RuntimePatchProposal.incident_id == incident.id).one()
            checkpoint = session.query(RuntimeCheckpoint).filter(
                RuntimeCheckpoint.run_id == run.id,
                RuntimeCheckpoint.scope_type == JobScopeType.CHAPTER,
                RuntimeCheckpoint.scope_id == target_chapter.id,
                RuntimeCheckpoint.checkpoint_key == "review_controller.deadlock_recovery",
            ).one()
            persisted_review_session = runtime_repo.get_review_session(target_review_session.id)
            persisted_work_items = session.query(WorkItem).filter(WorkItem.run_id == run.id).all()
            persisted_run = session.get(DocumentRun, run.id)
            persisted_target_chapter_run = session.get(ChapterRun, target_chapter_run.id)
            persisted_untouched_chapter_run = session.get(ChapterRun, untouched_chapter_run.id)

        self.assertEqual(incident.incident_kind, RuntimeIncidentKind.REVIEW_DEADLOCK)
        self.assertEqual(incident.status, RuntimeIncidentStatus.PUBLISHED)
        self.assertEqual(incident.scope_type, JobScopeType.CHAPTER)
        self.assertEqual(incident.scope_id, target_chapter.id)
        self.assertEqual(proposal.status.value, "published")
        self.assertIsNotNone(persisted_run)
        self.assertIsNotNone(persisted_target_chapter_run)
        self.assertIsNotNone(persisted_untouched_chapter_run)
        assert persisted_run is not None
        assert persisted_target_chapter_run is not None
        assert persisted_untouched_chapter_run is not None

        self.assertEqual(persisted_run.runtime_bundle_revision_id, proposal.published_bundle_revision_id)
        self.assertEqual(checkpoint.checkpoint_json["recovery"]["replay_scope_id"], target_chapter.id)
        self.assertEqual(checkpoint.checkpoint_json["recovery"]["replay_work_item_ids"], [work_item.id])
        self.assertEqual(
            persisted_review_session.status_detail_json["runtime_v2"]["last_deadlock_recovery"]["replay_work_item_ids"],
            [work_item.id],
        )
        self.assertEqual(
            persisted_review_session.status_detail_json["runtime_v2"]["last_deadlock_recovery"]["bundle_revision_id"],
            proposal.published_bundle_revision_id,
        )
        self.assertEqual(
            persisted_target_chapter_run.conditions_json["recovered_lineage"][0]["replay_scope_id"],
            target_chapter.id,
        )
        self.assertEqual(persisted_untouched_chapter_run.conditions_json, {})
        self.assertTrue(all(item.stage == WorkItemStage.REVIEW for item in persisted_work_items))
        self.assertTrue(all(item.scope_type == WorkItemScopeType.CHAPTER for item in persisted_work_items))
        self.assertEqual([item.scope_id for item in persisted_work_items], [target_chapter.id])


if __name__ == "__main__":
    unittest.main()
