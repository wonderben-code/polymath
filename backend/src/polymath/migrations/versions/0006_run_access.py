"""Add hashed browser sessions and explicit research run ownership."""

import sqlalchemy as sa
from alembic import op

revision = "0006_run_access"
down_revision = "0005_evidence_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_sessions",
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_browser_sessions_expires_at", "browser_sessions", ["expires_at"])
    op.create_table(
        "research_run_access",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_hash"], ["browser_sessions.token_hash"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_research_run_access_session", "research_run_access", ["session_hash"])


def downgrade() -> None:
    op.drop_table("research_run_access")
    op.drop_table("browser_sessions")
