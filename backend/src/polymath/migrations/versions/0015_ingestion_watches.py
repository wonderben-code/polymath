"""Persist owned literature watches, fixed provider pages, and unassessed additions."""

from alembic import op

revision = "0015_ingestion_watches"
down_revision = "0014_source_bibliography"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE research_runs DROP CONSTRAINT ck_research_runs_task_kind;
ALTER TABLE research_runs ADD CONSTRAINT ck_research_runs_task_kind CHECK
    (task_kind IN ('research','formalise','write_summary','write_paper','ingest_literature'));

CREATE TABLE literature_watches (
    id UUID PRIMARY KEY,
    owner_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions (token_hash),
    label VARCHAR(120) NOT NULL,
    query VARCHAR(500) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    interval_seconds INTEGER NOT NULL,
    next_due_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    last_run_at TIMESTAMPTZ,
    active_run_id UUID REFERENCES research_runs (id),
    source_result_id UUID REFERENCES evidence_results (id),
    source_sha256 VARCHAR(64),
    provider_cursors JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_watch_query_size CHECK (length(query) BETWEEN 1 AND 500),
    CONSTRAINT ck_watch_label_size CHECK (length(label) BETWEEN 1 AND 120),
    CONSTRAINT ck_watch_interval CHECK (interval_seconds BETWEEN 21600 AND 604800),
    CONSTRAINT ck_watch_cursors CHECK
        (jsonb_typeof(provider_cursors) = 'object' AND
         octet_length(provider_cursors::text) <= 16384),
    CONSTRAINT ck_watch_source_binding CHECK
        ((source_result_id IS NULL AND source_sha256 IS NULL) OR
         (source_result_id IS NOT NULL AND source_sha256 ~ '^[0-9a-f]{64}$')),
    CONSTRAINT uq_watch_active_run UNIQUE (active_run_id)
);
CREATE INDEX ix_literature_watches_due ON literature_watches (enabled, next_due_at);
CREATE INDEX ix_literature_watches_owner_created
ON literature_watches (owner_session_hash, created_at);

CREATE TABLE ingestion_ticks (
    run_id UUID PRIMARY KEY REFERENCES research_runs (id),
    watch_id UUID NOT NULL REFERENCES literature_watches (id),
    slot TIMESTAMPTZ NOT NULL,
    initial_cursors JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ingestion_watch_slot UNIQUE (watch_id, slot),
    CONSTRAINT uq_ingestion_tick_watch_run UNIQUE (watch_id, run_id),
    CONSTRAINT ck_ingestion_tick_cursors CHECK
        (jsonb_typeof(initial_cursors) = 'object' AND octet_length(initial_cursors::text) <= 16384)
);
CREATE INDEX ix_ingestion_ticks_watch_created ON ingestion_ticks (watch_id, created_at);

CREATE TABLE watch_seen_source_versions (
    watch_id UUID NOT NULL REFERENCES literature_watches (id),
    version_id UUID NOT NULL,
    retrieval_id UUID NOT NULL,
    run_id UUID NOT NULL,
    provider VARCHAR(16) NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (watch_id, version_id),
    FOREIGN KEY (version_id, retrieval_id) REFERENCES evidence_source_retrievals (version_id, id),
    FOREIGN KEY (run_id, retrieval_id) REFERENCES evidence_run_sources (run_id, retrieval_id),
    FOREIGN KEY (watch_id, run_id) REFERENCES ingestion_ticks (watch_id, run_id),
    CONSTRAINT ck_seen_source_provider CHECK (provider IN ('openalex','arxiv'))
);
CREATE INDEX ix_watch_sources_first_seen
ON watch_seen_source_versions (watch_id, first_seen_at, version_id);

CREATE TABLE ingestion_pages (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES ingestion_ticks (run_id),
    provider VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL,
    page_sha256 VARCHAR(64) NOT NULL,
    observation JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ingestion_page_replay UNIQUE (run_id, provider, page_sha256),
    CONSTRAINT ck_ingestion_page_provider CHECK (provider IN ('openalex','arxiv')),
    CONSTRAINT ck_ingestion_page_status CHECK (status IN ('succeeded','failed')),
    CONSTRAINT ck_ingestion_page_hash CHECK (page_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_ingestion_page_observation CHECK
        (jsonb_typeof(observation) = 'object' AND octet_length(observation::text) <= 32768)
);
CREATE INDEX ix_ingestion_pages_run_provider ON ingestion_pages (run_id, provider, created_at);
CREATE UNIQUE INDEX uq_ingestion_page_success
ON ingestion_pages (run_id, provider) WHERE status = 'succeeded';

CREATE TABLE ingestion_provider_gates (
    provider VARCHAR(16) PRIMARY KEY,
    next_allowed_at TIMESTAMPTZ NOT NULL,
    sequence BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT ck_ingestion_gate_provider CHECK (provider IN ('openalex','arxiv')),
    CONSTRAINT ck_ingestion_gate_sequence CHECK (sequence >= 0)
);
INSERT INTO ingestion_provider_gates (provider, next_allowed_at)
VALUES ('openalex', '1970-01-01T00:00:00Z'), ('arxiv', '1970-01-01T00:00:00Z');

CREATE FUNCTION polymath_watch_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Literature watch history is immutable' USING ERRCODE = '23514';
    END IF;
    IF (NEW.id, NEW.owner_session_hash, NEW.query, NEW.label, NEW.source_result_id,
        NEW.source_sha256, NEW.expires_at, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.owner_session_hash, OLD.query, OLD.label, OLD.source_result_id,
        OLD.source_sha256, OLD.expires_at, OLD.created_at) THEN
        RAISE EXCEPTION 'Literature watch inputs are immutable' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER immutable_watch_binding BEFORE UPDATE OR DELETE ON literature_watches
FOR EACH ROW EXECUTE FUNCTION polymath_watch_binding();
CREATE TRIGGER immutable_ingestion_tick BEFORE UPDATE OR DELETE ON ingestion_ticks
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE TRIGGER immutable_watch_seen_source BEFORE UPDATE OR DELETE ON watch_seen_source_versions
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE TRIGGER immutable_ingestion_page BEFORE UPDATE OR DELETE ON ingestion_pages
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
""")


def downgrade() -> None:
    op.execute("""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM literature_watches) OR
       EXISTS (SELECT 1 FROM ingestion_ticks) OR
       EXISTS (SELECT 1 FROM watch_seen_source_versions) OR
       EXISTS (SELECT 1 FROM ingestion_pages) OR
       EXISTS (SELECT 1 FROM research_runs WHERE task_kind = 'ingest_literature') THEN
        RAISE EXCEPTION 'Cannot discard literature watch or ingestion history';
    END IF;
END;
$$;
""")
    op.drop_table("ingestion_pages")
    op.drop_table("watch_seen_source_versions")
    op.drop_table("ingestion_ticks")
    op.drop_table("literature_watches")
    op.drop_table("ingestion_provider_gates")
    op.execute("DROP FUNCTION polymath_watch_binding()")
    op.execute("""
ALTER TABLE research_runs DROP CONSTRAINT ck_research_runs_task_kind;
ALTER TABLE research_runs ADD CONSTRAINT ck_research_runs_task_kind CHECK
    (task_kind IN ('research','formalise','write_summary','write_paper'));
""")
