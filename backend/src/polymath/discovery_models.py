"""Immutable discovery intent, scoped observations and committed normal findings."""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class DiscoveryIntent(Base):
    __tablename__ = "research_discovery_intents"
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id"), primary_key=True
    )
    protocol: Mapped[str] = mapped_column(String(64))
    max_stages: Mapped[int] = mapped_column(Integer)
    max_depth: Mapped[int] = mapped_column(Integer)
    config_sha256: Mapped[str] = mapped_column(String(64))


class StageContextRecord(Base):
    __tablename__ = "research_stage_contexts"
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_discovery_intents.run_id"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    context: Mapped[dict] = mapped_column(JSONB)
    context_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StageObservation(Base):
    __tablename__ = "research_stage_observations"
    __table_args__ = (
        UniqueConstraint("run_id", "observation_key", name="uq_stage_observation_key"),
        ForeignKeyConstraint(
            ["run_id", "context_ordinal"],
            ["research_stage_contexts.run_id", "research_stage_contexts.ordinal"],
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_discovery_intents.run_id"))
    context_ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    observation_key: Mapped[str] = mapped_column(String(256))
    model_request_id: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSONB)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StageCommit(Base):
    __tablename__ = "research_stage_commits"
    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_stage_commit_ordinal"),
        UniqueConstraint("run_id", "model_request_id", name="uq_stage_commit_request"),
        UniqueConstraint("run_id", "action_ordinal", name="uq_stage_commit_action"),
        UniqueConstraint("id", "run_id", name="uq_stage_commit_run"),
        UniqueConstraint("id", "result_id", "run_id", name="uq_stage_commit_result_run"),
        ForeignKeyConstraint(
            ["result_id", "run_id"], ["evidence_results.id", "evidence_results.run_id"]
        ),
        ForeignKeyConstraint(
            ["run_id", "context_ordinal"],
            ["research_stage_contexts.run_id", "research_stage_contexts.ordinal"],
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_discovery_intents.run_id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    context_ordinal: Mapped[int] = mapped_column(Integer)
    model_request_id: Mapped[str] = mapped_column(String(128))
    action_ordinal: Mapped[int] = mapped_column(Integer)
    action_sha256: Mapped[str] = mapped_column(String(64))
    packet_sha256: Mapped[str] = mapped_column(String(64))
    result_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    content_sha256: Mapped[str] = mapped_column(String(64))
    depth: Mapped[int] = mapped_column(Integer)
    scientific_sha256: Mapped[str] = mapped_column(String(64))
    assessment: Mapped[dict] = mapped_column(JSONB)
    parent_basis_sha256: Mapped[str] = mapped_column(String(64))
    receipt: Mapped[dict] = mapped_column(JSONB)
    receipt_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StageParent(Base):
    __tablename__ = "research_stage_parents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["parent_result_id", "parent_run_id"],
            ["evidence_results.id", "evidence_results.run_id"],
        ),
        ForeignKeyConstraint(
            ["parent_stage_id", "parent_result_id", "parent_run_id"],
            [
                "research_stage_commits.id",
                "research_stage_commits.result_id",
                "research_stage_commits.run_id",
            ],
        ),
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_stage_commits.id"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_result_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    parent_run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    parent_sha256: Mapped[str] = mapped_column(String(64))
    parent_stage_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(16))


class StageFinalSelection(Base):
    __tablename__ = "research_stage_final_selections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["stage_id", "run_id"], ["research_stage_commits.id", "research_stage_commits.run_id"]
        ),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_discovery_intents.run_id"), primary_key=True
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    model_request_id: Mapped[str] = mapped_column(String(128))
    action_ordinal: Mapped[int] = mapped_column(Integer)
    packet: Mapped[dict] = mapped_column(JSONB)
    packet_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DiscoveryStop(Base):
    __tablename__ = "research_discovery_stops"
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_discovery_intents.run_id"), primary_key=True
    )
    payload: Mapped[dict] = mapped_column(JSONB)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
