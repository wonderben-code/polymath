"""Durable research state and public events; no external calls live in this layer."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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


class ResearchRun(Base):
    __tablename__ = "research_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('explore', 'ask', 'surprise')", name="ck_research_runs_mode"),
        CheckConstraint(
            "task_kind IN ('research', 'formalise', 'write_summary', 'write_paper', "
            "'ingest_literature', 'reassess_finding', 'verify_meta')",
            name="ck_research_runs_task_kind",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_retry', 'cancel_requested', "
            "'cancelled', 'succeeded', 'failed', 'interrupted')",
            name="ck_research_runs_status",
        ),
        Index("ix_research_runs_status_created_at", "status", "created_at"),
        CheckConstraint("lease_token >= 0", name="ck_research_runs_lease_token"),
        CheckConstraint("checkpoint_version >= 0", name="ck_research_runs_checkpoint_version"),
        CheckConstraint("event_sequence >= 0", name="ck_research_runs_event_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    mode: Mapped[str] = mapped_column(String(32))
    task_kind: Mapped[str] = mapped_column(
        String(32), default="research", server_default="research"
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", server_default="queued")
    goal: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    lease_token: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    checkpoint: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    evidence_refs: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    outcome: Mapped[dict | None] = mapped_column(JSONB)
    event_sequence: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    external_request_in_flight: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    external_request_id: Mapped[str | None] = mapped_column(String(128))
    interruption_reason: Mapped[str | None] = mapped_column(String(128))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchEvent(Base):
    __tablename__ = "research_events"
    __table_args__ = (
        UniqueConstraint("run_id", "event_key", name="uq_research_events_run_key"),
        CheckConstraint("sequence > 0", name="ck_research_events_sequence"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    artifact_refs: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchExternalRequest(Base):
    """Retain each request outcome independently of bounded controller checkpoints."""

    __tablename__ = "research_external_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('prepared', 'observed', 'reconciled')",
            name="ck_research_external_requests_status",
        ),
        CheckConstraint(
            "(reservation_id IS NULL AND dispatch_lease_token IS NULL AND request_hash IS NULL) OR "
            "(reservation_id IS NOT NULL AND dispatch_lease_token IS NOT NULL "
            "AND dispatch_lease_token >= 0 AND request_hash IS NOT NULL)",
            name="ck_research_external_requests_dispatch_binding",
        ),
        UniqueConstraint("reservation_id", name="uq_research_external_requests_reservation"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id", ondelete="CASCADE"), primary_key=True
    )
    request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    observation: Mapped[dict | None] = mapped_column(JSONB)
    reconciliation: Mapped[dict | None] = mapped_column(JSONB)
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("budget_reservations.id")
    )
    dispatch_lease_token: Mapped[int | None] = mapped_column(BigInteger)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    late_observation: Mapped[dict | None] = mapped_column(JSONB)
    late_usage: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
