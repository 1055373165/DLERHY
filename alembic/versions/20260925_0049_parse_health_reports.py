"""Parse health reports: one row per run of the parse health checks on a document.

Revision ID: 20260925_0049
Revises: 20260919_0048
Create Date: 2026-09-25

A table rather than a key in documents.metadata_json: each bootstrap and structure refresh adds a
report and keeps the earlier ones (a repair is judged by comparing two), and the samples stay out
of every load of the document row.
"""

from alembic import op

revision = "20260925_0049"
down_revision = "20260919_0048"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE parse_health_reports (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parse_revision_id UUID REFERENCES document_parse_revisions(id) ON DELETE SET NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL CONSTRAINT ck_parse_health_reports_status CHECK (status IN ('passed', 'warnings', 'blocking')),
    blocking_failed INTEGER NOT NULL,
    major_failed INTEGER NOT NULL,
    minor_failed INTEGER NOT NULL,
    checks_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    duration_ms INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_parse_health_reports_document ON parse_health_reports (document_id, created_at)
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS parse_health_reports
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
