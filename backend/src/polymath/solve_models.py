"""Solve programmes and their stages; mirrors migration 0032 exactly."""

import uuid
from datetime import datetime

from sqlalchemy import (
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class SolveProgramme(Base):
    __tablename__ = "solve_programmes"
    __table_args__ = (
        CheckConstraint("char_length(problem) BETWEEN 1 AND 2000", name="ck_solve_problem"),
        CheckConstraint(
            "status IN ('active','paused','completed','stopped')", name="ck_solve_status"
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('found','forged','close','none')",
            name="ck_solve_outcome",
        ),
        CheckConstraint(
            "(frontier_node_id IS NULL) OR (frontier_programme_id IS NOT NULL)",
            name="ck_solve_frontier",
        ),
        Index("ix_solve_programmes_owner_created", "owner_session_hash", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    problem: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    outcome: Mapped[str | None] = mapped_column(String(16))
    frontier_programme_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("frontier_programmes.id")
    )
    frontier_node_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("frontier_nodes.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SolveStep(Base):
    __tablename__ = "solve_steps"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_solve_step_run"),
        UniqueConstraint("programme_id", "ordinal", name="uq_solve_step_ordinal"),
        CheckConstraint("stage IN ('find','fit','forge')", name="ck_solve_step_stage"),
        CheckConstraint("char_length(target) BETWEEN 1 AND 4000", name="ck_solve_step_target"),
        CheckConstraint(
            "status IN ('pending','running','done','failed')", name="ck_solve_step_status"
        ),
        CheckConstraint(
            "jsonb_typeof(premise_result_ids) = 'array' AND "
            "jsonb_array_length(premise_result_ids) <= 4",
            name="ck_solve_step_premises",
        ),
        Index("ix_solve_steps_programme", "programme_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    programme_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("solve_programmes.id"))
    stage: Mapped[str] = mapped_column(String(8))
    ordinal: Mapped[int] = mapped_column(Integer)
    target: Mapped[str] = mapped_column(Text)
    premise_result_ids: Mapped[list] = mapped_column(JSONB, server_default="[]")
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("research_runs.id"))
    result_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("evidence_results.id"))
    outcome: Mapped[str | None] = mapped_column(String(24))
    failed_attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
