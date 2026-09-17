from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, Uuid, text
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
    JsonDocument,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_value_type,
)


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    # The same file may be imported once per organisation.
    __table_args__ = (UniqueConstraint("org_id", "file_fingerprint", name="uq_documents_org_fingerprint"),)

    org_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("orgs.id", ondelete="RESTRICT"),
        nullable=False,
        default="00000000-0000-4000-8000-000000000001",
    )
    source_type: Mapped[SourceType] = mapped_column(
        enum_value_type(SourceType, name="source_type"),
        nullable=False,
    )
    file_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    title_src: Mapped[str | None] = mapped_column(Text)
    title_tgt: Mapped[str | None] = mapped_column(Text)
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
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)


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
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)


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
    parse_revision_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_parse_revisions.id", ondelete="SET NULL"),
    )
    canonical_node_id: Mapped[str | None] = mapped_column(Text)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    source_anchor: Mapped[str | None] = mapped_column(Text)
    source_span_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
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
    """A source sentence. Sentences are never rewritten when a block is re-segmented:
    the parse-revision fork retires them (``retired_by_revision_id``) and inserts
    a new set; ``sentence_lineage`` links the two. Readers use active sentences only."""

    __tablename__ = "sentences"
    __table_args__ = (
        Index(
            "uq_sentences_block_ordinal_active",
            "block_id",
            "ordinal_in_block",
            unique=True,
            postgresql_where=text("retired_by_revision_id IS NULL"),
            sqlite_where=text("retired_by_revision_id IS NULL"),
        ),
    )

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
    parse_revision_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_parse_revisions.id", ondelete="SET NULL"),
    )
    canonical_node_id: Mapped[str | None] = mapped_column(Text)
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
    source_span_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    upstream_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    sentence_status: Mapped[SentenceStatus] = mapped_column(
        enum_value_type(SentenceStatus, name="sentence_status"),
        nullable=False,
        default=SentenceStatus.PENDING,
    )
    active_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retired_by_revision_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_parse_revisions.id", ondelete="SET NULL"),
    )


class SentenceLineage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """How a retired sentence maps onto the sentences of the revision that replaced it."""

    __tablename__ = "sentence_lineage"
    __table_args__ = (
        CheckConstraint("relation IN ('same', 'split', 'merge', 'removed')", name="ck_sentence_lineage_relation"),
        Index("idx_sentence_lineage_from", "from_sentence_id"),
        Index("idx_sentence_lineage_to", "to_sentence_id"),
    )

    parse_revision_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_parse_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_sentence_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("sentences.id", ondelete="CASCADE"),
        nullable=False,
    )
    to_sentence_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("sentences.id", ondelete="CASCADE"),
    )
    # same | split | merge | removed
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    similarity: Mapped[float | None] = mapped_column(Numeric(4, 3))


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
    style_policy_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    quote_policy_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    special_content_policy_json: Mapped[dict[str, Any]] = mapped_column(
        JsonDocument,
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
    content_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    status: Mapped[MemoryStatus] = mapped_column(
        enum_value_type(MemoryStatus, name="memory_status"),
        nullable=False,
        default=MemoryStatus.ACTIVE,
    )


class DocumentImage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "document_images"

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    block_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("blocks.id", ondelete="SET NULL"),
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    image_type: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    bbox_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    latex: Mapped[str | None] = mapped_column(Text)
    alt_text: Mapped[str | None] = mapped_column(Text)
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JsonDocument, nullable=False, default=dict)
