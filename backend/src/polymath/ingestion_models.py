"""Owned literature watches, immutable scheduled ticks, and source-version additions."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class LiteratureWatch(Base):
    __tablename__ = "literature_watches"
    __table_args__ = (
        CheckConstraint("length(query) BETWEEN 1 AND 500", name="ck_watch_query_size"),
        CheckConstraint("length(label) BETWEEN 1 AND 120", name="ck_watch_label_size"),
        CheckConstraint("interval_seconds BETWEEN 21600 AND 604800", name="ck_watch_interval"),
        CheckConstraint(
            "jsonb_typeof(provider_cursors) = 'object' AND "
            "octet_length(provider_cursors::text) <= 16384",
            name="ck_watch_cursors",
        ),
        CheckConstraint(
            "(source_result_id IS NULL AND source_sha256 IS NULL) OR "
            "(source_result_id IS NOT NULL AND source_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_watch_source_binding",
        ),
        UniqueConstraint("active_run_id", name="uq_watch_active_run"),
        Index("ix_literature_watches_due", "enabled", "next_due_at"),
        Index("ix_literature_watches_owner_created", "owner_session_hash", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    label: Mapped[str] = mapped_column(String(120))
    query: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    interval_seconds: Mapped[int] = mapped_column(Integer)
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("research_runs.id"))
    source_result_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("evidence_results.id")
    )
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    provider_cursors: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestionTick(Base):
    __tablename__ = "ingestion_ticks"
    __table_args__ = (
        UniqueConstraint("watch_id", "slot", name="uq_ingestion_watch_slot"),
        UniqueConstraint("watch_id", "run_id", name="uq_ingestion_tick_watch_run"),
        CheckConstraint(
            "jsonb_typeof(initial_cursors) = 'object' AND "
            "octet_length(initial_cursors::text) <= 16384",
            name="ck_ingestion_tick_cursors",
        ),
        Index("ix_ingestion_ticks_watch_created", "watch_id", "created_at"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id"), primary_key=True
    )
    watch_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("literature_watches.id"))
    slot: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    initial_cursors: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WatchSeenSourceVersion(Base):
    __tablename__ = "watch_seen_source_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "retrieval_id"],
            ["evidence_source_retrievals.version_id", "evidence_source_retrievals.id"],
        ),
        ForeignKeyConstraint(
            ["run_id", "retrieval_id"],
            ["evidence_run_sources.run_id", "evidence_run_sources.retrieval_id"],
        ),
        ForeignKeyConstraint(
            ["watch_id", "run_id"], ["ingestion_ticks.watch_id", "ingestion_ticks.run_id"]
        ),
        CheckConstraint("provider IN ('openalex','arxiv')", name="ck_seen_source_provider"),
        Index("ix_watch_sources_first_seen", "watch_id", "first_seen_at", "version_id"),
    )

    watch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("literature_watches.id"), primary_key=True
    )
    version_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    retrieval_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    provider: Mapped[str] = mapped_column(String(16))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IngestionPage(Base):
    __tablename__ = "ingestion_pages"
    __table_args__ = (
        UniqueConstraint("run_id", "provider", "page_sha256", name="uq_ingestion_page_replay"),
        CheckConstraint("provider IN ('openalex','arxiv')", name="ck_ingestion_page_provider"),
        CheckConstraint("status IN ('succeeded','failed')", name="ck_ingestion_page_status"),
        CheckConstraint("page_sha256 ~ '^[0-9a-f]{64}$'", name="ck_ingestion_page_hash"),
        CheckConstraint(
            "jsonb_typeof(observation) = 'object' AND octet_length(observation::text) <= 32768",
            name="ck_ingestion_page_observation",
        ),
        Index("ix_ingestion_pages_run_provider", "run_id", "provider", "created_at"),
        Index(
            "uq_ingestion_page_success",
            "run_id",
            "provider",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("ingestion_ticks.run_id"))
    provider: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    page_sha256: Mapped[str] = mapped_column(String(64))
    observation: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestionProviderGate(Base):
    __tablename__ = "ingestion_provider_gates"
    __table_args__ = (
        CheckConstraint("provider IN ('openalex','arxiv')", name="ck_ingestion_gate_provider"),
        CheckConstraint("sequence >= 0", name="ck_ingestion_gate_sequence"),
    )

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    next_allowed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sequence: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
