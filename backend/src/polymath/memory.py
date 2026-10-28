"""Owned research memory: earlier model findings are leads, never primary evidence.

The current fenced run determines the session. Models cannot supply or expand
that scope. Reading a finding exposes only its actually quoted source versions;
reading their text is a separate action. The worker must persist those original
documents under its current lease before they can support a new finding.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from pydantic import ValidationError
from sqlalchemy import Engine, func, or_, select
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.evidence import _canonical, _document, _hash
from polymath.evidence_models import EvidenceQuote, EvidenceResult, SourceRetrieval, SourceVersion
from polymath.jobs import JobLease, _owned
from polymath.knowledge import _finding_match
from polymath.memory_models import ResearchMemoryRead
from polymath.models import ResearchRun
from polymath.research.models import (
    AnswerFinding,
    BridgeFinding,
    MemoryFinding,
    MemoryFindingReference,
    MemorySearchResults,
    MemorySourceReference,
    SourceDocument,
    SourceReference,
)

MAX_MEMORY_FINDINGS = 10
MAX_MEMORY_SOURCES = 64


class MemoryError(Exception):
    """Static error codes without private input, model output, or source text."""


def _uuid(value: str) -> uuid.UUID:
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise MemoryError("memory_finding_not_found") from None


def _query(value: str) -> str:
    if not isinstance(value, str):
        raise MemoryError("invalid_memory_query")
    value = value.strip()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise MemoryError("invalid_memory_query") from None
    if not 1 <= len(value) <= 200 or any(ord(character) < 32 for character in value):
        raise MemoryError("invalid_memory_query")
    return value


def _session_hash(session: Session, run_id: uuid.UUID) -> str | None:
    return session.scalar(
        select(ResearchRunAccess.session_hash)
        .join(BrowserSession, BrowserSession.token_hash == ResearchRunAccess.session_hash)
        .where(
            ResearchRunAccess.run_id == run_id,
            BrowserSession.expires_at > func.clock_timestamp(),
        )
    )


def _eligible(run: ResearchRun, owner_hash: str):
    return (
        select(EvidenceResult)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id)
        .join(ResearchRun, ResearchRun.id == EvidenceResult.run_id)
        .where(
            ResearchRunAccess.session_hash == owner_hash,
            ResearchRun.status == "succeeded",
            ResearchRun.task_kind == "research",
            EvidenceResult.status.in_(("bridge", "answer")),
            EvidenceResult.run_id != run.id,
            EvidenceResult.created_at < run.created_at,
        )
    )


def _finding(row: EvidenceResult) -> BridgeFinding | AnswerFinding:
    try:
        if _hash(_canonical(row.payload)) != row.content_sha256:
            raise ValueError
        raw = row.payload["result"]["finding"]
        kind = BridgeFinding if row.status == "bridge" else AnswerFinding
        return kind.model_validate_json(json.dumps(raw))
    except (ValueError, KeyError, TypeError, ValidationError):
        raise MemoryError("memory_finding_unavailable") from None


def _reference(
    row: EvidenceResult, finding: BridgeFinding | AnswerFinding
) -> MemoryFindingReference:
    if isinstance(finding, BridgeFinding):
        title, summary = finding.title, finding.explanation
    else:
        title, summary = finding.question, finding.summary
    return MemoryFindingReference(
        result_id=str(row.id),
        run_id=str(row.run_id),
        content_sha256=row.content_sha256,
        title=title[:300],
        summary=summary[:1000],
        outcome=finding.outcome,
    )


def _quoted_sources(session: Session, row: EvidenceResult):
    # A shared source version does not confer access to another run's retrieval.
    # Composite evidence FKs bind these exact metadata rows to this finding.
    return session.execute(
        select(SourceVersion, SourceRetrieval)
        .select_from(EvidenceQuote)
        .join(SourceVersion, SourceVersion.id == EvidenceQuote.version_id)
        .join(SourceRetrieval, SourceRetrieval.id == EvidenceQuote.retrieval_id)
        .where(EvidenceQuote.result_id == row.id, EvidenceQuote.run_id == row.run_id)
        .order_by(EvidenceQuote.ordinal)
        .limit(MAX_MEMORY_SOURCES + 1)
    ).all()


class ResearchMemory:
    def __init__(self, engine: Engine, lease: JobLease):
        if engine.dialect.name != "postgresql":
            raise ValueError("ResearchMemory requires PostgreSQL transaction semantics")
        self.engine, self.lease = engine, lease

    def validate_scope(self) -> bool:
        """Reauthorize cached observations without searching or changing their payload."""
        return self.scope_fingerprint() is not None

    def scope_fingerprint(self) -> str | None:
        """Opaque run/owner binding for cached observations; never a browser credential."""
        with Session(self.engine) as session, session.begin():
            run = _owned(session, self.lease)
            owner = _session_hash(session, run.id)
            if owner is None:
                return None
            return hashlib.sha256(f"polymath-memory-scope-v1:{run.id}:{owner}".encode()).hexdigest()

    def search(self, query: str) -> MemorySearchResults:
        query = _query(query)
        with Session(self.engine) as session, session.begin():
            run = _owned(session, self.lease)
            owner = _session_hash(session, run.id)
            if owner is None:
                return MemorySearchResults(findings=(), truncated=False)
            source_match = (
                select(EvidenceQuote.result_id)
                .join(SourceRetrieval, SourceRetrieval.id == EvidenceQuote.retrieval_id)
                .join(SourceVersion, SourceVersion.id == EvidenceQuote.version_id)
                .where(
                    or_(
                        SourceRetrieval.title.icontains(query, autoescape=True),
                        SourceVersion.source_id.icontains(query, autoescape=True),
                    )
                )
            )
            records = session.scalars(
                _eligible(run, owner)
                .where(or_(_finding_match(query), EvidenceResult.id.in_(source_match)))
                .order_by(EvidenceResult.created_at.desc(), EvidenceResult.id)
                .limit(MAX_MEMORY_FINDINGS + 1)
            ).all()
            return MemorySearchResults(
                findings=tuple(
                    _reference(row, _finding(row)) for row in records[:MAX_MEMORY_FINDINGS]
                ),
                truncated=len(records) > MAX_MEMORY_FINDINGS,
            )

    def read_finding(self, result_id: str) -> MemoryFinding:
        result_uuid = _uuid(result_id)
        with Session(self.engine) as session, session.begin():
            run = _owned(session, self.lease)
            owner = _session_hash(session, run.id)
            row = (
                session.scalar(_eligible(run, owner).where(EvidenceResult.id == result_uuid))
                if owner is not None
                else None
            )
            if row is None:
                raise MemoryError("memory_finding_not_found")
            finding = _finding(row)
            rows = _quoted_sources(session, row)
            if len(rows) > MAX_MEMORY_SOURCES:
                raise MemoryError("memory_source_limit")
            sources = {}
            for version, retrieval in rows:
                sources.setdefault(
                    retrieval.id,
                    MemorySourceReference(
                        retrieval_id=str(retrieval.id),
                        source=SourceReference(
                            source_id=version.source_id, title=retrieval.title, url=retrieval.url
                        ),
                        content_sha256=version.content_sha256,
                        content_kind=version.content_kind,
                    ),
                )
            prior = session.get(ResearchMemoryRead, (run.id, row.id))
            if prior is None:
                count = session.scalar(
                    select(func.count())
                    .select_from(ResearchMemoryRead)
                    .where(ResearchMemoryRead.run_id == run.id)
                )
                if count >= MAX_MEMORY_FINDINGS:
                    raise MemoryError("memory_read_limit")
                session.add(
                    ResearchMemoryRead(
                        run_id=run.id,
                        parent_result_id=row.id,
                        parent_run_id=row.run_id,
                        parent_content_sha256=row.content_sha256,
                    )
                )
            elif (prior.parent_run_id, prior.parent_content_sha256) != (
                row.run_id,
                row.content_sha256,
            ):
                raise MemoryError("memory_read_conflict")
            return MemoryFinding(
                reference=_reference(row, finding), finding=finding, sources=tuple(sources.values())
            )

    def read_source(self, result_id: str, retrieval_id: str) -> SourceDocument:
        result_uuid, retrieval_uuid = _uuid(result_id), _uuid(retrieval_id)
        with Session(self.engine) as session, session.begin():
            run = _owned(session, self.lease)
            owner = _session_hash(session, run.id)
            row = (
                session.scalar(_eligible(run, owner).where(EvidenceResult.id == result_uuid))
                if owner is not None
                else None
            )
            prior = session.get(ResearchMemoryRead, (run.id, result_uuid))
            if row is None or prior is None:
                raise MemoryError("memory_source_not_found")
            if (prior.parent_run_id, prior.parent_content_sha256) != (
                row.run_id,
                row.content_sha256,
            ):
                raise MemoryError("memory_read_conflict")
            for version, retrieval in _quoted_sources(session, row):
                if retrieval.id == retrieval_uuid:
                    return _document(version, retrieval)
            raise MemoryError("memory_source_not_found")
