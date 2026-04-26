from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, LargeBinary, Text
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

    Exactly one row may have ``is_active=True`` at a time. The partial
    unique index enforces that on PostgreSQL; on SQLite (used in some
    dev/test paths) the service layer enforces it inside a transaction.
    """

    __tablename__ = "provider_credentials"
    __table_args__ = (
        Index(
            "uq_provider_credentials_one_active",
            "is_active",
            unique=True,
            postgresql_where=("is_active"),
        ),
    )

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
    last_test_status: Mapped[ProviderTestStatus] = mapped_column(
        enum_value_type(ProviderTestStatus, name="provider_test_status"),
        nullable=False,
        default=ProviderTestStatus.UNKNOWN,
    )
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_message: Mapped[str | None] = mapped_column(Text)
