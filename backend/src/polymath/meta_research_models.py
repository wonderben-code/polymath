"""Immutable native research consent, owned run policies and known-call receipts."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class MetaResearchManifestRecord(Base):
    __tablename__ = "meta_research_manifests"
    __table_args__ = (
        Index("ix_meta_research_manifests_account_created", "account_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    readiness_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("meta_readiness_proposals.id"))
    canary_receipt_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("meta_probe_receipts.id"))
    account_id: Mapped[str] = mapped_column(String(120), ForeignKey("budget_accounts.id"))
    manifest_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    manifest: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MetaResearchApproval(Base):
    __tablename__ = "meta_research_approvals"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "manifest_sha256",
            "operator_session_hash",
            "browser_session_hash",
            "actor_id",
            "expires_at",
            name="uq_native_public_delegation_authority",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("meta_research_manifests.id"), unique=True
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    operator_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("operator_sessions.token_hash")
    )
    browser_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    actor_id: Mapped[str] = mapped_column(String(100))
    research_policy_sha256: Mapped[str] = mapped_column(String(64))
    armed_epoch: Mapped[int] = mapped_column(BigInteger)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MetaResearchRevocation(Base):
    __tablename__ = "meta_research_revocations"
    approval_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("meta_research_approvals.id"), primary_key=True
    )
    actor_id: Mapped[str] = mapped_column(String(100))
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NativeResearchRunBinding(Base):
    __tablename__ = "meta_research_run_bindings"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "approval_id",
            "manifest_sha256",
            "account_id",
            "browser_session_hash",
            name="uq_native_research_public_binding",
        ),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id"), primary_key=True
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("meta_research_approvals.id"))
    manifest_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("meta_research_manifests.id"))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(120), ForeignKey("budget_accounts.id"))
    browser_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    armed_epoch: Mapped[int] = mapped_column(BigInteger)
    task_kind: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(32))
    input_sha256: Mapped[str] = mapped_column(String(64))
    profile: Mapped[dict] = mapped_column(JSONB)
    profile_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NativeResearchCallReceipt(Base):
    __tablename__ = "meta_research_call_receipts"
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("meta_research_run_bindings.run_id"), primary_key=True
    )
    request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("budget_reservations.id"), unique=True
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    observation_sha256: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NativeResearchCallRequest(Base):
    __tablename__ = "meta_research_call_requests"
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("meta_research_run_bindings.run_id"), primary_key=True
    )
    request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("budget_reservations.id"), unique=True
    )
    request: Mapped[dict] = mapped_column(JSONB)
    request_sha256: Mapped[str] = mapped_column(String(64))
    step_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
