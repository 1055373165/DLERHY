"""Pilot repairs: the repair loop after the pilot's layered QA, its attempts and its defect tickets.

Revision ID: 20260925_0053
Revises: 20260925_0052
Create Date: 2026-09-25

docs/agent-upgrade/08-pilot-quality-gate.md §7. ``pilot_repairs`` has one row per run of the loop,
``pilot_repair_attempts`` one per repair it tried (classification, tool, parameters, metrics before
and after, kept or reverted, cost), ``pilot_defect_tickets`` one per systemic defect it found. The
lessons ledger (Q6) reads the attempts. Questions for the reader are ``approvals`` rows (kind
``pilot_question``), the inbox the UI already shows. Structure edits gain ``override_metadata``: the
repair's reading-copy overrides of one block (hide it, set a figure's crop, give a table its cells).
"""

from alembic import op

revision = "20260925_0053"
down_revision = "20260925_0052"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE pilot_repairs (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    run_id UUID REFERENCES document_runs(id) ON DELETE SET NULL,
    status TEXT NOT NULL CONSTRAINT ck_pilot_repairs_status CHECK (status IN ('running', 'finished', 'error')),
    outcome TEXT,
    stop_reason TEXT,
    rounds INTEGER NOT NULL,
    before_report_id UUID REFERENCES pilot_quality_reports(id) ON DELETE SET NULL,
    after_report_id UUID REFERENCES pilot_quality_reports(id) ON DELETE SET NULL,
    repaired_count INTEGER NOT NULL,
    escalated_count INTEGER NOT NULL,
    remaining_blocking INTEGER NOT NULL,
    remaining_major INTEGER NOT NULL,
    budget_usd NUMERIC(12, 6),
    cost_usd NUMERIC(12, 6) NOT NULL,
    summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_text TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pilot_repairs_document ON pilot_repairs (document_id, created_at);
CREATE INDEX idx_pilot_repairs_run ON pilot_repairs (run_id);
CREATE TABLE pilot_defect_tickets (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    repair_id UUID REFERENCES pilot_repairs(id) ON DELETE SET NULL,
    check_id TEXT NOT NULL,
    layer TEXT NOT NULL,
    issue_class TEXT NOT NULL,
    classification_rule INTEGER NOT NULL,
    component TEXT NOT NULL,
    title TEXT NOT NULL,
    suspected_cause TEXT NOT NULL,
    reproduction TEXT NOT NULL,
    evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    impact_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    workaround_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CONSTRAINT ck_pilot_defect_tickets_status CHECK (status IN ('open', 'fixed', 'wontfix')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pilot_defect_tickets_document ON pilot_defect_tickets (document_id, created_at);
CREATE INDEX idx_pilot_defect_tickets_check ON pilot_defect_tickets (check_id, status);
CREATE TABLE pilot_repair_attempts (
    id UUID PRIMARY KEY,
    repair_id UUID NOT NULL REFERENCES pilot_repairs(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    round INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    check_id TEXT NOT NULL,
    layer TEXT NOT NULL,
    severity TEXT NOT NULL,
    finding_keys_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    classification TEXT NOT NULL CONSTRAINT ck_pilot_repair_attempts_classification CHECK (classification IN ('systemic', 'book_specific')),
    classification_rule INTEGER NOT NULL,
    classification_reason TEXT NOT NULL,
    tool TEXT NOT NULL,
    path TEXT NOT NULL,
    params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    decided_by TEXT NOT NULL,
    packet_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    block_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    before_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    after_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    resolved_keys_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    outcome TEXT NOT NULL CONSTRAINT ck_pilot_repair_attempts_outcome CHECK (outcome IN ('kept', 'reverted', 'failed', 'skipped')),
    reason TEXT,
    ticket_id UUID REFERENCES pilot_defect_tickets(id) ON DELETE SET NULL,
    token_in INTEGER NOT NULL,
    token_out INTEGER NOT NULL,
    cost_usd NUMERIC(12, 6) NOT NULL,
    duration_ms INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pilot_repair_attempts_repair ON pilot_repair_attempts (repair_id, ordinal);
CREATE INDEX idx_pilot_repair_attempts_document ON pilot_repair_attempts (document_id, check_id);
ALTER TABLE structure_edits DROP CONSTRAINT ck_structure_edits_kind;
ALTER TABLE structure_edits ADD CONSTRAINT ck_structure_edits_kind CHECK (kind IN ('relabel_block', 'split_block', 'merge_blocks', 'link_caption', 'override_metadata'))
"""

DOWNGRADE_SQL = """
DELETE FROM structure_edits WHERE kind = 'override_metadata';
ALTER TABLE structure_edits DROP CONSTRAINT ck_structure_edits_kind;
ALTER TABLE structure_edits ADD CONSTRAINT ck_structure_edits_kind CHECK (kind IN ('relabel_block', 'split_block', 'merge_blocks', 'link_caption'));
DROP TABLE IF EXISTS pilot_repair_attempts;
DROP TABLE IF EXISTS pilot_defect_tickets;
DROP TABLE IF EXISTS pilot_repairs
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
