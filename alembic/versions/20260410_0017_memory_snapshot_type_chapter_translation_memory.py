"""Allow chapter_translation_memory in memory_snapshots.snapshot_type.

Revision ID: 20260410_0017
Revises: 20260403_0016
Create Date: 2026-04-10 01:35:00
"""

from alembic import op


revision = "20260410_0017"
down_revision = "20260403_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE memory_snapshots DROP CONSTRAINT IF EXISTS memory_snapshots_snapshot_type_check;")
    op.execute(
        """
        ALTER TABLE memory_snapshots
        ADD CONSTRAINT memory_snapshots_snapshot_type_check
        CHECK (snapshot_type IN (
            'chapter_brief',
            'chapter_translation_memory',
            'termbase',
            'entity_registry',
            'style_delta',
            'issue_memory'
        ));
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE memory_snapshots DROP CONSTRAINT IF EXISTS memory_snapshots_snapshot_type_check;")
    op.execute(
        """
        ALTER TABLE memory_snapshots
        ADD CONSTRAINT memory_snapshots_snapshot_type_check
        CHECK (snapshot_type IN (
            'chapter_brief',
            'termbase',
            'entity_registry',
            'style_delta',
            'issue_memory'
        ));
        """
    )
