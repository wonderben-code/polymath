"""Server-owned selection of prior bridges; a link does not establish support."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.evidence_models import EvidenceResult
from polymath.jobs import JobLease, _owned
from polymath.lineage_models import ResearchRunParent
from polymath.models import ResearchRun
from polymath.research.models import MemoryReadReference


class LineageError(Exception):
    """Static code; missing, ineligible and foreign versions are indistinguishable."""


def validate_parent_ids(values: list[str] | tuple[str, ...], mode: str) -> tuple[uuid.UUID, ...]:
    if not isinstance(values, (list, tuple)) or len(values) > 4:
        raise ValueError("Invalid research parent selection")
    if not values:
        return ()
    if mode != "explore" or len(values) < 2:
        raise ValueError("Select two to four bridges for an Explore investigation")
    identifiers = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("Invalid research parent selection")
        try:
            identifier = uuid.UUID(value)
        except (ValueError, AttributeError):
            raise ValueError("Invalid research parent selection") from None
        if str(identifier) != value:
            raise ValueError("Invalid research parent selection")
        identifiers.append(identifier)
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Research parents must be distinct")
    return tuple(identifiers)


def _owner_hash(session: Session, run_id: uuid.UUID) -> str | None:
    return session.scalar(
        select(ResearchRunAccess.session_hash)
        .join(BrowserSession, BrowserSession.token_hash == ResearchRunAccess.session_hash)
        .where(
            ResearchRunAccess.run_id == run_id,
            BrowserSession.expires_at > func.clock_timestamp(),
        )
    )


def _parent(session: Session, run: ResearchRun, owner_hash: str, result_id: uuid.UUID):
    return session.scalar(
        select(EvidenceResult)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id)
        .join(ResearchRun, ResearchRun.id == EvidenceResult.run_id)
        .where(
            EvidenceResult.id == result_id,
            ResearchRunAccess.session_hash == owner_hash,
            EvidenceResult.run_id != run.id,
            EvidenceResult.created_at < run.created_at,
            EvidenceResult.status == "bridge",
            ResearchRun.status == "succeeded",
        )
    )


def bind_parents(
    session: Session, run: ResearchRun, owner_hash: str, parent_result_ids: list[str]
) -> tuple[MemoryReadReference, ...]:
    """Validate and insert within the caller's enqueue/ownership transaction."""
    identifiers = validate_parent_ids(parent_result_ids, run.mode)
    if not identifiers:
        return ()
    if run.status != "queued" or _owner_hash(session, run.id) != owner_hash:
        raise LineageError("research_basis_not_found")
    if session.scalar(select(ResearchRunParent.run_id).where(ResearchRunParent.run_id == run.id)):
        raise LineageError("research_basis_already_bound")
    parents = []
    # Validate the entire selection before inserting any row, even when this
    # helper is used outside the HTTP handler's rollback boundary.
    for identifier in identifiers:
        parent = _parent(session, run, owner_hash, identifier)
        if parent is None:
            raise LineageError("research_basis_not_found")
        parents.append(parent)
    refs = []
    for ordinal, parent in enumerate(parents):
        session.add(
            ResearchRunParent(
                run_id=run.id,
                parent_result_id=parent.id,
                parent_run_id=parent.run_id,
                ordinal=ordinal,
                content_sha256=parent.content_sha256,
            )
        )
        refs.append(
            MemoryReadReference(
                result_id=str(parent.id),
                run_id=str(parent.run_id),
                content_sha256=parent.content_sha256,
            )
        )
    return tuple(refs)


def get_parent_refs(engine: Engine, lease: JobLease) -> tuple[MemoryReadReference, ...]:
    """Load ordered, hash-bound selections using only the current fenced run."""
    with Session(engine) as session, session.begin():
        run = _owned(session, lease)
        rows = session.scalars(
            select(ResearchRunParent)
            .where(ResearchRunParent.run_id == run.id)
            .order_by(ResearchRunParent.ordinal)
        ).all()
        if not rows:
            # Ordinary injected fixture jobs need no browser session or memory.
            return ()
        owner_hash = _owner_hash(session, run.id)
        if owner_hash is None or run.mode != "explore" or not 2 <= len(rows) <= 4:
            raise LineageError("research_basis_not_found")
        refs = []
        for ordinal, row in enumerate(rows):
            parent = _parent(session, run, owner_hash, row.parent_result_id)
            if (
                parent is None
                or row.ordinal != ordinal
                or parent.run_id != row.parent_run_id
                or parent.content_sha256 != row.content_sha256
            ):
                raise LineageError("research_basis_not_found")
            refs.append(
                MemoryReadReference(
                    result_id=str(parent.id),
                    run_id=str(parent.run_id),
                    content_sha256=parent.content_sha256,
                )
            )
        return tuple(refs)
