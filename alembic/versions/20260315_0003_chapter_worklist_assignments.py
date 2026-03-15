"""Persist chapter worklist assignments.

Revision ID: 20260315_0003
Revises: 20260313_0002
Create Date: 2026-03-15 01:15:00
"""

from alembic import op


revision = "20260315_0003"
down_revision = "20260313_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE chapter_worklist_assignments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            chapter_id UUID NOT NULL UNIQUE REFERENCES chapters(id) ON DELETE CASCADE,
            owner_name TEXT NOT NULL,
            assigned_by TEXT NOT NULL,
            note TEXT,
            assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_chapter_worklist_assignments_document_id "
        "ON chapter_worklist_assignments(document_id);"
    )
    op.execute(
        "CREATE INDEX idx_chapter_worklist_assignments_owner_name "
        "ON chapter_worklist_assignments(owner_name);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chapter_worklist_assignments_owner_name;")
    op.execute("DROP INDEX IF EXISTS idx_chapter_worklist_assignments_document_id;")
    op.execute("DROP TABLE IF EXISTS chapter_worklist_assignments;")
