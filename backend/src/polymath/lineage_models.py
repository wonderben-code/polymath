"""Immutable selections of older bridge versions for a follow-up investigation."""

import uuid

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class ResearchRunParent(Base):
    __tablename__ = "research_run_parents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parent_result_id", "parent_run_id"],
            ["evidence_results.id", "evidence_results.run_id"],
        ),
        UniqueConstraint("run_id", "ordinal", name="uq_research_run_parent_order"),
        CheckConstraint("ordinal BETWEEN 0 AND 3", name="ck_research_run_parent_order"),
        CheckConstraint("run_id <> parent_run_id", name="ck_research_run_parent_not_self"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="ck_research_run_parent_hash"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id"), primary_key=True
    )
    parent_result_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    parent_run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    ordinal: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
