"""Integration tests for the PatchProposal review workflow.

Exercises PatchReviewService (approve/reject/list/get) and the
``requires_human_review`` gate in :class:`IncidentController`.

Requires a live Postgres (the events bus uses ``clock_timestamp()`` and
the NOTIFY trigger). Skipped otherwise.
"""

from __future__ import annotations

import os
import unittest
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from book_agent.app.runtime.controllers.incident_controller import (
    IncidentController,
    PatchAwaitingReviewError,
)
from book_agent.domain.enums import (
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    JobScopeType,
    RuntimeIncidentKind,
    RuntimeIncidentStatus,
    RuntimePatchProposalStatus,
    SourceType,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import (
    DocumentRun,
    Event,
    RuntimeIncident,
    RuntimePatchProposal,
)
from book_agent.services.patch_review import PatchReviewError, PatchReviewService


DEFAULT_DEV_DSN = "postgresql+psycopg://postgres:postgres@localhost:55432/book_agent"


def _pg_dsn() -> str | None:
    dsn = os.environ.get("BOOK_AGENT_DATABASE_URL", DEFAULT_DEV_DSN)
    return dsn if dsn.startswith("postgresql") else None


class _PgCase(unittest.TestCase):
    """Base class that bootstraps a Postgres session factory and cleans up."""

    _created_proposal_ids: list[str]
    _created_incident_ids: list[str]
    _created_run_ids: list[str]
    _created_document_ids: list[str]

    @classmethod
    def setUpClass(cls) -> None:
        dsn = _pg_dsn()
        if dsn is None:
            pytest.skip("Patch review tests require Postgres.")
        cls.engine = create_engine(dsn, future=True)
        cls.session_factory = sessionmaker(
            bind=cls.engine, autoflush=False, expire_on_commit=False, future=True
        )

    def setUp(self) -> None:
        self._created_proposal_ids = []
        self._created_incident_ids = []
        self._created_run_ids = []
        self._created_document_ids = []

    def tearDown(self) -> None:
        with self.engine.begin() as conn:
            if self._created_proposal_ids:
                conn.execute(
                    text("DELETE FROM events WHERE correlation_id = ANY(:c)"),
                    {"c": [f"patch:{pid}" for pid in self._created_proposal_ids]},
                )
                conn.execute(
                    text("DELETE FROM runtime_patch_proposals WHERE id = ANY(:ids)"),
                    {"ids": self._created_proposal_ids},
                )
            if self._created_incident_ids:
                conn.execute(
                    text("DELETE FROM runtime_incidents WHERE id = ANY(:ids)"),
                    {"ids": self._created_incident_ids},
                )
            if self._created_run_ids:
                conn.execute(
                    text("DELETE FROM document_runs WHERE id = ANY(:ids)"),
                    {"ids": self._created_run_ids},
                )
            if self._created_document_ids:
                conn.execute(
                    text("DELETE FROM documents WHERE id = ANY(:ids)"),
                    {"ids": self._created_document_ids},
                )

    def _seed_proposal(
        self,
        *,
        status: RuntimePatchProposalStatus = RuntimePatchProposalStatus.VALIDATED,
        requires_human_review: bool = True,
    ) -> tuple[str, str]:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"patch-review-{uuid4()}",
                source_path="/tmp/patch-review.epub",
                title="Patch Review",
                author="Tester",
                src_lang="en",
                tgt_lang="zh",
                status=DocumentStatus.ACTIVE,
                parser_version=1,
                segmentation_version=1,
            )
            session.add(document)
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

            incident = RuntimeIncident(
                run_id=run.id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=str(uuid4()),
                incident_kind=RuntimeIncidentKind.RUNTIME_DEFECT,
                fingerprint=f"patch-review:{uuid4()}",
                status=RuntimeIncidentStatus.OPEN,
                failure_count=1,
                route_evidence_json={},
                latest_error_json={"error_code": "runtime_defect"},
                bundle_json={},
                status_detail_json={},
            )
            session.add(incident)
            session.flush()

            proposal = RuntimePatchProposal(
                incident_id=incident.id,
                status=status,
                proposed_by="repair-worker",
                patch_surface="runtime_bundle",
                requires_human_review=requires_human_review,
                diff_manifest_json={"files": ["src/book_agent/services/runtime_bundle.py"]},
                validation_report_json={"passed": True, "canary_verdict": "passed"},
                status_detail_json={},
            )
            session.add(proposal)
            session.commit()

            self._created_document_ids.append(document.id)
            self._created_run_ids.append(run.id)
            self._created_incident_ids.append(incident.id)
            self._created_proposal_ids.append(proposal.id)
            return run.id, proposal.id


class PatchReviewServiceTests(_PgCase):
    def test_list_and_get_roundtrip(self) -> None:
        run_id, proposal_id = self._seed_proposal()
        with self.session_factory() as session:
            service = PatchReviewService(session)
            summaries = service.list_proposals(run_id=run_id)
            self.assertEqual(len(summaries), 1)
            self.assertEqual(summaries[0].proposal_id, proposal_id)
            self.assertTrue(summaries[0].requires_human_review)

            detail = service.get_proposal(proposal_id)
            self.assertEqual(
                detail.diff_manifest_json["files"],
                ["src/book_agent/services/runtime_bundle.py"],
            )
            self.assertTrue(detail.validation_report_json["passed"])

    def test_approve_emits_event_and_stamps_fields(self) -> None:
        run_id, proposal_id = self._seed_proposal()
        with self.session_factory() as session:
            service = PatchReviewService(session)
            detail = service.approve_proposal(
                proposal_id, reviewer_id="alice@example.com", notes="looks good"
            )
            session.commit()

        self.assertEqual(detail.approved_by, "alice@example.com")
        self.assertIsNotNone(detail.approved_at)

        with self.session_factory() as session:
            persisted = session.get(RuntimePatchProposal, proposal_id)
            assert persisted is not None
            self.assertEqual(persisted.approved_by, "alice@example.com")
            self.assertIsNotNone(persisted.approved_at)
            self.assertEqual(persisted.review_notes, "looks good")

            events = session.scalars(
                select(Event)
                .where(Event.kind == "patch.approved")
                .where(Event.correlation_id == f"patch:{proposal_id}")
            ).all()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].actor_id, "alice@example.com")
            self.assertEqual(events[0].actor_kind, "user")
            self.assertEqual(events[0].run_id, run_id)
            self.assertEqual(events[0].payload["proposal_id"], proposal_id)

    def test_reject_terminal(self) -> None:
        _, proposal_id = self._seed_proposal()
        with self.session_factory() as session:
            service = PatchReviewService(session)
            detail = service.reject_proposal(
                proposal_id, reviewer_id="bob", reason="unsafe surface"
            )
            session.commit()

        self.assertEqual(detail.status, RuntimePatchProposalStatus.REJECTED.value)
        self.assertEqual(detail.rejected_by, "bob")

        with self.session_factory() as session:
            service = PatchReviewService(session)
            with self.assertRaises(PatchReviewError):
                service.approve_proposal(proposal_id, reviewer_id="alice")

    def test_empty_reviewer_rejected(self) -> None:
        _, proposal_id = self._seed_proposal()
        with self.session_factory() as session:
            service = PatchReviewService(session)
            with self.assertRaises(PatchReviewError):
                service.approve_proposal(proposal_id, reviewer_id="   ")
            with self.assertRaises(PatchReviewError):
                service.reject_proposal(proposal_id, reviewer_id="alice", reason="   ")


class PublishGateTests(_PgCase):
    def test_validated_patch_awaiting_review_is_blocked(self) -> None:
        _, proposal_id = self._seed_proposal(
            status=RuntimePatchProposalStatus.VALIDATED,
            requires_human_review=True,
        )
        with self.session_factory() as session:
            controller = IncidentController(session=session)
            with self.assertRaises(PatchAwaitingReviewError):
                controller.publish_validated_patch(
                    proposal_id=proposal_id,
                    revision_name="rev-blocked",
                    manifest_json={"dummy": True},
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
