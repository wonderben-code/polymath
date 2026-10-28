"""Immutable artifact scientific context and explicit Ask overview intents."""

from alembic import op

revision = "0024_scientific_context"
down_revision = "0023_public_research"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE research_runs ADD CONSTRAINT uq_scientific_run_kind UNIQUE(id,mode,task_kind);
ALTER TABLE evidence_results ADD CONSTRAINT uq_evidence_result_scientific_ref
UNIQUE(id,run_id,content_sha256);
ALTER TABLE artifact_requests ADD CONSTRAINT uq_artifact_context_request_source
UNIQUE(run_id,source_result_id,source_run_id,source_sha256);
ALTER TABLE assessment_requests ADD CONSTRAINT uq_assessment_overview_request_basis
UNIQUE(run_id,source_result_id,source_sha256,basis_sha256);
CREATE TABLE artifact_scientific_context_bindings (
	run_id UUID NOT NULL,
	protocol VARCHAR(64) NOT NULL,
	source_result_id UUID NOT NULL,
	source_run_id UUID NOT NULL,
	source_sha256 VARCHAR(64) NOT NULL,
	context JSONB NOT NULL,
	canonical_context TEXT NOT NULL,
	context_sha256 VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (run_id),
        FOREIGN KEY(run_id, source_result_id, source_run_id, source_sha256) REFERENCES
artifact_requests (run_id, source_result_id, source_run_id, source_sha256),
        FOREIGN KEY(source_result_id, source_run_id, source_sha256) REFERENCES evidence_results
(id, run_id, content_sha256),
        CONSTRAINT ck_scientific_context_protocol CHECK
(protocol='polymath-artifact-scientific-context-v1' AND (context->>'protocol') IS NOT DISTINCT
FROM protocol),
        CONSTRAINT ck_scientific_context_size CHECK (jsonb_typeof(context)='object' AND
octet_length(canonical_context) BETWEEN 1 AND 262144 AND context=canonical_context::jsonb),
        CONSTRAINT ck_scientific_context_hash CHECK
(context_sha256=encode(sha256(convert_to(canonical_context,'UTF8')),'hex')),
        CONSTRAINT ck_scientific_context_source CHECK
((context#>>'{source_reference,result_id}') IS NOT DISTINCT FROM source_result_id::text AND
(context#>>'{source_reference,run_id}') IS NOT DISTINCT FROM source_run_id::text AND
(context#>>'{source_reference,content_sha256}') IS NOT DISTINCT FROM source_sha256)
);
CREATE TABLE research_ask_overview_intents (
	run_id UUID NOT NULL,
	protocol VARCHAR(64) NOT NULL,
	mode VARCHAR(32) NOT NULL,
	task_kind VARCHAR(32) NOT NULL,
	intent JSONB NOT NULL,
	canonical_intent TEXT NOT NULL,
	intent_sha256 VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (run_id),
	FOREIGN KEY(run_id, mode, task_kind) REFERENCES research_runs (id, mode, task_kind),
	CONSTRAINT ck_scientific_ask_kind CHECK (mode='ask' AND task_kind='research'),
        CONSTRAINT ck_scientific_ask_protocol CHECK (protocol='polymath-ask-claim-overview-v1'
AND intent=jsonb_build_object('protocol',protocol)),
        CONSTRAINT ck_scientific_ask_hash CHECK (octet_length(canonical_intent) BETWEEN 1 AND
256 AND canonical_intent::jsonb=intent AND
intent_sha256=encode(sha256(convert_to(canonical_intent,'UTF8')),'hex'))
);
CREATE TABLE assessment_ask_overview_intents (
	run_id UUID NOT NULL,
	protocol VARCHAR(64) NOT NULL,
	mode VARCHAR(32) NOT NULL,
	task_kind VARCHAR(32) NOT NULL,
	intent JSONB NOT NULL,
	canonical_intent TEXT NOT NULL,
	intent_sha256 VARCHAR(64) NOT NULL,
	source_result_id UUID NOT NULL,
	source_run_id UUID NOT NULL,
	source_sha256 VARCHAR(64) NOT NULL,
	basis_sha256 VARCHAR(64) NOT NULL,
	prior_overview_binding JSONB,
	prior_overview_canonical TEXT,
	prior_overview_sha256 VARCHAR(64),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (run_id),
	FOREIGN KEY(run_id, mode, task_kind) REFERENCES research_runs (id, mode, task_kind),
        FOREIGN KEY(run_id, source_result_id, source_sha256, basis_sha256) REFERENCES
assessment_requests (run_id, source_result_id, source_sha256, basis_sha256),
        FOREIGN KEY(source_result_id, source_run_id, source_sha256) REFERENCES evidence_results
(id, run_id, content_sha256),
	CONSTRAINT ck_scientific_revision_kind CHECK (mode='ask' AND task_kind='reassess_finding'),
        CONSTRAINT ck_scientific_revision_protocol CHECK
(protocol='polymath-ask-reassessment-overview-v1' AND
intent=jsonb_build_object('protocol',protocol)),
        CONSTRAINT ck_scientific_revision_hash CHECK (octet_length(canonical_intent) BETWEEN 1
AND 256 AND canonical_intent::jsonb=intent AND
intent_sha256=encode(sha256(convert_to(canonical_intent,'UTF8')),'hex')),
        CONSTRAINT ck_scientific_revision_prior CHECK ((prior_overview_binding IS NULL AND
prior_overview_canonical IS NULL AND prior_overview_sha256 IS NULL) OR (prior_overview_binding
IS NOT NULL AND prior_overview_canonical IS NOT NULL AND prior_overview_sha256 IS NOT NULL AND
jsonb_typeof(prior_overview_binding)='object' AND octet_length(prior_overview_canonical) BETWEEN
1 AND 4096 AND prior_overview_canonical::jsonb=prior_overview_binding AND
prior_overview_sha256=encode(sha256(convert_to(prior_overview_canonical,'UTF8')),'hex')))
);
""")
    for table in (
        "artifact_scientific_context_bindings",
        "research_ask_overview_intents",
        "assessment_ask_overview_intents",
    ):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence()"
        )


def downgrade():
    op.execute("""DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM artifact_scientific_context_bindings) OR
 EXISTS(SELECT 1 FROM research_ask_overview_intents) OR
 EXISTS(SELECT 1 FROM assessment_ask_overview_intents) THEN
 RAISE EXCEPTION 'Cannot discard scientific context or overview intent history';
 END IF; END; $$;""")
    for table in (
        "assessment_ask_overview_intents",
        "research_ask_overview_intents",
        "artifact_scientific_context_bindings",
    ):
        op.drop_table(table)
    op.drop_constraint(
        "uq_assessment_overview_request_basis", "assessment_requests", type_="unique"
    )
    op.drop_constraint("uq_artifact_context_request_source", "artifact_requests", type_="unique")
    op.drop_constraint("uq_evidence_result_scientific_ref", "evidence_results", type_="unique")
    op.drop_constraint("uq_scientific_run_kind", "research_runs", type_="unique")
