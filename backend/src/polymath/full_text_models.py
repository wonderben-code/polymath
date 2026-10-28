"""Immutable arXiv HTML text bodies and run-owned index observations."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class FullTextSnapshot(Base):
    __tablename__ = "arxiv_full_text_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "arxiv_id",
            "arxiv_version",
            "extractor_version",
            "raw_html_sha256",
            "snapshot_sha256",
            name="uq_full_text_snapshot",
        ),
        CheckConstraint("arxiv_version BETWEEN 1 AND 9999", name="ck_full_text_version"),
        CheckConstraint("length(source_text) BETWEEN 1 AND 250000", name="ck_full_text_size"),
        CheckConstraint(
            "snapshot_sha256 = encode(sha256(convert_to(source_text, 'UTF8')), 'hex')",
            name="ck_full_text_hash",
        ),
        CheckConstraint("raw_html_sha256 ~ '^[0-9a-f]{64}$'", name="ck_full_text_raw_hash"),
        CheckConstraint(
            "provenance_sha256 ~ '^[0-9a-f]{64}$'", name="ck_full_text_provenance_hash"
        ),
        CheckConstraint(
            "jsonb_typeof(headings) = 'array' AND jsonb_array_length(headings) <= 80 "
            "AND octet_length(headings::text) <= 131072",
            name="ck_full_text_headings",
        ),
        CheckConstraint(
            "jsonb_typeof(limitations) = 'array' "
            "AND jsonb_array_length(limitations) BETWEEN 1 AND 8 "
            "AND octet_length(limitations::text) <= 65536",
            name="ck_full_text_limitations",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    arxiv_id: Mapped[str] = mapped_column(String(100))
    arxiv_version: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(String(2000))
    source_text: Mapped[str] = mapped_column(Text)
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    raw_html_sha256: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(100))
    headings: Mapped[list] = mapped_column(JSONB)
    headings_truncated: Mapped[bool] = mapped_column(Boolean)
    limitations: Mapped[list] = mapped_column(JSONB)
    provenance_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunFullTextIndex(Base):
    __tablename__ = "research_full_text_indexes"
    __table_args__ = (
        UniqueConstraint("run_id", "snapshot_id", name="uq_run_full_text_snapshot"),
        CheckConstraint("reference_sha256 ~ '^[0-9a-f]{64}$'", name="ck_full_text_reference_hash"),
        CheckConstraint(
            "jsonb_typeof(reference) = 'object' AND octet_length(reference::text) <= 131072",
            name="ck_full_text_reference_size",
        ),
        Index("ix_full_text_indexes_snapshot", "snapshot_id"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id"), primary_key=True
    )
    source_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("arxiv_full_text_snapshots.id"))
    reference: Mapped[dict] = mapped_column(JSONB)
    reference_sha256: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
