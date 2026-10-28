"""Explicit immutable public projections, with no model or network execution.

Public discovery reads only approved projection fields. Internal evidence/head
pointers supply authority and a status badge, never additional public content.
"""

from __future__ import annotations

import json
import re
import secrets
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, exists, func, or_, select, text, tuple_
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.assessment_eligibility import (
    EligibilityError,
    completed_result_condition,
    current_supported_condition,
    lock_current_source,
    result_assessment_state,
)
from polymath.assessment_models import FindingHead
from polymath.evidence import EvidenceError, _canonical, _document, _hash, _metadata
from polymath.evidence_models import (
    EvidenceQuote,
    EvidenceResult,
    RunEvidenceSource,
    SourceRetrieval,
    SourceVersion,
)
from polymath.full_text import FullTextError, owned_identity_context
from polymath.models import ResearchRun
from polymath.research.models import ResearchResult, VerifiedQuote
from polymath.sharing_models import SharedFinding, SharedFindingRevocation

SHARING_QUOTA_LOCK = 728319045
MAX_PUBLIC_FINDINGS = 60
MAX_PUBLIC_NODES = 200
MAX_PUBLIC_EDGES = 500
PUBLIC_SCOPE = "explicitly_shared_current_findings"


class SharingError(Exception):
    """Static failures; no private source content, identifiers or auth secrets."""


@dataclass(frozen=True)
class SharingPolicy:
    enabled: bool = False
    max_active_per_owner: int = 10
    max_active_global: int = 1000

    def __post_init__(self):
        if (
            type(self.enabled) is not bool
            or type(self.max_active_per_owner) is not int
            or not 1 <= self.max_active_per_owner <= 100
        ):
            raise ValueError("invalid sharing policy")
        if type(self.max_active_global) is not int or not 1 <= self.max_active_global <= 10000:
            raise ValueError("invalid sharing policy")


@dataclass(frozen=True)
class SharePreview:
    source_result_id: uuid.UUID
    projection_sha256: str
    projection: dict
    expires_at: datetime

    def to_response(self) -> dict:
        return {
            "source_result_id": str(self.source_result_id),
            "projection_sha256": self.projection_sha256,
            "projection": self.projection,
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass(frozen=True)
class ShareRecord:
    public_id: str
    source_result_id: uuid.UUID
    projection_sha256: str
    projection: dict
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    status: str
    idempotency_key: uuid.UUID

    def to_response(self) -> dict:
        return {
            "public_id": self.public_id,
            "source_result_id": str(self.source_result_id),
            "projection_sha256": self.projection_sha256,
            "projection": self.projection,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "status": self.status,
            "idempotency_key": str(self.idempotency_key),
            "public_path": f"/shared/{self.public_id}",
        }


def _uuid(value) -> uuid.UUID:
    try:
        parsed = uuid.UUID(str(value))
        if str(parsed) != str(value):
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise SharingError("sharing_invalid_request") from None


def _public_id(value) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is None:
        raise SharingError("sharing_not_found")
    return value


def _digest(value) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SharingError("sharing_invalid_request")
    return value


def _clock(session: Session):
    return session.scalar(select(func.clock_timestamp()))


def _browser(session: Session, owner_hash: str) -> BrowserSession:
    row = session.scalar(
        select(BrowserSession)
        .where(
            BrowserSession.token_hash == owner_hash,
            BrowserSession.expires_at > func.clock_timestamp(),
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise SharingError("sharing_not_found")
    return row


def _source(session: Session, owner: str, result_id) -> EvidenceResult:
    row = session.scalar(
        select(EvidenceResult)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id)
        .join(ResearchRun, ResearchRun.id == EvidenceResult.run_id)
        .where(
            EvidenceResult.id == _uuid(result_id),
            ResearchRunAccess.session_hash == owner,
            completed_result_condition(),
            ResearchRun.task_kind.in_(("research", "reassess_finding")),
            EvidenceResult.status.in_(("bridge", "answer")),
        )
    )
    if row is None:
        raise SharingError("sharing_not_found")
    if _hash(_canonical(row.payload)) != row.content_sha256:
        raise SharingError("sharing_source_integrity_failed")
    return row


def _projection(session: Session, source: EvidenceResult):
    from polymath.sharing_projection import build_public_projection, projection_sha256

    try:
        payload = json.loads(json.dumps(source.payload["result"]))
        documents = []
        source_refs = set()
        for ref in source.payload["source_artifacts"]:
            row = session.execute(
                select(SourceVersion, SourceRetrieval)
                .join(SourceRetrieval, SourceRetrieval.version_id == SourceVersion.id)
                .join(RunEvidenceSource, RunEvidenceSource.retrieval_id == SourceRetrieval.id)
                .where(
                    RunEvidenceSource.run_id == source.run_id,
                    SourceVersion.id == _uuid(ref["version_id"]),
                    SourceRetrieval.id == _uuid(ref["retrieval_id"]),
                )
            ).first()
            if row is None:
                raise SharingError("sharing_source_integrity_failed")
            document = _document(*row)
            if (
                document.content_sha256 != row[0].content_sha256
                or _hash(_canonical(_metadata(document))) != row[1].metadata_sha256
            ):
                raise SharingError("sharing_source_integrity_failed")
            documents.append(document)
            source_refs.add((row[0].id, row[1].id))
        payload["sources"] = [doc.model_dump(mode="json") for doc in documents]
        result = ResearchResult.model_validate_json(json.dumps(payload))
        source_run = session.get(ResearchRun, source.run_id)
        if result.status != source.status or result.mode != source_run.mode:
            raise SharingError("sharing_source_integrity_failed")
        rows = session.execute(
            select(EvidenceQuote, SourceVersion)
            .join(SourceVersion, SourceVersion.id == EvidenceQuote.version_id)
            .where(EvidenceQuote.result_id == source.id, EvidenceQuote.run_id == source.run_id)
            .order_by(EvidenceQuote.ordinal)
        ).all()
        quotes = []
        for ordinal, (quote, version) in enumerate(rows):
            if (
                quote.ordinal != ordinal
                or (quote.version_id, quote.retrieval_id) not in source_refs
            ):
                raise SharingError("sharing_source_integrity_failed")
            quotes.append(
                VerifiedQuote(
                    source_id=version.source_id,
                    side=quote.side,
                    claim_id=quote.claim_id,
                    quote=quote.quote,
                    supports=quote.supports,
                    start=quote.start_offset,
                    end=quote.end_offset,
                    source_sha256=version.content_sha256,
                    content_kind=version.content_kind,
                )
            )
        if tuple(quotes) != result.quotes:
            raise SharingError("sharing_source_integrity_failed")
        # Alias connectors remain private; they only reduce inferred independence.
        actual = tuple(
            _document(*row)
            for row in session.execute(
                select(SourceVersion, SourceRetrieval)
                .join(SourceRetrieval, SourceRetrieval.version_id == SourceVersion.id)
                .join(RunEvidenceSource, RunEvidenceSource.retrieval_id == SourceRetrieval.id)
                .where(RunEvidenceSource.run_id == source.run_id)
            ).all()
        )
        from polymath.literature.identity import paper_aliases

        distinct_context = {}
        for reference in actual + owned_identity_context(session, source.run_id):
            distinct_context.setdefault(paper_aliases(reference), reference)
            if len(distinct_context) > 256:
                raise SharingError("sharing_source_integrity_failed")
        projection = build_public_projection(
            result, identity_context=tuple(distinct_context.values())
        )
        return projection.model_dump(mode="json"), projection_sha256(projection)
    except (ValueError, TypeError, KeyError, AttributeError, EvidenceError, FullTextError):
        raise SharingError("sharing_source_integrity_failed") from None


def _saved_projection(row: SharedFinding) -> dict:
    from polymath.sharing_contracts import PublicFindingProjection
    from polymath.sharing_projection import projection_sha256

    try:
        projection = PublicFindingProjection.model_validate_json(json.dumps(row.projection))
        if projection_sha256(projection) != row.projection_sha256:
            raise SharingError("sharing_projection_integrity_failed")
        return projection.model_dump(mode="json")
    except (ValueError, TypeError, KeyError):
        raise SharingError("sharing_projection_integrity_failed") from None


def _revoked(session: Session, row: SharedFinding):
    return session.scalar(
        select(SharedFindingRevocation.revoked_at).where(SharedFindingRevocation.share_id == row.id)
    )


def _status(session: Session, row: SharedFinding) -> tuple[str, datetime | None]:
    revoked = _revoked(session, row)
    if revoked is not None:
        return "revoked", revoked
    owner_expiry = session.scalar(
        select(BrowserSession.expires_at).where(BrowserSession.token_hash == row.owner_session_hash)
    )
    now = _clock(session)
    if owner_expiry is None or min(row.expires_at, owner_expiry) <= now:
        return "expired", None
    head = session.get(FindingHead, row.root_result_id, populate_existing=True)
    if head is None:
        raise SharingError("sharing_source_integrity_failed")
    if head.current_result_id != row.source_result_id:
        return "superseded", None
    if head.state == "unsupported":
        return "unsupported", None
    if not result_assessment_state(session, row.owner_session_hash, row.source_result_id)[
        "is_supported"
    ]:
        return "needs_review", None
    return "current", None


def _record(session: Session, row: SharedFinding) -> ShareRecord:
    status, revoked = _status(session, row)
    return ShareRecord(
        public_id=row.public_id,
        source_result_id=row.source_result_id,
        projection_sha256=row.projection_sha256,
        projection=_saved_projection(row),
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=revoked,
        status=status,
        idempotency_key=row.idempotency_key,
    )


def _active():
    return (
        SharedFinding.expires_at > func.clock_timestamp(),
        exists(
            select(BrowserSession.token_hash).where(
                BrowserSession.token_hash == SharedFinding.owner_session_hash,
                BrowserSession.expires_at > func.clock_timestamp(),
            )
        ).correlate(SharedFinding),
        ~exists(
            select(SharedFindingRevocation.share_id).where(
                SharedFindingRevocation.share_id == SharedFinding.id
            )
        ).correlate(SharedFinding),
    )


def _query(value: str) -> str:
    if not isinstance(value, str) or len(value) > 200:
        raise SharingError("sharing_invalid_request")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise SharingError("sharing_invalid_request") from None
    if any(ord(char) < 32 for char in value):
        raise SharingError("sharing_invalid_request")
    return value.strip()


def _public_query(query: str):
    statement = (
        select(SharedFinding)
        .join(EvidenceResult, EvidenceResult.id == SharedFinding.source_result_id)
        .where(*_active(), current_supported_condition())
    )
    if query:
        statement = statement.where(
            or_(
                SharedFinding.projection["finding"].astext.icontains(query, autoescape=True),
                SharedFinding.projection["papers"].astext.icontains(query, autoescape=True),
                SharedFinding.projection["field_selection"].astext.icontains(
                    query, autoescape=True
                ),
            )
        )
    return statement


def _item(row: SharedFinding, projection: dict) -> dict:
    finding = projection["finding"]
    bridge = finding["kind"] == "bridge"
    fields = [finding["field_left"], finding["field_right"]] if bridge else []
    if projection.get("field_selection"):
        fields = list(dict.fromkeys(fields + projection["field_selection"]["fields"]))
    return {
        "public_id": row.public_id,
        "kind": finding["kind"],
        "mode": projection["mode"],
        "title": finding["title"] if bridge else finding["question"],
        "summary": (finding["explanation"] if bridge else finding["summary"])[:240],
        "fields": fields,
        "projection_sha256": row.projection_sha256,
        "created_at": row.created_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "status": "current",
    }


class SharingStore:
    def __init__(
        self,
        engine: Engine,
        policy: SharingPolicy | None = None,
        *,
        availability: Callable[[], bool] | None = None,
    ):
        if engine.dialect.name != "postgresql":
            raise ValueError("SharingStore requires PostgreSQL")
        self.engine, self.policy = engine, policy or SharingPolicy()
        self.availability = availability

    def _enabled(self) -> bool:
        if not self.policy.enabled:
            return False
        try:
            return self.availability is None or self.availability() is True
        except Exception:
            return False

    def preview(self, owner_hash: str, result_id) -> SharePreview:
        if not self._enabled():
            raise SharingError("sharing_unavailable")
        with Session(self.engine) as session:
            _browser(session, owner_hash)
            source = _source(session, owner_hash, result_id)
            if not result_assessment_state(session, source.id)["is_supported"]:
                raise SharingError("sharing_source_not_current")
            projection, digest = _projection(session, source)
            browser = _browser(session, owner_hash)
            if not self._enabled():
                raise SharingError("sharing_unavailable")
            return SharePreview(source.id, digest, projection, browser.expires_at)

    def share(
        self, owner_hash: str, result_id, expected_projection_sha256: str, idempotency_key
    ) -> ShareRecord:
        result_id, key = _uuid(result_id), _uuid(idempotency_key)
        digest = _digest(expected_projection_sha256)
        request_hash = _hash(
            _canonical({"source_result_id": str(result_id), "projection_sha256": digest})
        )
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SHARING_QUOTA_LOCK})
            _browser(session, owner_hash)
            existing = session.scalar(
                select(SharedFinding).where(
                    SharedFinding.owner_session_hash == owner_hash,
                    SharedFinding.idempotency_key == key,
                )
            )
            if existing is not None:
                if existing.request_sha256 != request_hash:
                    raise SharingError("sharing_idempotency_conflict")
                record = _record(session, existing)
                _browser(session, owner_hash)
                return record
            if not self._enabled():
                raise SharingError("sharing_unavailable")
            source = _source(session, owner_hash, result_id)
            try:
                head = lock_current_source(session, source.id)
            except EligibilityError:
                raise SharingError("sharing_source_not_current") from None
            _browser(session, owner_hash)
            if not self._enabled():
                raise SharingError("sharing_unavailable")
            count = select(func.count()).select_from(SharedFinding).where(*_active())
            if session.scalar(count.where(SharedFinding.owner_session_hash == owner_hash)) >= (
                self.policy.max_active_per_owner
            ):
                raise SharingError("sharing_active_limit")
            if session.scalar(count) >= self.policy.max_active_global:
                raise SharingError("sharing_global_limit")
            projection, actual_hash = _projection(session, source)
            if actual_hash != digest:
                raise SharingError("sharing_projection_changed")
            browser = _browser(session, owner_hash)
            share = SharedFinding(
                public_id=secrets.token_urlsafe(32),
                owner_session_hash=owner_hash,
                source_result_id=source.id,
                source_run_id=source.run_id,
                root_result_id=head.root_result_id,
                source_sha256=source.content_sha256,
                protocol="polymath-public-finding-v1",
                projection=projection,
                projection_sha256=digest,
                idempotency_key=key,
                request_sha256=request_hash,
                expires_at=browser.expires_at,
            )
            session.add(share)
            session.flush()
            record = _record(session, share)
            _browser(session, owner_hash)
            if not self._enabled():
                raise SharingError("sharing_unavailable")
            return record

    def list_owned(self, owner_hash: str, *, source_result_id=None, limit=20, before=None):
        if type(limit) is not int or not 1 <= limit <= 21:
            raise SharingError("sharing_invalid_request")
        with Session(self.engine) as session:
            _browser(session, owner_hash)
            query = select(SharedFinding).where(SharedFinding.owner_session_hash == owner_hash)
            if source_result_id is not None:
                query = query.where(SharedFinding.source_result_id == _uuid(source_result_id))
            if before is not None:
                anchor = session.scalar(
                    select(SharedFinding).where(
                        SharedFinding.public_id == _public_id(before),
                        SharedFinding.owner_session_hash == owner_hash,
                    )
                )
                if anchor is None:
                    raise SharingError("sharing_not_found")
                query = query.where(
                    tuple_(SharedFinding.created_at, SharedFinding.id)
                    < tuple_(anchor.created_at, anchor.id)
                )
            rows = session.scalars(
                query.order_by(SharedFinding.created_at.desc(), SharedFinding.id.desc()).limit(
                    limit
                )
            ).all()
            records = tuple(_record(session, row) for row in rows)
            _browser(session, owner_hash)
            return records

    def revoke(self, owner_hash: str, public_id: str) -> ShareRecord:
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            row = session.scalar(
                select(SharedFinding)
                .where(
                    SharedFinding.public_id == _public_id(public_id),
                    SharedFinding.owner_session_hash == owner_hash,
                )
                .with_for_update()
            )
            if row is None:
                raise SharingError("sharing_not_found")
            _browser(session, owner_hash)
            if session.get(SharedFindingRevocation, row.id) is None:
                session.add(SharedFindingRevocation(share_id=row.id, owner_session_hash=owner_hash))
                session.flush()
            record = _record(session, row)
            _browser(session, owner_hash)
            return record

    def get_public(self, public_id: str) -> dict:
        if not self._enabled():
            raise SharingError("sharing_not_found")
        with Session(self.engine) as session:
            row = session.scalar(
                select(SharedFinding).where(
                    SharedFinding.public_id == _public_id(public_id), *_active()
                )
            )
            if row is None:
                raise SharingError("sharing_not_found")
            projection = _saved_projection(row)
            status, _ = _status(session, row)
            if status in ("revoked", "expired") or not self._enabled():
                raise SharingError("sharing_not_found")
            return {
                "public_id": row.public_id,
                "projection_sha256": row.projection_sha256,
                "projection": projection,
                "created_at": row.created_at.isoformat(),
                "expires_at": row.expires_at.isoformat(),
                "status": status,
            }

    def list_public(self, *, query="", limit=20, before=None) -> dict:
        if not self._enabled():
            raise SharingError("sharing_not_found")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise SharingError("sharing_invalid_request")
        query = _query(query)
        with Session(self.engine) as session:
            statement = _public_query(query)
            if before is not None:
                anchor = session.scalar(
                    _public_query("").where(SharedFinding.public_id == _public_id(before))
                )
                if anchor is None:
                    raise SharingError("sharing_not_found")
                statement = statement.where(
                    tuple_(SharedFinding.created_at, SharedFinding.id)
                    < tuple_(anchor.created_at, anchor.id)
                )
            rows = session.scalars(
                statement.order_by(SharedFinding.created_at.desc(), SharedFinding.id.desc()).limit(
                    limit + 1
                )
            ).all()
            items = []
            for row in rows[:limit]:
                projection = _saved_projection(row)
                if _status(session, row)[0] == "current":
                    items.append(_item(row, projection))
            if not self._enabled():
                raise SharingError("sharing_not_found")
            return {
                "items": items,
                "next_cursor": items[-1]["public_id"] if len(rows) > limit and items else None,
            }

    def public_map(self, *, query="") -> dict:
        if not self._enabled():
            raise SharingError("sharing_not_found")
        query = _query(query)
        with Session(self.engine) as session:
            rows = session.scalars(
                _public_query(query)
                .order_by(SharedFinding.created_at.desc(), SharedFinding.id.desc())
                .limit(MAX_PUBLIC_FINDINGS + 1)
            ).all()
            nodes, edges = {}, {}
            truncated = len(rows) > MAX_PUBLIC_FINDINGS
            for row in rows[:MAX_PUBLIC_FINDINGS]:
                projection = _saved_projection(row)
                if _status(session, row)[0] != "current":
                    continue
                proposed_nodes, proposed_edges = _map_group(row, projection)
                if (
                    len(nodes.keys() | proposed_nodes.keys()) > MAX_PUBLIC_NODES
                    or len(edges.keys() | proposed_edges.keys()) > MAX_PUBLIC_EDGES
                ):
                    truncated = True
                    break
                nodes.update(proposed_nodes)
                edges.update(proposed_edges)
            if not self._enabled():
                raise SharingError("sharing_not_found")
            return {
                "scope": PUBLIC_SCOPE,
                "nodes": list(nodes.values()),
                "edges": list(edges.values()),
                "truncated": truncated,
                "coverage": {
                    "max_findings": MAX_PUBLIC_FINDINGS,
                    "max_nodes": MAX_PUBLIC_NODES,
                    "max_edges": MAX_PUBLIC_EDGES,
                },
            }


def _map_group(row: SharedFinding, projection: dict):
    finding = projection["finding"]
    share_id, path = f"share:{row.public_id}", f"/shared/{row.public_id}"
    nodes = {
        share_id: {
            "id": share_id,
            "kind": "finding",
            "label": finding.get("title", finding.get("question")),
            "public_id": row.public_id,
            "public_path": path,
            "paper_id": None,
            "links": [],
        }
    }
    edges = {}
    fields = []
    if finding["kind"] == "bridge":
        fields += [(finding["field_left"], "field_left"), (finding["field_right"], "field_right")]
    if projection.get("field_selection"):
        fields += [(value, "field_selected") for value in projection["field_selection"]["fields"]]
    for label, kind in fields:
        normalized = " ".join(unicodedata.normalize("NFKC", label).casefold().split())
        identifier = "field:" + _hash(normalized)
        nodes[identifier] = {
            "id": identifier,
            "kind": "field",
            "label": label,
            "public_id": None,
            "public_path": None,
            "paper_id": None,
            "links": [],
        }
        edge_id = f"{kind}:{share_id}:{identifier}"
        edges[edge_id] = {"id": edge_id, "source": share_id, "target": identifier, "kind": kind}
    for paper in projection["papers"]:
        identifier = f"paper:{row.public_id}:{paper['id']}"
        nodes[identifier] = {
            "id": identifier,
            "kind": "paper",
            "label": paper["title"],
            "public_id": row.public_id,
            "public_path": path,
            "paper_id": paper["id"],
            "links": paper["links"],
        }
        edge_id = f"cites:{share_id}:{identifier}"
        edges[edge_id] = {"id": edge_id, "source": share_id, "target": identifier, "kind": "cites"}
    return nodes, edges
