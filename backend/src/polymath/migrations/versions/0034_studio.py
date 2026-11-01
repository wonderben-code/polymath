"""Innovation Studio: design new things from mechanisms found across science.

A programme records a brief, its specs and how far afield to look; each stage is
one ordinary owned research run, so every inspiration, design and stress test is
a saved finding (docs/STUDIO.md).
"""

from alembic import op

revision = "0034_studio"
down_revision = "0033_rigour"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE studio_programmes (
 id UUID PRIMARY KEY,
 owner_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 brief TEXT NOT NULL,
 specs JSONB NOT NULL DEFAULT '[]'::jsonb,
 wildness VARCHAR(8) NOT NULL DEFAULT 'far',
 status VARCHAR(16) NOT NULL DEFAULT 'active',
 outcome VARCHAR(16),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ck_studio_brief CHECK(char_length(brief) BETWEEN 1 AND 1000),
 CONSTRAINT ck_studio_specs CHECK
  (jsonb_typeof(specs) = 'array' AND jsonb_array_length(specs) <= 6),
 CONSTRAINT ck_studio_wildness CHECK(wildness IN ('near','far','strange')),
 CONSTRAINT ck_studio_status CHECK(status IN ('active','paused','completed','stopped')),
 CONSTRAINT ck_studio_outcome CHECK
  (outcome IS NULL OR outcome IN ('designed','inspired','none'))
);
CREATE INDEX ix_studio_programmes_owner_created
 ON studio_programmes(owner_session_hash,created_at,id);
CREATE TABLE studio_steps (
 id UUID PRIMARY KEY,
 programme_id UUID NOT NULL REFERENCES studio_programmes(id),
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
 CONSTRAINT uq_studio_step_run UNIQUE(run_id),
 CONSTRAINT uq_studio_step_ordinal UNIQUE(programme_id,ordinal),
 CONSTRAINT ck_studio_step_stage CHECK(stage IN ('brief','inspire','design','engineer','test')),
 CONSTRAINT ck_studio_step_target CHECK(char_length(target) BETWEEN 1 AND 4000),
 CONSTRAINT ck_studio_step_status CHECK(status IN ('pending','running','done','failed')),
 CONSTRAINT ck_studio_step_premises CHECK
  (jsonb_typeof(premise_result_ids) = 'array' AND jsonb_array_length(premise_result_ids) <= 4)
);
CREATE INDEX ix_studio_steps_programme ON studio_steps(programme_id,ordinal);
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE studio_steps;
DROP TABLE studio_programmes;
""")
