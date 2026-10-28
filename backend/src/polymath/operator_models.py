"""Short-lived publisher identity sessions, bound to an anonymous browser session."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class OperatorSession(Base):
    __tablename__ = "operator_sessions"
    __table_args__ = (
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_operator_session_hash"),
        CheckConstraint(
            "credential_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_operator_credential_fingerprint"
        ),
        CheckConstraint("length(actor_id) BETWEEN 1 AND 100", name="ck_operator_actor_size"),
        CheckConstraint("length(display_name) BETWEEN 1 AND 120", name="ck_operator_display_size"),
        Index("ix_operator_sessions_browser_session_hash", "browser_session_hash"),
        Index("ix_operator_sessions_expires_at", "expires_at"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash")
    )
    credential_fingerprint: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
