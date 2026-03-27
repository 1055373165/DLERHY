import unittest
from uuid import uuid4

from book_agent.domain.enums import (
    DocumentRunStatus,
    DocumentRunType,
    DocumentStatus,
    JobScopeType,
    RuntimeBundleRevisionStatus,
    RuntimeIncidentKind,
    RuntimeIncidentStatus,
    RuntimePatchProposalStatus,
    SourceType,
)
from book_agent.domain.models import Document
from book_agent.domain.models.ops import DocumentRun, RuntimeBundleRevision, RuntimeIncident, RuntimePatchProposal
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory


class RuntimeIncidentsBundlesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def test_runtime_incident_bundle_and_patch_models_persist(self) -> None:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"runtime-bundle-{uuid4()}",
                source_path="/tmp/runtime-bundle.epub",
                title="Runtime Bundle",
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

            revision = RuntimeBundleRevision(
                id=str(uuid4()),
                bundle_type="runtime",
                revision_name="bundle-v1",
                status=RuntimeBundleRevisionStatus.PUBLISHED,
                manifest_json={"code": {"runner": "controller"}},
                rollout_scope_json={"mode": "dev"},
            )
            session.add(revision)
            session.flush()

            incident = RuntimeIncident(
                run_id=run.id,
                scope_type=JobScopeType.CHAPTER,
                scope_id=str(uuid4()),
                incident_kind=RuntimeIncidentKind.EXPORT_MISROUTING,
                fingerprint="runtime-incident:fingerprint",
                source_type="epub",
                selected_route="pdf",
                runtime_bundle_revision_id=revision.id,
                status=RuntimeIncidentStatus.OPEN,
                failure_count=1,
                route_evidence_json={"scope": "chapter"},
                latest_error_json={"error_code": "misroute"},
                bundle_json={"revision_name": "bundle-v1"},
                status_detail_json={"note": "seeded"},
            )
            session.add(incident)
            session.flush()

            patch = RuntimePatchProposal(
                incident_id=incident.id,
                status=RuntimePatchProposalStatus.PROPOSED,
                proposed_by="runtime",
                patch_surface="export_routing",
                diff_manifest_json={"files": ["src/book_agent/services/export.py"]},
                validation_report_json={"passed": True},
                status_detail_json={},
            )
            session.add(patch)
            session.commit()

        with self.session_factory() as session:
            persisted_revision = session.get(RuntimeBundleRevision, revision.id)
            persisted_incident = session.get(RuntimeIncident, incident.id)
            persisted_patch = session.get(RuntimePatchProposal, patch.id)

            self.assertIsNotNone(persisted_revision)
            self.assertIsNotNone(persisted_incident)
            self.assertIsNotNone(persisted_patch)
            assert persisted_revision is not None
            assert persisted_incident is not None
            assert persisted_patch is not None
            self.assertEqual(persisted_revision.status, RuntimeBundleRevisionStatus.PUBLISHED)
            self.assertEqual(persisted_incident.status, RuntimeIncidentStatus.OPEN)
            self.assertEqual(persisted_patch.status, RuntimePatchProposalStatus.PROPOSED)
