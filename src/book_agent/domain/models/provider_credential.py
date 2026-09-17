from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from book_agent.domain.enums import ProviderKind, ProviderTestStatus
from book_agent.infra.db.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_value_type,
)


class ProviderCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """User-configured translation provider, stored in DB so model swaps
    don't require a server restart or .env edit.

    At most one row per scope may have ``is_active=True``: one shared row
    (``org_id`` NULL) and one per organisation. The partial unique indexes
    enforce that on PostgreSQL and SQLite alike (without the ``sqlite_where``
    clause SQLite would treat them as plain unique indexes and refuse a second
    inactive row).
    """

    __tablename__ = "provider_credentials"
    __table_args__ = (
        # One active credential per scope: shared (org_id NULL) and each organisation.
        Index(
            "uq_provider_credentials_one_active_shared",
            "is_active",
            unique=True,
            postgresql_where=text("is_active AND org_id IS NULL"),
            sqlite_where=text("is_active AND org_id IS NULL"),
        ),
        Index(
            "uq_provider_credentials_one_active_per_org",
            "org_id",
            unique=True,
            postgresql_where=text("is_active AND org_id IS NOT NULL"),
            sqlite_where=text("is_active AND org_id IS NOT NULL"),
        ),
    )

    # NULL: shared by every organisation that has no active credential of its own.
    org_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), ForeignKey("orgs.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    provider_kind: Mapped[ProviderKind] = mapped_column(
        enum_value_type(ProviderKind, name="provider_kind"),
        nullable=False,
    )
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    # Fernet ciphertext; nullable so echo provider configs (no key needed) fit.
    api_key_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    streaming: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=8192)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    # Stored as ×10 so we keep an integer column but still allow 1-decimal precision.
    retry_backoff_seconds_x10: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Bumped on every change that affects the worker built from this row
    # (config edits, activation); worker caches in every process compare
    # (active id, config_revision) against their cached key.
    config_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    last_test_status: Mapped[ProviderTestStatus] = mapped_column(
        enum_value_type(ProviderTestStatus, name="provider_test_status"),
        nullable=False,
        default=ProviderTestStatus.UNKNOWN,
    )
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_message: Mapped[str | None] = mapped_column(Text)
