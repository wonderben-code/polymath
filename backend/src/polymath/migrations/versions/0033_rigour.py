"""The rigour bar: the evidence standard chosen for an investigation.

A sidecar written in the same transaction as the run. It narrows which sources
the search tools return and never enters the model's instructions, so existing
run inputs, protocols and integrity hashes are unchanged.
"""

from alembic import op

revision = "0033_rigour"
down_revision = "0032_solve"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE research_run_rigour (
 run_id UUID PRIMARY KEY REFERENCES research_runs(id) ON DELETE CASCADE,
 level VARCHAR(16) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ck_research_run_rigour_level
  CHECK(level IN ('frontier','standard','peer_reviewed','established'))
);
""")


def downgrade() -> None:
    op.execute("DROP TABLE research_run_rigour;")
