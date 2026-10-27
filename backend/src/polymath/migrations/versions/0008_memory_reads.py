"""Record immutable, non-circular saved-finding reads."""

import sqlalchemy as sa
from alembic import op

revision = "0008_memory_reads"
down_revision = "0007_answer_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_memory_reads",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("parent_result_id", sa.Uuid(), nullable=False),
        sa.Column("parent_run_id", sa.Uuid(), nullable=False),
        sa.Column("parent_content_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("run_id", "parent_result_id"),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"]),
        sa.ForeignKeyConstraint(
            ["parent_result_id", "parent_run_id"],
            ["evidence_results.id", "evidence_results.run_id"],
        ),
        sa.CheckConstraint("run_id <> parent_run_id", name="ck_memory_read_not_self"),
        sa.CheckConstraint("parent_content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_memory_read_hash"),
    )
    op.execute("""
CREATE TRIGGER immutable_memory_read BEFORE UPDATE OR DELETE ON research_memory_reads
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence()
""")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM research_memory_reads)")):
        raise RuntimeError(
            "Research memory provenance exists; an incompatible downgrade would lose data"
        )
    op.drop_table("research_memory_reads")
