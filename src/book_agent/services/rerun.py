from __future__ import annotations

from dataclasses import dataclass

from book_agent.domain.enums import ActionType, IssueStatus, JobScopeType, PacketStatus
from book_agent.infra.repositories.ops import OpsRepository
from book_agent.orchestrator.rerun import (
    RerunPlan,
    concept_overrides_for_issue,
    merge_concept_overrides,
    merge_style_hints,
    style_hints_for_issue,
)
from book_agent.services.context_compile import ChapterContextCompileOptions
from book_agent.services.pdf_structure_refresh import PdfStructureRefreshArtifacts, PdfStructureRefreshService
from book_agent.services.realign import RealignService
from book_agent.services.rebuild import TargetedRebuildArtifacts, TargetedRebuildService
from book_agent.services.review import ReviewArtifacts, ReviewService
from book_agent.services.translation import TranslationExecutionArtifacts, TranslationService


@dataclass(slots=True)
class RerunExecutionArtifacts:
    rerun_plan: RerunPlan
    translated_packet_ids: list[str]
    translation_run_ids: list[str]
    review_artifacts: ReviewArtifacts | None
    issue_resolved: bool
    rebuild_artifacts: TargetedRebuildArtifacts | None = None
    structure_refresh_artifacts: PdfStructureRefreshArtifacts | None = None


class RerunService:
    def __init__(
        self,
        ops_repository: OpsRepository,
        translation_service: TranslationService,
        review_service: ReviewService,
        targeted_rebuild_service: TargetedRebuildService,
        realign_service: RealignService,
        pdf_structure_refresh_service: PdfStructureRefreshService | None = None,
    ):
        self.ops_repository = ops_repository
        self.translation_service = translation_service
        self.review_service = review_service
        self.targeted_rebuild_service = targeted_rebuild_service
        self.realign_service = realign_service
        self.pdf_structure_refresh_service = pdf_structure_refresh_service

    def execute(self, rerun_plan: RerunPlan) -> RerunExecutionArtifacts:
        issue = self.ops_repository.get_issue(rerun_plan.issue_id)
        issue_id = issue.id
        issue_document_id = issue.document_id
        issue_chapter_id = issue.chapter_id
        concept_overrides = rerun_plan.concept_overrides
        style_hints = rerun_plan.style_hints
        if rerun_plan.scope_type == JobScopeType.PACKET:
            sibling_issues = []
            for packet_id in rerun_plan.scope_ids:
                sibling_issues.extend(
                    self.ops_repository.list_unresolved_issues_for_packet(
                        packet_id,
                        exclude_issue_id=issue.id,
                    )
                )
            if sibling_issues:
                concept_overrides = merge_concept_overrides(
                    [concept_overrides, *(concept_overrides_for_issue(item) for item in sibling_issues)]
                )
                style_hints = merge_style_hints(
                    [style_hints, *(style_hints_for_issue(item) for item in sibling_issues)]
                )
        effective_rerun_plan = RerunPlan(
            issue_id=rerun_plan.issue_id,
            action_type=rerun_plan.action_type,
            scope_type=rerun_plan.scope_type,
            scope_ids=list(rerun_plan.scope_ids),
            concept_overrides=concept_overrides,
            style_hints=style_hints,
        )
        translation_run_ids: list[str] = []
        rebuild_artifacts = None
        structure_refresh_artifacts = None

        if effective_rerun_plan.action_type == ActionType.REALIGN_ONLY:
            realign_artifacts = self.realign_service.execute(self._packet_ids_for_plan(effective_rerun_plan))
            packet_ids = realign_artifacts.packet_ids
        elif effective_rerun_plan.action_type in {ActionType.REPARSE_CHAPTER, ActionType.REPARSE_DOCUMENT}:
            if self.pdf_structure_refresh_service is None:
                raise ValueError("PDF structure refresh service is not configured for reparse actions.")
            structure_refresh_artifacts = self.pdf_structure_refresh_service.refresh_document(
                issue_document_id,
                chapter_ids=(
                    effective_rerun_plan.scope_ids
                    if effective_rerun_plan.scope_type == JobScopeType.CHAPTER
                    else None
                ),
            )
            self.ops_repository.session.expire_all()
            packet_ids = []
        else:
            rebuild_artifacts = self.targeted_rebuild_service.apply(issue.id, effective_rerun_plan)
            packet_ids = (
                rebuild_artifacts.rebuilt_packet_ids if rebuild_artifacts else self._packet_ids_for_plan(effective_rerun_plan)
            )

            for packet_id in packet_ids:
                packet = self.ops_repository.mark_packet_ready_for_rerun(packet_id)
                if packet.status != PacketStatus.BUILT:
                    continue
                compile_options = None
                if concept_overrides:
                    compile_options = ChapterContextCompileOptions(
                        concept_overrides=concept_overrides,
                    )
                artifacts: TranslationExecutionArtifacts = self.translation_service.execute_packet(
                    packet.id,
                    compile_options=compile_options,
                    rerun_hints=style_hints,
                )
                translation_run_ids.append(artifacts.translation_run.id)

        review_artifacts: ReviewArtifacts | None = None
        if issue_chapter_id and effective_rerun_plan.scope_type in {
            JobScopeType.PACKET,
            JobScopeType.CHAPTER,
            JobScopeType.DOCUMENT,
        }:
            review_artifacts = self.review_service.review_chapter(issue_chapter_id)

        try:
            refreshed_issue = self.ops_repository.get_issue(issue_id)
            issue_resolved = refreshed_issue.status == IssueStatus.RESOLVED
        except ValueError:
            issue_resolved = True
        return RerunExecutionArtifacts(
            rerun_plan=effective_rerun_plan,
            translated_packet_ids=packet_ids,
            translation_run_ids=translation_run_ids,
            review_artifacts=review_artifacts,
            issue_resolved=issue_resolved,
            rebuild_artifacts=rebuild_artifacts,
            structure_refresh_artifacts=structure_refresh_artifacts,
        )

    def _packet_ids_for_plan(self, rerun_plan: RerunPlan) -> list[str]:
        if rerun_plan.scope_type == JobScopeType.PACKET:
            return rerun_plan.scope_ids
        if rerun_plan.scope_type == JobScopeType.CHAPTER:
            packet_ids: list[str] = []
            for chapter_id in rerun_plan.scope_ids:
                packet_ids.extend(packet.id for packet in self.ops_repository.list_packets_for_chapter(chapter_id))
            return packet_ids
        return []
