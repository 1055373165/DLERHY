"""Index events by organisation (org budgets); allow split_block structure edits.

Revision ID: 20260918_0043
Revises: 20260918_0042
Create Date: 2026-09-18
"""

from alembic import op

revision = "20260918_0043"
down_revision = "20260918_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS events_org_kind_occurred_idx ON events (org_id, kind, occurred_at)")
    op.execute("ALTER TABLE structure_edits DROP CONSTRAINT ck_structure_edits_kind")
    op.execute(
        "ALTER TABLE structure_edits ADD CONSTRAINT ck_structure_edits_kind "
        "CHECK (kind IN ('relabel_block', 'split_block', 'merge_blocks', 'link_caption'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM structure_edits WHERE kind = 'split_block'")
    op.execute("ALTER TABLE structure_edits DROP CONSTRAINT ck_structure_edits_kind")
    op.execute(
        "ALTER TABLE structure_edits ADD CONSTRAINT ck_structure_edits_kind "
        "CHECK (kind IN ('relabel_block', 'merge_blocks', 'link_caption'))"
    )
    op.execute("DROP INDEX IF EXISTS events_org_kind_occurred_idx")
