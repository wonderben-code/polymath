"""Bind external requests to a unique budget reservation and worker fence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_dispatch_binding"
down_revision = "0003_budget_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_external_requests", sa.Column("reservation_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "research_external_requests",
        sa.Column("dispatch_lease_token", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "research_external_requests", sa.Column("request_hash", sa.String(64), nullable=True)
    )
    op.add_column(
        "research_external_requests", sa.Column("late_observation", JSONB(), nullable=True)
    )
    op.add_column("research_external_requests", sa.Column("late_usage", JSONB(), nullable=True))
    op.create_foreign_key(
        "fk_research_external_requests_reservation",
        "research_external_requests",
        "budget_reservations",
        ["reservation_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_research_external_requests_reservation",
        "research_external_requests",
        ["reservation_id"],
    )
    op.create_check_constraint(
        "ck_research_external_requests_dispatch_binding",
        "research_external_requests",
        "(reservation_id IS NULL AND dispatch_lease_token IS NULL AND request_hash IS NULL) OR "
        "(reservation_id IS NOT NULL AND dispatch_lease_token IS NOT NULL "
        "AND dispatch_lease_token >= 0 AND request_hash IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_column("research_external_requests", "late_usage")
    op.drop_column("research_external_requests", "late_observation")
    op.drop_constraint(
        "ck_research_external_requests_dispatch_binding",
        "research_external_requests",
        type_="check",
    )
    op.drop_constraint(
        "uq_research_external_requests_reservation", "research_external_requests", type_="unique"
    )
    op.drop_constraint(
        "fk_research_external_requests_reservation",
        "research_external_requests",
        type_="foreignkey",
    )
    op.drop_column("research_external_requests", "request_hash")
    op.drop_column("research_external_requests", "dispatch_lease_token")
    op.drop_column("research_external_requests", "reservation_id")
