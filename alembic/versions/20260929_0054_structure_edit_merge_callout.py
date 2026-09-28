"""Structure edits gain ``merge_callout``: an icon block and the loose lines after it become one callout.

Revision ID: 20260929_0054
Revises: 20260925_0053
Create Date: 2026-09-29

A book parsed before icon-led callouts were recognised (a prompt box: a small icon, then a few
lines in a non-body face) has the icon as an image block and each line as a paragraph. The pilot
repair merges them with this edit; it is logged and replayed like the other structure edits.
"""

from alembic import op

revision = "20260929_0054"
down_revision = "20260925_0053"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE structure_edits DROP CONSTRAINT ck_structure_edits_kind;
ALTER TABLE structure_edits ADD CONSTRAINT ck_structure_edits_kind CHECK (kind IN ('relabel_block', 'split_block', 'merge_blocks', 'link_caption', 'override_metadata', 'merge_callout'))
"""

DOWNGRADE_SQL = """
DELETE FROM structure_edits WHERE kind = 'merge_callout';
ALTER TABLE structure_edits DROP CONSTRAINT ck_structure_edits_kind;
ALTER TABLE structure_edits ADD CONSTRAINT ck_structure_edits_kind CHECK (kind IN ('relabel_block', 'split_block', 'merge_blocks', 'link_caption', 'override_metadata'))
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
