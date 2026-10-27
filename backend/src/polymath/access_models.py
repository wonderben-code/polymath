"""Anonymous browser ownership; only hashes of bearer cookies are persisted."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class BrowserSession(Base):
    __tablename__ = "browser_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ResearchRunAccess(Base):
    __tablename__ = "research_run_access"
    __table_args__ = (Index("ix_research_run_access_session", "session_hash"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id", ondelete="CASCADE"), primary_key=True
    )
    session_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("browser_sessions.token_hash", ondelete="CASCADE")
    )
