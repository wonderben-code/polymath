"""Create the initial research run identity and state table."""

import sqlalchemy as sa
from alembic import op

revision = "0001_research_runs"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("mode IN ('explore', 'ask', 'surprise')", name="ck_research_runs_mode"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting_retry', 'cancel_requested', "
            "'cancelled', 'succeeded', 'failed', 'interrupted')",
            name="ck_research_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_runs_status_created_at", "research_runs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_research_runs_status_created_at", table_name="research_runs")
    op.drop_table("research_runs")
