"""Immutable preparation proposals and privileged budget administration audit."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class MetaReadinessProposalRecord(Base):
    __tablename__ = "meta_readiness_proposals"
    __table_args__ = (
        UniqueConstraint("account_id", "proposal_sha256", name="uq_readiness_account_proposal"),
        CheckConstraint("proposal_sha256 ~ '^[0-9a-f]{64}$'", name="ck_readiness_proposal_hash"),
        CheckConstraint("quote_sha256 ~ '^[0-9a-f]{64}$'", name="ck_readiness_quote_hash"),
        CheckConstraint(
            "jsonb_typeof(proposal) = 'object' AND octet_length(proposal::text) <= 262144",
            name="ck_readiness_proposal_size",
        ),
        CheckConstraint(
            "jsonb_typeof(quote) = 'object' AND octet_length(quote::text) <= 262144",
            name="ck_readiness_quote_size",
        ),
        CheckConstraint("native_status = 'not_run'", name="ck_readiness_native_not_run"),
        CheckConstraint("NOT execution_available", name="ck_readiness_no_execution"),
        Index("ix_readiness_proposals_account_created", "account_id", "created_at", "id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_id: Mapped[str] = mapped_column(String(120), ForeignKey("budget_accounts.id"))
    proposal_sha256: Mapped[str] = mapped_column(String(64))
    proposal: Mapped[dict] = mapped_column(JSONB)
    quote: Mapped[dict] = mapped_column(JSONB)
    quote_sha256: Mapped[str] = mapped_column(String(64))
    operator_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("operator_sessions.token_hash")
    )
    browser_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    actor_id: Mapped[str] = mapped_column(String(100))
    native_status: Mapped[str] = mapped_column(
        String(16), default="not_run", server_default="not_run"
    )
    execution_available: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MetaReadinessAudit(Base):
    __tablename__ = "meta_readiness_audits"
    __table_args__ = (
        CheckConstraint(
            "action IN ('proposal_prepared','safe_disabled')", name="ck_readiness_audit_action"
        ),
        CheckConstraint("detail_sha256 ~ '^[0-9a-f]{64}$'", name="ck_readiness_audit_hash"),
        CheckConstraint(
            "jsonb_typeof(detail) = 'object' AND octet_length(detail::text) <= 16384",
            name="ck_readiness_audit_size",
        ),
        Index("ix_readiness_audits_account_created", "account_id", "created_at", "id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_id: Mapped[str] = mapped_column(String(120), ForeignKey("budget_accounts.id"))
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("meta_readiness_proposals.id")
    )
    action: Mapped[str] = mapped_column(String(32))
    operator_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("operator_sessions.token_hash")
    )
    browser_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    actor_id: Mapped[str] = mapped_column(String(100))
    detail: Mapped[dict] = mapped_column(JSONB)
    detail_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
