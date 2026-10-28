"""Immutable requests and generated document versions, separate from findings."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base

ARTIFACT_KINDS_SQL = "'formalise', 'write_summary', 'write_paper'"


class GeneratedArtifact(Base):
    __tablename__ = "generated_artifacts"
    __table_args__ = (
        CheckConstraint(f"kind IN ({ARTIFACT_KINDS_SQL})", name="ck_artifact_kind"),
        CheckConstraint(
            "status IN ('ready', 'insufficient_evidence', 'failed', 'limit_reached')",
            name="ck_artifact_status",
        ),
        CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name="ck_artifact_payload_hash"),
        CheckConstraint("basis_sha256 ~ '^[0-9a-f]{64}$'", name="ck_artifact_basis_hash"),
        CheckConstraint(
            "markdown_sha256 = encode(sha256(convert_to(markdown, 'UTF8')), 'hex')",
            name="ck_artifact_markdown_hash",
        ),
        CheckConstraint(
            "octet_length(markdown) BETWEEN 1 AND 262144", name="ck_artifact_markdown_size"
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_artifact_payload_object"),
        CheckConstraint("jsonb_typeof(basis) = 'object'", name="ck_artifact_basis_object"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_runs.id"), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    basis: Mapped[dict] = mapped_column(JSONB)
    basis_sha256: Mapped[str] = mapped_column(String(64))
    markdown: Mapped[str] = mapped_column(Text)
    markdown_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArtifactRequest(Base):
    __tablename__ = "artifact_requests"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "source_result_id",
            "source_run_id",
            "source_sha256",
            name="uq_artifact_context_request_source",
        ),
        ForeignKeyConstraint(
            ["source_result_id", "source_run_id"],
            ["evidence_results.id", "evidence_results.run_id"],
        ),
        CheckConstraint(f"kind IN ({ARTIFACT_KINDS_SQL})", name="ck_artifact_request_kind"),
        CheckConstraint("run_id <> source_run_id", name="ck_artifact_request_not_self"),
        CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_artifact_request_source_hash"),
        CheckConstraint(
            "(formalisation_id IS NULL AND formalisation_sha256 IS NULL) OR "
            "(kind <> 'formalise' AND formalisation_id IS NOT NULL "
            "AND formalisation_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_artifact_request_formalisation",
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    source_result_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_sha256: Mapped[str] = mapped_column(String(64))
    formalisation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("generated_artifacts.id")
    )
    formalisation_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
