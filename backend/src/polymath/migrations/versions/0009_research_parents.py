"""Bind higher-level research to exact, immutable older bridge versions."""

from alembic import op

revision = "0009_research_parents"
down_revision = "0008_memory_reads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE research_run_parents (
    run_id UUID NOT NULL REFERENCES research_runs (id),
    parent_result_id UUID NOT NULL,
    parent_run_id UUID NOT NULL,
    ordinal INTEGER NOT NULL,
    content_sha256 VARCHAR(64) NOT NULL,
    PRIMARY KEY (run_id, parent_result_id),
    FOREIGN KEY (parent_result_id, parent_run_id) REFERENCES evidence_results (id, run_id),
    CONSTRAINT uq_research_run_parent_order UNIQUE (run_id, ordinal),
    CONSTRAINT ck_research_run_parent_order CHECK (ordinal BETWEEN 0 AND 3),
    CONSTRAINT ck_research_run_parent_not_self CHECK (run_id <> parent_run_id),
    CONSTRAINT ck_research_run_parent_hash CHECK (content_sha256 ~ '^[0-9a-f]{64}$')
)
""")
    op.execute("""
CREATE TRIGGER immutable_research_parents BEFORE UPDATE OR DELETE ON research_run_parents
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence()
""")


def downgrade() -> None:
    # Do not silently discard an investigation's selected immutable premises.
    op.execute("""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM research_run_parents) THEN
        RAISE EXCEPTION 'Cannot discard saved research parent selections';
    END IF;
END;
$$
""")
    op.drop_table("research_run_parents")
