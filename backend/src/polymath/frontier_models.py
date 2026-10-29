"""Experimental Frontier programme tree; attempts are ordinary owned research runs."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class FrontierProgramme(Base):
    __tablename__ = "frontier_programmes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    question: Mapped[str] = mapped_column(Text)
    success_criteria: Mapped[str | None] = mapped_column(Text)
    max_attempts: Mapped[int] = mapped_column(Integer)
    max_depth: Mapped[int] = mapped_column(Integer)
    max_branches: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FrontierNode(Base):
    __tablename__ = "frontier_nodes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    programme_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("frontier_programmes.id"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("frontier_nodes.id"))
    kind: Mapped[str] = mapped_column(String(16))
    statement: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer)
    depth: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    pending_action: Mapped[str | None] = mapped_column(String(16))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    origin_result_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("evidence_results.id")
    )
    origin_ref: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FrontierAttempt(Base):
    __tablename__ = "frontier_attempts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    programme_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("frontier_programmes.id"))
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("frontier_nodes.id"))
    action: Mapped[str] = mapped_column(String(16))
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_runs.id"), unique=True)
    result_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("evidence_results.id"))
    outcome: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    absorbed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
