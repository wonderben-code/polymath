"""Expiring resource counters; these rows never grant research or funding."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class PublicRateBucket(Base):
    __tablename__ = "public_rate_buckets"
    __table_args__ = (
        CheckConstraint("count >= 0", name="ck_public_rate_count"),
        Index("ix_public_rate_expiry", "expires_at"),
    )

    bucket_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicStreamLease(Base):
    __tablename__ = "public_stream_leases"
    __table_args__ = (
        Index("ix_public_stream_expiry", "expires_at"),
        Index("ix_public_stream_owner", "owner_hash", "expires_at"),
        Index("ix_public_stream_network", "network_bucket", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_hash: Mapped[str] = mapped_column(String(64), ForeignKey("browser_sessions.token_hash"))
    network_bucket: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
