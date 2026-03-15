from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, Numeric, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from book_agent.domain.enums import (
    ArtifactStatus,
    BlockType,
    BookType,
    ChapterStatus,
    DocumentStatus,
    MemoryScopeType,
    MemoryStatus,
    ProtectedPolicy,
    SentenceStatus,
    Severity,
    SnapshotType,
    SourceType,
)
from book_agent.infra.db.base import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_value_type,
)


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    source_type: Mapped[SourceType] = mapped_column(
        enum_value_type(SourceType, name="source_type"),
        nullable=False,
    )
    file_fingerprint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    source_path: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    src_lang: Mapped[str] = mapped_column(Text, nullable=False, default="en")
    tgt_lang: Mapped[str] = mapped_column(Text, nullable=False, default="zh")
    status: Mapped[DocumentStatus] = mapped_column(
        enum_value_type(DocumentStatus, name="document_status"),
        nullable=False,
    )
    parser_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    segmentation_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active_book_profile_version: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class Chapter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("document_id", "ordinal", name="uq_chapters_document_ordinal"),)

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    title_src: Mapped[str | None] = mapped_column(Text)
    title_tgt: Mapped[str | None] = mapped_column(Text)
    anchor_start: Mapped[str | None] = mapped_column(Text)
    anchor_end: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ChapterStatus] = mapped_column(
        enum_value_type(ChapterStatus, name="chapter_status"),
        nullable=False,
    )
    summary_version: Mapped[int | None] = mapped_column(Integer)
    risk_level: Mapped[Severity | None] = mapped_column(
        enum_value_type(Severity, name="chapter_risk_level"),
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class Block(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "blocks"
    __table_args__ = (UniqueConstraint("chapter_id", "ordinal", name="uq_blocks_chapter_ordinal"),)

    chapter_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[BlockType] = mapped_column(
        enum_value_type(BlockType, name="block_type"),
        nullable=False,
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    source_anchor: Mapped[str | None] = mapped_column(Text)
    source_span_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    parse_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    protected_policy: Mapped[ProtectedPolicy] = mapped_column(
        enum_value_type(ProtectedPolicy, name="protected_policy"),
        nullable=False,
    )
    status: Mapped[ArtifactStatus] = mapped_column(
        enum_value_type(ArtifactStatus, name="artifact_status"),
        nullable=False,
        default=ArtifactStatus.ACTIVE,
    )


class Sentence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sentences"
    __table_args__ = (UniqueConstraint("block_id", "ordinal_in_block", name="uq_sentences_block_ordinal"),)

    block_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("blocks.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal_in_block: Mapped[int] = mapped_column(Integer, nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    source_lang: Mapped[str] = mapped_column(Text, nullable=False, default="en")
    translatable: Mapped[bool] = mapped_column(nullable=False, default=True)
    nontranslatable_reason: Mapped[str | None] = mapped_column(Text)
    source_anchor: Mapped[str | None] = mapped_column(Text)
    source_span_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    upstream_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    sentence_status: Mapped[SentenceStatus] = mapped_column(
        enum_value_type(SentenceStatus, name="sentence_status"),
        nullable=False,
        default=SentenceStatus.PENDING,
    )
    active_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class BookProfile(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "book_profiles"
    __table_args__ = (UniqueConstraint("document_id", "version", name="uq_book_profiles_document_version"),)

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    book_type: Mapped[BookType] = mapped_column(
        enum_value_type(BookType, name="book_type"),
        nullable=False,
    )
    style_policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    quote_policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    special_content_policy_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)


class MemorySnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "memory_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "scope_type",
            "scope_id",
            "snapshot_type",
            "version",
            name="uq_memory_snapshots_scope_version",
        ),
    )

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope_type: Mapped[MemoryScopeType] = mapped_column(
        enum_value_type(MemoryScopeType, name="memory_scope_type"),
        nullable=False,
    )
    scope_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    snapshot_type: Mapped[SnapshotType] = mapped_column(
        enum_value_type(SnapshotType, name="snapshot_type"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[MemoryStatus] = mapped_column(
        enum_value_type(MemoryStatus, name="memory_status"),
        nullable=False,
        default=MemoryStatus.ACTIVE,
    )
