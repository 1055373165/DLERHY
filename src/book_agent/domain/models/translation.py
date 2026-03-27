from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from book_agent.domain.enums import (
    ActorType,
    ArtifactStatus,
    LockLevel,
    MemoryProposalStatus,
    MemoryScopeType,
    PacketSentenceRole,
    PacketStatus,
    PacketType,
    RelationType,
    RunStatus,
    SegmentType,
    TargetSegmentStatus,
    TermStatus,
    TermType,
)
from book_agent.infra.db.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_value_type,
)


class TranslationPacket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "translation_packets"

    chapter_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    block_start_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("blocks.id", ondelete="SET NULL"),
    )
    block_end_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("blocks.id", ondelete="SET NULL"),
    )
    packet_type: Mapped[PacketType] = mapped_column(
        enum_value_type(PacketType, name="packet_type"),
        nullable=False,
    )
    book_profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_brief_version: Mapped[int | None] = mapped_column(Integer)
    termbase_version: Mapped[int | None] = mapped_column(Integer)
    entity_snapshot_version: Mapped[int | None] = mapped_column(Integer)
    style_snapshot_version: Mapped[int | None] = mapped_column(Integer)
    packet_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    risk_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    status: Mapped[PacketStatus] = mapped_column(
        enum_value_type(PacketStatus, name="packet_status"),
        nullable=False,
        default=PacketStatus.BUILT,
    )


class PacketSentenceMap(Base):
    __tablename__ = "packet_sentence_map"

    packet_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("translation_packets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sentence_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("sentences.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[PacketSentenceRole] = mapped_column(
        enum_value_type(PacketSentenceRole, name="packet_sentence_role"),
        nullable=False,
    )


class TranslationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "translation_runs"
    __table_args__ = (UniqueConstraint("packet_id", "attempt", name="uq_translation_runs_packet_attempt"),)

    packet_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("translation_packets.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    model_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[RunStatus] = mapped_column(
        enum_value_type(RunStatus, name="translation_run_status"),
        nullable=False,
    )
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    token_in: Mapped[int | None] = mapped_column(Integer)
    token_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)


class ChapterMemoryProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chapter_memory_proposals"
    __table_args__ = (UniqueConstraint("translation_run_id", name="uq_chapter_memory_proposals_run"),)

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    packet_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("translation_packets.id", ondelete="CASCADE"),
        nullable=False,
    )
    translation_run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("translation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    base_snapshot_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("memory_snapshots.id", ondelete="SET NULL"),
    )
    base_snapshot_version: Mapped[int | None] = mapped_column(Integer)
    proposed_content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[MemoryProposalStatus] = mapped_column(
        enum_value_type(MemoryProposalStatus, name="memory_proposal_status"),
        nullable=False,
        default=MemoryProposalStatus.PROPOSED,
    )
    committed_snapshot_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("memory_snapshots.id", ondelete="SET NULL"),
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TargetSegment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "target_segments"
    __table_args__ = (UniqueConstraint("translation_run_id", "ordinal", name="uq_target_segments_run_ordinal"),)

    chapter_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    translation_run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("translation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text_zh: Mapped[str] = mapped_column(Text, nullable=False)
    segment_type: Mapped[SegmentType] = mapped_column(
        enum_value_type(SegmentType, name="segment_type"),
        nullable=False,
    )
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    final_status: Mapped[TargetSegmentStatus] = mapped_column(
        enum_value_type(TargetSegmentStatus, name="target_segment_status"),
        nullable=False,
        default=TargetSegmentStatus.DRAFT,
    )


class AlignmentEdge(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "alignment_edges"

    sentence_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("sentences.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_segment_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("target_segments.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation_type: Mapped[RelationType] = mapped_column(
        enum_value_type(RelationType, name="relation_type"),
        nullable=False,
    )
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    created_by: Mapped[ActorType] = mapped_column(
        enum_value_type(ActorType, name="alignment_actor_type"),
        nullable=False,
    )


class TermEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "term_entries"

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope_type: Mapped[MemoryScopeType] = mapped_column(
        enum_value_type(MemoryScopeType, name="term_scope_type"),
        nullable=False,
    )
    scope_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    source_term: Mapped[str] = mapped_column(Text, nullable=False)
    target_term: Mapped[str] = mapped_column(Text, nullable=False)
    term_type: Mapped[TermType] = mapped_column(
        enum_value_type(TermType, name="term_type"),
        nullable=False,
    )
    lock_level: Mapped[LockLevel] = mapped_column(
        enum_value_type(LockLevel, name="lock_level"),
        nullable=False,
    )
    status: Mapped[TermStatus] = mapped_column(
        enum_value_type(TermStatus, name="term_status"),
        nullable=False,
        default=TermStatus.ACTIVE,
    )
    evidence_sentence_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("sentences.id", ondelete="SET NULL"),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
