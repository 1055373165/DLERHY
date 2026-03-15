"""Initial schema bootstrap.

Revision ID: 20260313_0001
Revises:
Create Date: 2026-03-13 01:00:00
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260313_0001"
down_revision = None
branch_labels = None
depends_on = None


INITIAL_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type TEXT NOT NULL CHECK (source_type IN ('epub', 'pdf_text', 'pdf_scan')),
    file_fingerprint TEXT NOT NULL UNIQUE,
    source_path TEXT,
    title TEXT,
    author TEXT,
    src_lang TEXT NOT NULL DEFAULT 'en',
    tgt_lang TEXT NOT NULL DEFAULT 'zh',
    status TEXT NOT NULL CHECK (status IN (
        'ingested', 'parsed', 'active', 'partially_exported', 'exported', 'failed'
    )),
    parser_version INTEGER NOT NULL DEFAULT 1,
    segmentation_version INTEGER NOT NULL DEFAULT 1,
    active_book_profile_version INTEGER,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chapters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    title_src TEXT,
    title_tgt TEXT,
    anchor_start TEXT,
    anchor_end TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'ready', 'segmented', 'packet_built', 'translated', 'qa_checked',
        'review_required', 'approved', 'exported', 'failed'
    )),
    summary_version INTEGER,
    risk_level TEXT CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, ordinal)
);

CREATE TABLE blocks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    block_type TEXT NOT NULL CHECK (block_type IN (
        'heading', 'paragraph', 'quote', 'footnote', 'caption', 'code', 'table', 'list_item'
    )),
    source_text TEXT NOT NULL,
    normalized_text TEXT,
    source_anchor TEXT,
    source_span_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    parse_confidence NUMERIC(4,3),
    protected_policy TEXT NOT NULL CHECK (protected_policy IN ('translate', 'protect', 'mixed')),
    status TEXT NOT NULL CHECK (status IN ('active', 'invalidated')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (chapter_id, ordinal)
);

CREATE TABLE sentences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    block_id UUID NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal_in_block INTEGER NOT NULL,
    source_text TEXT NOT NULL,
    normalized_text TEXT,
    source_lang TEXT NOT NULL DEFAULT 'en',
    translatable BOOLEAN NOT NULL DEFAULT TRUE,
    nontranslatable_reason TEXT,
    source_anchor TEXT,
    source_span_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    upstream_confidence NUMERIC(4,3),
    sentence_status TEXT NOT NULL CHECK (sentence_status IN (
        'pending', 'protected', 'translated', 'review_required', 'finalized', 'blocked'
    )),
    active_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (block_id, ordinal_in_block)
);

CREATE TABLE book_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    book_type TEXT NOT NULL CHECK (book_type IN (
        'tech', 'business', 'nonfiction', 'history', 'fiction', 'other'
    )),
    style_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    quote_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    special_content_policy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, version)
);

CREATE TABLE memory_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('global', 'chapter')),
    scope_id UUID,
    snapshot_type TEXT NOT NULL CHECK (snapshot_type IN (
        'chapter_brief', 'termbase', 'entity_registry', 'style_delta', 'issue_memory'
    )),
    version INTEGER NOT NULL,
    content_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'invalidated')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, scope_type, scope_id, snapshot_type, version)
);

CREATE TABLE translation_packets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    block_start_id UUID REFERENCES blocks(id) ON DELETE SET NULL,
    block_end_id UUID REFERENCES blocks(id) ON DELETE SET NULL,
    packet_type TEXT NOT NULL CHECK (packet_type IN ('translate', 'retranslate', 'review')),
    book_profile_version INTEGER NOT NULL,
    chapter_brief_version INTEGER,
    termbase_version INTEGER,
    entity_snapshot_version INTEGER,
    style_snapshot_version INTEGER,
    packet_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk_score NUMERIC(4,3),
    status TEXT NOT NULL CHECK (status IN (
        'built', 'running', 'translated', 'invalidated', 'failed'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE packet_sentence_map (
    packet_id UUID NOT NULL REFERENCES translation_packets(id) ON DELETE CASCADE,
    sentence_id UUID NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('current', 'prev_context', 'next_context', 'lookback')),
    PRIMARY KEY (packet_id, sentence_id)
);

CREATE TABLE translation_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    packet_id UUID NOT NULL REFERENCES translation_packets(id) ON DELETE CASCADE,
    model_name TEXT NOT NULL,
    model_config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    prompt_version TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    output_json JSONB,
    token_in INTEGER,
    token_out INTEGER,
    cost_usd NUMERIC(12,6),
    latency_ms INTEGER,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (packet_id, attempt)
);

CREATE TABLE target_segments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    translation_run_id UUID NOT NULL REFERENCES translation_runs(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    text_zh TEXT NOT NULL,
    segment_type TEXT NOT NULL CHECK (segment_type IN (
        'sentence', 'merged_sentence', 'heading', 'footnote', 'caption', 'protected'
    )),
    confidence NUMERIC(4,3),
    final_status TEXT NOT NULL CHECK (final_status IN (
        'draft', 'review_required', 'finalized', 'superseded'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (translation_run_id, ordinal)
);

CREATE TABLE alignment_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sentence_id UUID NOT NULL REFERENCES sentences(id) ON DELETE CASCADE,
    target_segment_id UUID NOT NULL REFERENCES target_segments(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (relation_type IN ('1:1', '1:n', 'n:1', 'protected')),
    confidence NUMERIC(4,3),
    created_by TEXT NOT NULL CHECK (created_by IN ('system', 'model', 'human')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE term_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('global', 'chapter')),
    scope_id UUID,
    source_term TEXT NOT NULL,
    target_term TEXT NOT NULL,
    term_type TEXT NOT NULL CHECK (term_type IN (
        'person', 'org', 'place', 'concept', 'title', 'abbr', 'other'
    )),
    lock_level TEXT NOT NULL CHECK (lock_level IN ('suggested', 'preferred', 'locked')),
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded', 'rejected')),
    evidence_sentence_id UUID REFERENCES sentences(id) ON DELETE SET NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE review_issues (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chapter_id UUID REFERENCES chapters(id) ON DELETE CASCADE,
    block_id UUID REFERENCES blocks(id) ON DELETE CASCADE,
    sentence_id UUID REFERENCES sentences(id) ON DELETE CASCADE,
    packet_id UUID REFERENCES translation_packets(id) ON DELETE SET NULL,
    issue_type TEXT NOT NULL,
    root_cause_layer TEXT NOT NULL CHECK (root_cause_layer IN (
        'ingest', 'parse', 'structure', 'segment', 'memory', 'packet',
        'translation', 'alignment', 'review', 'export', 'ops'
    )),
    severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    blocking BOOLEAN NOT NULL DEFAULT FALSE,
    detector TEXT NOT NULL CHECK (detector IN ('rule', 'model', 'human')),
    confidence NUMERIC(4,3),
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (status IN ('open', 'triaged', 'resolved', 'wontfix')),
    suggested_action TEXT,
    resolution_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE issue_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id UUID NOT NULL REFERENCES review_issues(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL CHECK (action_type IN (
        'EDIT_TARGET_ONLY', 'REALIGN_ONLY', 'RERUN_PACKET',
        'REBUILD_PACKET_THEN_RERUN', 'REBUILD_CHAPTER_BRIEF',
        'UPDATE_TERMBASE_THEN_RERUN_TARGETED',
        'UPDATE_ENTITY_REGISTRY_THEN_RERUN_TARGETED',
        'RESEGMENT_CHAPTER', 'REPARSE_CHAPTER', 'REPARSE_DOCUMENT',
        'REEXPORT_ONLY', 'MANUAL_FINALIZE'
    )),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('sentence', 'packet', 'chapter', 'document')),
    scope_id UUID,
    status TEXT NOT NULL CHECK (status IN ('planned', 'running', 'completed', 'failed', 'cancelled')),
    reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT NOT NULL CHECK (created_by IN ('system', 'human')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE exports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    export_type TEXT NOT NULL CHECK (export_type IN (
        'bilingual_html', 'zh_epub', 'review_package', 'jsonl'
    )),
    input_version_bundle_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    file_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE job_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type TEXT NOT NULL CHECK (job_type IN (
        'ingest', 'parse', 'segment', 'profile', 'brief', 'packet',
        'translate', 'qa', 'rerun', 'export'
    )),
    scope_type TEXT NOT NULL CHECK (scope_type IN ('document', 'chapter', 'packet', 'sentence')),
    scope_id UUID NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    rerun_reason TEXT,
    error_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE artifact_invalidations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_type TEXT NOT NULL CHECK (object_type IN (
        'chapter', 'block', 'sentence', 'packet', 'translation_run',
        'target_segment', 'alignment_edge', 'memory_snapshot', 'export'
    )),
    object_id UUID NOT NULL,
    invalidated_by_type TEXT NOT NULL CHECK (invalidated_by_type IN (
        'issue', 'version_change', 'human', 'system'
    )),
    invalidated_by_id UUID,
    reason_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_type TEXT NOT NULL,
    object_id UUID NOT NULL,
    action TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('system', 'model', 'human')),
    actor_id TEXT,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_chapters_document_id ON chapters(document_id);
CREATE INDEX idx_blocks_chapter_id ON blocks(chapter_id);
CREATE INDEX idx_sentences_chapter_id ON sentences(chapter_id);
CREATE INDEX idx_sentences_document_id ON sentences(document_id);
CREATE INDEX idx_sentences_status ON sentences(sentence_status);
CREATE INDEX idx_memory_snapshots_doc_scope_type ON memory_snapshots(document_id, scope_type, snapshot_type, version DESC);
CREATE INDEX idx_translation_packets_chapter_id ON translation_packets(chapter_id);
CREATE INDEX idx_translation_runs_packet_id ON translation_runs(packet_id);
CREATE INDEX idx_target_segments_chapter_id ON target_segments(chapter_id);
CREATE INDEX idx_alignment_edges_sentence_id ON alignment_edges(sentence_id);
CREATE INDEX idx_alignment_edges_target_segment_id ON alignment_edges(target_segment_id);
CREATE INDEX idx_term_entries_doc_scope ON term_entries(document_id, scope_type, scope_id);
CREATE INDEX idx_term_entries_source_term ON term_entries(source_term);
CREATE INDEX idx_review_issues_chapter_id ON review_issues(chapter_id);
CREATE INDEX idx_review_issues_status ON review_issues(status);
CREATE INDEX idx_review_issues_issue_type ON review_issues(issue_type);
CREATE INDEX idx_issue_actions_issue_id ON issue_actions(issue_id);
CREATE INDEX idx_job_runs_scope ON job_runs(scope_type, scope_id, status);
CREATE INDEX idx_artifact_invalidations_object ON artifact_invalidations(object_type, object_id);
CREATE INDEX idx_audit_events_object ON audit_events(object_type, object_id);
"""


DROP_SQL = """
DROP INDEX IF EXISTS idx_audit_events_object;
DROP INDEX IF EXISTS idx_artifact_invalidations_object;
DROP INDEX IF EXISTS idx_job_runs_scope;
DROP INDEX IF EXISTS idx_issue_actions_issue_id;
DROP INDEX IF EXISTS idx_review_issues_issue_type;
DROP INDEX IF EXISTS idx_review_issues_status;
DROP INDEX IF EXISTS idx_review_issues_chapter_id;
DROP INDEX IF EXISTS idx_term_entries_source_term;
DROP INDEX IF EXISTS idx_term_entries_doc_scope;
DROP INDEX IF EXISTS idx_alignment_edges_target_segment_id;
DROP INDEX IF EXISTS idx_alignment_edges_sentence_id;
DROP INDEX IF EXISTS idx_target_segments_chapter_id;
DROP INDEX IF EXISTS idx_translation_runs_packet_id;
DROP INDEX IF EXISTS idx_translation_packets_chapter_id;
DROP INDEX IF EXISTS idx_memory_snapshots_doc_scope_type;
DROP INDEX IF EXISTS idx_sentences_status;
DROP INDEX IF EXISTS idx_sentences_document_id;
DROP INDEX IF EXISTS idx_sentences_chapter_id;
DROP INDEX IF EXISTS idx_blocks_chapter_id;
DROP INDEX IF EXISTS idx_chapters_document_id;

DROP TABLE IF EXISTS audit_events;
DROP TABLE IF EXISTS artifact_invalidations;
DROP TABLE IF EXISTS job_runs;
DROP TABLE IF EXISTS exports;
DROP TABLE IF EXISTS issue_actions;
DROP TABLE IF EXISTS review_issues;
DROP TABLE IF EXISTS term_entries;
DROP TABLE IF EXISTS alignment_edges;
DROP TABLE IF EXISTS target_segments;
DROP TABLE IF EXISTS translation_runs;
DROP TABLE IF EXISTS packet_sentence_map;
DROP TABLE IF EXISTS translation_packets;
DROP TABLE IF EXISTS memory_snapshots;
DROP TABLE IF EXISTS book_profiles;
DROP TABLE IF EXISTS sentences;
DROP TABLE IF EXISTS blocks;
DROP TABLE IF EXISTS chapters;
DROP TABLE IF EXISTS documents;
"""


def _execute_sql_blob(sql_blob: str) -> None:
    statements = [statement.strip() for statement in sql_blob.strip().split(";") if statement.strip()]
    for statement in statements:
        op.execute(statement.rstrip(";"))


def upgrade() -> None:
    _execute_sql_blob(INITIAL_SCHEMA_SQL)


def downgrade() -> None:
    _execute_sql_blob(DROP_SQL)
