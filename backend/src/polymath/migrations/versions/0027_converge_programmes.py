"""Experimental Converge programmes: many fields, their connections, and deeper ones.

Each step is an ordinary owned research run; this schema only records the
circle of fields, the steps, and links them to their runs and saved results.
"""

from alembic import op

revision = "0027_converge_programmes"
down_revision = "0026_frontier_programmes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE converge_programmes (
 id UUID PRIMARY KEY,
 owner_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 theme TEXT,
 fields JSONB NOT NULL,
 ceiling_microdollars BIGINT NOT NULL,
 max_steps INTEGER NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'active',
 finished_reason VARCHAR(32),
 threads_from TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ck_converge_theme CHECK(theme IS NULL OR char_length(theme) BETWEEN 1 AND 2000),
 CONSTRAINT ck_converge_fields CHECK
  (jsonb_typeof(fields) = 'array' AND jsonb_array_length(fields) BETWEEN 2 AND 240),
 CONSTRAINT ck_converge_ceiling CHECK(ceiling_microdollars BETWEEN 100000 AND 150000000),
 CONSTRAINT ck_converge_steps CHECK(max_steps BETWEEN 1 AND 400),
 CONSTRAINT ck_converge_status CHECK(status IN ('active','paused','completed','stopped'))
);
CREATE INDEX ix_converge_programmes_owner_created
 ON converge_programmes(owner_session_hash,created_at,id);
CREATE TABLE converge_steps (
 id UUID PRIMARY KEY,
 programme_id UUID NOT NULL REFERENCES converge_programmes(id),
 kind VARCHAR(8) NOT NULL,
 level INTEGER NOT NULL,
 run_id UUID NOT NULL UNIQUE REFERENCES research_runs(id),
 premise_result_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
 result_id UUID REFERENCES evidence_results(id),
 outcome VARCHAR(32),
 title TEXT,
 field_left TEXT,
 field_right TEXT,
 field_indices JSONB NOT NULL DEFAULT '[]'::jsonb,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 absorbed_at TIMESTAMPTZ,
 CONSTRAINT ck_converge_step_kind CHECK(kind IN ('thread','meta')),
 CONSTRAINT ck_converge_step_level CHECK(level BETWEEN 1 AND 40),
 CONSTRAINT ck_converge_step_shape CHECK
  ((kind = 'thread' AND level = 1 AND premise_result_ids = '[]'::jsonb) OR
   (kind = 'meta' AND level >= 2 AND jsonb_array_length(premise_result_ids) BETWEEN 2 AND 4)),
 CONSTRAINT ck_converge_step_indices CHECK(jsonb_typeof(field_indices) = 'array')
);
CREATE INDEX ix_converge_steps_programme ON converge_steps(programme_id,created_at);
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE converge_steps;
DROP TABLE converge_programmes;
""")
