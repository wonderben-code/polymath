"""Immutable source versions, retrieval provenance, and version-bound findings."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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


class SourceVersion(Base):
    __tablename__ = "evidence_source_versions"
    __table_args__ = (
        UniqueConstraint("source_id", "content_sha256", "content_kind", name="uq_source_version"),
        CheckConstraint("content_kind IN ('abstract', 'full_text')", name="ck_source_kind"),
        CheckConstraint("length(source_text) BETWEEN 1 AND 20000", name="ck_source_text_size"),
        CheckConstraint(
            "content_sha256 = encode(sha256(convert_to(source_text, 'UTF8')), 'hex')",
            name="ck_source_content_hash",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(String(200))
    content_sha256: Mapped[str] = mapped_column(String(64))
    content_kind: Mapped[str] = mapped_column(String(16))
    source_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceRetrieval(Base):
    __tablename__ = "evidence_source_retrievals"
    __table_args__ = (
        UniqueConstraint("version_id", "metadata_sha256", name="uq_source_retrieval"),
        UniqueConstraint("version_id", "id", name="uq_source_retrieval_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("evidence_source_versions.id"))
    metadata_sha256: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(1000))
    url: Mapped[str] = mapped_column(String(2000))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    extraction_method: Mapped[str] = mapped_column(String(100))


class RunEvidenceSource(Base):
    __tablename__ = "evidence_run_sources"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id", ondelete="CASCADE"), primary_key=True
    )
    retrieval_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("evidence_source_retrievals.id"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvidenceResult(Base):
    __tablename__ = "evidence_results"
    __table_args__ = (
        UniqueConstraint("run_id", "content_sha256", name="uq_evidence_result"),
        UniqueConstraint("id", "run_id", name="uq_evidence_result_run"),
        CheckConstraint(
            "status IN ('bridge', 'answer', 'insufficient_evidence', 'limit_reached', 'failed')",
            name="ck_evidence_result_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_runs.id"))
    content_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvidenceQuote(Base):
    __tablename__ = "evidence_quotes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["result_id", "run_id"], ["evidence_results.id", "evidence_results.run_id"]
        ),
        ForeignKeyConstraint(
            ["run_id", "retrieval_id"],
            ["evidence_run_sources.run_id", "evidence_run_sources.retrieval_id"],
        ),
        ForeignKeyConstraint(
            ["version_id", "retrieval_id"],
            ["evidence_source_retrievals.version_id", "evidence_source_retrievals.id"],
        ),
        CheckConstraint("side IN ('left', 'right', 'answer')", name="ck_evidence_quote_side"),
        CheckConstraint(
            "(side = 'answer' AND claim_id IS NOT NULL) OR (side <> 'answer' AND claim_id IS NULL)",
            name="ck_evidence_quote_claim",
        ),
        CheckConstraint("start_offset >= 0 AND end_offset > start_offset", name="ck_quote_offsets"),
    )

    result_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    retrieval_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    side: Mapped[str] = mapped_column(String(8))
    claim_id: Mapped[str | None] = mapped_column(String(100))
    quote: Mapped[str] = mapped_column(Text)
    supports: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
