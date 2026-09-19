"""Prepaid credit: a ledger of top-ups per organisation, charged by usage from the event ledger.

Revision ID: 20260919_0045
Revises: 20260919_0044
Create Date: 2026-09-19
"""

from alembic import op

revision = "20260919_0045"
down_revision = "20260919_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE orgs ADD COLUMN prepaid_since TIMESTAMPTZ")
    op.execute(
        """
        CREATE TABLE credit_entries (
            id UUID PRIMARY KEY,
            org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
            amount_usd NUMERIC(12, 4) NOT NULL,
            kind TEXT NOT NULL,
            reference TEXT,
            note TEXT,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_credit_entries_kind CHECK (kind IN ('top_up', 'refund', 'adjustment')),
            CONSTRAINT ck_credit_entries_top_up_positive CHECK (kind <> 'top_up' OR amount_usd > 0)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_credit_entries_org_reference ON credit_entries (org_id, reference)")
    op.execute("CREATE INDEX idx_credit_entries_org_created ON credit_entries (org_id, created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE credit_entries")
    op.execute("ALTER TABLE orgs DROP COLUMN prepaid_since")
