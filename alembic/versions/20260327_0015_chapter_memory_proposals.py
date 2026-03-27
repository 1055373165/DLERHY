"""Add chapter memory proposals for proposal-vs-commit split.

Revision ID: 20260327_0015
Revises: 20260327_0014
Create Date: 2026-03-27 22:15:00
"""

from alembic import op


revision = "20260327_0015"
down_revision = "20260327_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE chapter_memory_proposals (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
            packet_id UUID NOT NULL REFERENCES translation_packets(id) ON DELETE CASCADE,
            translation_run_id UUID NOT NULL REFERENCES translation_runs(id) ON DELETE CASCADE,
            base_snapshot_id UUID REFERENCES memory_snapshots(id) ON DELETE SET NULL,
            base_snapshot_version INTEGER,
            proposed_content_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL CHECK (status IN ('proposed', 'committed', 'rejected')),
            committed_snapshot_id UUID REFERENCES memory_snapshots(id) ON DELETE SET NULL,
            committed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_chapter_memory_proposals_run UNIQUE (translation_run_id)
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_chapter_memory_proposals_chapter_status ON chapter_memory_proposals(chapter_id, status);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chapter_memory_proposals_chapter_status;")
    op.execute("DROP TABLE IF EXISTS chapter_memory_proposals;")
