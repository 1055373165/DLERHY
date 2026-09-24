"""Pilot quality reports: one row per run of the pilot's layered QA (L1–L7).

Revision ID: 20260925_0051
Revises: 20260925_0050
Create Date: 2026-09-25

A table like parse_health_reports: each QA run (the pilot's pilot_qa stage, or a re-run by hand)
adds a report and keeps the earlier ones, so a repair round is judged by comparing two. The
findings live in layers_json, out of every load of the run or document row.
"""

from alembic import op

revision = "20260925_0051"
down_revision = "20260925_0050"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE pilot_quality_reports (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    run_id UUID REFERENCES document_runs(id) ON DELETE SET NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL CONSTRAINT ck_pilot_quality_reports_status CHECK (status IN ('passed', 'warnings', 'blocking', 'error')),
    blocking_count INTEGER NOT NULL,
    major_count INTEGER NOT NULL,
    minor_count INTEGER NOT NULL,
    chapter_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    layers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    export_source TEXT NOT NULL,
    error_text TEXT,
    duration_ms INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pilot_quality_reports_document ON pilot_quality_reports (document_id, created_at);
CREATE INDEX idx_pilot_quality_reports_run ON pilot_quality_reports (run_id)
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS pilot_quality_reports
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
