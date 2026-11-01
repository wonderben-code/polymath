"""Connect Zenodo for every visitor; mirrors migration 0029 exactly."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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


class VisitorPublishingPreference(Base):
    __tablename__ = "visitor_publishing_preferences"
    __table_args__ = (
        CheckConstraint("level BETWEEN 1 AND 4", name="ck_visitor_publishing_level"),
        CheckConstraint(
            "draft_destination IN ('sandbox','production')", name="ck_visitor_publishing_draft"
        ),
        CheckConstraint(
            "write_kind IN ('write_summary','write_paper')", name="ck_visitor_publishing_kind"
        ),
        CheckConstraint("daily_cap BETWEEN 1 AND 10", name="ck_visitor_publishing_cap"),
        CheckConstraint("license IN ('cc-by-4.0','cc0-1.0')", name="ck_visitor_publishing_license"),
        CheckConstraint(
            "jsonb_typeof(creators) = 'array' AND jsonb_array_length(creators) <= 10",
            name="ck_visitor_publishing_creators",
        ),
    )

    owner_session_hash: Mapped[str] = mapped_column(String(64), _owner(), primary_key=True)
    level: Mapped[int] = mapped_column(Integer, server_default="1")
    draft_destination: Mapped[str] = mapped_column(String(16), server_default="sandbox")
    write_kind: Mapped[str] = mapped_column(String(16), server_default="write_paper")
    full_text_only: Mapped[bool] = mapped_column(Boolean, server_default="false")
    daily_cap: Mapped[int] = mapped_column(Integer, server_default="3")
    creators: Mapped[list] = mapped_column(JSONB, server_default="[]")
    license: Mapped[str] = mapped_column(String(16), server_default="cc-by-4.0")
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    production_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VisitorPublishingItem(Base):
    __tablename__ = "visitor_publishing_items"
    __table_args__ = (
        UniqueConstraint("owner_session_hash", "result_id", name="uq_visitor_publishing_item"),
        CheckConstraint("level BETWEEN 2 AND 4", name="ck_visitor_item_level"),
        CheckConstraint(
            "destination IN ('sandbox','production')", name="ck_visitor_item_destination"
        ),
        CheckConstraint(
            "write_kind IN ('write_summary','write_paper')", name="ck_visitor_item_kind"
        ),
        CheckConstraint(
            "status IN ('pending_write','writing','drafted','ready','queued','published',"
            "'failed','skipped')",
            name="ck_visitor_item_status",
        ),
        Index("ix_visitor_publishing_items_owner_created", "owner_session_hash", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_session_hash: Mapped[str] = mapped_column(String(64), _owner())
    result_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("evidence_results.id"))
    level: Mapped[int] = mapped_column(Integer)
    destination: Mapped[str] = mapped_column(String(16))
    write_kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default="pending_write")
    reason: Mapped[str | None] = mapped_column(String(80))
    artifact_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("generated_artifacts.id")
    )
    publication_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("visitor_publications.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
