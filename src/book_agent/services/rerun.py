from __future__ import annotations

from dataclasses import dataclass

from book_agent.domain.enums import ActionType, IssueStatus, JobScopeType, PacketStatus
from book_agent.infra.repositories.ops import OpsRepository
from book_agent.orchestrator.rerun import RerunPlan
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


class RerunService:
    def __init__(
        self,
        ops_repository: OpsRepository,
        translation_service: TranslationService,
        review_service: ReviewService,
        targeted_rebuild_service: TargetedRebuildService,
        realign_service: RealignService,
    ):
        self.ops_repository = ops_repository
        self.translation_service = translation_service
        self.review_service = review_service
        self.targeted_rebuild_service = targeted_rebuild_service
        self.realign_service = realign_service

    def execute(self, rerun_plan: RerunPlan) -> RerunExecutionArtifacts:
        issue = self.ops_repository.get_issue(rerun_plan.issue_id)
        translation_run_ids: list[str] = []
        rebuild_artifacts = None

        if rerun_plan.action_type == ActionType.REALIGN_ONLY:
            realign_artifacts = self.realign_service.execute(self._packet_ids_for_plan(rerun_plan))
            packet_ids = realign_artifacts.packet_ids
        else:
            rebuild_artifacts = self.targeted_rebuild_service.apply(issue.id, rerun_plan)
            packet_ids = rebuild_artifacts.rebuilt_packet_ids if rebuild_artifacts else self._packet_ids_for_plan(rerun_plan)

            for packet_id in packet_ids:
                packet = self.ops_repository.mark_packet_ready_for_rerun(packet_id)
                if packet.status != PacketStatus.BUILT:
                    continue
                artifacts: TranslationExecutionArtifacts = self.translation_service.execute_packet(packet.id)
                translation_run_ids.append(artifacts.translation_run.id)

        review_artifacts: ReviewArtifacts | None = None
        if issue.chapter_id and rerun_plan.scope_type in {JobScopeType.PACKET, JobScopeType.CHAPTER}:
            review_artifacts = self.review_service.review_chapter(issue.chapter_id)

        refreshed_issue = self.ops_repository.get_issue(issue.id)
        issue_resolved = refreshed_issue.status == IssueStatus.RESOLVED
        return RerunExecutionArtifacts(
            rerun_plan=rerun_plan,
            translated_packet_ids=packet_ids,
            translation_run_ids=translation_run_ids,
            review_artifacts=review_artifacts,
            issue_resolved=issue_resolved,
            rebuild_artifacts=rebuild_artifacts,
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
