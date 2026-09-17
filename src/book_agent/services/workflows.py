"""DocumentWorkflowService: the facade the API, CLI and run executor call.

It wires repositories and domain services for one session and delegates to the
application use cases in ``book_agent.application`` (document queries, review
repair, export, issue actions, worklist, memory proposals). Bootstrap,
structure refresh, synchronous translation and deletion still live here.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from book_agent.application.document_queries import DocumentQueryService
from book_agent.application.export_use_case import DocumentExportUseCase
from book_agent.application.issue_actions import IssueActionWorkflow
from book_agent.application.issue_queries import IssueQueries
from book_agent.application.memory_proposals import ChapterMemoryProposalService
from book_agent.application.read_models import (
    ActionWorkflowResult,
    ChapterMemoryProposalDecisionResult,
    ChapterMemoryProposalSummary,
    ChapterWorklistAssignmentSummary,
    DocumentBlockerRepairResult,
    DocumentChapterWorklist,
    DocumentChapterWorklistDetail,
    DocumentExportDashboard,
    DocumentExportResult,
    DocumentHistoryPage,
    DocumentReviewResult,
    DocumentSummary,
    DocumentTranslationResult,
    ExportDetail,
)
from book_agent.application.review_repair import ReviewRepairService
from book_agent.application.worklist import ChapterWorklistService
from book_agent.domain.enums import (
    DocumentRunStatus,
    DocumentStatus,
    ExportStatus,
    ExportType,
    PacketStatus,
    SourceType,
)
from book_agent.domain.models import Document
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.export import ExportRepository
from book_agent.infra.repositories.ops import OpsRepository
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.infra.repositories.run_control import RunControlRepository
from book_agent.infra.repositories.translation import TranslationRepository
from book_agent.orchestrator.bootstrap import BootstrapOrchestrator
from book_agent.services.actions import IssueActionExecutor
from book_agent.services.bootstrap import BootstrapArtifacts
from book_agent.services.parse_revision_fork import ParseRevisionForkService
from book_agent.services.epub_structure_refresh import (
    EpubStructureRefreshArtifacts,
    EpubStructureRefreshService,
)
from book_agent.services.export import ExportService
from book_agent.services.pdf_structure_refresh import (
    PdfStructureRefreshArtifacts,
    PdfStructureRefreshService,
)
from book_agent.services.realign import RealignService
from book_agent.services.rebuild import TargetedRebuildService
from book_agent.services.rerun import RerunService
from book_agent.services.review import ReviewService
from book_agent.services.translation import TranslationExecutionArtifacts, TranslationService
from book_agent.workers.translator import TranslationWorker


class DocumentBusyError(RuntimeError):
    """Raised when a destructive operation targets a document whose run
    is still in the orchestrator's hot active set (RUNNING/DRAINING)."""


class DocumentWorkflowService:
    def __init__(
        self,
        session: Session,
        export_root: str | Path = "artifacts/exports",
        translation_worker: TranslationWorker | None = None,
        translation_auto_commit_memory: bool = False,
        translation_max_output_repairs: int = 1,
    ):
        self.session = session
        self.bootstrap_repository = BootstrapRepository(session)
        self.review_repository = ReviewRepository(session)
        self.export_repository = ExportRepository(session)
        self.run_control_repository = RunControlRepository(session)
        self.translation_service = TranslationService(
            TranslationRepository(session),
            worker=translation_worker,
            default_auto_commit_memory=translation_auto_commit_memory,
            max_output_repairs=translation_max_output_repairs,
        )
        self.memory_service = self.translation_service.memory_service
        self.review_service = ReviewService(self.review_repository)
        self.export_service = ExportService(
            self.export_repository,
            output_root=export_root,
        )
        self.ops_repository = OpsRepository(session)
        self.action_executor = IssueActionExecutor(self.ops_repository)
        self.targeted_rebuild_service = TargetedRebuildService(
            session,
            self.bootstrap_repository,
        )
        self.pdf_structure_refresh_service = PdfStructureRefreshService(
            session,
            self.bootstrap_repository,
        )
        self.epub_structure_refresh_service = EpubStructureRefreshService(
            session,
            self.bootstrap_repository,
        )
        self.parse_revision_fork = ParseRevisionForkService(
            session,
            bootstrap_repository=self.bootstrap_repository,
            rebuild_service=self.targeted_rebuild_service,
        )
        self.rerun_service = RerunService(
            self.ops_repository,
            self.translation_service,
            self.review_service,
            self.targeted_rebuild_service,
            RealignService(self.ops_repository),
            self.pdf_structure_refresh_service,
            export_gate_revalidator=self._revalidate_export_gate,
            parse_revision_fork=self.parse_revision_fork,
        )
        self.memory_proposals = ChapterMemoryProposalService(session, self.memory_service)
        self.issue_queries = IssueQueries(session)
        self.worklist = ChapterWorklistService(
            session,
            self.bootstrap_repository,
            self.review_repository,
            self.ops_repository,
            self.issue_queries,
            self.memory_proposals,
        )
        self.documents = DocumentQueryService(
            session,
            self.bootstrap_repository,
            self.review_repository,
            self.export_repository,
            self.issue_queries,
            self.memory_proposals,
        )
        self.issue_actions = IssueActionWorkflow(session, self.action_executor, self.rerun_service)
        self.review_repair = ReviewRepairService(
            session,
            self.bootstrap_repository,
            self.export_repository,
            self.ops_repository,
            self.review_service,
            self.translation_service,
            self.issue_actions,
        )
        self.exports = DocumentExportUseCase(
            session,
            self.bootstrap_repository,
            self.export_repository,
            self.export_service,
            self.ops_repository,
            self.issue_actions,
        )

    def bootstrap_document(self, source_path: str | Path, *, org_id: str | None = None) -> DocumentSummary:
        artifacts: BootstrapArtifacts = BootstrapOrchestrator().bootstrap_document(source_path, org_id=org_id)
        self.bootstrap_repository.save(artifacts)
        return self.documents.get_document_summary(artifacts.document.id)

    def _revalidate_export_gate(self, chapter_id: str) -> None:
        """Re-run the final-export gate checks for one chapter and sync their issues, without raising."""
        bundle = self.export_repository.load_chapter_bundle(chapter_id)
        evaluation = self.export_service.evaluate_chapter_gate(bundle, ExportType.BILINGUAL_HTML)
        self.export_service.sync_gate_issues(bundle, evaluation)

    def bootstrap_epub(self, source_path: str | Path) -> DocumentSummary:
        return self.bootstrap_document(source_path)

    def refresh_pdf_structure(
        self,
        document_id: str,
        *,
        chapter_ids: list[str] | None = None,
    ) -> PdfStructureRefreshArtifacts:
        artifacts = self.pdf_structure_refresh_service.refresh_document(document_id, chapter_ids=chapter_ids)
        artifacts.parse_revision_fork = self.parse_revision_fork.resegment_blocks(
            document_id, reason="pdf structure refresh"
        )
        return artifacts

    def refresh_epub_structure(
        self,
        document_id: str,
        *,
        chapter_ids: list[str] | None = None,
    ) -> EpubStructureRefreshArtifacts:
        artifacts = self.epub_structure_refresh_service.refresh_document(document_id, chapter_ids=chapter_ids)
        artifacts.parse_revision_fork = self.parse_revision_fork.resegment_blocks(
            document_id,
            block_ids=[*artifacts.refreshed_block_ids, *artifacts.created_block_ids, *artifacts.invalidated_block_ids],
            reason="epub structure refresh",
        )
        return artifacts

    def get_document_summary(self, document_id: str) -> DocumentSummary:
        return self.documents.get_document_summary(document_id)

    def delete_document(self, document_id: str) -> None:
        """Hard-delete a document and all dependent rows via FK CASCADE.

        Refuses to delete while a run is in the hot active set
        ({RUNNING, DRAINING}) to avoid yanking the rug under the
        orchestrator. Terminal and dormant states (QUEUED, PAUSED,
        SUCCEEDED, FAILED, …) are safe to clean up.
        """
        document = self.session.get(Document, document_id)
        if document is None:
            raise ValueError(f"Document not found: {document_id}")
        latest_run = self.documents.latest_document_run(document_id)
        if latest_run is not None and latest_run.status in {
            DocumentRunStatus.RUNNING,
            DocumentRunStatus.DRAINING,
        }:
            raise DocumentBusyError(
                f"Document {document_id} has an active run ({latest_run.status.value}); "
                "pause or cancel it before deletion."
            )
        self.session.delete(document)
        self.session.flush()

    def list_document_history(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        query: str | None = None,
        source_type: SourceType | None = None,
        status: DocumentStatus | None = None,
        latest_run_status: DocumentRunStatus | None = None,
        merged_export_ready: bool | None = None,
        org_id: str | None = None,
    ) -> DocumentHistoryPage:
        return self.documents.list_document_history(
            limit=limit,
            offset=offset,
            query=query,
            source_type=source_type,
            status=status,
            latest_run_status=latest_run_status,
            merged_export_ready=merged_export_ready,
            org_id=org_id,
        )

    def translate_document(self, document_id: str, packet_ids: list[str] | None = None) -> DocumentTranslationResult:
        bundle = self.bootstrap_repository.load_document_bundle(document_id)
        requested_packet_ids = set(packet_ids or [])
        translated_packet_count = 0
        recorded_memory_proposal_count = 0
        translation_run_ids: list[str] = []
        review_required_sentence_ids: list[str] = []
        skipped_packet_ids: list[str] = []

        packets = [
            packet
            for chapter_bundle in bundle.chapters
            for packet in chapter_bundle.translation_packets
            if not requested_packet_ids or packet.id in requested_packet_ids
        ]
        for packet in packets:
            if packet.status != PacketStatus.BUILT:
                skipped_packet_ids.append(packet.id)
                continue
            artifacts: TranslationExecutionArtifacts = self.translation_service.execute_packet(packet.id)
            translated_packet_count += 1
            recorded_memory_proposal_count += 1
            translation_run_ids.append(artifacts.translation_run.id)
            review_required_sentence_ids.extend(
                sentence.id for sentence in artifacts.updated_sentences if sentence.sentence_status.value == "review_required"
            )

        return DocumentTranslationResult(
            document_id=document_id,
            translated_packet_count=translated_packet_count,
            skipped_packet_ids=skipped_packet_ids,
            translation_run_ids=translation_run_ids,
            review_required_sentence_ids=review_required_sentence_ids,
            memory_commit_mode=(
                "eager_commit" if self.translation_service.default_auto_commit_memory else "proposal_first"
            ),
            recorded_memory_proposal_count=recorded_memory_proposal_count,
        )

    def review_document(
        self,
        document_id: str,
        *,
        auto_execute_packet_followups: bool = False,
        max_auto_followup_attempts: int = 2,
    ) -> DocumentReviewResult:
        return self.review_repair.review_document(
            document_id,
            auto_execute_packet_followups=auto_execute_packet_followups,
            max_auto_followup_attempts=max_auto_followup_attempts,
        )

    def list_chapter_memory_proposals(
        self,
        document_id: str,
        chapter_id: str,
        *,
        status: str | None = None,
    ) -> list[ChapterMemoryProposalSummary]:
        return self.memory_proposals.list_chapter_memory_proposals(document_id, chapter_id, status=status)

    def approve_chapter_memory_proposal(
        self,
        document_id: str,
        chapter_id: str,
        proposal_id: str,
        *,
        actor_name: str | None = None,
        note: str | None = None,
    ) -> ChapterMemoryProposalDecisionResult:
        return self.memory_proposals.approve_chapter_memory_proposal(
            document_id,
            chapter_id,
            proposal_id,
            actor_name=actor_name,
            note=note,
        )

    def reject_chapter_memory_proposal(
        self,
        document_id: str,
        chapter_id: str,
        proposal_id: str,
        *,
        actor_name: str | None = None,
        note: str | None = None,
    ) -> ChapterMemoryProposalDecisionResult:
        return self.memory_proposals.reject_chapter_memory_proposal(
            document_id,
            chapter_id,
            proposal_id,
            actor_name=actor_name,
            note=note,
        )

    def repair_document_blockers_until_exportable(
        self,
        document_id: str,
        *,
        max_rounds: int = 4,
        max_actions_per_round: int = 64,
    ) -> DocumentBlockerRepairResult:
        return self.review_repair.repair_document_blockers_until_exportable(
            document_id,
            max_rounds=max_rounds,
            max_actions_per_round=max_actions_per_round,
        )

    def export_document(
        self,
        document_id: str,
        export_type: ExportType,
        *,
        auto_execute_followup_on_gate: bool = False,
        max_auto_followup_attempts: int = 3,
    ) -> DocumentExportResult:
        return self.exports.export_document(
            document_id,
            export_type,
            auto_execute_followup_on_gate=auto_execute_followup_on_gate,
            max_auto_followup_attempts=max_auto_followup_attempts,
        )

    def execute_action(self, action_id: str, run_followup: bool = False) -> ActionWorkflowResult:
        return self.issue_actions.execute_action(action_id, run_followup)

    def get_document_export_dashboard(
        self,
        document_id: str,
        *,
        export_type: ExportType | None = None,
        status: ExportStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> DocumentExportDashboard:
        return self.documents.get_document_export_dashboard(
            document_id,
            export_type=export_type,
            status=status,
            limit=limit,
            offset=offset,
        )

    def get_document_export_detail(self, document_id: str, export_id: str) -> ExportDetail:
        return self.documents.get_document_export_detail(document_id, export_id)

    def get_document_chapter_worklist(
        self,
        document_id: str,
        *,
        queue_priority: str | None = None,
        sla_status: str | None = None,
        owner_ready: bool | None = None,
        needs_immediate_attention: bool | None = None,
        assigned: bool | None = None,
        assigned_owner_name: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> DocumentChapterWorklist:
        return self.worklist.get_document_chapter_worklist(
            document_id,
            queue_priority=queue_priority,
            sla_status=sla_status,
            owner_ready=owner_ready,
            needs_immediate_attention=needs_immediate_attention,
            assigned=assigned,
            assigned_owner_name=assigned_owner_name,
            limit=limit,
            offset=offset,
        )

    def get_document_chapter_worklist_detail(
        self,
        document_id: str,
        chapter_id: str,
    ) -> DocumentChapterWorklistDetail:
        return self.worklist.get_document_chapter_worklist_detail(document_id, chapter_id)

    def assign_document_chapter_worklist_owner(
        self,
        document_id: str,
        chapter_id: str,
        *,
        owner_name: str,
        assigned_by: str,
        note: str | None = None,
    ) -> ChapterWorklistAssignmentSummary:
        return self.worklist.assign_document_chapter_worklist_owner(
            document_id,
            chapter_id,
            owner_name=owner_name,
            assigned_by=assigned_by,
            note=note,
        )

    def clear_document_chapter_worklist_owner(
        self,
        document_id: str,
        chapter_id: str,
        *,
        cleared_by: str,
        note: str | None = None,
    ) -> ChapterWorklistAssignmentSummary:
        return self.worklist.clear_document_chapter_worklist_owner(
            document_id,
            chapter_id,
            cleared_by=cleared_by,
            note=note,
        )

