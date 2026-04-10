"""Expand exports export_type check to match ExportType enum.

Revision ID: 20260410_0018
Revises: 20260410_0017
Create Date: 2026-04-10 08:50:00
"""

from alembic import op


revision = "20260410_0018"
down_revision = "20260410_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE exports DROP CONSTRAINT IF EXISTS exports_export_type_check;")
    op.execute(
        """
        ALTER TABLE exports
        ADD CONSTRAINT exports_export_type_check
        CHECK (export_type IN (
            'bilingual_html',
            'bilingual_markdown',
            'merged_html',
            'merged_markdown',
            'rebuilt_epub',
            'rebuilt_pdf',
            'zh_epub',
            'zh_pdf',
            'review_package',
            'jsonl'
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
            'bilingual_html',
            'merged_html',
            'zh_epub',
            'review_package',
            'jsonl'
        ));
        """
    )
