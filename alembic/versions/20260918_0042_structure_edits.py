"""Structure edits: an append-only log of block edits made by agents or people, replayed after reparsing.

Revision ID: 20260918_0042
Revises: 20260918_0041
Create Date: 2026-09-18
"""

from alembic import op

revision = "20260918_0042"
down_revision = "20260918_0041"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE structure_edits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CONSTRAINT ck_structure_edits_kind CHECK (kind IN ('relabel_block', 'merge_blocks', 'link_caption')),
    status TEXT NOT NULL CONSTRAINT ck_structure_edits_status CHECK (status IN ('applied', 'reapplied', 'stale')),
    args_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    blocks_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    replay_of_edit_id UUID REFERENCES structure_edits(id) ON DELETE CASCADE,
    parse_revision_id UUID REFERENCES document_parse_revisions(id) ON DELETE SET NULL,
    turn_id UUID,
    actor_id TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_structure_edits_document ON structure_edits (document_id, created_at)
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS structure_edits
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
