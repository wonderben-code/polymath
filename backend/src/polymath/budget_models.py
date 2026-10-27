"""Conservative monetary ledger. Unknown outcomes retain their full reservation."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class BudgetAccount(Base):
    __tablename__ = "budget_accounts"
    __table_args__ = (
        CheckConstraint("hard_cap_microdollars >= 0", name="ck_budget_accounts_hard_cap"),
        CheckConstraint("daily_cap_microdollars >= 0", name="ck_budget_accounts_daily_cap"),
        CheckConstraint("max_in_flight > 0", name="ck_budget_accounts_concurrency"),
        CheckConstraint(
            "NOT pricing_verified OR pricing_reference IS NOT NULL",
            name="ck_budget_accounts_verified_pricing",
        ),
    )

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    hard_cap_microdollars: Mapped[int] = mapped_column(BigInteger)
    daily_cap_microdollars: Mapped[int] = mapped_column(BigInteger)
    max_in_flight: Mapped[int] = mapped_column(Integer)
    execution_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pricing_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pricing_reference: Mapped[str | None] = mapped_column(String(200))
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    frozen: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    frozen_reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BudgetRunLimit(Base):
    __tablename__ = "budget_run_limits"
    __table_args__ = (
        CheckConstraint("cap_microdollars >= 0", name="ck_budget_run_limits_cap"),
        CheckConstraint("max_calls > 0", name="ck_budget_run_limits_calls"),
    )

    account_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("budget_accounts.id"), primary_key=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id"), primary_key=True
    )
    cap_microdollars: Mapped[int] = mapped_column(BigInteger)
    max_calls: Mapped[int] = mapped_column(Integer)


class BudgetReservation(Base):
    __tablename__ = "budget_reservations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "run_id"],
            ["budget_run_limits.account_id", "budget_run_limits.run_id"],
        ),
        UniqueConstraint("account_id", "call_key", name="uq_budget_reservations_call"),
        CheckConstraint(
            "status IN ('reserved', 'in_flight', 'unknown', 'settled')",
            name="ck_budget_reservations_status",
        ),
        CheckConstraint("estimated_microdollars > 0", name="ck_budget_reservations_estimate"),
        CheckConstraint(
            "(status = 'settled' AND actual_microdollars IS NOT NULL "
            "AND actual_microdollars >= 0 AND settlement_key IS NOT NULL "
            "AND settled_day IS NOT NULL) OR "
            "(status <> 'settled' AND actual_microdollars IS NULL AND settlement_key IS NULL "
            "AND settled_day IS NULL)",
            name="ck_budget_reservations_actual_state",
        ),
        CheckConstraint(
            "(prompt_tokens IS NULL AND completion_tokens IS NULL) OR "
            "(prompt_tokens >= 0 AND completion_tokens >= 0 "
            "AND prompt_tokens IS NOT NULL AND completion_tokens IS NOT NULL)",
            name="ck_budget_reservations_token_usage",
        ),
        Index("ix_budget_reservations_account_day", "account_id", "reserved_day"),
        Index("ix_budget_reservations_account_run", "account_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_id: Mapped[str] = mapped_column(String(120))
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    call_key: Mapped[str] = mapped_column(String(120))
    estimated_microdollars: Mapped[int] = mapped_column(BigInteger)
    actual_microdollars: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default="reserved", server_default="reserved")
    reserved_day: Mapped[date] = mapped_column(Date)
    settled_day: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unknown_reason: Mapped[str | None] = mapped_column(String(64))
    settlement_key: Mapped[str | None] = mapped_column(String(200))
    prompt_tokens: Mapped[int | None] = mapped_column(BigInteger)
    completion_tokens: Mapped[int | None] = mapped_column(BigInteger)
