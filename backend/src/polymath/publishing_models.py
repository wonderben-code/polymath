"""Publishing approval levels and the items the publishing autopilot handled."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
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


class PublishingPreference(Base):
    __tablename__ = "publishing_preferences"

    actor_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    draft_destination: Mapped[str] = mapped_column(
        String(16), default="sandbox", server_default="sandbox"
    )
    write_kind: Mapped[str] = mapped_column(
        String(16), default="write_paper", server_default="write_paper"
    )
    full_text_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    daily_cap: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    creators: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    license: Mapped[str] = mapped_column(
        String(16), default="cc-by-4.0", server_default="cc-by-4.0"
    )
    keywords: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    production_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PublishingItem(Base):
    __tablename__ = "publishing_items"
    __table_args__ = (
        UniqueConstraint("owner_session_hash", "result_id", name="uq_publishing_item_result"),
        Index("ix_publishing_items_owner_created", "owner_session_hash", "created_at"),
        Index("ix_publishing_items_actor_created", "actor_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[str] = mapped_column(String(100))
    owner_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    result_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("evidence_results.id"))
    level: Mapped[int] = mapped_column(Integer)
    destination: Mapped[str] = mapped_column(String(16))
    write_kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(16), default="pending_write", server_default="pending_write"
    )
    artifact_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("research_runs.id"), unique=True
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("generated_artifacts.id")
    )
    manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("publication_manifests.id")
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("publication_approvals.id")
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("publication_jobs.id"))
    reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
