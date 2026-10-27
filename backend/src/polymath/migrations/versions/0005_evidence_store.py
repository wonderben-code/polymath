"""Persist immutable source snapshots, read provenance, and exact-quote findings."""

from alembic import op

revision = "0005_evidence_store"
down_revision = "0004_dispatch_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE evidence_source_versions (
	id UUID NOT NULL,
	source_id VARCHAR(200) NOT NULL,
	content_sha256 VARCHAR(64) NOT NULL,
	content_kind VARCHAR(16) NOT NULL,
	source_text TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_source_version UNIQUE (source_id, content_sha256, content_kind),
	CONSTRAINT ck_source_kind CHECK (content_kind IN ('abstract', 'full_text')),
	CONSTRAINT ck_source_text_size CHECK (length(source_text) BETWEEN 1 AND 20000),
	CONSTRAINT ck_source_content_hash CHECK (
        content_sha256 = encode(sha256(convert_to(source_text, 'UTF8')), 'hex'))
)
""")
    op.execute("""
CREATE TABLE evidence_source_retrievals (
	id UUID NOT NULL,
	version_id UUID NOT NULL,
	metadata_sha256 VARCHAR(64) NOT NULL,
	title VARCHAR(1000) NOT NULL,
	url VARCHAR(2000) NOT NULL,
	retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL,
	extraction_method VARCHAR(100) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_source_retrieval UNIQUE (version_id, metadata_sha256),
	CONSTRAINT uq_source_retrieval_version UNIQUE (version_id, id),
	FOREIGN KEY(version_id) REFERENCES evidence_source_versions (id)
)
""")
    op.execute("""
CREATE TABLE evidence_run_sources (
	run_id UUID NOT NULL,
	retrieval_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (run_id, retrieval_id),
	FOREIGN KEY(run_id) REFERENCES research_runs (id) ON DELETE CASCADE,
	FOREIGN KEY(retrieval_id) REFERENCES evidence_source_retrievals (id)
)
""")
    op.execute("""
CREATE TABLE evidence_results (
	id UUID NOT NULL,
	run_id UUID NOT NULL,
	content_sha256 VARCHAR(64) NOT NULL,
	status VARCHAR(32) NOT NULL,
	payload JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_evidence_result UNIQUE (run_id, content_sha256),
	CONSTRAINT uq_evidence_result_run UNIQUE (id, run_id),
	CONSTRAINT ck_evidence_result_status CHECK (
        status IN ('bridge', 'insufficient_evidence', 'limit_reached', 'failed')),
	FOREIGN KEY(run_id) REFERENCES research_runs (id)
)
""")
    op.execute("""
CREATE TABLE evidence_quotes (
	result_id UUID NOT NULL,
	ordinal INTEGER NOT NULL,
	run_id UUID NOT NULL,
	version_id UUID NOT NULL,
	retrieval_id UUID NOT NULL,
	side VARCHAR(8) NOT NULL,
	quote TEXT NOT NULL,
	supports TEXT NOT NULL,
	start_offset INTEGER NOT NULL,
	end_offset INTEGER NOT NULL,
	PRIMARY KEY (result_id, ordinal),
	FOREIGN KEY(result_id, run_id) REFERENCES evidence_results (id, run_id),
	FOREIGN KEY(run_id, retrieval_id) REFERENCES evidence_run_sources (run_id, retrieval_id),
	FOREIGN KEY(version_id, retrieval_id) REFERENCES evidence_source_retrievals (version_id, id),
	CONSTRAINT ck_evidence_quote_side CHECK (side IN ('left', 'right')),
	CONSTRAINT ck_quote_offsets CHECK (start_offset >= 0 AND end_offset > start_offset)
)
""")
    op.execute("""
CREATE FUNCTION polymath_immutable_evidence() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Evidence artifacts are immutable' USING ERRCODE = '23000';
END;
$$ LANGUAGE plpgsql
""")
    op.execute("""
CREATE TRIGGER immutable_evidence BEFORE UPDATE ON evidence_source_versions
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence()
""")
    op.execute("""
CREATE TRIGGER immutable_evidence BEFORE UPDATE ON evidence_source_retrievals
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence()
""")
    op.execute("""
CREATE TRIGGER immutable_evidence BEFORE UPDATE ON evidence_results
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence()
""")
    op.execute("""
CREATE TRIGGER immutable_evidence BEFORE UPDATE ON evidence_quotes
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence()
""")


def downgrade() -> None:
    op.drop_table("evidence_quotes")
    op.drop_table("evidence_results")
    op.drop_table("evidence_run_sources")
    op.drop_table("evidence_source_retrievals")
    op.drop_table("evidence_source_versions")
    op.execute("DROP FUNCTION polymath_immutable_evidence()")
