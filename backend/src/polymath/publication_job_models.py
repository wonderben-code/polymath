"""Durable publication execution state and append-only external-operation audit."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class PublicationJob(Base):
    __tablename__ = "publication_jobs"
    __table_args__ = (
        UniqueConstraint("manifest_id", name="uq_publication_job_manifest"),
        CheckConstraint(
            "status IN ('queued','running','interrupted','published','failed','cancelled')",
            name="ck_publication_job_status",
        ),
        CheckConstraint("work_kind IN ('publish','reconcile')", name="ck_publication_job_kind"),
        CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_publication_job_hash"),
        CheckConstraint("lease_token >= 0", name="ck_publication_job_lease_token"),
        CheckConstraint("event_sequence >= 0", name="ck_publication_job_event_sequence"),
        CheckConstraint(
            "stage IN ('queued','create','upload','verify','publish','reconcile','complete')",
            name="ck_publication_job_stage",
        ),
        CheckConstraint(
            "octet_length(outcome::text) <= 524288 AND jsonb_typeof(outcome) = 'object'",
            name="ck_publication_job_outcome_size",
        ),
        Index("ix_publication_jobs_status_created_at", "status", "created_at"),
        Index("ix_publication_jobs_owner_created_at", "owner_session_hash", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    manifest_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("publication_manifests.id"))
    approval_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("publication_approvals.id"))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued", server_default="queued")
    work_kind: Mapped[str] = mapped_column(String(16), default="publish", server_default="publish")
    lease_token: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_deposit_id: Mapped[str | None] = mapped_column(String(20))
    outcome: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    reason: Mapped[str | None] = mapped_column(String(100))
    stage: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    event_sequence: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    frozen: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    publication_reported: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PublicationOperation(Base):
    __tablename__ = "publication_operations"
    __table_args__ = (
        UniqueConstraint("job_id", "stage", name="uq_publication_operation_stage"),
        CheckConstraint(
            "stage IN ('create','upload','publish')", name="ck_publication_operation_stage"
        ),
        CheckConstraint(
            "status IN ('pending','observed','unknown','rejected','reconciled')",
            name="ck_publication_operation_status",
        ),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="ck_publication_operation_hash"),
        CheckConstraint("lease_token >= 1", name="ck_publication_operation_token"),
        CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_publication_operation_manifest_hash"
        ),
        CheckConstraint(
            "observation IS NULL OR (octet_length(observation::text) <= 524288 "
            "AND jsonb_typeof(observation) = 'object')",
            name="ck_publication_operation_observation_size",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("publication_jobs.id"))
    stage: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    request_hash: Mapped[str] = mapped_column(String(64))
    lease_token: Mapped[int] = mapped_column(BigInteger)
    approval_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("publication_approvals.id"))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    observation: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PublicationOperationAudit(Base):
    __tablename__ = "publication_operation_audit"
    __table_args__ = (
        UniqueConstraint(
            "operation_id", "kind", "observation_sha256", name="uq_publication_operation_audit"
        ),
        CheckConstraint(
            "kind IN ('observed','unknown','rejected','reconciled','conflict')",
            name="ck_publication_operation_audit_kind",
        ),
        CheckConstraint(
            "observation_sha256 ~ '^[0-9a-f]{64}$'", name="ck_publication_operation_audit_hash"
        ),
        CheckConstraint(
            "octet_length(observation::text) <= 524288 AND jsonb_typeof(observation) = 'object'",
            name="ck_publication_operation_audit_size",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("publication_operations.id"))
    kind: Mapped[str] = mapped_column(String(16))
    observation_sha256: Mapped[str] = mapped_column(String(64))
    observation: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PublicationJobEvent(Base):
    __tablename__ = "publication_job_events"
    __table_args__ = (
        UniqueConstraint("job_id", "event_key", name="uq_publication_job_event"),
        CheckConstraint("sequence > 0", name="ck_publication_job_event_sequence"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("publication_jobs.id"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PublicationRemoteObservation(Base):
    __tablename__ = "publication_remote_observations"
    __table_args__ = (
        UniqueConstraint("job_id", "observation_sha256", name="uq_publication_remote_observation"),
        CheckConstraint(
            "observation_sha256 ~ '^[0-9a-f]{64}$'", name="ck_publication_remote_observation_hash"
        ),
        CheckConstraint("lease_token >= 1", name="ck_publication_remote_observation_token"),
        CheckConstraint(
            "octet_length(observation::text) <= 524288 AND jsonb_typeof(observation) = 'object'",
            name="ck_publication_remote_observation_size",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("publication_jobs.id"))
    lease_token: Mapped[int] = mapped_column(BigInteger)
    observation_sha256: Mapped[str] = mapped_column(String(64))
    observation: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
