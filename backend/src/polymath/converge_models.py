"""Experimental Converge programmes; every step is an ordinary owned research run."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class ConvergeProgramme(Base):
    __tablename__ = "converge_programmes"
    __table_args__ = (
        Index("ix_converge_programmes_owner_created", "owner_session_hash", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    theme: Mapped[str | None] = mapped_column(Text)
    fields: Mapped[list] = mapped_column(JSONB)
    ceiling_microdollars: Mapped[int] = mapped_column(BigInteger)
    max_steps: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    finished_reason: Mapped[str | None] = mapped_column(String(32))
    threads_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConvergeStep(Base):
    __tablename__ = "converge_steps"
    __table_args__ = (Index("ix_converge_steps_programme", "programme_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    programme_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("converge_programmes.id"))
    kind: Mapped[str] = mapped_column(String(8))
    level: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_runs.id"), unique=True)
    premise_result_ids: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    result_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("evidence_results.id"))
    outcome: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(Text)
    field_left: Mapped[str | None] = mapped_column(Text)
    field_right: Mapped[str | None] = mapped_column(Text)
    field_indices: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    absorbed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
