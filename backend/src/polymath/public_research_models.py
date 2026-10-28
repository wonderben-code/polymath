"""Immutable durable sponsorship and the requesting visitor's exact admission."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class PublicResearchDelegation(Base):
    __tablename__ = "public_research_delegations"
    __table_args__ = (
        ForeignKeyConstraint(
            [
                "approval_id",
                "manifest_sha256",
                "sponsor_operator_session_hash",
                "sponsor_browser_session_hash",
                "sponsor_actor_id",
                "valid_until",
            ],
            [
                "meta_research_approvals.id",
                "meta_research_approvals.manifest_sha256",
                "meta_research_approvals.operator_session_hash",
                "meta_research_approvals.browser_session_hash",
                "meta_research_approvals.actor_id",
                "meta_research_approvals.expires_at",
            ],
        ),
        UniqueConstraint(
            "approval_id", "manifest_sha256", "account_id", name="uq_public_delegation_binding"
        ),
        CheckConstraint(
            "authorization_mode = 'durable_owner_delegation'", name="ck_public_delegation_mode"
        ),
        CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$' AND sponsor_policy_sha256 ~ '^[0-9a-f]{64}$' "
            "AND public_policy_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_public_delegation_hashes",
        ),
        Index("ix_public_delegation_account", "account_id", "created_at"),
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(120), ForeignKey("budget_accounts.id"))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    authorization_mode: Mapped[str] = mapped_column(String(40))
    sponsor_operator_session_hash: Mapped[str] = mapped_column(String(64))
    sponsor_browser_session_hash: Mapped[str] = mapped_column(String(64))
    sponsor_actor_id: Mapped[str] = mapped_column(String(100))
    sponsor_policy_sha256: Mapped[str] = mapped_column(String(64))
    public_policy_sha256: Mapped[str] = mapped_column(String(64))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PublicResearchRunAccess(Base):
    __tablename__ = "public_research_run_access"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "approval_id", "manifest_sha256", "account_id", "requester_session_hash"],
            [
                "meta_research_run_bindings.run_id",
                "meta_research_run_bindings.approval_id",
                "meta_research_run_bindings.manifest_sha256",
                "meta_research_run_bindings.account_id",
                "meta_research_run_bindings.browser_session_hash",
            ],
        ),
        ForeignKeyConstraint(
            ["approval_id", "manifest_sha256", "account_id"],
            [
                "public_research_delegations.approval_id",
                "public_research_delegations.manifest_sha256",
                "public_research_delegations.account_id",
            ],
        ),
        CheckConstraint(
            "network_bucket ~ '^[0-9a-f]{64}$' AND public_policy_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_public_run_access_hashes",
        ),
        Index("ix_public_access_account_admitted", "account_id", "admitted_at"),
        Index(
            "ix_public_access_requester_admitted",
            "account_id",
            "requester_session_hash",
            "admitted_at",
        ),
        Index("ix_public_access_network", "account_id", "network_bucket"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    approval_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(120))
    requester_session_hash: Mapped[str] = mapped_column(String(64))
    network_bucket: Mapped[str] = mapped_column(String(64))
    public_policy_sha256: Mapped[str] = mapped_column(String(64))
    admitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
