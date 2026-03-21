"""Add document-level title_src/title_tgt columns.

Revision ID: 20260321_0007
Revises: 20260317_0006
Create Date: 2026-03-21 11:35:00
"""

from alembic import op


revision = "20260321_0007"
down_revision = "20260317_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('ALTER TABLE documents ADD COLUMN IF NOT EXISTS title_src TEXT;')
    op.execute('ALTER TABLE documents ADD COLUMN IF NOT EXISTS title_tgt TEXT;')
    op.execute(
        """
        UPDATE documents
        SET title_src = COALESCE(title_src, title)
        WHERE (title_src IS NULL OR BTRIM(title_src) = '')
          AND title IS NOT NULL
          AND BTRIM(title) <> '';
        """
    )


def downgrade() -> None:
    op.execute('ALTER TABLE documents DROP COLUMN IF EXISTS title_tgt;')
    op.execute('ALTER TABLE documents DROP COLUMN IF EXISTS title_src;')
