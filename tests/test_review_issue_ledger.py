"""Review issue ledger (R8): versioned issues, append-only history, human decisions win, issue API."""

import tempfile
import unittest
import zipfile
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from book_agent.app.main import create_app
from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    ActionStatus,
    ChapterStatus,
    IssueEventKind,
    IssueStatus,
)
from book_agent.domain.models import Chapter
from book_agent.domain.models.review import IssueAction, ReviewIssue, ReviewIssueEvent
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.ops import OpsRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.actions import ActionNotExecutable, IssueActionExecutor
from book_agent.services.issues import IssueService
from book_agent.services.review import ReviewService
from book_agent.services.translation import TranslationService
from book_agent.translation.contracts import (
    AlignmentSuggestion,
    TranslationTargetSegment,
    TranslationWorkerOutput,
    TranslationWorkerResult,
)
from book_agent.workers.translator import TranslationTask, TranslationWorkerMetadata
from tests.test_translation_worker_abstraction import CHAPTER_XHTML, CONTAINER_XML, CONTENT_OPF, NAV_XHTML

DROPPED = "A quoted paragraph."


class DroppingWorker:
    """Translates everything except the quoted paragraph, which becomes an OMISSION."""

    def metadata(self) -> TranslationWorkerMetadata:
        return TranslationWorkerMetadata(worker_name="dropping", model_name="dropping", prompt_version="v1")

    def translate(self, task: TranslationTask) -> TranslationWorkerResult:
        segments, alignments = [], []
        for sentence in task.current_sentences:
            if sentence.source_text.strip() == DROPPED:
                continue
            temp_id = stable_id("temp", sentence.id)
            segments.append(
                TranslationTargetSegment(
                    temp_id=temp_id,
                    text_zh=f"译::{sentence.source_text}",
                    segment_type="sentence",
                    source_sentence_ids=[sentence.id],
                    confidence=0.95,
                )
            )
            alignments.append(
                AlignmentSuggestion(source_sentence_ids=[sentence.id], target_temp_ids=[temp_id], relation_type="1:1")
            )
        return TranslationWorkerResult(
            output=TranslationWorkerOutput(
                packet_id=task.context_packet.packet_id, target_segments=segments, alignment_suggestions=alignments
            )
        )


class IssueLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.engine = build_engine(
            f"sqlite+pysqlite:///{Path(self.tempdir.name) / 'ledger.db'}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)
        epub_path = Path(self.tempdir.name) / "sample.epub"
        with zipfile.ZipFile(epub_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", CONTENT_OPF)
            archive.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            archive.writestr("OEBPS/chapter1.xhtml", CHAPTER_XHTML)
        artifacts = BootstrapOrchestrator().bootstrap_epub(epub_path)
        with self.session_factory() as session:
            BootstrapRepository(session).save(artifacts)
            session.commit()
        self.document_id = artifacts.document.id
        self.chapter_id = artifacts.chapters[0].id
        with self.session_factory() as session:
            service = TranslationService(TranslationRepository(session), worker=DroppingWorker(), max_output_repairs=0)
            for packet in artifacts.translation_packets:
                service.execute_packet(packet.id)
            session.commit()

    def _review(self) -> None:
        with self.session_factory() as session:
            ReviewService(ReviewRepository(session)).review_chapter(self.chapter_id)
            session.commit()

    def _omission(self, session) -> ReviewIssue:
        return session.scalars(select(ReviewIssue).where(ReviewIssue.issue_type == "OMISSION")).one()

    def _events(self, session, issue_id: str) -> list[ReviewIssueEvent]:
        return ReviewRepository(session).list_issue_events(issue_id)

    def test_first_sighting_opens_version_one_and_rereview_changes_nothing(self) -> None:
        self._review()
        with self.session_factory() as session:
            issue = self._omission(session)
            first_created_at = issue.created_at
            self.assertEqual((issue.status, issue.version, issue.reopen_count), (IssueStatus.OPEN, 1, 0))
            self.assertIsNotNone(issue.last_seen_at)
            self.assertEqual([event.kind for event in self._events(session, issue.id)], [IssueEventKind.OPENED])

        self._review()
        with self.session_factory() as session:
            issue = self._omission(session)
            self.assertEqual(issue.created_at, first_created_at)
            self.assertEqual(issue.version, 1)
            self.assertEqual([event.kind for event in self._events(session, issue.id)], [IssueEventKind.OPENED])

    def test_human_wontfix_survives_rereview_and_no_longer_blocks_the_chapter(self) -> None:
        self._review()
        with self.session_factory() as session:
            self.assertEqual(session.get(Chapter, self.chapter_id).status, ChapterStatus.REVIEW_REQUIRED)
            issue_id = self._omission(session).id
            IssueService(session).transition(
                issue_id, to_status=IssueStatus.WONTFIX, actor_id="human:editor", note="quote stays in English"
            )
            session.commit()

        self._review()
        with self.session_factory() as session:
            issue = self._omission(session)
            self.assertEqual(issue.status, IssueStatus.WONTFIX)
            self.assertEqual(issue.decided_by, "human:editor")
            self.assertEqual(issue.resolution_note, "quote stays in English")
            kinds = [event.kind for event in self._events(session, issue.id)]
            self.assertEqual(kinds, [IssueEventKind.OPENED, IssueEventKind.WONTFIX, IssueEventKind.SEEN_WHILE_CLOSED])
            self.assertEqual(session.get(Chapter, self.chapter_id).status, ChapterStatus.QA_CHECKED)

    def test_system_resolved_issue_is_reopened_keeping_created_at_and_replanning_its_action(self) -> None:
        self._review()
        with self.session_factory() as session:
            issue = self._omission(session)
            created_at = issue.created_at
            action = session.scalars(select(IssueAction).where(IssueAction.issue_id == issue.id)).one()
            action.status = ActionStatus.COMPLETED
            action.updated_at = action.updated_at - timedelta(minutes=5)
            # A clean pass that no longer sees the problem resolves it.
            ReviewRepository(session).sync_issues(
                [], [], owned_existing=[issue], resolution_note="Resolved by latest QA pass.", actor_id="test"
            )
            session.commit()
            self.assertEqual(issue.status, IssueStatus.RESOLVED)

        self._review()
        with self.session_factory() as session:
            issue = self._omission(session)
            self.assertEqual(issue.status, IssueStatus.OPEN)
            self.assertEqual(issue.created_at, created_at)
            self.assertEqual(issue.reopen_count, 1)
            self.assertIsNone(issue.resolution_note)
            self.assertEqual(issue.version, 3)
            kinds = [event.kind for event in self._events(session, issue.id)]
            self.assertEqual(kinds, [IssueEventKind.OPENED, IssueEventKind.RESOLVED, IssueEventKind.REOPENED])
            action = session.scalars(select(IssueAction).where(IssueAction.issue_id == issue.id)).one()
            self.assertEqual(action.status, ActionStatus.PLANNED)
            self.assertEqual(action.reason_json["replan_count"], 1)

    def test_completed_action_cannot_be_executed_again(self) -> None:
        self._review()
        with self.session_factory() as session:
            issue = self._omission(session)
            action = session.scalars(select(IssueAction).where(IssueAction.issue_id == issue.id)).one()
            action_id = action.id
            IssueActionExecutor(OpsRepository(session)).execute(action_id)
            session.commit()
            self.assertEqual(session.get(ReviewIssue, issue.id).status, IssueStatus.TRIAGED)

        with self.session_factory() as session:
            with self.assertRaises(ActionNotExecutable):
                IssueActionExecutor(OpsRepository(session)).execute(action_id)

    def test_failed_rerun_validation_returns_system_triage_to_open(self) -> None:
        self._review()
        with self.session_factory() as session:
            issue = self._omission(session)
            action = session.scalars(select(IssueAction).where(IssueAction.issue_id == issue.id)).one()
            IssueActionExecutor(OpsRepository(session)).execute(action.id)
            session.commit()

        self._review()
        with self.session_factory() as session:
            issue = self._omission(session)
            self.assertEqual(issue.status, IssueStatus.OPEN)
            kinds = [event.kind for event in self._events(session, issue.id)]
            self.assertEqual(kinds, [IssueEventKind.OPENED, IssueEventKind.TRIAGED, IssueEventKind.UPDATED])

    def test_human_triage_stays_triaged_on_rereview(self) -> None:
        self._review()
        with self.session_factory() as session:
            issue_id = self._omission(session).id
            IssueService(session).transition(issue_id, to_status=IssueStatus.TRIAGED, actor_id="human:lead", note=None)
            session.commit()

        self._review()
        with self.session_factory() as session:
            self.assertEqual(self._omission(session).status, IssueStatus.TRIAGED)

    def test_actions_of_a_closed_issue_cannot_run(self) -> None:
        self._review()
        with self.session_factory() as session:
            issue = self._omission(session)
            IssueService(session).transition(issue.id, to_status=IssueStatus.WONTFIX, actor_id="human:editor", note=None)
            action = session.scalars(select(IssueAction).where(IssueAction.issue_id == issue.id)).one()
            with self.assertRaises(ActionNotExecutable):
                IssueActionExecutor(OpsRepository(session)).execute(action.id)

    def test_issue_api_lists_details_and_transitions(self) -> None:
        self._review()
        app = create_app()
        app.state.session_factory = self.session_factory
        app.state.export_root = str(Path(self.tempdir.name) / "exports")
        client = TestClient(app)
        self.addCleanup(client.close)

        listing = client.get(f"/v1/documents/{self.document_id}/issues", params={"issue_type": "OMISSION"})
        self.assertEqual(listing.status_code, 200)
        body = listing.json()
        self.assertEqual(body["total_count"], 1)
        issue = body["entries"][0]
        self.assertEqual((issue["status"], issue["version"], issue["blocking"]), ("open", 1, True))

        detail = client.get(f"/v1/issues/{issue['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["source_text"], DROPPED)
        self.assertIsNone(detail.json()["target_text"])
        self.assertEqual([event["kind"] for event in detail.json()["events"]], ["opened"])
        self.assertEqual(len(detail.json()["actions"]), 1)

        wontfix = client.post(f"/v1/issues/{issue['id']}/wontfix", json={"actor_id": "editor", "note": "keep"})
        self.assertEqual(wontfix.status_code, 200)
        self.assertEqual((wontfix.json()["status"], wontfix.json()["decided_by"]), ("wontfix", "human:editor"))
        self.assertEqual(client.post(f"/v1/issues/{issue['id']}/triage", json={"actor_id": "editor"}).status_code, 409)
        self.assertEqual(
            client.get(f"/v1/documents/{self.document_id}/issues", params={"issue_type": "OMISSION"}).json()["total_count"],
            0,
        )
        self.assertEqual(
            client.get(f"/v1/documents/{self.document_id}/issues", params={"status": "wontfix"}).json()["total_count"], 1
        )
        reopened = client.post(f"/v1/issues/{issue['id']}/reopen", json={"actor_id": "editor"})
        self.assertEqual((reopened.json()["status"], reopened.json()["reopen_count"]), ("open", 1))
        self.assertEqual(client.get("/v1/issues/00000000-0000-0000-0000-000000000000").status_code, 404)
        self.assertEqual(client.get(f"/v1/documents/{self.document_id}/issues", params={"status": "bogus"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
