"""Immutable human consent, identity bindings and receipts for one native canary."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class MetaProbeManifestRecord(Base):
    __tablename__ = "meta_probe_manifests"
    __table_args__ = (Index("ix_meta_probe_manifests_account_created", "account_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    readiness_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("meta_readiness_proposals.id"))
    account_id: Mapped[str] = mapped_column(String(120), ForeignKey("budget_accounts.id"))
    manifest_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    manifest: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MetaProbeApproval(Base):
    __tablename__ = "meta_probe_approvals"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("meta_probe_manifests.id"), unique=True
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    operator_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("operator_sessions.token_hash")
    )
    browser_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    actor_id: Mapped[str] = mapped_column(String(100))
    probe_policy_sha256: Mapped[str] = mapped_column(String(64))
    armed_epoch: Mapped[int] = mapped_column(BigInteger)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MetaProbeRequest(Base):
    __tablename__ = "meta_probe_requests"
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id"), primary_key=True
    )
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("meta_probe_manifests.id"), unique=True
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("meta_probe_approvals.id"), unique=True
    )
    account_id: Mapped[str] = mapped_column(String(120), ForeignKey("budget_accounts.id"))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    request_sha256: Mapped[str] = mapped_column(String(64))
    armed_epoch: Mapped[int] = mapped_column(BigInteger)


class MetaProbeRevocation(Base):
    __tablename__ = "meta_probe_revocations"
    approval_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("meta_probe_approvals.id"), primary_key=True
    )
    actor_id: Mapped[str] = mapped_column(String(100))
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MetaNativeIdentityBinding(Base):
    __tablename__ = "meta_native_identity_bindings"
    kind: Mapped[str] = mapped_column(String(24), primary_key=True)
    binding_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(120), ForeignKey("budget_accounts.id"))
    actor_id: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MetaProbeReceipt(Base):
    __tablename__ = "meta_probe_receipts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("meta_probe_requests.run_id"), unique=True
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    observation_sha256: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
