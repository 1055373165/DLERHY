"""Persist chapter quality summaries.

Revision ID: 20260313_0002
Revises: 20260313_0001
Create Date: 2026-03-13 10:00:00
"""

from alembic import op


revision = "20260313_0002"
down_revision = "20260313_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE chapter_quality_summaries (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            chapter_id UUID NOT NULL UNIQUE REFERENCES chapters(id) ON DELETE CASCADE,
            issue_count INTEGER NOT NULL DEFAULT 0,
            action_count INTEGER NOT NULL DEFAULT 0,
            resolved_issue_count INTEGER NOT NULL DEFAULT 0,
            coverage_ok BOOLEAN NOT NULL DEFAULT FALSE,
            alignment_ok BOOLEAN NOT NULL DEFAULT FALSE,
            term_ok BOOLEAN NOT NULL DEFAULT FALSE,
            format_ok BOOLEAN NOT NULL DEFAULT FALSE,
            blocking_issue_count INTEGER NOT NULL DEFAULT 0,
            low_confidence_count INTEGER NOT NULL DEFAULT 0,
            format_pollution_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_chapter_quality_summaries_document_id "
        "ON chapter_quality_summaries(document_id);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chapter_quality_summaries_document_id;")
    op.execute("DROP TABLE IF EXISTS chapter_quality_summaries;")
