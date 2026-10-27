"""Append-only provenance for saved findings revisited by a later investigation."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class ResearchMemoryRead(Base):
    __tablename__ = "research_memory_reads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parent_result_id", "parent_run_id"],
            ["evidence_results.id", "evidence_results.run_id"],
        ),
        CheckConstraint("run_id <> parent_run_id", name="ck_memory_read_not_self"),
        CheckConstraint("parent_content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_memory_read_hash"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id"), primary_key=True
    )
    parent_result_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    parent_run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    parent_content_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
