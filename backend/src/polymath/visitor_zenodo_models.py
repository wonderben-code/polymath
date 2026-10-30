"""Connect Zenodo for every visitor; mirrors migration 0029 exactly."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


def _owner() -> ForeignKey:
    return ForeignKey("browser_sessions.token_hash", ondelete="CASCADE")


class ZenodoOAuthState(Base):
    __tablename__ = "zenodo_oauth_states"
    __table_args__ = (
        CheckConstraint("state_hash ~ '^[0-9a-f]{64}$'", name="ck_zenodo_state_hash"),
        CheckConstraint(
            "destination IN ('sandbox','production')", name="ck_zenodo_state_destination"
        ),
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '15 minutes'",
            name="ck_zenodo_state_lifetime",
        ),
        Index("ix_zenodo_oauth_states_owner", "owner_session_hash"),
    )

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_session_hash: Mapped[str] = mapped_column(String(64), _owner())
    destination: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ZenodoConnection(Base):
    __tablename__ = "zenodo_connections"
    __table_args__ = (
        UniqueConstraint("owner_session_hash", "destination", name="uq_zenodo_connection"),
        CheckConstraint(
            "destination IN ('sandbox','production')", name="ck_zenodo_connection_destination"
        ),
        CheckConstraint(
            "octet_length(access_ciphertext) BETWEEN 29 AND 8192 AND (refresh_ciphertext IS "
            "NULL OR octet_length(refresh_ciphertext) BETWEEN 29 AND 8192)",
            name="ck_zenodo_connection_sizes",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_session_hash: Mapped[str] = mapped_column(String(64), _owner())
    destination: Mapped[str] = mapped_column(String(16))
    access_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    refresh_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    zenodo_user_id: Mapped[str | None] = mapped_column(String(64))
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VisitorPublication(Base):
    __tablename__ = "visitor_publications"
    __table_args__ = (
        UniqueConstraint("owner_session_hash", "manifest_sha256", name="uq_visitor_publication"),
        CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$'", name="ck_visitor_publication_hash"),
        CheckConstraint(
            "jsonb_typeof(manifest) = 'object'", name="ck_visitor_publication_manifest"
        ),
        CheckConstraint(
            "destination IN ('sandbox','production')", name="ck_visitor_publication_destination"
        ),
        CheckConstraint(
            "status IN ('preview','queued','running','checking','published','failed',"
            "'missing','reconnect')",
            name="ck_visitor_publication_status",
        ),
        CheckConstraint(
            "deposit_id IS NULL OR deposit_id > 0", name="ck_visitor_publication_deposit"
        ),
        CheckConstraint(
            "status <> 'published' OR (deposit_id IS NOT NULL AND published_at IS NOT NULL)",
            name="ck_visitor_publication_published",
        ),
        Index("ix_visitor_publications_owner_created", "owner_session_hash", "created_at"),
        Index("ix_visitor_publications_due", "status", "lease_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_session_hash: Mapped[str] = mapped_column(String(64), _owner())
    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("generated_artifacts.id"))
    destination: Mapped[str] = mapped_column(String(16))
    manifest: Mapped[dict] = mapped_column(JSONB)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), server_default="preview")
    create_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deposit_id: Mapped[int | None] = mapped_column(BigInteger)
    doi: Mapped[str | None] = mapped_column(String(200))
    record_url: Mapped[str | None] = mapped_column(String(500))
    error_code: Mapped[str | None] = mapped_column(String(80))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
