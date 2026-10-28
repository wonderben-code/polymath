"""Immutable explicit public snapshots and permanent owner revocations."""

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class SharedFinding(Base):
    __tablename__ = "shared_findings"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_shared_finding_public_id"),
        UniqueConstraint("owner_session_hash", "idempotency_key", name="uq_share_owner_request"),
        ForeignKeyConstraint(
            ["source_result_id", "source_run_id"],
            ["evidence_results.id", "evidence_results.run_id"],
        ),
        CheckConstraint("public_id ~ '^[A-Za-z0-9_-]{43}$'", name="ck_share_public_id"),
        CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_share_source_hash"),
        CheckConstraint("projection_sha256 ~ '^[0-9a-f]{64}$'", name="ck_share_projection_hash"),
        CheckConstraint("request_sha256 ~ '^[0-9a-f]{64}$'", name="ck_share_request_hash"),
        CheckConstraint("protocol = 'polymath-public-finding-v1'", name="ck_share_protocol"),
        CheckConstraint(
            "jsonb_typeof(projection) = 'object' AND octet_length(projection::text) <= 300000",
            name="ck_share_projection_size",
        ),
        CheckConstraint("expires_at > created_at", name="ck_share_expiry"),
        Index("ix_shared_findings_owner_created", "owner_session_hash", "created_at", "id"),
        Index("ix_shared_findings_expiry", "expires_at"),
        Index("ix_shared_findings_source", "source_result_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    public_id: Mapped[str] = mapped_column(String(43))
    owner_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    source_result_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    root_result_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("evidence_results.id"))
    source_sha256: Mapped[str] = mapped_column(String(64))
    protocol: Mapped[str] = mapped_column(String(40))
    projection: Mapped[dict] = mapped_column(JSONB)
    projection_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid)
    request_sha256: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SharedFindingRevocation(Base):
    __tablename__ = "shared_finding_revocations"

    share_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("shared_findings.id"), primary_key=True
    )
    owner_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
