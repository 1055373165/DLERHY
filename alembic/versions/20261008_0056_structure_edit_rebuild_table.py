"""Structure edits gain ``rebuild_table``: a ruled table's rows read again from the PDF page.

Revision ID: 20261008_0056
Revises: 20260930_0055
Create Date: 2026-10-08

Booktabs-style tables (rules only under the header and at the bottom) came out of older parses with
every body row in one row, each cell holding all rows' text. The parser now splits those rows; for a
book parsed before, the repair reads the table again from its page and replaces the block's text
with this edit, which is logged and replayed like the other structure edits.
"""

from alembic import op

revision = "20261008_0056"
down_revision = "20260930_0055"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE structure_edits DROP CONSTRAINT ck_structure_edits_kind;
ALTER TABLE structure_edits ADD CONSTRAINT ck_structure_edits_kind CHECK (kind IN ('relabel_block', 'split_block', 'merge_blocks', 'link_caption', 'override_metadata', 'merge_callout', 'rebuild_table'))
"""

DOWNGRADE_SQL = """
DELETE FROM structure_edits WHERE kind = 'rebuild_table';
ALTER TABLE structure_edits DROP CONSTRAINT ck_structure_edits_kind;
ALTER TABLE structure_edits ADD CONSTRAINT ck_structure_edits_kind CHECK (kind IN ('relabel_block', 'split_block', 'merge_blocks', 'link_caption', 'override_metadata', 'merge_callout'))
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
