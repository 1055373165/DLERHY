"""Add merged_html export type.

Revision ID: 20260315_0005
Revises: 20260315_0004
Create Date: 2026-03-15 08:45:00
"""

from alembic import op


revision = "20260315_0005"
down_revision = "20260315_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE exports DROP CONSTRAINT IF EXISTS exports_export_type_check;")
    op.execute(
        """
        ALTER TABLE exports
        ADD CONSTRAINT exports_export_type_check
        CHECK (export_type IN (
            'bilingual_html', 'merged_html', 'zh_epub', 'review_package', 'jsonl'
        ));
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE exports DROP CONSTRAINT IF EXISTS exports_export_type_check;")
    op.execute(
        """
        ALTER TABLE exports
        ADD CONSTRAINT exports_export_type_check
        CHECK (export_type IN (
            'bilingual_html', 'zh_epub', 'review_package', 'jsonl'
        ));
        """
    )
