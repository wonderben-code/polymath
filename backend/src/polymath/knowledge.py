"""Bounded, session-owned projections of saved evidence; no model or network calls.

The graph reports recorded fields and quote associations. It does not establish
scientific validity, causal equivalence, novelty, or independent corroboration.
"""

from __future__ import annotations

import hashlib
import unicodedata
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Engine, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.access_models import ResearchRunAccess
from polymath.evidence_models import (
    EvidenceQuote,
    EvidenceResult,
    RunEvidenceSource,
    SourceRetrieval,
    SourceVersion,
)
from polymath.run_api import (
    _authenticated,
    _private_response,
    _PrivateHTTPException,
    _PrivateRoute,
)

MAX_CANDIDATES = 60
MAX_NODES = 200
MAX_EDGES = 500
POSITIVE_STATUSES = ("bridge", "answer")


def _query(value: str) -> str:
    value = value.strip()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _PrivateHTTPException(422, "invalid_knowledge_query") from None
    if len(value) > 200 or any(ord(character) < 32 for character in value):
        raise _PrivateHTTPException(422, "invalid_knowledge_query")
    return value


def _scope(**values) -> dict:
    return {"scope": "current_browser_session", **values}


def _finding(envelope: object) -> tuple[dict, dict] | None:
    if not isinstance(envelope, dict) or not isinstance(envelope.get("result"), dict):
        return None
    result = envelope["result"]
    finding = result.get("finding")
    if not isinstance(finding, dict) or finding.get("outcome") not in POSITIVE_STATUSES:
        return None
    return result, finding


def _text(value: object, maximum: int = 3000) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        return None
    return value.strip()


def _finding_item(row: EvidenceResult) -> dict | None:
    found = _finding(row.payload)
    if found is None:
        return None
    _, finding = found
    if finding["outcome"] == "bridge":
        title = _text(finding.get("title"), 300)
        summary = _text(finding.get("explanation"))
    else:
        title = _text(finding.get("question"), 4000)
        summary = _text(finding.get("summary"), 6000)
    if title is None:
        return None
    return {
        "id": f"finding:{row.id}",
        "type": "finding",
        "title": title,
        "run_id": str(row.run_id),
        "result_id": str(row.id),
        "source_id": None,
        "content_kind": None,
        "summary": (summary or "Saved research finding")[:240],
    }


def _sources(owner_hash: str, query: str = ""):
    statement = (
        select(
            SourceVersion.id,
            SourceVersion.source_id,
            SourceVersion.content_kind,
            SourceRetrieval.title,
            RunEvidenceSource.run_id,
            func.row_number()
            .over(
                partition_by=SourceVersion.id,
                order_by=(
                    SourceRetrieval.retrieved_at.desc(),
                    RunEvidenceSource.run_id,
                    SourceRetrieval.id,
                ),
            )
            .label("version_rank"),
        )
        .join(SourceRetrieval, SourceRetrieval.version_id == SourceVersion.id)
        .join(RunEvidenceSource, RunEvidenceSource.retrieval_id == SourceRetrieval.id)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == RunEvidenceSource.run_id)
        .where(ResearchRunAccess.session_hash == owner_hash)
    )
    if query:
        statement = statement.where(
            or_(
                SourceRetrieval.title.icontains(query, autoescape=True),
                SourceVersion.source_id.icontains(query, autoescape=True),
            )
        )
    ranked = statement.subquery()
    return (
        select(
            ranked.c.id, ranked.c.source_id, ranked.c.content_kind, ranked.c.title, ranked.c.run_id
        )
        .where(ranked.c.version_rank == 1)
        .order_by(ranked.c.id)
    )


def _finding_match(query: str):
    finding = EvidenceResult.payload["result"]["finding"]
    selected_field = func.jsonb_array_elements_text(
        func.jsonb_path_query_array(
            EvidenceResult.payload, cast("$.result.field_selections[*].fields[*]", JSONPATH)
        )
    ).column_valued("selected_field")
    selection_match = (
        select(1)
        .where(selected_field.icontains(query, autoescape=True))
        .correlate(EvidenceResult)
        .exists()
    )
    return or_(
        selection_match,
        *(
            finding[key].astext.icontains(query, autoescape=True)
            for key in ("title", "explanation", "field_left", "field_right", "question", "summary")
        ),
    )


def _findings(owner_hash: str, query: str = ""):
    statement = (
        select(EvidenceResult)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id)
        .where(
            ResearchRunAccess.session_hash == owner_hash,
            EvidenceResult.status.in_(POSITIVE_STATUSES),
        )
    )
    if query:
        matching_quotes = (
            select(EvidenceQuote.result_id)
            .join(SourceVersion, SourceVersion.id == EvidenceQuote.version_id)
            .join(SourceRetrieval, SourceRetrieval.id == EvidenceQuote.retrieval_id)
            .where(
                or_(
                    SourceRetrieval.title.icontains(query, autoescape=True),
                    SourceVersion.source_id.icontains(query, autoescape=True),
                )
            )
        )
        statement = statement.where(
            or_(_finding_match(query), EvidenceResult.id.in_(matching_quotes))
        )
    return statement.order_by(EvidenceResult.created_at.desc(), EvidenceResult.id)


def _source_item(row) -> dict:
    return {
        "id": f"source:{row.id}",
        "type": "source",
        "title": row.title,
        "run_id": str(row.run_id),
        "result_id": None,
        "source_id": row.source_id,
        "content_kind": row.content_kind,
        "summary": f"Saved {row.content_kind.replace('_', ' ')} snapshot",
    }


def _field_id(name: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
    return "field:" + hashlib.sha256(normalized.encode()).hexdigest()


def _add_field(
    proposed_nodes: dict, proposed_edges: dict, finding_id: str, name: object, kind: str
) -> None:
    label = _text(name, 200)
    if label is None:
        return
    identifier = _field_id(label)
    proposed_nodes[identifier] = {
        "id": identifier,
        "kind": "field",
        "label": label,
        "run_id": None,
        "result_id": None,
        "source_id": None,
        "content_kind": None,
    }
    edge_id = f"{kind}:{finding_id}:{identifier}"
    proposed_edges[edge_id] = {
        "id": edge_id,
        "source": finding_id,
        "target": identifier,
        "kind": kind,
    }


def _graph(session: Session, owner_hash: str, query: str) -> dict:
    candidates = session.scalars(_findings(owner_hash, query).limit(MAX_CANDIDATES + 1)).all()
    additional_sources = session.execute(
        _sources(owner_hash, query).limit(MAX_CANDIDATES + 1)
    ).all()
    truncated = len(candidates) > MAX_CANDIDATES or len(additional_sources) > MAX_CANDIDATES
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    ids = [row.id for row in candidates[:MAX_CANDIDATES]]
    quoted: dict[uuid.UUID, list] = {}
    if ids:
        # Result ownership was checked above. Composite FKs bind each quoted
        # retrieval to this result's run and immutable source version.
        records = session.execute(
            select(
                EvidenceQuote.result_id,
                SourceVersion.id,
                SourceVersion.source_id,
                SourceVersion.content_kind,
                SourceRetrieval.title,
            )
            .join(SourceVersion, SourceVersion.id == EvidenceQuote.version_id)
            .join(SourceRetrieval, SourceRetrieval.id == EvidenceQuote.retrieval_id)
            .where(EvidenceQuote.result_id.in_(ids))
            .order_by(EvidenceQuote.result_id, EvidenceQuote.ordinal)
            .limit(MAX_CANDIDATES * 64 + 1)
        ).all()
        if len(records) > MAX_CANDIDATES * 64:
            truncated = True
        for record in records[: MAX_CANDIDATES * 64]:
            quoted.setdefault(record.result_id, []).append(record)
    for row in candidates[:MAX_CANDIDATES]:
        item = _finding_item(row)
        found = _finding(row.payload)
        if item is None or found is None:
            continue
        result, finding = found
        node = {
            "id": item["id"],
            "kind": "finding",
            "label": item["title"],
            "run_id": item["run_id"],
            "result_id": item["result_id"],
            "source_id": None,
            "content_kind": None,
        }
        proposed_nodes = {node["id"]: node}
        proposed_edges = {}

        if finding["outcome"] == "bridge":
            _add_field(
                proposed_nodes, proposed_edges, node["id"], finding.get("field_left"), "maps_to"
            )
            _add_field(
                proposed_nodes, proposed_edges, node["id"], finding.get("field_right"), "maps_to"
            )
        selections = result.get("field_selections", [])
        if isinstance(selections, list):
            for selection in selections[:3]:
                if isinstance(selection, dict) and isinstance(selection.get("fields"), list):
                    for name in selection["fields"][:4]:
                        _add_field(
                            proposed_nodes, proposed_edges, node["id"], name, "selected_field"
                        )
        for source in quoted.get(row.id, []):
            source_node_id = f"source:{source.id}"
            proposed_nodes[source_node_id] = {
                "id": source_node_id,
                "kind": "source",
                "label": source.title,
                "run_id": str(row.run_id),
                "result_id": str(row.id),
                "source_id": source.source_id,
                "content_kind": source.content_kind,
            }
            edge_id = f"supported_by:{node['id']}:{source_node_id}"
            proposed_edges[edge_id] = {
                "id": edge_id,
                "source": node["id"],
                "target": source_node_id,
                "kind": "supported_by",
            }
        if (
            len(set(nodes) | set(proposed_nodes)) > MAX_NODES
            or len(set(edges) | set(proposed_edges)) > MAX_EDGES
        ):
            truncated = True
            continue
        for identifier, proposed in proposed_nodes.items():
            nodes.setdefault(identifier, proposed)
        edges.update(proposed_edges)
    for row in additional_sources[:MAX_CANDIDATES]:
        identifier = f"source:{row.id}"
        if identifier not in nodes:
            if len(nodes) >= MAX_NODES:
                truncated = True
                break
            nodes[identifier] = {
                "id": identifier,
                "kind": "source",
                "label": row.title,
                "run_id": str(row.run_id),
                "result_id": None,
                "source_id": row.source_id,
                "content_kind": row.content_kind,
            }
    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "truncated": truncated,
        "coverage": _scope(
            findings_considered=min(len(candidates), MAX_CANDIDATES),
            source_snapshots_considered=min(len(additional_sources), MAX_CANDIDATES),
            candidate_limit=MAX_CANDIDATES,
            node_limit=MAX_NODES,
            edge_limit=MAX_EDGES,
        ),
    }


def create_knowledge_router(engine: Engine) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/knowledge",
        route_class=_PrivateRoute,
        dependencies=[Depends(_private_response)],
    )

    @router.get("")
    def search(
        request: Request,
        q: Annotated[str, Query(max_length=200)] = "",
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> dict:
        query = _query(q)
        try:
            with Session(engine) as session:
                owner = _authenticated(session, request)
                sources = session.execute(_sources(owner.token_hash, query).limit(limit + 1)).all()
                findings = session.scalars(
                    _findings(owner.token_hash, query).limit(limit + 1)
                ).all()
                items = [_source_item(row) for row in sources]
                items.extend(item for row in findings if (item := _finding_item(row)) is not None)
                items.sort(key=lambda item: (item["title"].casefold(), item["type"], item["id"]))
                return {
                    "items": items[:limit],
                    "truncated": len(items) > limit,
                    "coverage": _scope(
                        source_snapshots_considered=len(sources),
                        findings_considered=len(findings),
                        limit=limit,
                    ),
                }
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "knowledge_storage_unavailable") from None

    @router.get("/graph")
    def graph(request: Request, q: Annotated[str, Query(max_length=200)] = "") -> dict:
        query = _query(q)
        try:
            with Session(engine) as session:
                owner = _authenticated(session, request)
                return _graph(session, owner.token_hash, query)
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "knowledge_storage_unavailable") from None

    @router.get("/findings/{result_id}")
    def finding(result_id: uuid.UUID, request: Request) -> dict:
        try:
            with Session(engine) as session:
                owner = _authenticated(session, request)
                row = session.scalar(
                    _findings(owner.token_hash).where(EvidenceResult.id == result_id)
                )
                item = _finding_item(row) if row is not None else None
                if item is None:
                    raise _PrivateHTTPException(404, "knowledge_finding_not_found")
                return item
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "knowledge_storage_unavailable") from None

    return router
