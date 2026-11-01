"""Solve: key finder and key maker (docs/SOLVE.md).

A programme records a problem and its few stages; each stage is one ordinary
owned research run, so every key, close fit and forged key is a saved finding.
"""

from alembic import op

revision = "0032_solve"
down_revision = "0031_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE solve_programmes (
 id UUID PRIMARY KEY,
 owner_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 problem TEXT NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'active',
 outcome VARCHAR(16),
 frontier_programme_id UUID REFERENCES frontier_programmes(id),
 frontier_node_id UUID REFERENCES frontier_nodes(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ck_solve_problem CHECK(char_length(problem) BETWEEN 1 AND 2000),
 CONSTRAINT ck_solve_status CHECK(status IN ('active','paused','completed','stopped')),
 CONSTRAINT ck_solve_outcome CHECK
  (outcome IS NULL OR outcome IN ('found','forged','close','none')),
 CONSTRAINT ck_solve_frontier CHECK
  ((frontier_node_id IS NULL) OR (frontier_programme_id IS NOT NULL))
);
CREATE INDEX ix_solve_programmes_owner_created
 ON solve_programmes(owner_session_hash,created_at,id);
CREATE TABLE solve_steps (
 id UUID PRIMARY KEY,
 programme_id UUID NOT NULL REFERENCES solve_programmes(id),
 stage VARCHAR(8) NOT NULL,
 ordinal INTEGER NOT NULL,
 target TEXT NOT NULL,
 premise_result_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
 status VARCHAR(16) NOT NULL DEFAULT 'pending',
 run_id UUID REFERENCES research_runs(id),
 result_id UUID REFERENCES evidence_results(id),
 outcome VARCHAR(24),
 failed_attempts INTEGER NOT NULL DEFAULT 0,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_solve_step_run UNIQUE(run_id),
 CONSTRAINT uq_solve_step_ordinal UNIQUE(programme_id,ordinal),
 CONSTRAINT ck_solve_step_stage CHECK(stage IN ('find','fit','forge')),
 CONSTRAINT ck_solve_step_target CHECK(char_length(target) BETWEEN 1 AND 4000),
 CONSTRAINT ck_solve_step_status CHECK(status IN ('pending','running','done','failed')),
 CONSTRAINT ck_solve_step_premises CHECK
  (jsonb_typeof(premise_result_ids) = 'array' AND jsonb_array_length(premise_result_ids) <= 4)
);
CREATE INDEX ix_solve_steps_programme ON solve_steps(programme_id,ordinal);
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE solve_steps;
DROP TABLE solve_programmes;
""")
