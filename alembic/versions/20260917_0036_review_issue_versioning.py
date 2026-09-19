"""Review issue versioning: version/reopen/decision columns, review_issue_events, document index.

Revision ID: 20260917_0036
Revises: 20260917_0035
Create Date: 2026-09-17
"""

from alembic import op

revision = "20260917_0036"
down_revision = "20260917_0035"
branch_labels = None
depends_on = None


UPGRADE_SQL = """
ALTER TABLE review_issues ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE review_issues ADD COLUMN reopen_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE review_issues ADD COLUMN last_seen_at TIMESTAMPTZ;
ALTER TABLE review_issues ADD COLUMN decided_by TEXT;
ALTER TABLE review_issues ADD COLUMN decided_at TIMESTAMPTZ;
UPDATE review_issues SET last_seen_at = updated_at WHERE last_seen_at IS NULL;
CREATE INDEX idx_review_issues_document_id ON review_issues (document_id);

CREATE TABLE review_issue_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id UUID NOT NULL REFERENCES review_issues(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'opened', 'updated', 'reopened', 'seen_while_closed', 'resolved', 'triaged', 'wontfix', 'action_replanned'
    )),
    from_status TEXT CHECK (from_status IN ('open', 'triaged', 'resolved', 'wontfix')),
    to_status TEXT NOT NULL CHECK (to_status IN ('open', 'triaged', 'resolved', 'wontfix')),
    actor_kind TEXT NOT NULL,
    actor_id TEXT,
    note TEXT,
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_review_issue_events_issue_created ON review_issue_events (issue_id, created_at);
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS review_issue_events;
DROP INDEX IF EXISTS idx_review_issues_document_id;
ALTER TABLE review_issues DROP COLUMN IF EXISTS decided_at;
ALTER TABLE review_issues DROP COLUMN IF EXISTS decided_by;
ALTER TABLE review_issues DROP COLUMN IF EXISTS last_seen_at;
ALTER TABLE review_issues DROP COLUMN IF EXISTS reopen_count;
ALTER TABLE review_issues DROP COLUMN IF EXISTS version;
"""


def _run(sql: str) -> None:
    for statement in sql.strip().split(";\n"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    _run(UPGRADE_SQL)


def downgrade() -> None:
    _run(DOWNGRADE_SQL)
