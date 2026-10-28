"""Pinned reassessment inputs, immutable cited conclusions, and current finding heads."""

import uuid
from datetime import datetime

from sqlalchemy import (
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class AssessmentRequest(Base):
    __tablename__ = "assessment_requests"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "source_result_id",
            "source_sha256",
            "basis_sha256",
            name="uq_assessment_overview_request_basis",
        ),
        CheckConstraint("expected_head_revision >= 0", name="ck_assessment_request_revision"),
        CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_assessment_source_hash"),
        CheckConstraint("basis_sha256 ~ '^[0-9a-f]{64}$'", name="ck_assessment_basis_hash"),
        CheckConstraint(
            "jsonb_typeof(basis) = 'object' AND octet_length(basis::text) <= 262144",
            name="ck_assessment_basis_size",
        ),
        Index("ix_assessment_requests_root", "root_result_id", "created_at"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id"), primary_key=True
    )
    root_result_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("evidence_results.id"))
    source_result_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("evidence_results.id"))
    source_sha256: Mapped[str] = mapped_column(String(64))
    watch_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("literature_watches.id"))
    expected_head_revision: Mapped[int] = mapped_column(Integer)
    basis: Mapped[dict] = mapped_column(JSONB)
    basis_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssessmentInputSource(Base):
    __tablename__ = "assessment_input_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "retrieval_id"],
            ["evidence_source_retrievals.version_id", "evidence_source_retrievals.id"],
        ),
        CheckConstraint("role IN ('historical','new')", name="ck_assessment_input_role"),
        CheckConstraint("ordinal >= 0 AND ordinal < 205", name="ck_assessment_input_ordinal"),
        UniqueConstraint("run_id", "retrieval_id", name="uq_assessment_input_retrieval"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("assessment_requests.run_id"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    retrieval_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    role: Mapped[str] = mapped_column(String(16))


class FindingAssessment(Base):
    __tablename__ = "finding_assessments"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_finding_assessment_run"),
        CheckConstraint(
            "disposition IN ('unchanged','revised','unsupported','inconclusive')",
            name="ck_finding_assessment_disposition",
        ),
        CheckConstraint("head_revision >= 0", name="ck_finding_assessment_revision"),
        CheckConstraint("NOT applied OR disposition <> 'inconclusive'", name="ck_assessment_apply"),
        CheckConstraint(
            "(disposition = 'revised' AND candidate_result_id IS NOT NULL) OR "
            "(disposition <> 'revised' AND candidate_result_id IS NULL)",
            name="ck_assessment_candidate",
        ),
        CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name="ck_assessment_payload_hash"),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 262144",
            name="ck_assessment_payload_size",
        ),
        Index("ix_finding_assessments_created", "created_at", "id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("assessment_requests.run_id"))
    disposition: Mapped[str] = mapped_column(String(16))
    applied: Mapped[bool] = mapped_column(Boolean)
    head_revision: Mapped[int] = mapped_column(Integer)
    candidate_result_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("evidence_results.id")
    )
    payload: Mapped[dict] = mapped_column(JSONB)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FindingHead(Base):
    __tablename__ = "finding_heads"
    __table_args__ = (
        CheckConstraint("state IN ('supported','unsupported')", name="ck_finding_head_state"),
        CheckConstraint("revision >= 0", name="ck_finding_head_revision"),
        UniqueConstraint("current_result_id", name="uq_finding_head_current"),
    )
    root_result_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("evidence_results.id"), primary_key=True
    )
    current_result_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("evidence_results.id"))
    state: Mapped[str] = mapped_column(String(16), default="supported", server_default="supported")
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("finding_assessments.id")
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
