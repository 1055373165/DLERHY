"""Agent harness ledger: agent_turns, agent_items, approvals, decisions; work_items.stage += agent.

Revision ID: 20260917_0035
Revises: 20260917_0034
Create Date: 2026-09-17
"""

from alembic import op

revision = "20260917_0035"
down_revision = "20260917_0034"
branch_labels = None
depends_on = None


UPGRADE_SQL = """
CREATE TABLE agent_turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    run_id UUID REFERENCES document_runs(id) ON DELETE SET NULL,
    work_item_id UUID REFERENCES work_items(id) ON DELETE SET NULL,
    agent_kind TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id UUID,
    status TEXT NOT NULL CHECK (status IN ('running', 'awaiting_approval', 'paused', 'succeeded', 'failed', 'cancelled')),
    model_name TEXT,
    harness_version TEXT NOT NULL DEFAULT 'h1',
    skills_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    budget_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    usage_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    stop_reason TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_agent_turns_document_kind ON agent_turns (document_id, agent_kind, created_at);
CREATE INDEX idx_agent_turns_run ON agent_turns (run_id);

CREATE TABLE agent_items (
    id BIGSERIAL PRIMARY KEY,
    turn_id UUID NOT NULL REFERENCES agent_turns(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('system', 'developer', 'user', 'assistant', 'tool_call', 'tool_result', 'compaction', 'approval_request', 'approval_result')),
    content_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    token_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_agent_items_turn_ordinal UNIQUE (turn_id, ordinal)
);

CREATE TABLE approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    turn_id UUID REFERENCES agent_turns(id) ON DELETE CASCADE,
    tool_call_item_id BIGINT,
    kind TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'auto_approved', 'expired')),
    policy_id TEXT,
    decided_by TEXT,
    decided_at TIMESTAMPTZ,
    decision_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_approvals_document_status ON approvals (document_id, status);

CREATE TABLE decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    scope TEXT NOT NULL CHECK (scope IN ('book', 'chapter')),
    scope_id UUID,
    key TEXT NOT NULL,
    value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    rationale TEXT,
    decided_by TEXT NOT NULL,
    turn_id UUID REFERENCES agent_turns(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_decisions_document_scope_key ON decisions (document_id, scope, key, created_at);

ALTER TABLE work_items DROP CONSTRAINT IF EXISTS work_items_stage_check;
ALTER TABLE work_items ADD CONSTRAINT work_items_stage_check
    CHECK (stage IN ('bootstrap', 'translate', 'review', 'export', 'agent'));
"""

DOWNGRADE_SQL = """
DELETE FROM work_items WHERE stage = 'agent';
ALTER TABLE work_items DROP CONSTRAINT IF EXISTS work_items_stage_check;
ALTER TABLE work_items ADD CONSTRAINT work_items_stage_check
    CHECK (stage IN ('bootstrap', 'translate', 'review', 'export'));
DROP TABLE IF EXISTS decisions;
DROP TABLE IF EXISTS approvals;
DROP TABLE IF EXISTS agent_items;
DROP TABLE IF EXISTS agent_turns;
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
