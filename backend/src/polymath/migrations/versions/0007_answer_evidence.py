"""Retain mode-specific answer evidence without rewriting historical findings."""

import sqlalchemy as sa
from alembic import op

revision = "0007_answer_evidence"
down_revision = "0006_run_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_evidence_result_status", "evidence_results", type_="check")
    op.create_check_constraint(
        "ck_evidence_result_status",
        "evidence_results",
        "status IN ('bridge', 'answer', 'insufficient_evidence', 'limit_reached', 'failed')",
    )
    op.add_column("evidence_quotes", sa.Column("claim_id", sa.String(100), nullable=True))
    op.drop_constraint("ck_evidence_quote_side", "evidence_quotes", type_="check")
    op.create_check_constraint(
        "ck_evidence_quote_side",
        "evidence_quotes",
        "side IN ('left', 'right', 'answer')",
    )
    op.create_check_constraint(
        "ck_evidence_quote_claim",
        "evidence_quotes",
        "(side = 'answer' AND claim_id IS NOT NULL) OR (side <> 'answer' AND claim_id IS NULL)",
    )


def downgrade() -> None:
    # Immutable evidence is user data. Refuse an incompatible rollback rather
    # than deleting findings or silently weakening their provenance.
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM evidence_results WHERE status = 'answer') "
            "OR EXISTS (SELECT 1 FROM evidence_quotes "
            "WHERE side = 'answer' OR claim_id IS NOT NULL)"
        )
    ):
        raise RuntimeError("Answer evidence exists; an incompatible downgrade would lose data")
    op.drop_constraint("ck_evidence_quote_claim", "evidence_quotes", type_="check")
    op.drop_constraint("ck_evidence_quote_side", "evidence_quotes", type_="check")
    op.create_check_constraint(
        "ck_evidence_quote_side",
        "evidence_quotes",
        "side IN ('left', 'right')",
    )
    op.drop_column("evidence_quotes", "claim_id")
    op.drop_constraint("ck_evidence_result_status", "evidence_results", type_="check")
    op.create_check_constraint(
        "ck_evidence_result_status",
        "evidence_results",
        "status IN ('bridge', 'insufficient_evidence', 'limit_reached', 'failed')",
    )
