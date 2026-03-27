from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from book_agent.domain.enums import (
    ChapterRunPhase,
    ChapterRunStatus,
    JobScopeType,
    PacketTaskAction,
    PacketTaskStatus,
    ReviewSessionStatus,
    ReviewTerminalityState,
    WorkItemScopeType,
    WorkItemStatus,
)
from book_agent.domain.models.ops import (
    ChapterRun,
    PacketTask,
    ReviewSession,
    RuntimeCheckpoint,
    RuntimeIncident,
    RuntimePatchProposal,
    WorkItem,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_UNSET = object()


def _merge_json(current: dict[str, Any] | None, patch: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(current or {})
    for key, value in dict(patch or {}).items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_json(existing, value)
        else:
            merged[key] = value
    return merged


class RuntimeResourcesRepository:
    """Persistence boundary for Runtime V2 control-plane resources."""

    def __init__(self, session: Session):
        self.session = session

    def get_chapter_run(self, chapter_run_id: str) -> ChapterRun:
        chapter_run = self.session.get(ChapterRun, chapter_run_id)
        if chapter_run is None:
            raise ValueError(f"ChapterRun not found: {chapter_run_id}")
        return chapter_run

    def get_chapter_run_by_run_and_chapter(self, *, run_id: str, chapter_id: str) -> ChapterRun | None:
        return self.session.scalar(
            select(ChapterRun).where(
                ChapterRun.run_id == run_id,
                ChapterRun.chapter_id == chapter_id,
            )
        )

    def list_chapter_runs_for_run(self, *, run_id: str) -> list[ChapterRun]:
        return self.session.scalars(
            select(ChapterRun)
            .where(ChapterRun.run_id == run_id)
            .order_by(ChapterRun.created_at.asc(), ChapterRun.id.asc())
        ).all()

    def ensure_chapter_run(
        self,
        *,
        run_id: str,
        document_id: str,
        chapter_id: str,
        desired_phase: ChapterRunPhase = ChapterRunPhase.PACKETIZE,
    ) -> ChapterRun:
        existing = self.get_chapter_run_by_run_and_chapter(run_id=run_id, chapter_id=chapter_id)
        if existing is not None:
            return existing

        now = _utcnow()
        chapter_run = ChapterRun(
            run_id=run_id,
            document_id=document_id,
            chapter_id=chapter_id,
            desired_phase=desired_phase,
            observed_phase=desired_phase,
            status=ChapterRunStatus.ACTIVE,
            generation=1,
            observed_generation=1,
            conditions_json={},
            status_detail_json={},
            pause_reason=None,
            last_reconciled_at=None,
            created_at=now,
            updated_at=now,
        )
        self.session.add(chapter_run)
        self.session.flush()
        return chapter_run

    def update_chapter_run(
        self,
        chapter_run_id: str,
        *,
        conditions_json: dict[str, Any] | None = None,
        status_detail_json: dict[str, Any] | None = None,
        last_reconciled_at: datetime | None | object = _UNSET,
    ) -> ChapterRun:
        chapter_run = self.get_chapter_run(chapter_run_id)
        now = _utcnow()
        if conditions_json is not None:
            chapter_run.conditions_json = dict(conditions_json)
        if status_detail_json is not None:
            chapter_run.status_detail_json = dict(status_detail_json)
        if last_reconciled_at is not _UNSET:
            chapter_run.last_reconciled_at = last_reconciled_at
        chapter_run.updated_at = now
        self.session.add(chapter_run)
        self.session.flush()
        return chapter_run

    def merge_chapter_run_conditions(self, chapter_run_id: str, patch: dict[str, Any]) -> ChapterRun:
        chapter_run = self.get_chapter_run(chapter_run_id)
        return self.update_chapter_run(
            chapter_run_id,
            conditions_json=_merge_json(chapter_run.conditions_json, patch),
        )

    def merge_chapter_run_status_detail(self, chapter_run_id: str, patch: dict[str, Any]) -> ChapterRun:
        chapter_run = self.get_chapter_run(chapter_run_id)
        return self.update_chapter_run(
            chapter_run_id,
            status_detail_json=_merge_json(chapter_run.status_detail_json, patch),
        )

    def append_chapter_recovered_lineage(
        self,
        *,
        chapter_run_id: str,
        lineage_event: dict[str, Any],
    ) -> ChapterRun:
        if not lineage_event:
            raise ValueError("lineage_event must not be empty")

        chapter_run = self.get_chapter_run(chapter_run_id)
        conditions = dict(chapter_run.conditions_json or {})
        recovered_lineage = [
            dict(entry)
            for entry in list(conditions.get("recovered_lineage") or [])
            if isinstance(entry, dict)
        ]
        normalized_event = dict(lineage_event)
        normalized_event.setdefault("recorded_at", _utcnow().isoformat())
        recovered_lineage.append(normalized_event)
        conditions["recovered_lineage"] = recovered_lineage
        return self.update_chapter_run(
            chapter_run_id,
            conditions_json=conditions,
        )

    def get_packet_task(self, packet_task_id: str) -> PacketTask:
        packet_task = self.session.get(PacketTask, packet_task_id)
        if packet_task is None:
            raise ValueError(f"PacketTask not found: {packet_task_id}")
        return packet_task

    def get_packet_task_by_identity(
        self,
        *,
        chapter_run_id: str,
        packet_id: str,
        packet_generation: int,
    ) -> PacketTask | None:
        return self.session.scalar(
            select(PacketTask).where(
                PacketTask.chapter_run_id == chapter_run_id,
                PacketTask.packet_id == packet_id,
                PacketTask.packet_generation == packet_generation,
            )
        )

    def list_packet_tasks_for_chapter_run(self, *, chapter_run_id: str) -> list[PacketTask]:
        return self.session.scalars(
            select(PacketTask)
            .where(PacketTask.chapter_run_id == chapter_run_id)
            .order_by(PacketTask.created_at.asc(), PacketTask.id.asc())
        ).all()

    def ensure_packet_task(
        self,
        *,
        chapter_run_id: str,
        packet_id: str,
        packet_generation: int = 1,
        desired_action: PacketTaskAction = PacketTaskAction.TRANSLATE,
    ) -> PacketTask:
        existing = self.get_packet_task_by_identity(
            chapter_run_id=chapter_run_id,
            packet_id=packet_id,
            packet_generation=packet_generation,
        )
        if existing is not None:
            return existing

        now = _utcnow()
        packet_task = PacketTask(
            chapter_run_id=chapter_run_id,
            packet_id=packet_id,
            packet_generation=packet_generation,
            desired_action=desired_action,
            status=PacketTaskStatus.PENDING,
            input_version_bundle_json={},
            context_snapshot_id=None,
            runtime_bundle_revision_id=None,
            attempt_count=0,
            last_translation_run_id=None,
            last_work_item_id=None,
            last_error_class=None,
            conditions_json={},
            status_detail_json={},
            invalidated_at=None,
            created_at=now,
            updated_at=now,
        )
        self.session.add(packet_task)
        self.session.flush()
        return packet_task

    def update_packet_task(
        self,
        packet_task_id: str,
        *,
        status: PacketTaskStatus | None = None,
        attempt_count: int | None = None,
        last_work_item_id: str | None | object = _UNSET,
        last_translation_run_id: str | None | object = _UNSET,
        last_error_class: str | None | object = _UNSET,
        runtime_bundle_revision_id: str | None | object = _UNSET,
        conditions_json: dict[str, Any] | None = None,
        status_detail_json: dict[str, Any] | None = None,
    ) -> PacketTask:
        packet_task = self.get_packet_task(packet_task_id)
        packet_task.updated_at = _utcnow()
        if status is not None:
            packet_task.status = status
        if attempt_count is not None:
            packet_task.attempt_count = attempt_count
        if last_work_item_id is not _UNSET:
            packet_task.last_work_item_id = last_work_item_id
        if last_translation_run_id is not _UNSET:
            packet_task.last_translation_run_id = last_translation_run_id
        if last_error_class is not _UNSET:
            packet_task.last_error_class = last_error_class
        if runtime_bundle_revision_id is not _UNSET:
            packet_task.runtime_bundle_revision_id = runtime_bundle_revision_id
        if conditions_json is not None:
            packet_task.conditions_json = dict(conditions_json)
        if status_detail_json is not None:
            packet_task.status_detail_json = dict(status_detail_json)
        self.session.add(packet_task)
        self.session.flush()
        return packet_task

    def merge_packet_task_conditions(self, packet_task_id: str, patch: dict[str, Any]) -> PacketTask:
        packet_task = self.get_packet_task(packet_task_id)
        return self.update_packet_task(
            packet_task_id,
            conditions_json=_merge_json(packet_task.conditions_json, patch),
        )

    def merge_packet_task_status_detail(self, packet_task_id: str, patch: dict[str, Any]) -> PacketTask:
        packet_task = self.get_packet_task(packet_task_id)
        return self.update_packet_task(
            packet_task_id,
            status_detail_json=_merge_json(packet_task.status_detail_json, patch),
        )

    def get_review_session(self, review_session_id: str) -> ReviewSession:
        review_session = self.session.get(ReviewSession, review_session_id)
        if review_session is None:
            raise ValueError(f"ReviewSession not found: {review_session_id}")
        return review_session

    def get_review_session_by_identity(
        self,
        *,
        chapter_run_id: str,
        desired_generation: int,
    ) -> ReviewSession | None:
        return self.session.scalar(
            select(ReviewSession).where(
                ReviewSession.chapter_run_id == chapter_run_id,
                ReviewSession.desired_generation == desired_generation,
            )
        )

    def get_active_review_session_for_chapter_run(self, *, chapter_run_id: str) -> ReviewSession | None:
        return self.session.scalar(
            select(ReviewSession)
            .where(
                ReviewSession.chapter_run_id == chapter_run_id,
                ReviewSession.status == ReviewSessionStatus.ACTIVE,
            )
            .order_by(ReviewSession.desired_generation.desc(), ReviewSession.id.desc())
        )

    def list_review_sessions_for_chapter_run(self, *, chapter_run_id: str) -> list[ReviewSession]:
        return self.session.scalars(
            select(ReviewSession)
            .where(ReviewSession.chapter_run_id == chapter_run_id)
            .order_by(ReviewSession.created_at.asc(), ReviewSession.id.asc())
        ).all()

    def ensure_review_session(
        self,
        *,
        chapter_run_id: str,
        desired_generation: int = 1,
        observed_generation: int | None = None,
        scope_json: dict[str, Any] | None = None,
        runtime_bundle_revision_id: str | None = None,
    ) -> ReviewSession:
        existing = self.get_review_session_by_identity(
            chapter_run_id=chapter_run_id,
            desired_generation=desired_generation,
        )
        if existing is not None:
            return existing

        now = _utcnow()
        review_session = ReviewSession(
            chapter_run_id=chapter_run_id,
            desired_generation=desired_generation,
            observed_generation=observed_generation or desired_generation,
            status=ReviewSessionStatus.ACTIVE,
            terminality_state=ReviewTerminalityState.OPEN,
            scope_json=dict(scope_json or {}),
            runtime_bundle_revision_id=runtime_bundle_revision_id,
            last_work_item_id=None,
            conditions_json={},
            status_detail_json={},
            last_terminal_at=None,
            last_reconciled_at=None,
            created_at=now,
            updated_at=now,
        )
        self.session.add(review_session)
        self.session.flush()
        return review_session

    def update_review_session(
        self,
        review_session_id: str,
        *,
        status: ReviewSessionStatus | None = None,
        terminality_state: ReviewTerminalityState | None = None,
        observed_generation: int | None = None,
        scope_json: dict[str, Any] | None = None,
        conditions_json: dict[str, Any] | None = None,
        status_detail_json: dict[str, Any] | None = None,
        runtime_bundle_revision_id: str | None | object = _UNSET,
        last_work_item_id: str | None | object = _UNSET,
        last_terminal_at: datetime | None | object = _UNSET,
        last_reconciled_at: datetime | None | object = _UNSET,
    ) -> ReviewSession:
        review_session = self.get_review_session(review_session_id)
        now = _utcnow()
        if status is not None:
            review_session.status = status
        if terminality_state is not None:
            review_session.terminality_state = terminality_state
        if observed_generation is not None:
            review_session.observed_generation = observed_generation
        if scope_json is not None:
            review_session.scope_json = dict(scope_json)
        if conditions_json is not None:
            review_session.conditions_json = dict(conditions_json)
        if status_detail_json is not None:
            review_session.status_detail_json = dict(status_detail_json)
        if runtime_bundle_revision_id is not _UNSET:
            review_session.runtime_bundle_revision_id = runtime_bundle_revision_id
        if last_work_item_id is not _UNSET:
            review_session.last_work_item_id = last_work_item_id
        if last_terminal_at is not _UNSET:
            review_session.last_terminal_at = last_terminal_at
        if last_reconciled_at is not _UNSET:
            review_session.last_reconciled_at = last_reconciled_at
        review_session.updated_at = now
        self.session.add(review_session)
        self.session.flush()
        return review_session

    def merge_review_session_conditions(self, review_session_id: str, patch: dict[str, Any]) -> ReviewSession:
        review_session = self.get_review_session(review_session_id)
        return self.update_review_session(
            review_session_id,
            conditions_json=_merge_json(review_session.conditions_json, patch),
        )

    def merge_review_session_status_detail(self, review_session_id: str, patch: dict[str, Any]) -> ReviewSession:
        review_session = self.get_review_session(review_session_id)
        return self.update_review_session(
            review_session_id,
            status_detail_json=_merge_json(review_session.status_detail_json, patch),
        )

    def get_checkpoint(
        self,
        *,
        run_id: str,
        scope_type: JobScopeType,
        scope_id: str,
        checkpoint_key: str,
    ) -> RuntimeCheckpoint | None:
        return self.session.scalar(
            select(RuntimeCheckpoint).where(
                RuntimeCheckpoint.run_id == run_id,
                RuntimeCheckpoint.scope_type == scope_type,
                RuntimeCheckpoint.scope_id == scope_id,
                RuntimeCheckpoint.checkpoint_key == checkpoint_key,
            )
        )

    def upsert_checkpoint(
        self,
        *,
        run_id: str,
        scope_type: JobScopeType,
        scope_id: str,
        checkpoint_key: str,
        checkpoint_json: dict[str, Any],
        generation: int = 1,
    ) -> RuntimeCheckpoint:
        now = _utcnow()
        existing = self.get_checkpoint(
            run_id=run_id,
            scope_type=scope_type,
            scope_id=scope_id,
            checkpoint_key=checkpoint_key,
        )
        if existing is not None:
            existing.generation = generation
            existing.checkpoint_json = dict(checkpoint_json)
            existing.updated_at = now
            self.session.add(existing)
            self.session.flush()
            return existing

        checkpoint = RuntimeCheckpoint(
            run_id=run_id,
            scope_type=scope_type,
            scope_id=scope_id,
            checkpoint_key=checkpoint_key,
            generation=generation,
            checkpoint_json=dict(checkpoint_json),
            created_at=now,
            updated_at=now,
        )
        self.session.add(checkpoint)
        self.session.flush()
        return checkpoint

    def list_checkpoints_for_run(self, *, run_id: str) -> list[RuntimeCheckpoint]:
        return self.session.scalars(
            select(RuntimeCheckpoint)
            .where(RuntimeCheckpoint.run_id == run_id)
            .order_by(RuntimeCheckpoint.created_at.asc(), RuntimeCheckpoint.id.asc())
        ).all()

    def get_runtime_incident(self, incident_id: str) -> RuntimeIncident:
        incident = self.session.get(RuntimeIncident, incident_id)
        if incident is None:
            raise ValueError(f"RuntimeIncident not found: {incident_id}")
        return incident

    def get_runtime_patch_proposal(self, proposal_id: str) -> RuntimePatchProposal:
        proposal = self.session.get(RuntimePatchProposal, proposal_id)
        if proposal is None:
            raise ValueError(f"RuntimePatchProposal not found: {proposal_id}")
        return proposal

    def list_retryable_work_items_for_scope(
        self,
        *,
        run_id: str,
        scope_type: WorkItemScopeType,
        scope_id: str,
    ) -> list[WorkItem]:
        return self.session.scalars(
            select(WorkItem)
            .where(
                WorkItem.run_id == run_id,
                WorkItem.scope_type == scope_type,
                WorkItem.scope_id == scope_id,
                WorkItem.status.in_([WorkItemStatus.PENDING, WorkItemStatus.RETRYABLE_FAILED]),
            )
            .order_by(WorkItem.created_at.asc(), WorkItem.id.asc())
        ).all()
