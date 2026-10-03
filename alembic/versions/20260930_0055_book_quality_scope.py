"""Quality reports and repairs gain a scope: the pilot's chapters, or the whole book.

Revision ID: 20260930_0055
Revises: 20260929_0054
Create Date: 2026-09-30

The whole-book run ends with the same layered QA and repair loop as the pilot (``book_qa`` and
``book_repair``, services/book_quality.py), on every chapter. Their rows live in the pilot's tables;
``scope`` says which they are, so the book page shows the pilot's report and the book's apart.
"""

from alembic import op

revision = "20260930_0055"
down_revision = "20260929_0054"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
ALTER TABLE pilot_quality_reports ADD COLUMN scope TEXT NOT NULL DEFAULT 'pilot';
ALTER TABLE pilot_quality_reports ADD CONSTRAINT ck_pilot_quality_reports_scope CHECK (scope IN ('pilot', 'book'));
ALTER TABLE pilot_repairs ADD COLUMN scope TEXT NOT NULL DEFAULT 'pilot';
ALTER TABLE pilot_repairs ADD CONSTRAINT ck_pilot_repairs_scope CHECK (scope IN ('pilot', 'book'))
"""

DOWNGRADE_SQL = """
ALTER TABLE pilot_repairs DROP CONSTRAINT ck_pilot_repairs_scope;
ALTER TABLE pilot_repairs DROP COLUMN scope;
ALTER TABLE pilot_quality_reports DROP CONSTRAINT ck_pilot_quality_reports_scope;
ALTER TABLE pilot_quality_reports DROP COLUMN scope
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
