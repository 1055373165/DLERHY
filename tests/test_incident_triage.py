import unittest
from uuid import uuid4

from book_agent.domain.enums import (
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    JobScopeType,
    RootCauseLayer,
    RuntimeIncidentKind,
    RuntimeIncidentStatus,
    SourceType,
    WorkItemScopeType,
    WorkItemStage,
    WorkItemStatus,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun, RuntimeIncident, WorkItem
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.incident_triage import IncidentTriageService


class IncidentTriageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _create_run(self) -> tuple[str, str]:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"incident-triage-{uuid4()}",
                source_path="/tmp/incident-triage.epub",
                title="Incident Triage Document",
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

            work_item = WorkItem(
                run_id=run.id,
                stage=WorkItemStage.TRANSLATE,
                scope_type=WorkItemScopeType.PACKET,
                scope_id=str(uuid4()),
                priority=100,
                status=WorkItemStatus.RUNNING,
                lease_owner="incident-triage-worker",
                input_version_bundle_json={},
                output_artifact_refs_json={},
                error_detail_json={},
            )
            session.add(work_item)
            session.commit()
            return run.id, work_item.id

    def test_build_route_evidence_and_open_or_update_incident(self) -> None:
        run_id, work_item_id = self._create_run()
        service = IncidentTriageService()
        evidence = service.build_route_evidence(
            run_id=run_id,
            scope_type=JobScopeType.CHAPTER,
            scope_id=str(uuid4()),
            source_type="epub",
            selected_route="pdf",
            runtime_bundle_revision_id=None,
            error_code="misroute",
            error_message="selected the wrong export route",
            route_candidates=["pdf", "epub"],
            extra_json={"chapter_ordinal": 3},
        )

        self.assertEqual(evidence["run_id"], run_id)
        self.assertEqual(evidence["scope_type"], JobScopeType.CHAPTER.value)
        self.assertEqual(evidence["route_candidates"], ["pdf", "epub"])
        self.assertEqual(evidence["extra_json"]["chapter_ordinal"], 3)

        fingerprint = service.fingerprint_incident(
            incident_kind=RuntimeIncidentKind.EXPORT_MISROUTING,
            scope_type=JobScopeType.CHAPTER,
            scope_id=evidence["scope_id"],
            source_type="epub",
            selected_route="pdf",
            runtime_bundle_revision_id=None,
            route_evidence_json=evidence,
        )

        with self.session_factory() as session:
            first = service.open_or_update_incident(
                session,
                run_id=run_id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=evidence["scope_id"],
                incident_kind=RuntimeIncidentKind.EXPORT_MISROUTING,
                source_type="epub",
                selected_route="pdf",
                runtime_bundle_revision_id=None,
                error_code="misroute",
                error_message="selected the wrong export route",
                route_evidence_json=evidence,
                latest_work_item_id=work_item_id,
            )
            second = service.open_or_update_incident(
                session,
                run_id=run_id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=evidence["scope_id"],
                incident_kind=RuntimeIncidentKind.EXPORT_MISROUTING,
                source_type="epub",
                selected_route="pdf",
                runtime_bundle_revision_id=None,
                error_code="misroute-again",
                error_message="same defect surfaced again",
                route_evidence_json=evidence,
                latest_work_item_id=work_item_id,
            )
            persisted = session.get(RuntimeIncident, first.id)
            session.commit()

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.fingerprint, fingerprint)
        self.assertEqual(second.failure_count, 2)
        self.assertEqual(second.status, RuntimeIncidentStatus.DIAGNOSING)
        self.assertEqual(second.latest_work_item_id, work_item_id)
        self.assertEqual(second.latest_error_json["error_code"], "misroute-again")
        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertEqual(persisted.failure_count, 2)

    def test_runtime_defect_fingerprint_and_preview_are_stable_for_repeated_review_deadlocks(self) -> None:
        run_id, work_item_id = self._create_run()
        service = IncidentTriageService()
        scope_id = str(uuid4())
        bundle_revision_id = str(uuid4())
        evidence = service.build_runtime_defect_evidence(
            run_id=run_id,
            scope_type=JobScopeType.CHAPTER,
            scope_id=scope_id,
            failure_family=RootCauseLayer.REVIEW,
            reason_code="review_failed_without_terminality",
            runtime_bundle_revision_id=bundle_revision_id,
            lane_health_state="deadlocked",
            work_item_id=work_item_id,
            chapter_run_id=str(uuid4()),
            review_session_id=str(uuid4()),
            extra_json={"replay_scope": "review_session", "next_boundary": "review_session"},
        )

        self.assertEqual(
            service.classify_runtime_incident_kind(
                failure_family=RootCauseLayer.REVIEW,
                reason_code="review_failed_without_terminality",
            ),
            RuntimeIncidentKind.REVIEW_DEADLOCK,
        )
        self.assertEqual(evidence["fingerprint_basis"]["failure_family"], RootCauseLayer.REVIEW.value)
        self.assertEqual(evidence["fingerprint_basis"]["reason_code"], "review_failed_without_terminality")

        with self.session_factory() as session:
            first = service.open_or_update_incident(
                session,
                run_id=run_id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=scope_id,
                incident_kind=RuntimeIncidentKind.REVIEW_DEADLOCK,
                source_type="epub",
                selected_route="runtime.review_failed_without_terminality",
                runtime_bundle_revision_id=bundle_revision_id,
                error_code="review_failed_without_terminality",
                error_message="review session is stuck without terminality",
                route_evidence_json=evidence,
                latest_work_item_id=work_item_id,
            )
            preview = service.preview_occurrence_count(
                session,
                scope_type=JobScopeType.CHAPTER,
                scope_id=scope_id,
                incident_kind=RuntimeIncidentKind.REVIEW_DEADLOCK,
                source_type="epub",
                selected_route="runtime.review_failed_without_terminality",
                runtime_bundle_revision_id=bundle_revision_id,
                route_evidence_json=evidence,
            )
            second = service.open_or_update_incident(
                session,
                run_id=run_id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=scope_id,
                incident_kind=RuntimeIncidentKind.REVIEW_DEADLOCK,
                source_type="epub",
                selected_route="runtime.review_failed_without_terminality",
                runtime_bundle_revision_id=bundle_revision_id,
                error_code="review_failed_without_terminality",
                error_message="review session is still stuck without terminality",
                route_evidence_json=evidence,
                latest_work_item_id=work_item_id,
            )
            session.commit()

        self.assertEqual(first.id, second.id)
        self.assertEqual(preview, 2)
        self.assertEqual(second.failure_count, 2)
        self.assertEqual(second.status, RuntimeIncidentStatus.DIAGNOSING)

    def test_translation_runtime_defects_are_classified_as_packet_incidents(self) -> None:
        service = IncidentTriageService()
        self.assertEqual(
            service.classify_runtime_incident_kind(
                failure_family=RootCauseLayer.TRANSLATION,
                reason_code="packet_runtime_stalled",
            ),
            RuntimeIncidentKind.PACKET_RUNTIME_DEFECT,
        )
