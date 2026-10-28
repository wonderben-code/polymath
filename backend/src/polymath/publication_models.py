"""Append-only exact-artifact publication previews, approvals, and withdrawals."""

import uuid
from datetime import datetime

from sqlalchemy import (
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


class PublicationManifestRecord(Base):
    __tablename__ = "publication_manifests"
    __table_args__ = (
        UniqueConstraint("owner_session_hash", "manifest_sha256", name="uq_publication_manifest"),
        CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_publication_manifest_hash"),
        CheckConstraint("jsonb_typeof(manifest) = 'object'", name="ck_publication_manifest_object"),
        Index("ix_publication_manifests_owner_created_at", "owner_session_hash", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("generated_artifacts.id"))
    manifest: Mapped[dict] = mapped_column(JSONB)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PublicationApproval(Base):
    __tablename__ = "publication_approvals"
    __table_args__ = (
        CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_publication_approval_hash"),
        CheckConstraint("expires_at > approved_at", name="ck_publication_approval_expiry"),
        CheckConstraint(
            "expires_at <= approved_at + interval '1 hour'",
            name="ck_publication_approval_lifetime",
        ),
        Index("ix_publication_approvals_manifest_approved_at", "manifest_id", "approved_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    manifest_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("publication_manifests.id"))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    operator_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("operator_sessions.token_hash")
    )
    actor_id: Mapped[str] = mapped_column(String(100))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicationRevocation(Base):
    __tablename__ = "publication_revocations"

    approval_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("publication_approvals.id"), primary_key=True
    )
    actor_id: Mapped[str] = mapped_column(String(100))
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
