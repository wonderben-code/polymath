"""The rigour bar (docs/RIGOUR.md): which literature an investigation may use.

frontier       everything, including brand-new preprints
standard       never retracted; preprints allowed (the default)
peer_reviewed  only work published in journals or conference proceedings
established    peer reviewed and cited at least ten times

Applied where sources are found, through OpenAlex's documented filters; arXiv
preprints are left out above standard. The level is recorded with the run.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, Session, mapped_column

from polymath.database import Base

Level = Literal["frontier", "standard", "peer_reviewed", "established"]
LEVELS: tuple[str, ...] = ("frontier", "standard", "peer_reviewed", "established")
DEFAULT_LEVEL = "standard"
OPENALEX_FILTERS = {
    "frontier": None,
    "standard": "is_retracted:false",
    "peer_reviewed": "is_retracted:false,primary_location.source.type:journal|conference",
    "established": (
        "is_retracted:false,primary_location.source.type:journal|conference,cited_by_count:>9"
    ),
}
PREPRINTS_ALLOWED = {
    "frontier": True,
    "standard": True,
    "peer_reviewed": False,
    "established": False,
}


class ResearchRunRigour(Base):
    __tablename__ = "research_run_rigour"
    __table_args__ = (
        CheckConstraint(
            "level IN ('frontier','standard','peer_reviewed','established')",
            name="ck_research_run_rigour_level",
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_runs.id", ondelete="CASCADE"), primary_key=True
    )
    level: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def level_for(session: Session, run_id: uuid.UUID) -> str:
    row = session.get(ResearchRunRigour, run_id)
    return row.level if row is not None and row.level in LEVELS else DEFAULT_LEVEL


def recorded_level(session: Session, run_id: uuid.UUID) -> str | None:
    row = session.get(ResearchRunRigour, run_id)
    return row.level if row is not None else None
