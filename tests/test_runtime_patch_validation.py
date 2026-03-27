import unittest
from uuid import uuid4

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
from book_agent.domain.models.ops import DocumentRun, RuntimeIncident, RuntimePatchProposal
from book_agent.infra.db.base import Base
from book_agent.infra.db.session import build_engine, build_session_factory
from book_agent.services.runtime_patch_validation import RuntimePatchValidationService


class RuntimePatchValidationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = build_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = build_session_factory(engine=self.engine)

    def _create_proposal(self) -> str:
        with self.session_factory() as session:
            document = Document(
                source_type=SourceType.EPUB,
                file_fingerprint=f"runtime-patch-validation-{uuid4()}",
                source_path="/tmp/runtime-patch-validation.epub",
                title="Runtime Patch Validation",
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
                fingerprint=f"runtime-patch-validation:{uuid4()}",
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
                status=RuntimePatchProposalStatus.PROPOSED,
                proposed_by="tester",
                patch_surface="runtime_bundle",
                diff_manifest_json={"files": ["src/book_agent/services/runtime_bundle.py"]},
                validation_report_json={},
                status_detail_json={},
            )
            session.add(proposal)
            session.commit()
            return proposal.id

    def test_begin_and_record_validation_result(self) -> None:
        proposal_id = self._create_proposal()

        with self.session_factory() as session:
            service = RuntimePatchValidationService(session)
            proposal = service.begin_validation(proposal_id=proposal_id)
            self.assertEqual(proposal.status, RuntimePatchProposalStatus.VALIDATING)

            result = service.record_validation_result(
                proposal_id=proposal_id,
                passed=True,
                report_json={"command": "uv run pytest tests/test_runtime_patch_validation.py"},
            )
            session.commit()

        self.assertTrue(result.passed)
        self.assertEqual(result.status, RuntimePatchProposalStatus.VALIDATED)
        self.assertEqual(result.report_json["command"], "uv run pytest tests/test_runtime_patch_validation.py")
        self.assertEqual(result.report_json["canary_verdict"], "passed")

        with self.session_factory() as session:
            persisted = session.get(RuntimePatchProposal, proposal_id)
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertEqual(persisted.status, RuntimePatchProposalStatus.VALIDATED)
            self.assertTrue(persisted.validation_report_json["passed"])
            self.assertEqual(persisted.validation_report_json["canary_verdict"], "passed")
