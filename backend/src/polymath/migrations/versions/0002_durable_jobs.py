"""Add fenced job leases, checkpoints, and ordered public events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_durable_jobs"
down_revision = "0001_research_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column("lease_token", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("research_runs", sa.Column("lease_owner", sa.String(128), nullable=True))
    op.add_column(
        "research_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "research_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "research_runs",
        sa.Column("checkpoint_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "research_runs",
        sa.Column(
            "checkpoint", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
    )
    op.add_column(
        "research_runs",
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("research_runs", sa.Column("outcome", postgresql.JSONB(), nullable=True))
    op.add_column(
        "research_runs",
        sa.Column("event_sequence", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "research_runs",
        sa.Column(
            "external_request_in_flight",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column("research_runs", sa.Column("external_request_id", sa.String(128), nullable=True))
    op.add_column("research_runs", sa.Column("interruption_reason", sa.String(128), nullable=True))
    op.add_column(
        "research_runs", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint("ck_research_runs_lease_token", "research_runs", "lease_token >= 0")
    op.create_check_constraint(
        "ck_research_runs_checkpoint_version", "research_runs", "checkpoint_version >= 0"
    )
    op.create_check_constraint(
        "ck_research_runs_event_sequence", "research_runs", "event_sequence >= 0"
    )
    op.create_table(
        "research_events",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_key", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "artifact_refs",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "sequence"),
        sa.UniqueConstraint("run_id", "event_key", name="uq_research_events_run_key"),
        sa.CheckConstraint("sequence > 0", name="ck_research_events_sequence"),
    )
    op.create_table(
        "research_external_requests",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("observation", postgresql.JSONB(), nullable=True),
        sa.Column("reconciliation", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "request_id"),
        sa.CheckConstraint(
            "status IN ('prepared', 'observed', 'reconciled')",
            name="ck_research_external_requests_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("research_external_requests")
    op.drop_table("research_events")
    for name in ("event_sequence", "checkpoint_version", "lease_token"):
        op.drop_constraint(f"ck_research_runs_{name}", "research_runs", type_="check")
    for name in (
        "finished_at",
        "interruption_reason",
        "external_request_id",
        "external_request_in_flight",
        "event_sequence",
        "outcome",
        "evidence_refs",
        "checkpoint",
        "checkpoint_version",
        "heartbeat_at",
        "lease_expires_at",
        "lease_owner",
        "lease_token",
    ):
        op.drop_column("research_runs", name)
