"""Accounts and their OAuth states; mirrors migration 0031 exactly."""

import uuid
from datetime import datetime

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class AccountOAuthState(Base):
    __tablename__ = "account_oauth_states"
    __table_args__ = (
        CheckConstraint("state_hash ~ '^[0-9a-f]{64}$'", name="ck_account_state_hash"),
        CheckConstraint(
            "provider IN ('orcid','github','google')", name="ck_account_state_provider"
        ),
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '15 minutes'",
            name="ck_account_state_lifetime",
        ),
        Index("ix_account_oauth_states_owner", "owner_session_hash"),
    )

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_account_identity"),
        UniqueConstraint("home_session_hash", name="uq_account_home"),
        CheckConstraint("provider IN ('orcid','github','google')", name="ck_account_provider"),
        CheckConstraint(
            "(home_session_hash IS NULL) = (home_ciphertext IS NULL)", name="ck_account_home"
        ),
        CheckConstraint(
            "home_ciphertext IS NULL OR octet_length(home_ciphertext) BETWEEN 29 AND 512",
            name="ck_account_home_size",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(120))
    orcid: Mapped[str | None] = mapped_column(String(19))
    home_session_hash: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash", ondelete="SET NULL")
    )
    home_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    signed_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
