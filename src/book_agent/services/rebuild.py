from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.block_rules import block_is_context_translatable
from book_agent.domain.context.builders import ChapterBriefBuilder, ContextPacketBuilder
from book_agent.domain.enums import (
    ActionType,
    ActorType,
    ArtifactStatus,
    ChapterStatus,
    JobScopeType,
    JobStatus,
    JobType,
    LockLevel,
    MemoryScopeType,
    MemoryStatus,
    PacketType,
    SnapshotType,
    TermStatus,
)
from book_agent.domain.models import Block, BookProfile, Chapter, Document, JobRun, MemorySnapshot, Sentence
from book_agent.domain.models.ops import AuditEvent
from book_agent.domain.models.review import ReviewIssue
from book_agent.domain.models.translation import PacketSentenceMap, TermEntry, TranslationPacket
from book_agent.infra.repositories.bootstrap import BootstrapRepository, PersistedChapterBundle
from book_agent.orchestrator.rerun import RerunPlan


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class TargetedRebuildArtifacts:
    rebuilt_packet_ids: list[str]
    rebuilt_snapshot_ids: list[str]
    rebuilt_snapshots: list["RebuiltSnapshotEvidence"]
    chapter_brief_version: int | None = None
    termbase_version: int | None = None
    entity_snapshot_version: int | None = None


@dataclass(slots=True)
class RebuiltSnapshotEvidence:
    snapshot_id: str
    snapshot_type: str
    version: int


class TargetedRebuildService:
    def __init__(
        self,
        session: Session,
        bootstrap_repository: BootstrapRepository,
        chapter_brief_builder: ChapterBriefBuilder | None = None,
        context_packet_builder: ContextPacketBuilder | None = None,
    ):
        self.session = session
        self.bootstrap_repository = bootstrap_repository
        self.chapter_brief_builder = chapter_brief_builder or ChapterBriefBuilder()
        self.context_packet_builder = context_packet_builder or ContextPacketBuilder()

    def apply(self, issue_id: str, rerun_plan: RerunPlan) -> TargetedRebuildArtifacts | None:
        if rerun_plan.action_type not in {
            ActionType.REBUILD_PACKET_THEN_RERUN,
            ActionType.REBUILD_CHAPTER_BRIEF,
            ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED,
            ActionType.UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED,
        }:
            return None

        issue = self.session.get(ReviewIssue, issue_id)
        if issue is None:
            raise ValueError(f"Review issue not found: {issue_id}")
        if issue.chapter_id is None:
            return None

        bundle = self.bootstrap_repository.load_document_bundle(issue.document_id)
        chapter_bundle = self._find_chapter_bundle(bundle.chapters, issue.chapter_id)
        if chapter_bundle is None or bundle.book_profile is None:
            return None

        now = _utcnow()
        book_profile = bundle.book_profile
        chapter = chapter_bundle.chapter
        chapter_blocks = [block for block in chapter_bundle.blocks if block.status == ArtifactStatus.ACTIVE]
        chapter_sentences = chapter_bundle.sentences
        latest_brief = self._latest_snapshot(
            bundle.memory_snapshots,
            snapshot_type=SnapshotType.CHAPTER_BRIEF,
            scope_type=MemoryScopeType.CHAPTER,
            scope_id=chapter.id,
        )
        latest_termbase = self._latest_snapshot(
            bundle.memory_snapshots,
            snapshot_type=SnapshotType.TERMBASE,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
        )
        latest_entity = self._latest_snapshot(
            bundle.memory_snapshots,
            snapshot_type=SnapshotType.ENTITY_REGISTRY,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
        )

        if latest_brief is None or latest_termbase is None or latest_entity is None:
            raise ValueError("Rebuild prerequisites are missing for chapter context.")

        rebuilt_snapshot_ids: list[str] = []
        rebuilt_snapshots: list[RebuiltSnapshotEvidence] = []
        chapter_brief = latest_brief
        termbase_snapshot = latest_termbase
        entity_snapshot = latest_entity

        if rerun_plan.action_type == ActionType.REBUILD_CHAPTER_BRIEF:
            chapter_brief = self._rebuild_chapter_brief(
                bundle.document,
                chapter,
                chapter_blocks,
                chapter_sentences,
                latest_brief,
                now,
            )
            rebuilt_snapshot_ids.append(chapter_brief.id)
            rebuilt_snapshots.append(self._snapshot_evidence(chapter_brief))

        if rerun_plan.action_type == ActionType.UPDATE_TERMBASE_THEN_RERUN_TARGETED:
            termbase_snapshot = self._refresh_termbase_snapshot(
                bundle.document.id,
                chapter.id,
                latest_termbase,
                now,
            )
            if termbase_snapshot.id != latest_termbase.id:
                rebuilt_snapshot_ids.append(termbase_snapshot.id)
                rebuilt_snapshots.append(self._snapshot_evidence(termbase_snapshot))

        if rerun_plan.action_type == ActionType.UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED:
            entity_snapshot = self._refresh_entity_snapshot(latest_entity, now)
            if entity_snapshot.id != latest_entity.id:
                rebuilt_snapshot_ids.append(entity_snapshot.id)
                rebuilt_snapshots.append(self._snapshot_evidence(entity_snapshot))

        target_packet_ids = self._packet_ids_for_scope(chapter_bundle, rerun_plan)
        rebuilt_packets, rebuilt_maps = self._rebuild_packets(
            document=bundle.document,
            chapter=chapter,
            chapter_blocks=chapter_blocks,
            chapter_sentences=chapter_sentences,
            book_profile=book_profile,
            chapter_brief=chapter_brief,
            termbase_snapshot=termbase_snapshot,
            entity_snapshot=entity_snapshot,
            packet_ids=target_packet_ids,
        )

        self._replace_packet_maps(target_packet_ids, rebuilt_maps)
        for packet in rebuilt_packets:
            self.session.merge(packet)
        chapter.status = ChapterStatus.PACKET_BUILT
        chapter.summary_version = chapter_brief.version
        chapter.updated_at = now
        self.session.merge(chapter)

        for snapshot in {chapter_brief, termbase_snapshot, entity_snapshot}:
            if snapshot.id in rebuilt_snapshot_ids:
                self.session.merge(snapshot)

        for job in self._build_jobs(
            document_id=bundle.document.id,
            chapter_id=chapter.id,
            chapter_brief=chapter_brief if chapter_brief.id in rebuilt_snapshot_ids else None,
            termbase_snapshot=termbase_snapshot if termbase_snapshot.id in rebuilt_snapshot_ids else None,
            entity_snapshot=entity_snapshot if entity_snapshot.id in rebuilt_snapshot_ids else None,
            rebuilt_packet_ids=target_packet_ids,
            action_type=rerun_plan.action_type,
            now=now,
            issue_id=issue_id,
        ):
            self.session.merge(job)

        for audit in self._build_audits(
            issue_id=issue_id,
            action_type=rerun_plan.action_type,
            chapter=chapter,
            rebuilt_packets=rebuilt_packets,
            rebuilt_snapshots=[
                snapshot
                for snapshot in (chapter_brief, termbase_snapshot, entity_snapshot)
                if snapshot.id in rebuilt_snapshot_ids
            ],
            now=now,
        ):
            self.session.merge(audit)

        self.session.flush()
        return TargetedRebuildArtifacts(
            rebuilt_packet_ids=target_packet_ids,
            rebuilt_snapshot_ids=rebuilt_snapshot_ids,
            rebuilt_snapshots=rebuilt_snapshots,
            chapter_brief_version=chapter_brief.version,
            termbase_version=termbase_snapshot.version,
            entity_snapshot_version=entity_snapshot.version,
        )

    def _rebuild_chapter_brief(
        self,
        document: Document,
        chapter: Chapter,
        blocks: list[Block],
        sentences: list[Sentence],
        current_snapshot: MemorySnapshot,
        now: datetime,
    ) -> MemorySnapshot:
        current_snapshot.status = MemoryStatus.SUPERSEDED
        self.session.merge(current_snapshot)
        snapshot = self.chapter_brief_builder.build(
            document,
            chapter,
            blocks,
            sentences,
            version=current_snapshot.version + 1,
        )
        snapshot.created_at = now
        return snapshot

    def _refresh_termbase_snapshot(
        self,
        document_id: str,
        chapter_id: str,
        current_snapshot: MemorySnapshot,
        now: datetime,
    ) -> MemorySnapshot:
        terms = self.session.scalars(
            select(TermEntry).where(
                TermEntry.document_id == document_id,
                TermEntry.status == TermStatus.ACTIVE,
            )
        ).all()
        content = {"terms": self._serialize_terms(terms, chapter_id)}
        if content == current_snapshot.content_json:
            return current_snapshot

        current_snapshot.status = MemoryStatus.SUPERSEDED
        self.session.merge(current_snapshot)
        return MemorySnapshot(
            id=stable_id(
                "snapshot",
                document_id,
                SnapshotType.TERMBASE.value,
                MemoryScopeType.GLOBAL.value,
                current_snapshot.version + 1,
            ),
            document_id=document_id,
            scope_type=MemoryScopeType.GLOBAL,
            scope_id=None,
            snapshot_type=SnapshotType.TERMBASE,
            version=current_snapshot.version + 1,
            content_json=content,
            status=MemoryStatus.ACTIVE,
            created_at=now,
        )

    def _refresh_entity_snapshot(self, current_snapshot: MemorySnapshot, now: datetime) -> MemorySnapshot:
        # P0 still lacks a first-class entity registry editor; reuse the latest active snapshot for now.
        return current_snapshot

    def _serialize_terms(self, term_entries: list[TermEntry], chapter_id: str) -> list[dict[str, str]]:
        lock_rank = {
            LockLevel.LOCKED: 3,
            LockLevel.PREFERRED: 2,
            LockLevel.SUGGESTED: 1,
        }
        candidates = [
            entry
            for entry in term_entries
            if entry.scope_type == MemoryScopeType.GLOBAL
            or (entry.scope_type == MemoryScopeType.CHAPTER and entry.scope_id == chapter_id)
        ]
        candidates.sort(
            key=lambda entry: (
                1 if entry.scope_type == MemoryScopeType.CHAPTER and entry.scope_id == chapter_id else 0,
                lock_rank.get(entry.lock_level, 0),
                entry.version,
            ),
            reverse=True,
        )

        deduped: dict[str, dict[str, str]] = {}
        for entry in candidates:
            key = entry.source_term.lower()
            if key in deduped:
                continue
            deduped[key] = {
                "source_term": entry.source_term,
                "target_term": entry.target_term,
                "lock_level": entry.lock_level.value,
            }
        return list(deduped.values())

    def _rebuild_packets(
        self,
        *,
        document: Document,
        chapter: Chapter,
        chapter_blocks: list[Block],
        chapter_sentences: list[Sentence],
        book_profile: BookProfile,
        chapter_brief: MemorySnapshot,
        termbase_snapshot: MemorySnapshot,
        entity_snapshot: MemorySnapshot,
        packet_ids: list[str],
    ) -> tuple[list[TranslationPacket], list[PacketSentenceMap]]:
        blocks_by_id = {block.id: block for block in chapter_blocks}
        translatable_blocks = [
            block
            for block in sorted(chapter_blocks, key=lambda item: item.ordinal)
            if block_is_context_translatable(block)
        ]
        sentences_by_block: dict[str, list[Sentence]] = {}
        for sentence in chapter_sentences:
            sentences_by_block.setdefault(sentence.block_id, []).append(sentence)
        packets = self.session.scalars(
            select(TranslationPacket).where(TranslationPacket.id.in_(packet_ids))
        ).all()
        packet_by_id = {packet.id: packet for packet in packets}

        rebuilt_packets: list[TranslationPacket] = []
        rebuilt_maps: list[PacketSentenceMap] = []
        for packet_id in packet_ids:
            packet = packet_by_id.get(packet_id)
            if packet is None or packet.block_start_id is None:
                continue
            start_block = blocks_by_id.get(packet.block_start_id)
            end_block = blocks_by_id.get(packet.block_end_id or packet.block_start_id)
            if start_block is None or end_block is None:
                continue
            try:
                start_index = next(idx for idx, item in enumerate(translatable_blocks) if item.id == start_block.id)
            except StopIteration:
                continue
            try:
                end_index = next(idx for idx, item in enumerate(translatable_blocks) if item.id == end_block.id)
            except StopIteration:
                continue
            if end_index < start_index:
                continue
            current_blocks = translatable_blocks[start_index : end_index + 1]
            prev_blocks = translatable_blocks[max(0, start_index - 2):start_index]
            next_blocks = translatable_blocks[end_index + 1:end_index + 3]
            if len(current_blocks) == 1:
                rebuilt_packet, packet_maps = self.context_packet_builder.build_packet(
                    document=document,
                    chapter=chapter,
                    block=current_blocks[0],
                    prev_blocks=prev_blocks,
                    next_blocks=next_blocks,
                    sentences_by_block=sentences_by_block,
                    book_profile=book_profile,
                    chapter_brief=chapter_brief,
                    termbase_snapshot=termbase_snapshot,
                    entity_snapshot=entity_snapshot,
                    packet_id=packet.id,
                    packet_type=PacketType.RETRANSLATE,
                    created_at=packet.created_at,
                )
            else:
                rebuilt_packet, packet_maps = self.context_packet_builder._build_merged_packet(
                    document=document,
                    chapter=chapter,
                    current_blocks=current_blocks,
                    prev_blocks=prev_blocks,
                    next_blocks=next_blocks,
                    sentences_by_block=sentences_by_block,
                    book_profile=book_profile,
                    chapter_brief=chapter_brief,
                    termbase_snapshot=termbase_snapshot,
                    entity_snapshot=entity_snapshot,
                    now=packet.created_at,
                    packet_id=packet.id,
                    packet_type=PacketType.RETRANSLATE,
                    created_at=packet.created_at,
                )
            rebuilt_packets.append(rebuilt_packet)
            rebuilt_maps.extend(packet_maps)
        return rebuilt_packets, rebuilt_maps

    def _replace_packet_maps(self, packet_ids: list[str], packet_maps: list[PacketSentenceMap]) -> None:
        if not packet_ids:
            return
        self.session.execute(
            delete(PacketSentenceMap).where(PacketSentenceMap.packet_id.in_(packet_ids))
        )
        for mapping in packet_maps:
            self.session.merge(mapping)

    def _packet_ids_for_scope(self, chapter_bundle: PersistedChapterBundle, rerun_plan: RerunPlan) -> list[str]:
        if rerun_plan.scope_type == JobScopeType.PACKET:
            return rerun_plan.scope_ids
        if rerun_plan.scope_type == JobScopeType.CHAPTER:
            return [packet.id for packet in chapter_bundle.translation_packets]
        return []

    def _build_jobs(
        self,
        *,
        document_id: str,
        chapter_id: str,
        chapter_brief: MemorySnapshot | None,
        termbase_snapshot: MemorySnapshot | None,
        entity_snapshot: MemorySnapshot | None,
        rebuilt_packet_ids: list[str],
        action_type: ActionType,
        now: datetime,
        issue_id: str,
    ) -> list[JobRun]:
        jobs: list[JobRun] = []
        if chapter_brief is not None:
            jobs.append(
                JobRun(
                    id=stable_id("job", JobType.BRIEF.value, document_id, chapter_id, chapter_brief.version),
                    job_type=JobType.BRIEF,
                    scope_type=JobScopeType.CHAPTER,
                    scope_id=chapter_id,
                    status=JobStatus.SUCCEEDED,
                    rerun_reason=f"{action_type.value}:{issue_id}",
                    started_at=now,
                    ended_at=now,
                    created_at=now,
                )
            )
        for snapshot in (termbase_snapshot, entity_snapshot):
            if snapshot is None:
                continue
            jobs.append(
                JobRun(
                    id=stable_id("job", JobType.PROFILE.value, document_id, snapshot.snapshot_type.value, snapshot.version),
                    job_type=JobType.PROFILE,
                    scope_type=JobScopeType.DOCUMENT,
                    scope_id=document_id,
                    status=JobStatus.SUCCEEDED,
                    rerun_reason=f"{action_type.value}:{issue_id}",
                    started_at=now,
                    ended_at=now,
                    created_at=now,
                )
            )
        if rebuilt_packet_ids:
            jobs.append(
                JobRun(
                    id=stable_id("job", JobType.PACKET.value, document_id, chapter_id, now.isoformat()),
                    job_type=JobType.PACKET,
                    scope_type=JobScopeType.CHAPTER,
                    scope_id=chapter_id,
                    status=JobStatus.SUCCEEDED,
                    rerun_reason=f"{action_type.value}:{issue_id}",
                    started_at=now,
                    ended_at=now,
                    created_at=now,
                )
            )
        return jobs

    def _build_audits(
        self,
        *,
        issue_id: str,
        action_type: ActionType,
        chapter: Chapter,
        rebuilt_packets: list[TranslationPacket],
        rebuilt_snapshots: list[MemorySnapshot],
        now: datetime,
    ) -> list[AuditEvent]:
        audits: list[AuditEvent] = []
        for snapshot in rebuilt_snapshots:
            audits.append(
                AuditEvent(
                    id=stable_id("audit", "memory_snapshot", snapshot.id, "snapshot.rebuilt", issue_id),
                    object_type="memory_snapshot",
                    object_id=snapshot.id,
                    action="snapshot.rebuilt",
                    actor_type=ActorType.SYSTEM,
                    actor_id="targeted-rebuild-service",
                    payload_json={
                        "issue_id": issue_id,
                        "chapter_id": chapter.id,
                        "action_type": action_type.value,
                        "snapshot_type": snapshot.snapshot_type.value,
                        "version": snapshot.version,
                    },
                    created_at=now,
                )
            )
        for packet in rebuilt_packets:
            audits.append(
                AuditEvent(
                    id=stable_id("audit", "packet", packet.id, "packet.rebuilt", issue_id),
                    object_type="packet",
                    object_id=packet.id,
                    action="packet.rebuilt",
                    actor_type=ActorType.SYSTEM,
                    actor_id="targeted-rebuild-service",
                    payload_json={
                        "issue_id": issue_id,
                        "chapter_id": chapter.id,
                        "action_type": action_type.value,
                        "packet_type": packet.packet_type.value,
                        "book_profile_version": packet.book_profile_version,
                        "chapter_brief_version": packet.chapter_brief_version,
                        "termbase_version": packet.termbase_version,
                        "entity_snapshot_version": packet.entity_snapshot_version,
                    },
                    created_at=now,
                )
            )
        return audits

    def _latest_snapshot(
        self,
        snapshots: list[MemorySnapshot],
        *,
        snapshot_type: SnapshotType,
        scope_type: MemoryScopeType,
        scope_id: str | None,
    ) -> MemorySnapshot | None:
        matches = [
            snapshot
            for snapshot in snapshots
            if snapshot.snapshot_type == snapshot_type
            and snapshot.scope_type == scope_type
            and snapshot.scope_id == scope_id
            and snapshot.status == MemoryStatus.ACTIVE
        ]
        matches.sort(key=lambda snapshot: snapshot.version, reverse=True)
        return matches[0] if matches else None

    def _find_chapter_bundle(
        self,
        chapter_bundles: list[PersistedChapterBundle],
        chapter_id: str,
    ) -> PersistedChapterBundle | None:
        for chapter_bundle in chapter_bundles:
            if chapter_bundle.chapter.id == chapter_id:
                return chapter_bundle
        return None

    def _snapshot_evidence(self, snapshot: MemorySnapshot) -> RebuiltSnapshotEvidence:
        return RebuiltSnapshotEvidence(
            snapshot_id=snapshot.id,
            snapshot_type=snapshot.snapshot_type.value,
            version=snapshot.version,
        )
