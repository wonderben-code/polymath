"""Atomic same-run discovery stages with exact original evidence references."""

from alembic import op

revision = "0022_discovery_ladder"
down_revision = "0021_meta_research"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE research_discovery_intents (
 run_id UUID PRIMARY KEY REFERENCES research_runs(id), protocol VARCHAR(64) NOT NULL,
 max_stages INTEGER NOT NULL, max_depth INTEGER NOT NULL, config_sha256 VARCHAR(64) NOT NULL);
CREATE TABLE research_stage_contexts (
 run_id UUID NOT NULL REFERENCES research_discovery_intents(run_id), ordinal INTEGER NOT NULL,
 context JSONB NOT NULL, context_sha256 VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(run_id,ordinal));
CREATE TABLE research_stage_observations (
 id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES research_discovery_intents(run_id),
 context_ordinal INTEGER NOT NULL, kind VARCHAR(32) NOT NULL, observation_key VARCHAR(256) NOT NULL,
 model_request_id VARCHAR(128) NOT NULL, payload JSONB NOT NULL,
 payload_sha256 VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_stage_observation_key UNIQUE(run_id,observation_key),
 FOREIGN KEY(run_id,context_ordinal) REFERENCES research_stage_contexts(run_id,ordinal));
CREATE TABLE research_stage_commits (
 id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES research_discovery_intents(run_id),
 ordinal INTEGER NOT NULL, context_ordinal INTEGER NOT NULL, model_request_id VARCHAR(128) NOT NULL,
 action_ordinal INTEGER NOT NULL, action_sha256 VARCHAR(64) NOT NULL,
 packet_sha256 VARCHAR(64) NOT NULL, result_id UUID NOT NULL UNIQUE,
 content_sha256 VARCHAR(64) NOT NULL,
 depth INTEGER NOT NULL, scientific_sha256 VARCHAR(64) NOT NULL, assessment JSONB NOT NULL,
 parent_basis_sha256 VARCHAR(64) NOT NULL, receipt JSONB NOT NULL,
 receipt_sha256 VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_stage_commit_ordinal UNIQUE(run_id,ordinal),
 CONSTRAINT uq_stage_commit_request UNIQUE(run_id,model_request_id),
 CONSTRAINT uq_stage_commit_action UNIQUE(run_id,action_ordinal),
 CONSTRAINT uq_stage_commit_run UNIQUE(id,run_id),
 CONSTRAINT uq_stage_commit_result_run UNIQUE(id,result_id,run_id),
 FOREIGN KEY(result_id,run_id) REFERENCES evidence_results(id,run_id),
 FOREIGN KEY(run_id,context_ordinal) REFERENCES research_stage_contexts(run_id,ordinal));
CREATE TABLE research_stage_parents (
 stage_id UUID NOT NULL REFERENCES research_stage_commits(id), ordinal INTEGER NOT NULL,
 parent_result_id UUID NOT NULL, parent_run_id UUID NOT NULL, parent_sha256 VARCHAR(64) NOT NULL,
 parent_stage_id UUID, kind VARCHAR(16) NOT NULL, PRIMARY KEY(stage_id,ordinal),
 FOREIGN KEY(parent_result_id,parent_run_id) REFERENCES evidence_results(id,run_id),
 FOREIGN KEY(parent_stage_id,parent_result_id,parent_run_id)
 REFERENCES research_stage_commits(id,result_id,run_id));
CREATE TABLE research_stage_final_selections (
 run_id UUID PRIMARY KEY REFERENCES research_discovery_intents(run_id),
 stage_id UUID NOT NULL, model_request_id VARCHAR(128) NOT NULL,
 action_ordinal INTEGER NOT NULL, packet JSONB NOT NULL, packet_sha256 VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(stage_id,run_id) REFERENCES research_stage_commits(id,run_id));
CREATE TABLE research_discovery_stops (
 run_id UUID PRIMARY KEY REFERENCES research_discovery_intents(run_id),
 payload JSONB NOT NULL, payload_sha256 VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now());
""")
    for table in (
        "research_discovery_intents",
        "research_stage_contexts",
        "research_stage_observations",
        "research_stage_commits",
        "research_stage_parents",
        "research_stage_final_selections",
        "research_discovery_stops",
    ):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence()"
        )


def downgrade():
    op.execute("""DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM research_discovery_intents) THEN
  RAISE EXCEPTION 'Cannot discard discovery stages or intent history';
 END IF; END; $$;""")
    for table in (
        "research_discovery_stops",
        "research_stage_final_selections",
        "research_stage_parents",
        "research_stage_commits",
        "research_stage_observations",
        "research_stage_contexts",
        "research_discovery_intents",
    ):
        op.drop_table(table)
