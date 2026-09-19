"""Organisations and API keys: who may call the API and whose documents they see."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, Text, Uuid, event
from sqlalchemy.orm import Mapped, mapped_column

from book_agent.infra.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

# Every document created before multi-tenancy (and every document of a
# single-tenant deployment) belongs to this organisation.
DEFAULT_ORG_ID = "00000000-0000-4000-8000-000000000001"
DEFAULT_ORG_NAME = "default"

API_KEY_ROLES = ("viewer", "editor", "admin")


class Org(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "orgs"
    __table_args__ = (
        CheckConstraint(
            "monthly_budget_usd IS NULL OR monthly_budget_usd >= 0", name="ck_orgs_monthly_budget_non_negative"
        ),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Model spend cap per calendar month (UTC); NULL means unlimited.
    monthly_budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Set by the first credit entry: from then on usage is charged against the prepaid balance.
    prepaid_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


CREDIT_ENTRY_KINDS = ("top_up", "refund", "adjustment")


class CreditEntry(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One movement of an organisation's prepaid balance; usage is charged from the event ledger, not here."""

    __tablename__ = "credit_entries"
    __table_args__ = (
        CheckConstraint("kind IN ('top_up', 'refund', 'adjustment')", name="ck_credit_entries_kind"),
        CheckConstraint("kind <> 'top_up' OR amount_usd > 0", name="ck_credit_entries_top_up_positive"),
        # A payment's id: a retried webhook or a double click credits once.
        Index("uq_credit_entries_org_reference", "org_id", "reference", unique=True),
        Index("idx_credit_entries_org_created", "org_id", "created_at"),
    )

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)


class ApiKey(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A hashed API key. The plaintext is shown once at creation and never stored."""

    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint("role IN ('viewer', 'editor', 'admin')", name="ck_api_keys_role"),
        Index("idx_api_keys_org_id", "org_id"),
    )

    org_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Schemas built from the models (tests, create_all) get the default org like migrations do.
# Inserted through the table so each dialect binds the UUID its own way (SQLite stores hex).
def _insert_default_org(target, connection, **_kw) -> None:
    connection.execute(target.insert().values(id=DEFAULT_ORG_ID, name=DEFAULT_ORG_NAME))


event.listen(Org.__table__, "after_create", _insert_default_org)
