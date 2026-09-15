"""Accepted target variants on glossary entries.

Revision ID: 20260915_0033
Revises: 20260914_0032
Create Date: 2026-09-15

Terminology consistency accepts a small set of equivalent renderings for a term
(e.g. with or without a trailing classifier). Checks that only knew the one
target_term flagged those correct translations as conflicts.
"""

from alembic import op

revision = "20260915_0033"
down_revision = "20260914_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE term_entries ADD COLUMN IF NOT EXISTS target_variants_json JSONB NOT NULL DEFAULT '[]'::jsonb;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE term_entries DROP COLUMN IF EXISTS target_variants_json;")
