"""Add ReviewSession runtime resource table.

Revision ID: 20260327_0013
Revises: 20260326_0012
Create Date: 2026-03-27 11:30:00
"""

from alembic import op


revision = "20260327_0013"
down_revision = "20260326_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE review_sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            chapter_run_id UUID NOT NULL REFERENCES chapter_runs(id) ON DELETE CASCADE,
            desired_generation INTEGER NOT NULL DEFAULT 1,
            observed_generation INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL CHECK (status IN (
                'active', 'paused', 'succeeded', 'failed', 'cancelled'
            )),
            terminality_state TEXT NOT NULL CHECK (terminality_state IN (
                'open', 'approved', 'blocked'
            )),
            scope_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            runtime_bundle_revision_id UUID,
            last_work_item_id UUID REFERENCES work_items(id) ON DELETE SET NULL,
            conditions_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            status_detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_terminal_at TIMESTAMPTZ,
            last_reconciled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_review_sessions_chapter_generation UNIQUE (chapter_run_id, desired_generation)
        );
        """
    )
    op.execute("CREATE INDEX idx_review_sessions_chapter_status ON review_sessions(chapter_run_id, status);")
    op.execute(
        "CREATE INDEX idx_review_sessions_bundle_generation "
        "ON review_sessions(runtime_bundle_revision_id, desired_generation);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_review_sessions_bundle_generation;")
    op.execute("DROP INDEX IF EXISTS idx_review_sessions_chapter_status;")
    op.execute("DROP TABLE IF EXISTS review_sessions;")
