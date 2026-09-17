"""Parse-revision fork, sentence level: re-segment changed blocks without losing translations.

Structure refresh changes a block's text but used to leave its sentences
alone (``refresh_sentences_stale``), so packets, translations and issues kept
pointing at sentences that no longer matched the source. The fork:

1. opens a new ``document_parse_revisions`` version (the previous active one
   is superseded);
2. retires the block's sentences (``retired_by_revision_id``) and inserts a
   new sentence set segmented from the current block text;
3. records ``sentence_lineage`` (same / split / merge / removed);
4. rebuilds the packets that covered those sentences, in place;
5. carries each rebuilt packet's latest translation into a new attempt,
   remapping alignments of ``same`` sentences; the packet is TRANSLATED when
   every translatable sentence is covered, otherwise BUILT for retranslation;
6. resolves open, non-human issues on retired sentences (review re-detects
   whatever still applies on the new sentences);
7. writes an audit row and a ``document.reparsed`` event.

Everything happens in the caller's transaction; a failure rolls back the
whole fork. Block and chapter forks (new blocks, moved chapters) are out of
scope here; see docs/agent-upgrade/04.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from book_agent.core.ids import stable_id
from book_agent.domain.enums import (
    ActorType,
    ArtifactStatus,
    ChapterStatus,
    IssueStatus,
    PacketSentenceRole,
    PacketStatus,
    ParseRevisionStatus,
    RunStatus,
    SentenceStatus,
    TargetSegmentStatus,
)
from book_agent.domain.event_kinds import DOCUMENT_REPARSED
from book_agent.domain.models import Block, Chapter, Document, DocumentParseRevision, Sentence, SentenceLineage
from book_agent.domain.models.ops import AuditEvent
from book_agent.domain.models.review import ReviewIssue
from book_agent.domain.models.translation import AlignmentEdge, PacketSentenceMap, TargetSegment, TranslationPacket, TranslationRun
from book_agent.domain.structure.sentence_alignment import SentenceLink, align_sentences, normalize_sentence
from book_agent.infra.repositories.bootstrap import BootstrapRepository
from book_agent.infra.repositories.events import emit_event
from book_agent.infra.repositories.review import ReviewRepository
from book_agent.services.bootstrap import SegmentationService
from book_agent.services.rebuild import TargetedRebuildService

STALE_FLAG = "refresh_sentences_stale"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ForkResult:
    document_id: str
    parse_revision_id: str | None = None
    parse_revision_version: int | None = None
    block_ids: list[str] = field(default_factory=list)
    retired_sentence_count: int = 0
    created_sentence_count: int = 0
    relation_counts: dict[str, int] = field(default_factory=dict)
    rebuilt_packet_ids: list[str] = field(default_factory=list)
    carried_packet_ids: list[str] = field(default_factory=list)
    retranslate_packet_ids: list[str] = field(default_factory=list)
    invalidated_packet_ids: list[str] = field(default_factory=list)
    # New sentences outside every packet's block range (a block-level fork would build packets for them).
    unpacketed_block_ids: list[str] = field(default_factory=list)
    resolved_issue_ids: list[str] = field(default_factory=list)
    # Human-decided issues on retired sentences are left untouched; they need re-confirmation.
    human_decided_issue_ids: list[str] = field(default_factory=list)

    @property
    def forked(self) -> bool:
        return self.parse_revision_id is not None

    @property
    def carried_ratio(self) -> float:
        total = sum(self.relation_counts.values())
        return (self.relation_counts.get("same", 0) / total) if total else 1.0


class ParseRevisionForkService:
    def __init__(
        self,
        session: Session,
        *,
        bootstrap_repository: BootstrapRepository | None = None,
        rebuild_service: TargetedRebuildService | None = None,
        segmentation_service: SegmentationService | None = None,
    ) -> None:
        self.session = session
        self.bootstrap_repository = bootstrap_repository or BootstrapRepository(session)
        self.rebuild_service = rebuild_service or TargetedRebuildService(session, self.bootstrap_repository)
        self.segmentation = segmentation_service or SegmentationService()

    # --- public ---------------------------------------------------------------------

    def stale_block_ids(self, document_id: str) -> list[str]:
        blocks = self.session.scalars(
            select(Block)
            .join(Chapter, Chapter.id == Block.chapter_id)
            .where(Chapter.document_id == document_id)
        ).all()
        flagged = [block.id for block in blocks if (block.source_span_json or {}).get(STALE_FLAG)]
        invalidated_with_sentences = [
            block.id
            for block in blocks
            if block.status != ArtifactStatus.ACTIVE and self._active_sentences(block.id)
        ]
        return list(dict.fromkeys([*flagged, *invalidated_with_sentences]))

    def resegment_blocks(
        self,
        document_id: str,
        *,
        block_ids: list[str] | None = None,
        reason: str,
        actor_id: str = "services.parse_revision_fork",
        run_id: str | None = None,
    ) -> ForkResult:
        document = self.session.get(Document, document_id)
        if document is None:
            raise ValueError(f"Document not found: {document_id}")
        result = ForkResult(document_id=document_id)
        now = _utcnow()
        candidate_ids = block_ids if block_ids is not None else self.stale_block_ids(document_id)
        revision: DocumentParseRevision | None = None
        links: list[SentenceLink] = []
        retired_by_block: dict[str, list[Sentence]] = {}
        new_by_block: dict[str, list[Sentence]] = {}

        for block_id in dict.fromkeys(candidate_ids):
            block = self.session.get(Block, block_id)
            if block is None:
                continue
            chapter = self.session.get(Chapter, block.chapter_id)
            if chapter is None or chapter.document_id != document_id:
                continue
            old = self._active_sentences(block.id)
            fresh = (
                self.segmentation._build_sentences(document, chapter, block, now)
                if block.status == ArtifactStatus.ACTIVE
                else []
            )
            if [normalize_sentence(s.source_text) for s in old] == [normalize_sentence(s.source_text) for s in fresh]:
                self._clear_stale_flag(block, now)
                continue
            if revision is None:
                revision = self._open_revision(document, reason=reason, actor_id=actor_id, now=now)
            for sentence in old:
                sentence.retired_by_revision_id = revision.id
                sentence.updated_at = now
            self.session.flush()
            for ordinal, sentence in enumerate(fresh, start=1):
                sentence.id = stable_id("sentence", document.id, block.id, "revision", revision.version, ordinal)
                sentence.parse_revision_id = revision.id
                sentence.source_span_json = {**(sentence.source_span_json or {}), "parse_revision_id": revision.id}
                self.session.add(sentence)
            self.session.flush()
            block_links = align_sentences(
                [(s.id, s.source_text) for s in old], [(s.id, s.source_text) for s in fresh]
            )
            for link in block_links:
                self.session.add(
                    SentenceLineage(
                        parse_revision_id=revision.id,
                        from_sentence_id=link.from_id,
                        to_sentence_id=link.to_id,
                        relation=link.relation,
                        similarity=link.similarity,
                    )
                )
            links.extend(block_links)
            retired_by_block[block.id] = old
            new_by_block[block.id] = fresh
            self._clear_stale_flag(block, now)
            result.block_ids.append(block.id)

        if revision is None:
            self.session.flush()
            return result
        self.session.flush()

        result.parse_revision_id = revision.id
        result.parse_revision_version = revision.version
        result.retired_sentence_count = sum(len(items) for items in retired_by_block.values())
        result.created_sentence_count = sum(len(items) for items in new_by_block.values())
        for link in links:
            result.relation_counts[link.relation] = result.relation_counts.get(link.relation, 0) + 1

        same_map = {link.from_id: link.to_id for link in links if link.relation == "same" and link.to_id}
        retired_ids = {sentence.id for items in retired_by_block.values() for sentence in items}
        status_by_old = {sentence.id: sentence.sentence_status for items in retired_by_block.values() for sentence in items}

        packets_by_chapter = self._affected_packets(retired_ids, list(new_by_block))

        for chapter_id, packet_ids in packets_by_chapter.items():
            previous_runs = {packet_id: self._latest_run(packet_id) for packet_id in packet_ids}
            rebuilt = self.rebuild_service.rebuild_packets(document_id, chapter_id, packet_ids)
            result.rebuilt_packet_ids.extend(packet.id for packet in rebuilt)
            rebuilt_ids = {packet.id for packet in rebuilt}
            for packet_id in packet_ids:
                if packet_id in rebuilt_ids:
                    continue
                # Its blocks are gone (invalidated); nothing is left to translate in it.
                orphan = self.session.get(TranslationPacket, packet_id)
                if orphan is not None:
                    orphan.status = PacketStatus.INVALIDATED
                    orphan.updated_at = now
                    result.invalidated_packet_ids.append(packet_id)
            for packet in rebuilt:
                carried = self._carry_translation(
                    packet,
                    previous_runs.get(packet.id),
                    same_map=same_map,
                    retired_ids=retired_ids,
                    status_by_old=status_by_old,
                    revision=revision,
                    now=now,
                )
                (result.carried_packet_ids if carried else result.retranslate_packet_ids).append(packet.id)
            chapter = self.session.get(Chapter, chapter_id)
            if chapter is not None and any(pid in result.retranslate_packet_ids for pid in packet_ids):
                chapter.status = ChapterStatus.PACKET_BUILT
                chapter.updated_at = now

        for block_id, fresh in new_by_block.items():
            if not fresh:
                continue
            fresh_ids = [sentence.id for sentence in fresh]
            mapped = self.session.scalar(
                select(func.count(PacketSentenceMap.packet_id)).where(PacketSentenceMap.sentence_id.in_(fresh_ids))
            )
            if not mapped:
                result.unpacketed_block_ids.append(block_id)
        self._settle_issues(retired_ids, links, revision, result)
        self._record(document, revision, result, actor_id=actor_id, reason=reason, run_id=run_id, now=now)
        self.session.flush()
        return result

    # --- steps ----------------------------------------------------------------------

    def _active_sentences(self, block_id: str) -> list[Sentence]:
        return list(
            self.session.scalars(
                select(Sentence)
                .where(Sentence.block_id == block_id, Sentence.retired_by_revision_id.is_(None))
                .order_by(Sentence.ordinal_in_block)
            ).all()
        )

    def _clear_stale_flag(self, block: Block, now: datetime) -> None:
        metadata = dict(block.source_span_json or {})
        if metadata.pop(STALE_FLAG, None) is not None:
            block.source_span_json = metadata
            block.updated_at = now

    def _open_revision(self, document: Document, *, reason: str, actor_id: str, now: datetime) -> DocumentParseRevision:
        latest = self.session.scalar(
            select(func.max(DocumentParseRevision.version)).where(DocumentParseRevision.document_id == document.id)
        ) or 0
        for previous in self.session.scalars(
            select(DocumentParseRevision).where(
                DocumentParseRevision.document_id == document.id,
                DocumentParseRevision.status == ParseRevisionStatus.ACTIVE,
            )
        ).all():
            previous.status = ParseRevisionStatus.SUPERSEDED
            previous.updated_at = now
        revision = DocumentParseRevision(
            id=stable_id("document-parse-revision", document.id, latest + 1, "resegment"),
            document_id=document.id,
            version=latest + 1,
            parser_version=document.parser_version,
            source_type=document.source_type,
            source_path=document.source_path,
            source_fingerprint=document.file_fingerprint,
            status=ParseRevisionStatus.ACTIVE,
            metadata_json={"kind": "resegment", "reason": reason, "actor_id": actor_id},
            created_at=now,
            updated_at=now,
        )
        self.session.add(revision)
        self.session.flush()
        return revision

    def _affected_packets(self, retired_ids: set[str], block_ids: list[str]) -> dict[str, list[str]]:
        packet_ids: set[str] = set()
        if retired_ids:
            packet_ids.update(
                self.session.scalars(
                    select(PacketSentenceMap.packet_id).where(PacketSentenceMap.sentence_id.in_(retired_ids))
                ).all()
            )
        # Blocks that had no sentences yet belong to the packet whose block range covers them.
        for block_id in block_ids:
            block = self.session.get(Block, block_id)
            if block is None:
                continue
            for packet in self.session.scalars(select(TranslationPacket).where(TranslationPacket.chapter_id == block.chapter_id)).all():
                start = self.session.get(Block, packet.block_start_id) if packet.block_start_id else None
                end = self.session.get(Block, packet.block_end_id or packet.block_start_id) if packet.block_start_id else None
                if start is not None and end is not None and start.ordinal <= block.ordinal <= end.ordinal:
                    packet_ids.add(packet.id)
        grouped: dict[str, list[str]] = {}
        for packet in self.session.scalars(select(TranslationPacket).where(TranslationPacket.id.in_(packet_ids))).all():
            grouped.setdefault(packet.chapter_id, []).append(packet.id)
        return {chapter_id: sorted(ids) for chapter_id, ids in grouped.items()}

    def _latest_run(self, packet_id: str) -> TranslationRun | None:
        return self.session.scalars(
            select(TranslationRun)
            .where(TranslationRun.packet_id == packet_id, TranslationRun.status == RunStatus.SUCCEEDED)
            .order_by(TranslationRun.attempt.desc())
            .limit(1)
        ).first()

    def _carry_translation(
        self,
        packet: TranslationPacket,
        previous_run: TranslationRun | None,
        *,
        same_map: dict[str, str],
        retired_ids: set[str],
        status_by_old: dict[str, SentenceStatus],
        revision: DocumentParseRevision,
        now: datetime,
    ) -> bool:
        """Copy the packet's latest translation onto the new sentences; True when fully covered."""
        current = list(
            self.session.scalars(
                select(Sentence)
                .join(PacketSentenceMap, PacketSentenceMap.sentence_id == Sentence.id)
                .where(PacketSentenceMap.packet_id == packet.id, PacketSentenceMap.role == PacketSentenceRole.CURRENT)
            ).all()
        )
        covered: set[str] = set()
        if previous_run is not None:
            segments = list(
                self.session.scalars(
                    select(TargetSegment).where(TargetSegment.translation_run_id == previous_run.id).order_by(TargetSegment.ordinal)
                ).all()
            )
            edges = list(
                self.session.scalars(
                    select(AlignmentEdge).where(AlignmentEdge.target_segment_id.in_([segment.id for segment in segments]))
                ).all()
            ) if segments else []
            remapped: dict[str, list[tuple[AlignmentEdge, str]]] = {}
            for edge in edges:
                if edge.sentence_id in same_map:
                    target_sentence = same_map[edge.sentence_id]
                elif edge.sentence_id in retired_ids:
                    continue
                else:
                    target_sentence = edge.sentence_id
                remapped.setdefault(edge.target_segment_id, []).append((edge, target_sentence))
            kept = [segment for segment in segments if segment.id in remapped]
            if kept:
                attempt = (self.session.scalar(select(func.max(TranslationRun.attempt)).where(TranslationRun.packet_id == packet.id)) or 0) + 1
                new_run = TranslationRun(
                    id=stable_id("translation-run", packet.id, attempt),
                    packet_id=packet.id,
                    model_name=previous_run.model_name,
                    model_config_json={
                        **(previous_run.model_config_json or {}),
                        "worker": "parse_revision_fork",
                        "carried_from_run_id": previous_run.id,
                        "parse_revision_id": revision.id,
                    },
                    prompt_version=previous_run.prompt_version,
                    attempt=attempt,
                    status=RunStatus.SUCCEEDED,
                    output_json={"carried_from_run_id": previous_run.id},
                    token_in=0,
                    token_out=0,
                    cost_usd=0,
                    latency_ms=0,
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(new_run)
                self.session.flush()
                for ordinal, segment in enumerate(kept, start=1):
                    new_segment_id = stable_id("target-segment", new_run.id, ordinal)
                    self.session.add(
                        TargetSegment(
                            id=new_segment_id,
                            chapter_id=segment.chapter_id,
                            translation_run_id=new_run.id,
                            ordinal=ordinal,
                            text_zh=segment.text_zh,
                            segment_type=segment.segment_type,
                            confidence=segment.confidence,
                            final_status=TargetSegmentStatus.DRAFT,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    self.session.flush()
                    for edge, sentence_id in remapped[segment.id]:
                        self.session.add(
                            AlignmentEdge(
                                id=stable_id("alignment-edge", sentence_id, new_segment_id),
                                sentence_id=sentence_id,
                                target_segment_id=new_segment_id,
                                relation_type=edge.relation_type,
                                confidence=edge.confidence,
                                created_by=edge.created_by,
                            )
                        )
                        covered.add(sentence_id)
                for segment in segments:
                    segment.final_status = TargetSegmentStatus.SUPERSEDED
                    segment.updated_at = now
        old_by_new = {new: old for old, new in same_map.items()}
        for sentence in current:
            if sentence.id in covered and sentence.id in old_by_new:
                sentence.sentence_status = status_by_old.get(old_by_new[sentence.id], sentence.sentence_status)
        fully_covered = all(sentence.id in covered for sentence in current if sentence.translatable)
        packet.status = PacketStatus.TRANSLATED if fully_covered and previous_run is not None else PacketStatus.BUILT
        packet.updated_at = now
        self.session.flush()
        return packet.status == PacketStatus.TRANSLATED

    def _settle_issues(
        self,
        retired_ids: set[str],
        links: list[SentenceLink],
        revision: DocumentParseRevision,
        result: ForkResult,
    ) -> None:
        if not retired_ids:
            return
        relation_by_old: dict[str, str] = {}
        for link in links:
            relation_by_old.setdefault(link.from_id, link.relation)
        issues = list(
            self.session.scalars(
                select(ReviewIssue).where(
                    ReviewIssue.sentence_id.in_(retired_ids),
                    ReviewIssue.status.in_([IssueStatus.OPEN, IssueStatus.TRIAGED, IssueStatus.WONTFIX]),
                )
            ).all()
        )
        result.human_decided_issue_ids = [issue.id for issue in issues if issue.decided_by is not None]
        system_owned = [
            issue for issue in issues if issue.decided_by is None and issue.status in (IssueStatus.OPEN, IssueStatus.TRIAGED)
        ]
        if not system_owned:
            return
        sync = ReviewRepository(self.session).sync_issues(
            [],
            [],
            owned_existing=system_owned,
            resolution_note=f"Source sentence retired by parse revision v{revision.version}; review re-checks the new sentences.",
            actor_id="services.parse_revision_fork",
        )
        result.resolved_issue_ids = [issue.id for issue in sync.resolved]

    def _record(
        self,
        document: Document,
        revision: DocumentParseRevision,
        result: ForkResult,
        *,
        actor_id: str,
        reason: str,
        run_id: str | None,
        now: datetime,
    ) -> None:
        payload = {
            "parse_revision_id": revision.id,
            "version": revision.version,
            "reason": reason,
            "block_count": len(result.block_ids),
            "retired_sentence_count": result.retired_sentence_count,
            "created_sentence_count": result.created_sentence_count,
            "relation_counts": dict(result.relation_counts),
            "carried_ratio": round(result.carried_ratio, 3),
            "rebuilt_packet_count": len(result.rebuilt_packet_ids),
            "retranslate_packet_count": len(result.retranslate_packet_ids),
            "resolved_issue_count": len(result.resolved_issue_ids),
            "human_decided_issue_count": len(result.human_decided_issue_ids),
            "unpacketed_block_count": len(result.unpacketed_block_ids),
        }
        revision.metadata_json = {**(revision.metadata_json or {}), **payload}
        self.session.add(
            AuditEvent(
                object_type="document",
                object_id=document.id,
                action="document.reparsed",
                actor_type=ActorType.SYSTEM,
                actor_id=actor_id,
                payload_json=payload,
                created_at=now,
            )
        )
        emit_event(
            self.session,
            kind=DOCUMENT_REPARSED,
            run_id=run_id,
            actor_kind="system",
            actor_id=actor_id,
            payload=payload,
        )
