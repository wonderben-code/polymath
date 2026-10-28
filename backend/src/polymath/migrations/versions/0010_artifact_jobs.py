"""Add immutable source-bound Formalise and Write jobs and exact document bytes."""

from alembic import op

revision = "0010_artifact_jobs"
down_revision = "0009_research_parents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE research_runs ADD COLUMN task_kind VARCHAR(32) NOT NULL DEFAULT 'research';
ALTER TABLE research_runs ADD CONSTRAINT ck_research_runs_task_kind
CHECK (task_kind IN ('research', 'formalise', 'write_summary', 'write_paper'));

CREATE TABLE generated_artifacts (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL UNIQUE REFERENCES research_runs (id),
    kind VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    payload JSONB NOT NULL,
    payload_sha256 VARCHAR(64) NOT NULL,
    basis JSONB NOT NULL,
    basis_sha256 VARCHAR(64) NOT NULL,
    markdown TEXT NOT NULL,
    markdown_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_artifact_kind CHECK (kind IN ('formalise', 'write_summary', 'write_paper')),
    CONSTRAINT ck_artifact_status CHECK
        (status IN ('ready', 'insufficient_evidence', 'failed', 'limit_reached')),
    CONSTRAINT ck_artifact_payload_hash CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_artifact_basis_hash CHECK (basis_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_artifact_markdown_hash CHECK
        (markdown_sha256 = encode(sha256(convert_to(markdown, 'UTF8')), 'hex')),
    CONSTRAINT ck_artifact_markdown_size CHECK (octet_length(markdown) BETWEEN 1 AND 262144),
    CONSTRAINT ck_artifact_payload_object CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT ck_artifact_basis_object CHECK (jsonb_typeof(basis) = 'object')
);

CREATE TABLE artifact_requests (
    run_id UUID PRIMARY KEY REFERENCES research_runs (id),
    kind VARCHAR(32) NOT NULL,
    source_result_id UUID NOT NULL,
    source_run_id UUID NOT NULL,
    source_sha256 VARCHAR(64) NOT NULL,
    formalisation_id UUID REFERENCES generated_artifacts (id),
    formalisation_sha256 VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (source_result_id, source_run_id) REFERENCES evidence_results (id, run_id),
    CONSTRAINT ck_artifact_request_kind CHECK
        (kind IN ('formalise', 'write_summary', 'write_paper')),
    CONSTRAINT ck_artifact_request_not_self CHECK (run_id <> source_run_id),
    CONSTRAINT ck_artifact_request_source_hash CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_artifact_request_formalisation CHECK (
        (formalisation_id IS NULL AND formalisation_sha256 IS NULL) OR
        (kind <> 'formalise' AND formalisation_id IS NOT NULL AND
         formalisation_sha256 ~ '^[0-9a-f]{64}$'))
);

CREATE TRIGGER immutable_generated_artifact BEFORE UPDATE OR DELETE ON generated_artifacts
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE TRIGGER immutable_artifact_request BEFORE UPDATE OR DELETE ON artifact_requests
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
""")


def downgrade() -> None:
    op.execute("""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM generated_artifacts) OR
       EXISTS (SELECT 1 FROM artifact_requests) OR
       EXISTS (SELECT 1 FROM research_runs WHERE task_kind <> 'research') THEN
        RAISE EXCEPTION 'Cannot discard saved artifact requests or document versions';
    END IF;
END;
$$;
""")
    op.drop_table("artifact_requests")
    op.drop_table("generated_artifacts")
    op.drop_constraint("ck_research_runs_task_kind", "research_runs", type_="check")
    op.drop_column("research_runs", "task_kind")
