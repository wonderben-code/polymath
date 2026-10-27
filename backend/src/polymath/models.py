"""Initial run identity/state schema. Workers and research artifacts follow later."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class ResearchRun(Base):
    __tablename__ = "research_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('explore', 'ask', 'surprise')", name="ck_research_runs_mode"),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_retry', 'cancel_requested', "
            "'cancelled', 'succeeded', 'failed', 'interrupted')",
            name="ck_research_runs_status",
        ),
        Index("ix_research_runs_status_created_at", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued", server_default="queued")
    goal: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
