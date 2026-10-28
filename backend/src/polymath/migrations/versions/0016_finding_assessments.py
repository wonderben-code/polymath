"""Immutable cited reassessments and compare-and-swap current finding heads."""

from alembic import op

revision = "0016_finding_assessments"
down_revision = "0015_ingestion_watches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE research_runs DROP CONSTRAINT ck_research_runs_task_kind;
ALTER TABLE research_runs ADD CONSTRAINT ck_research_runs_task_kind CHECK
 (task_kind IN ('research','formalise','write_summary','write_paper','ingest_literature',
                'reassess_finding'));
CREATE TABLE assessment_requests (
 run_id UUID PRIMARY KEY REFERENCES research_runs(id),
 root_result_id UUID NOT NULL REFERENCES evidence_results(id),
 source_result_id UUID NOT NULL REFERENCES evidence_results(id),
 source_sha256 VARCHAR(64) NOT NULL,
 watch_id UUID NOT NULL REFERENCES literature_watches(id),
 expected_head_revision INTEGER NOT NULL,
 basis JSONB NOT NULL,
 basis_sha256 VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ck_assessment_request_revision CHECK(expected_head_revision >= 0),
 CONSTRAINT ck_assessment_source_hash CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_assessment_basis_hash CHECK(basis_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_assessment_basis_size CHECK
  (jsonb_typeof(basis) = 'object' AND octet_length(basis::text) <= 262144)
);
CREATE INDEX ix_assessment_requests_root ON assessment_requests(root_result_id, created_at);
CREATE TABLE assessment_input_sources (
 run_id UUID NOT NULL REFERENCES assessment_requests(run_id),
 ordinal INTEGER NOT NULL,
 version_id UUID NOT NULL,
 retrieval_id UUID NOT NULL,
 role VARCHAR(16) NOT NULL,
 PRIMARY KEY(run_id, ordinal),
 CONSTRAINT uq_assessment_input_retrieval UNIQUE(run_id, retrieval_id),
 FOREIGN KEY(version_id, retrieval_id) REFERENCES evidence_source_retrievals(version_id, id),
 CONSTRAINT ck_assessment_input_role CHECK(role IN ('historical','new')),
 CONSTRAINT ck_assessment_input_ordinal CHECK(ordinal >= 0 AND ordinal < 205)
);
CREATE TABLE finding_assessments (
 id UUID PRIMARY KEY,
 run_id UUID NOT NULL REFERENCES assessment_requests(run_id),
 disposition VARCHAR(16) NOT NULL,
 applied BOOLEAN NOT NULL,
 head_revision INTEGER NOT NULL,
 candidate_result_id UUID REFERENCES evidence_results(id),
 payload JSONB NOT NULL,
 payload_sha256 VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_finding_assessment_run UNIQUE(run_id),
 CONSTRAINT ck_finding_assessment_disposition CHECK
  (disposition IN ('unchanged','revised','unsupported','inconclusive')),
 CONSTRAINT ck_finding_assessment_revision CHECK(head_revision >= 0),
 CONSTRAINT ck_assessment_apply CHECK(NOT applied OR disposition <> 'inconclusive'),
 CONSTRAINT ck_assessment_candidate CHECK
  ((disposition = 'revised' AND candidate_result_id IS NOT NULL) OR
   (disposition <> 'revised' AND candidate_result_id IS NULL)),
 CONSTRAINT ck_assessment_payload_hash CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_assessment_payload_size CHECK
  (jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 262144)
);
CREATE INDEX ix_finding_assessments_created ON finding_assessments(created_at,id);
CREATE TABLE finding_heads (
 root_result_id UUID PRIMARY KEY REFERENCES evidence_results(id),
 current_result_id UUID NOT NULL REFERENCES evidence_results(id),
 state VARCHAR(16) NOT NULL DEFAULT 'supported',
 revision INTEGER NOT NULL DEFAULT 0,
 last_assessment_id UUID REFERENCES finding_assessments(id),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_finding_head_current UNIQUE(current_result_id),
 CONSTRAINT ck_finding_head_state CHECK(state IN ('supported','unsupported')),
 CONSTRAINT ck_finding_head_revision CHECK(revision >= 0)
);
CREATE TRIGGER immutable_assessment_request BEFORE UPDATE OR DELETE ON assessment_requests
 FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE TRIGGER immutable_assessment_input BEFORE UPDATE OR DELETE ON assessment_input_sources
 FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE TRIGGER immutable_finding_assessment BEFORE UPDATE OR DELETE ON finding_assessments
 FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE FUNCTION polymath_finding_head_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' OR NEW.root_result_id IS DISTINCT FROM OLD.root_result_id THEN
  RAISE EXCEPTION 'Finding head identity is immutable' USING ERRCODE = '23514';
 END IF;
 IF NEW.revision <> OLD.revision + 1 THEN
  RAISE EXCEPTION 'Finding head revisions must advance once' USING ERRCODE = '23514';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER finding_head_binding BEFORE UPDATE OR DELETE ON finding_heads
 FOR EACH ROW EXECUTE FUNCTION polymath_finding_head_binding();
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM assessment_requests) OR EXISTS(SELECT 1 FROM finding_assessments)
    OR EXISTS(SELECT 1 FROM finding_heads WHERE revision <> 0 OR state <> 'supported'
              OR last_assessment_id IS NOT NULL OR current_result_id <> root_result_id) OR
       EXISTS(SELECT 1 FROM research_runs WHERE task_kind='reassess_finding') THEN
  RAISE EXCEPTION 'Cannot discard finding assessment history';
 END IF;
END; $$;
""")
    op.drop_table("finding_heads")
    op.drop_table("finding_assessments")
    op.drop_table("assessment_input_sources")
    op.drop_table("assessment_requests")
    op.execute("DROP FUNCTION polymath_finding_head_binding()")
    op.execute("""
ALTER TABLE research_runs DROP CONSTRAINT ck_research_runs_task_kind;
ALTER TABLE research_runs ADD CONSTRAINT ck_research_runs_task_kind CHECK
 (task_kind IN ('research','formalise','write_summary','write_paper','ingest_literature'));
""")
