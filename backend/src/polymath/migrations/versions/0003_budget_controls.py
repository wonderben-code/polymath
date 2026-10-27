"""Create conservative, disabled-by-default monetary reservation ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0003_budget_controls"
down_revision = "0002_durable_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_accounts",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("hard_cap_microdollars", sa.BigInteger(), nullable=False),
        sa.Column("daily_cap_microdollars", sa.BigInteger(), nullable=False),
        sa.Column("max_in_flight", sa.Integer(), nullable=False),
        sa.Column("execution_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("pricing_verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("pricing_reference", sa.String(200), nullable=True),
        sa.Column("kill_switch", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("frozen", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("frozen_reason", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("hard_cap_microdollars >= 0", name="ck_budget_accounts_hard_cap"),
        sa.CheckConstraint("daily_cap_microdollars >= 0", name="ck_budget_accounts_daily_cap"),
        sa.CheckConstraint("max_in_flight > 0", name="ck_budget_accounts_concurrency"),
        sa.CheckConstraint(
            "NOT pricing_verified OR pricing_reference IS NOT NULL",
            name="ck_budget_accounts_verified_pricing",
        ),
    )
    op.create_table(
        "budget_run_limits",
        sa.Column(
            "account_id", sa.String(120), sa.ForeignKey("budget_accounts.id"), primary_key=True
        ),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("research_runs.id"), primary_key=True),
        sa.Column("cap_microdollars", sa.BigInteger(), nullable=False),
        sa.Column("max_calls", sa.Integer(), nullable=False),
        sa.CheckConstraint("cap_microdollars >= 0", name="ck_budget_run_limits_cap"),
        sa.CheckConstraint("max_calls > 0", name="ck_budget_run_limits_calls"),
    )
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.String(120), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("call_key", sa.String(120), nullable=False),
        sa.Column("estimated_microdollars", sa.BigInteger(), nullable=False),
        sa.Column("actual_microdollars", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(24), server_default="reserved", nullable=False),
        sa.Column("reserved_day", sa.Date(), nullable=False),
        sa.Column("settled_day", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unknown_reason", sa.String(64), nullable=True),
        sa.Column("settlement_key", sa.String(200), nullable=True),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=True),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id", "run_id"],
            ["budget_run_limits.account_id", "budget_run_limits.run_id"],
        ),
        sa.UniqueConstraint("account_id", "call_key", name="uq_budget_reservations_call"),
        sa.CheckConstraint(
            "status IN ('reserved', 'in_flight', 'unknown', 'settled')",
            name="ck_budget_reservations_status",
        ),
        sa.CheckConstraint("estimated_microdollars > 0", name="ck_budget_reservations_estimate"),
        sa.CheckConstraint(
            "(status = 'settled' AND actual_microdollars IS NOT NULL "
            "AND actual_microdollars >= 0 AND settlement_key IS NOT NULL "
            "AND settled_day IS NOT NULL) OR "
            "(status <> 'settled' AND actual_microdollars IS NULL AND settlement_key IS NULL "
            "AND settled_day IS NULL)",
            name="ck_budget_reservations_actual_state",
        ),
        sa.CheckConstraint(
            "(prompt_tokens IS NULL AND completion_tokens IS NULL) OR "
            "(prompt_tokens >= 0 AND completion_tokens >= 0 "
            "AND prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL)",
            name="ck_budget_reservations_token_usage",
        ),
    )
    op.create_index(
        "ix_budget_reservations_account_day", "budget_reservations", ["account_id", "reserved_day"]
    )
    op.create_index(
        "ix_budget_reservations_account_run", "budget_reservations", ["account_id", "run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_budget_reservations_account_run", table_name="budget_reservations")
    op.drop_index("ix_budget_reservations_account_day", table_name="budget_reservations")
    op.drop_table("budget_reservations")
    op.drop_table("budget_run_limits")
    op.drop_table("budget_accounts")
