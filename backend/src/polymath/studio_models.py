"""Innovation Studio programmes and their stages; mirrors migration 0034 exactly."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class StudioProgramme(Base):
    __tablename__ = "studio_programmes"
    __table_args__ = (
        CheckConstraint("char_length(brief) BETWEEN 1 AND 1000", name="ck_studio_brief"),
        CheckConstraint(
            "jsonb_typeof(specs) = 'array' AND jsonb_array_length(specs) <= 6",
            name="ck_studio_specs",
        ),
        CheckConstraint("wildness IN ('near','far','strange')", name="ck_studio_wildness"),
        CheckConstraint(
            "status IN ('active','paused','completed','stopped')", name="ck_studio_status"
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('designed','inspired','none')",
            name="ck_studio_outcome",
        ),
        Index("ix_studio_programmes_owner_created", "owner_session_hash", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    brief: Mapped[str] = mapped_column(Text)
    specs: Mapped[list] = mapped_column(JSONB, server_default="[]")
    wildness: Mapped[str] = mapped_column(String(8), server_default="far")
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    outcome: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudioStep(Base):
    __tablename__ = "studio_steps"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_studio_step_run"),
        UniqueConstraint("programme_id", "ordinal", name="uq_studio_step_ordinal"),
        CheckConstraint(
            "stage IN ('brief','inspire','design','engineer','test')", name="ck_studio_step_stage"
        ),
        CheckConstraint("char_length(target) BETWEEN 1 AND 4000", name="ck_studio_step_target"),
        CheckConstraint(
            "status IN ('pending','running','done','failed')", name="ck_studio_step_status"
        ),
        CheckConstraint(
            "jsonb_typeof(premise_result_ids) = 'array' AND "
            "jsonb_array_length(premise_result_ids) <= 4",
            name="ck_studio_step_premises",
        ),
        Index("ix_studio_steps_programme", "programme_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    programme_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("studio_programmes.id"))
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


class StudioImage(Base):
    __tablename__ = "studio_images"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_studio_image_run"),
        CheckConstraint("char_length(prompt) BETWEEN 1 AND 4000", name="ck_studio_image_prompt"),
        CheckConstraint(
            "status IN ('rendering','succeeded','refused','unknown')", name="ck_studio_image_status"
        ),
        CheckConstraint(
            "mime IS NULL OR mime IN ('image/webp','image/png','image/jpeg')",
            name="ck_studio_image_mime",
        ),
        CheckConstraint(
            "(status = 'succeeded') = "
            "(content IS NOT NULL AND mime IS NOT NULL AND content_sha256 IS NOT NULL)",
            name="ck_studio_image_content",
        ),
        CheckConstraint(
            "content IS NULL OR octet_length(content) <= 8000000", name="ck_studio_image_size"
        ),
        CheckConstraint(
            "view IN ('concept','in_use','schematic','detail','sequence','scale','origins')",
            name="ck_studio_image_view",
        ),
        Index("ix_studio_images_programme", "programme_id", "created_at"),
        Index("ix_studio_images_owner", "owner_session_hash", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    programme_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("studio_programmes.id"))
    owner_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("research_runs.id"))
    prompt: Mapped[str] = mapped_column(Text)
    view: Mapped[str] = mapped_column(String(16), server_default="concept")
    status: Mapped[str] = mapped_column(String(16), server_default="rendering")
    error: Mapped[str | None] = mapped_column(String(64))
    mime: Mapped[str | None] = mapped_column(String(16))
    content: Mapped[bytes | None] = mapped_column(LargeBinary)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
