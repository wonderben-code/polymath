"""Durable evidence integrity, not scientific truth or source authenticity.

Documents enter through a trusted retrieval-tool boundary. A final model finding
cannot invent that boundary: every source in a saved result must already have a
durable read link to the same run. Writes require a current fenced worker lease.
Read methods are internal; callers must authorize run ownership separately.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC

from pydantic import ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymath.evidence_models import (
    EvidenceQuote,
    EvidenceResult,
    RunEvidenceSource,
    SourceRetrieval,
    SourceVersion,
)
from polymath.jobs import JobLease, _owned
from polymath.memory_models import ResearchMemoryRead
from polymath.research.models import (
    AnswerFinding,
    BridgeFinding,
    InsufficientFinding,
    ResearchResult,
    SourceDocument,
)

MAX_RUN_RETRIEVALS = 200
MAX_RESULT_BYTES = 262_144


class EvidenceError(Exception):
    """Static error code; never includes raw papers, model output, or secrets."""


@dataclass(frozen=True)
class SourceArtifact:
    version_id: uuid.UUID
    retrieval_id: uuid.UUID
    source_id: str
    content_sha256: str
    content_kind: str

    @property
    def artifact_ref(self) -> str:
        return f"source:{self.version_id}:{self.retrieval_id}"


@dataclass(frozen=True)
class StoredResult:
    id: uuid.UUID
    run_id: uuid.UUID
    status: str
    payload: dict

    @property
    def artifact_ref(self) -> str:
        return f"result:{self.id}"


def _canonical(value: dict) -> str:
    try:
        encoded = json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        encoded.encode("utf-8")
        # PostgreSQL JSONB rejects NUL even though JSON can represent it. Check
        # recursively so a literal backslash-u sequence remains valid text.
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str) and "\x00" in item:
                raise ValueError
            if isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, (list, tuple)):
                pending.extend(item)
        return encoded
    except (TypeError, ValueError, UnicodeError):
        raise EvidenceError("invalid_evidence_payload") from None


def _hash(value: str) -> str:
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise EvidenceError("invalid_evidence_text") from None


def _validate_document(document: SourceDocument) -> SourceDocument:
    if not isinstance(document, SourceDocument):
        raise EvidenceError("invalid_source_document")
    try:
        document = SourceDocument.model_validate_json(document.model_dump_json())
        _hash(document.text)
        textual = (
            document.source_id
            + document.text
            + document.title
            + document.url
            + document.extraction_method
        )
        _hash(textual)
        if "\x00" in textual:
            raise ValueError
        return document
    except (ValueError, TypeError, ValidationError):
        raise EvidenceError("invalid_source_document") from None


def _metadata(document: SourceDocument) -> dict:
    return {
        "title": document.title,
        "url": document.url,
        "retrieved_at": document.retrieved_at.astimezone(UTC).isoformat(),
        "extraction_method": document.extraction_method,
    }


def _artifact(version: SourceVersion, retrieval: SourceRetrieval) -> SourceArtifact:
    return SourceArtifact(
        version.id, retrieval.id, version.source_id, version.content_sha256, version.content_kind
    )


def _document(version: SourceVersion, retrieval: SourceRetrieval) -> SourceDocument:
    return SourceDocument(
        source_id=version.source_id,
        title=retrieval.title,
        url=retrieval.url,
        text=version.source_text,
        content_kind=version.content_kind,
        retrieved_at=retrieval.retrieved_at,
        extraction_method=retrieval.extraction_method,
    )


def _bound_sources(session: Session, run_id: uuid.UUID):
    return (
        select(SourceVersion, SourceRetrieval)
        .join(SourceRetrieval, SourceRetrieval.version_id == SourceVersion.id)
        .join(RunEvidenceSource, RunEvidenceSource.retrieval_id == SourceRetrieval.id)
        .where(RunEvidenceSource.run_id == run_id)
    )


class EvidenceStore:
    def __init__(self, engine: Engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("EvidenceStore requires PostgreSQL transaction semantics")
        self.engine = engine

    def save_source(self, lease: JobLease, document: SourceDocument) -> SourceArtifact:
        document = _validate_document(document)
        metadata = _metadata(document)
        metadata_hash = _hash(_canonical(metadata))
        with Session(self.engine) as session, session.begin():
            _owned(session, lease)
            version_id = uuid.uuid4()
            session.execute(
                insert(SourceVersion)
                .values(
                    id=version_id,
                    source_id=document.source_id,
                    content_sha256=document.content_sha256,
                    content_kind=document.content_kind,
                    source_text=document.text,
                )
                .on_conflict_do_nothing(constraint="uq_source_version")
            )
            version = session.scalar(
                select(SourceVersion).where(
                    SourceVersion.source_id == document.source_id,
                    SourceVersion.content_sha256 == document.content_sha256,
                    SourceVersion.content_kind == document.content_kind,
                )
            )
            if version.source_text != document.text:
                raise EvidenceError("source_hash_conflict")
            session.execute(
                insert(SourceRetrieval)
                .values(
                    id=uuid.uuid4(),
                    version_id=version.id,
                    metadata_sha256=metadata_hash,
                    title=document.title,
                    url=document.url,
                    retrieved_at=document.retrieved_at,
                    extraction_method=document.extraction_method,
                )
                .on_conflict_do_nothing(constraint="uq_source_retrieval")
            )
            retrieval = session.scalar(
                select(SourceRetrieval).where(
                    SourceRetrieval.version_id == version.id,
                    SourceRetrieval.metadata_sha256 == metadata_hash,
                )
            )
            if session.get(RunEvidenceSource, (lease.run_id, retrieval.id)) is None:
                count = session.scalar(
                    select(func.count())
                    .select_from(RunEvidenceSource)
                    .where(RunEvidenceSource.run_id == lease.run_id)
                )
                if count >= MAX_RUN_RETRIEVALS:
                    raise EvidenceError("run_source_limit")
                session.add(RunEvidenceSource(run_id=lease.run_id, retrieval_id=retrieval.id))
            return _artifact(version, retrieval)

    def get_document(self, run_id: uuid.UUID, retrieval_id: uuid.UUID) -> SourceDocument:
        with Session(self.engine) as session:
            row = session.execute(
                _bound_sources(session, run_id).where(SourceRetrieval.id == retrieval_id)
            ).first()
            if row is None:
                raise EvidenceError("source_not_found")
            return _document(*row)

    def list_sources(self, run_id: uuid.UUID) -> tuple[SourceArtifact, ...]:
        with Session(self.engine) as session:
            rows = session.execute(
                _bound_sources(session, run_id).order_by(
                    SourceRetrieval.retrieved_at, SourceRetrieval.id
                )
            ).all()
            return tuple(_artifact(*row) for row in rows)

    def save_result(self, lease: JobLease, result: ResearchResult) -> StoredResult:
        if not isinstance(result, ResearchResult):
            raise EvidenceError("invalid_research_result")
        try:
            result = ResearchResult.model_validate_json(result.model_dump_json())
        except (ValueError, TypeError, ValidationError):
            raise EvidenceError("invalid_research_result") from None
        self._validate_result_shape(result)
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            self._validate_memory_reads(session, run, result)
            if result.mode != run.mode:
                raise EvidenceError("result_mode_mismatch")
            if isinstance(result.finding, AnswerFinding) and (
                " ".join(result.finding.question.split()).casefold()
                != " ".join(run.goal.split()).casefold()
            ):
                raise EvidenceError("answer_question_mismatch")
            source_map = {}
            refs = []
            for document in result.sources:
                document = _validate_document(document)
                metadata_hash = _hash(_canonical(_metadata(document)))
                row = session.execute(
                    _bound_sources(session, lease.run_id).where(
                        SourceVersion.source_id == document.source_id,
                        SourceVersion.content_sha256 == document.content_sha256,
                        SourceVersion.content_kind == document.content_kind,
                        SourceRetrieval.metadata_sha256 == metadata_hash,
                    )
                ).first()
                if row is None:
                    raise EvidenceError("source_not_observed")
                version, retrieval = row
                if document.source_id in source_map:
                    raise EvidenceError("ambiguous_source_version")
                source_map[document.source_id] = (version, retrieval)
                refs.append({"version_id": str(version.id), "retrieval_id": str(retrieval.id)})
            quote_rows = self._validate_quotes(result, source_map)
            payload = result.model_dump(mode="json")
            payload["sources"] = []
            envelope = {"result": payload, "source_artifacts": refs}
            serialized = _canonical(envelope)
            if len(serialized.encode("utf-8")) > MAX_RESULT_BYTES:
                raise EvidenceError("result_artifact_too_large")
            digest = _hash(serialized)
            prior = session.scalar(
                select(EvidenceResult).where(
                    EvidenceResult.run_id == lease.run_id, EvidenceResult.content_sha256 == digest
                )
            )
            if prior is not None:
                return self._stored_result(session, prior)
            stored = EvidenceResult(
                run_id=lease.run_id, content_sha256=digest, status=result.status, payload=envelope
            )
            session.add(stored)
            session.flush()
            for ordinal, (quote, version, retrieval) in enumerate(quote_rows):
                session.add(
                    EvidenceQuote(
                        result_id=stored.id,
                        ordinal=ordinal,
                        run_id=lease.run_id,
                        version_id=version.id,
                        retrieval_id=retrieval.id,
                        side=quote.side,
                        claim_id=quote.claim_id,
                        quote=quote.quote,
                        supports=quote.supports,
                        start_offset=quote.start,
                        end_offset=quote.end,
                    )
                )
            return self._stored_result(session, stored)

    @staticmethod
    def _validate_memory_reads(session: Session, run, result: ResearchResult) -> None:
        recorded = session.scalars(
            select(ResearchMemoryRead).where(ResearchMemoryRead.run_id == run.id)
        ).all()
        actual = {
            (str(row.parent_result_id), str(row.parent_run_id), row.parent_content_sha256)
            for row in recorded
        }
        declared = {
            (item.result_id, item.run_id, item.content_sha256) for item in result.memory_reads
        }
        if len(declared) != len(result.memory_reads) or declared != actual:
            raise EvidenceError("memory_read_provenance_mismatch")
        if not recorded:
            return
        # Do not trust a hash string stored by a caller. Re-bind every observed
        # artifact to the immutable parent row and an earlier investigation.
        from polymath.access_models import ResearchRunAccess

        owner = session.scalar(
            select(ResearchRunAccess.session_hash).where(ResearchRunAccess.run_id == run.id)
        )
        if owner is None:
            raise EvidenceError("memory_read_provenance_mismatch")
        parents = session.execute(
            select(EvidenceResult, ResearchRunAccess.session_hash)
            .join(ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id)
            .where(EvidenceResult.id.in_([row.parent_result_id for row in recorded]))
        ).all()
        bound = {
            (str(parent.id), str(parent.run_id), parent.content_sha256)
            for parent, parent_owner in parents
            if parent_owner == owner
            and parent.run_id != run.id
            and parent.created_at < run.created_at
            and parent.status in ("bridge", "answer")
        }
        if bound != actual:
            raise EvidenceError("memory_read_provenance_mismatch")

    @staticmethod
    def _validate_result_shape(result: ResearchResult) -> None:
        if len(result.sources) > MAX_RUN_RETRIEVALS or len(result.events) > 1000:
            raise EvidenceError("result_artifact_limit")
        if result.error_code is not None and not re.fullmatch(r"[a-z_]{1,100}", result.error_code):
            raise EvidenceError("invalid_result_error")
        if any(value < 0 for value in result.usage.model_dump().values()):
            raise EvidenceError("invalid_result_usage")
        if any(
            event.sequence != index + 1 or len(event.summary) > 2000 or len(event.kind) > 64
            for index, event in enumerate(result.events)
        ):
            raise EvidenceError("invalid_result_events")
        if result.status == "bridge":
            if not isinstance(result.finding, BridgeFinding) or result.mode == "ask":
                raise EvidenceError("invalid_result_finding")
        elif result.status == "answer":
            if not isinstance(result.finding, AnswerFinding) or result.mode != "ask":
                raise EvidenceError("invalid_result_finding")
        elif result.status == "insufficient_evidence":
            if not isinstance(result.finding, InsufficientFinding) or result.quotes:
                raise EvidenceError("invalid_result_finding")
        elif result.finding is not None or result.quotes:
            raise EvidenceError("invalid_result_finding")
        if result.mode != "surprise" and result.field_selections:
            raise EvidenceError("unexpected_field_selection")
        if len(result.field_selections) > 3:
            raise EvidenceError("field_selection_limit")
        if result.mode == "surprise" and result.status == "bridge":
            if not result.field_selections:
                raise EvidenceError("missing_field_selection")
            selected = {
                " ".join(field.split()).casefold() for field in result.field_selections[-1].fields
            }
            if not {
                " ".join(result.finding.field_left.split()).casefold(),
                " ".join(result.finding.field_right.split()).casefold(),
            }.issubset(selected):
                raise EvidenceError("bridge_fields_not_selected")

    @staticmethod
    def _validate_quotes(result: ResearchResult, sources: dict):
        if result.status not in {"bridge", "answer"}:
            return []
        if isinstance(result.finding, AnswerFinding):
            expected = [
                (evidence, "answer", claim.id)
                for claim in result.finding.claims
                for evidence in claim.evidence
            ]
        else:
            expected = [(evidence, evidence.side, None) for evidence in result.finding.evidence]
        if len(result.quotes) != len(expected):
            raise EvidenceError("missing_verified_quotes")
        rows = []
        sides = {"left": set(), "right": set()}
        claim_sources: dict[str, set[str]] = {}
        for (evidence, side, claim_id), quote in zip(expected, result.quotes, strict=True):
            source = sources.get(quote.source_id)
            if source is None:
                raise EvidenceError("quote_source_not_observed")
            version, retrieval = source
            if (
                (quote.source_id, quote.side, quote.claim_id, quote.quote, quote.supports)
                != (evidence.source_id, side, claim_id, evidence.quote, evidence.supports)
                or quote.source_sha256 != version.content_sha256
                or quote.content_kind != version.content_kind
                or type(quote.start) is not int
                or type(quote.end) is not int
                or not 0 <= quote.start < quote.end <= len(version.source_text)
                or version.source_text[quote.start : quote.end] != quote.quote
            ):
                raise EvidenceError("quote_integrity_failed")
            if side == "answer":
                claim_sources.setdefault(claim_id, set()).add(retrieval.url)
            else:
                sides[side].add(retrieval.url)
            rows.append((quote, version, retrieval))
        if result.status == "answer":
            if any(
                claim.label == "postulated" and len(claim_sources.get(claim.id, ())) < 2
                for claim in result.finding.claims
            ):
                raise EvidenceError("postulated_distinct_sources_required")
        elif (
            not sides["left"]
            or not sides["right"]
            or not any(left != right for left in sides["left"] for right in sides["right"])
        ):
            raise EvidenceError("distinct_evidence_sources_required")
        return rows

    @staticmethod
    def _stored_result(session: Session, stored: EvidenceResult) -> StoredResult:
        envelope = json.loads(json.dumps(stored.payload))
        payload = envelope["result"]
        payload["sources"] = []
        for ref in envelope["source_artifacts"]:
            row = session.execute(
                _bound_sources(session, stored.run_id).where(
                    SourceRetrieval.id == uuid.UUID(ref["retrieval_id"]),
                    SourceVersion.id == uuid.UUID(ref["version_id"]),
                )
            ).first()
            if row is None:
                raise EvidenceError("stored_source_missing")
            payload["sources"].append(_document(*row).model_dump(mode="json"))
        return StoredResult(stored.id, stored.run_id, stored.status, payload)

    def get_result(self, run_id: uuid.UUID, result_id: uuid.UUID) -> StoredResult:
        with Session(self.engine) as session:
            stored = session.scalar(
                select(EvidenceResult).where(
                    EvidenceResult.id == result_id, EvidenceResult.run_id == run_id
                )
            )
            if stored is None:
                raise EvidenceError("result_not_found")
            return self._stored_result(session, stored)

    def list_results(self, run_id: uuid.UUID) -> tuple[StoredResult, ...]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(EvidenceResult)
                .where(EvidenceResult.run_id == run_id)
                .order_by(EvidenceResult.created_at, EvidenceResult.id)
            ).all()
            return tuple(self._stored_result(session, row) for row in rows)
