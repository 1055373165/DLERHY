from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from book_agent.domain.enums import ArtifactStatus, ParseRevisionStatus, SourceType
from book_agent.infra.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_value_type


class DocumentParseRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_parse_revisions"
    __table_args__ = (UniqueConstraint("document_id", "version", name="uq_document_parse_revisions_document_version"),)

    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parser_version: Mapped[int] = mapped_column(Integer, nullable=False)
    parse_ir_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_type: Mapped[SourceType] = mapped_column(
        enum_value_type(SourceType, name="parse_revision_source_type"),
        nullable=False,
    )
    source_path: Mapped[str | None] = mapped_column(Text)
    source_fingerprint: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ParseRevisionStatus] = mapped_column(
        enum_value_type(ParseRevisionStatus, name="parse_revision_status"),
        nullable=False,
        default=ParseRevisionStatus.ACTIVE,
    )
    canonical_ir_path: Mapped[str | None] = mapped_column(Text)
    canonical_ir_checksum: Mapped[str | None] = mapped_column(Text)
    projection_hints_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class DocumentParseRevisionArtifact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_parse_revision_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "document_parse_revision_id",
            "artifact_type",
            name="uq_document_parse_revision_artifacts_revision_type",
        ),
    )

    document_parse_revision_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("document_parse_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_type: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ArtifactStatus] = mapped_column(
        enum_value_type(ArtifactStatus, name="parse_revision_artifact_status"),
        nullable=False,
        default=ArtifactStatus.ACTIVE,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
