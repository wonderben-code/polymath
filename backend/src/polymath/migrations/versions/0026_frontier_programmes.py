"""Experimental Frontier programmes: question → gaps → solve-path steps.

Each attempt is an ordinary owned research run; this schema only records the
programme tree and links attempts to their runs and saved results.
"""

from alembic import op

revision = "0026_frontier_programmes"
down_revision = "0025_explore_collection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE frontier_programmes (
 id UUID PRIMARY KEY,
 owner_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 question TEXT NOT NULL,
 success_criteria TEXT,
 max_attempts INTEGER NOT NULL,
 max_depth INTEGER NOT NULL,
 max_branches INTEGER NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'active',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ck_frontier_question CHECK(char_length(question) BETWEEN 1 AND 2000),
 CONSTRAINT ck_frontier_criteria CHECK
  (success_criteria IS NULL OR char_length(success_criteria) BETWEEN 1 AND 1000),
 CONSTRAINT ck_frontier_attempts CHECK(max_attempts BETWEEN 1 AND 60),
 CONSTRAINT ck_frontier_depth CHECK(max_depth BETWEEN 1 AND 6),
 CONSTRAINT ck_frontier_branches CHECK(max_branches BETWEEN 1 AND 8),
 CONSTRAINT ck_frontier_status CHECK(status IN ('active','paused','completed','stopped'))
);
CREATE INDEX ix_frontier_programmes_owner_created
 ON frontier_programmes(owner_session_hash,created_at,id);
CREATE TABLE frontier_nodes (
 id UUID PRIMARY KEY,
 programme_id UUID NOT NULL REFERENCES frontier_programmes(id),
 parent_id UUID REFERENCES frontier_nodes(id),
 kind VARCHAR(16) NOT NULL,
 statement TEXT NOT NULL,
 ordinal INTEGER NOT NULL,
 depth INTEGER NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'pending',
 pending_action VARCHAR(16),
 failed_attempts INTEGER NOT NULL DEFAULT 0,
 origin_result_id UUID REFERENCES evidence_results(id),
 origin_ref VARCHAR(64),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_frontier_node_origin UNIQUE(programme_id,parent_id,origin_result_id,origin_ref),
 CONSTRAINT ck_frontier_node_kind CHECK(kind IN ('question','gap','step')),
 CONSTRAINT ck_frontier_node_statement CHECK(char_length(statement) BETWEEN 1 AND 4000),
 CONSTRAINT ck_frontier_node_depth CHECK(depth BETWEEN 0 AND 7),
 CONSTRAINT ck_frontier_node_status CHECK(status IN ('pending','running','surveyed','supported',
  'transferred','derived','conjectured','decomposed','open','skipped')),
 CONSTRAINT ck_frontier_node_action CHECK
  (pending_action IS NULL OR pending_action IN ('survey','transfer','decompose','attempt')),
 CONSTRAINT ck_frontier_node_root CHECK((kind = 'question') = (parent_id IS NULL))
);
CREATE INDEX ix_frontier_nodes_programme ON frontier_nodes(programme_id,depth,ordinal,created_at);
CREATE TABLE frontier_attempts (
 id UUID PRIMARY KEY,
 programme_id UUID NOT NULL REFERENCES frontier_programmes(id),
 node_id UUID NOT NULL REFERENCES frontier_nodes(id),
 action VARCHAR(16) NOT NULL,
 run_id UUID NOT NULL UNIQUE REFERENCES research_runs(id),
 result_id UUID REFERENCES evidence_results(id),
 outcome VARCHAR(32),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 absorbed_at TIMESTAMPTZ,
 CONSTRAINT ck_frontier_attempt_action CHECK(action IN ('survey','transfer','decompose','attempt'))
);
CREATE INDEX ix_frontier_attempts_programme ON frontier_attempts(programme_id,created_at);
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE frontier_attempts;
DROP TABLE frontier_nodes;
DROP TABLE frontier_programmes;
""")
