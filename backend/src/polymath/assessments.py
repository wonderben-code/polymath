"""Owned immutable reassessments with exact version citations and atomic head CAS.

Source reads use retrieval handles, allowing two versions of the same paper to
be compared without presenting them as independent papers. A generated positive
candidate becomes eligible only when its assessment wins the head revision CAS.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy import Engine, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.assessment_eligibility import (
    EligibilityError,
    completed_result_condition,
    result_assessment_state,
)
from polymath.assessment_models import (
    AssessmentInputSource,
    AssessmentRequest,
    FindingAssessment,
    FindingHead,
)
from polymath.evidence import (
    EvidenceError,
    EvidenceStore,
    _canonical,
    _document,
    _hash,
    _metadata,
)
from polymath.evidence_models import (
    EvidenceQuote,
    EvidenceResult,
    RunEvidenceSource,
    SourceRetrieval,
    SourceVersion,
)
from polymath.ingestion_models import LiteratureWatch, WatchSeenSourceVersion
from polymath.jobs import JobLease, _clear_lease, _event, _now, _owned
from polymath.memory_models import ResearchMemoryRead
from polymath.models import ResearchRun
from polymath.research.models import MemoryReadReference, ResearchResult, SourceDocument

if TYPE_CHECKING:
    from polymath.scientific_context_contracts import AskReassessmentOverviewIntent


MAX_ASSESSMENT_BYTES = 262_144


class AssessmentError(Exception):
    """Static private failure, never raw source/model output or foreign IDs."""


def _id(value) -> uuid.UUID:
    try:
        parsed = uuid.UUID(str(value))
        if str(parsed) != str(value):
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise AssessmentError("assessment_not_found") from None


def _owner(session: Session, run_id: uuid.UUID) -> str:
    owner = session.scalar(
        select(ResearchRunAccess.session_hash)
        .join(BrowserSession, BrowserSession.token_hash == ResearchRunAccess.session_hash)
        .where(
            ResearchRunAccess.run_id == run_id,
            BrowserSession.expires_at > func.clock_timestamp(),
        )
    )
    if owner is None:
        raise AssessmentError("assessment_not_found")
    return owner


def _owned_result(session: Session, owner: str, result_id) -> EvidenceResult:
    row = session.scalar(
        select(EvidenceResult)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id)
        .join(ResearchRun, ResearchRun.id == EvidenceResult.run_id)
        .join(BrowserSession, BrowserSession.token_hash == ResearchRunAccess.session_hash)
        .where(
            EvidenceResult.id == _id(result_id),
            EvidenceResult.status.in_(("bridge", "answer")),
            completed_result_condition(),
            ResearchRun.task_kind.in_(("research", "reassess_finding")),
            ResearchRunAccess.session_hash == owner,
            BrowserSession.expires_at > func.clock_timestamp(),
        )
    )
    if row is None or _hash(_canonical(row.payload)) != row.content_sha256:
        raise AssessmentError("assessment_not_found")
    return row


def assessment_source(session: Session, owner_hash: str, source_result_id) -> EvidenceResult:
    """Allow the current historical baseline even after an unsupported assessment."""
    row = _owned_result(session, owner_hash, source_result_id)
    try:
        state = result_assessment_state(session, row.id)
    except EligibilityError:
        raise AssessmentError("assessment_not_found") from None
    if not state["is_current"]:
        raise AssessmentError("assessment_source_not_current")
    return row


def _checked_document(version: SourceVersion, retrieval: SourceRetrieval) -> SourceDocument:
    document = _document(version, retrieval)
    if (
        document.content_sha256 != version.content_sha256
        or _hash(_canonical(_metadata(document))) != retrieval.metadata_sha256
    ):
        raise AssessmentError("assessment_input_integrity_failed")
    return document


def _basis_for_source(session: Session, source: EvidenceResult, state: dict, new_rows):
    from polymath.assessment_contracts import AssessmentBasis, AssessmentSourceReference

    result = ResearchResult.model_validate_json(
        json.dumps(EvidenceStore._stored_result(session, source).payload)
    )
    historical = session.execute(
        select(EvidenceQuote, SourceVersion, SourceRetrieval)
        .join(SourceVersion, SourceVersion.id == EvidenceQuote.version_id)
        .join(SourceRetrieval, SourceRetrieval.id == EvidenceQuote.retrieval_id)
        .where(EvidenceQuote.result_id == source.id, EvidenceQuote.run_id == source.run_id)
        .order_by(EvidenceQuote.ordinal)
        .limit(65)
    ).all()
    if not 1 <= len(historical) <= 64 or len(historical) != len(result.quotes):
        raise AssessmentError("assessment_input_integrity_failed")
    rows = {}
    for ordinal, ((quote, version, retrieval), declared) in enumerate(
        zip(historical, result.quotes, strict=True)
    ):
        document = _checked_document(version, retrieval)
        if (
            quote.ordinal != ordinal
            or (
                quote.side,
                quote.claim_id,
                quote.quote,
                quote.supports,
                quote.start_offset,
                quote.end_offset,
                version.source_id,
                version.content_sha256,
                version.content_kind,
            )
            != (
                declared.side,
                declared.claim_id,
                declared.quote,
                declared.supports,
                declared.start,
                declared.end,
                declared.source_id,
                declared.source_sha256,
                declared.content_kind,
            )
            or document.text[quote.start_offset : quote.end_offset] != quote.quote
        ):
            raise AssessmentError("assessment_input_integrity_failed")
        rows[retrieval.id] = (version, retrieval, "historical")
    for version, retrieval in new_rows:
        if retrieval.id in rows or any(old[0].id == version.id for old in rows.values()):
            raise AssessmentError("assessment_source_already_cited")
        _checked_document(version, retrieval)
        rows[retrieval.id] = (version, retrieval, "new")
    references = tuple(
        AssessmentSourceReference(
            retrieval_id=str(retrieval.id),
            version_id=str(version.id),
            source_id=version.source_id,
            title=retrieval.title,
            url=retrieval.url,
            bibliography=_document(version, retrieval).bibliography,
            coverage=_document(version, retrieval).coverage,
            content_sha256=version.content_sha256,
            content_kind=version.content_kind,
            role=role,
        )
        for version, retrieval, role in rows.values()
    )
    basis = AssessmentBasis(
        prior_reference=MemoryReadReference(
            result_id=str(source.id),
            run_id=str(source.run_id),
            content_sha256=source.content_sha256,
        ),
        mode=result.mode,
        prior_finding=result.finding,
        prior_quotes=result.quotes,
        prior_state=state["state"],
        field_selections=result.field_selections,
        sources=references,
        **(
            {"prior_overview_binding": result.overview_binding}
            if result.overview_binding is not None
            else {}
        ),
    )
    payload = basis.model_dump(mode="json")
    if len(_canonical(payload).encode("utf-8")) > MAX_ASSESSMENT_BYTES:
        raise AssessmentError("assessment_input_too_large")
    return basis, tuple(rows.values())


def bind_request(
    session: Session,
    run: ResearchRun,
    owner_hash: str,
    source_result_id,
    watch_id,
    new_source_version_ids,
    expected_head_revision: int,
    *,
    assessment_overview_intent: AskReassessmentOverviewIntent | None = None,
) -> AssessmentRequest:
    if run.task_kind != "reassess_finding" or _owner(session, run.id) != owner_hash:
        raise AssessmentError("assessment_not_found")
    source = assessment_source(session, owner_hash, source_result_id)
    state = result_assessment_state(session, source.id)
    if type(expected_head_revision) is not int or expected_head_revision != state["revision"]:
        raise AssessmentError("assessment_head_changed")
    source_run = session.get(ResearchRun, source.run_id)
    if source.created_at >= run.created_at or (run.mode, run.goal) != (
        source_run.mode,
        source_run.goal,
    ):
        raise AssessmentError("assessment_input_integrity_failed")
    identifiers = tuple(_id(value) for value in new_source_version_ids)
    if not 1 <= len(identifiers) <= 5 or len(set(identifiers)) != len(identifiers):
        raise AssessmentError("assessment_invalid_sources")
    watch = session.scalar(
        select(LiteratureWatch).where(
            LiteratureWatch.id == _id(watch_id),
            LiteratureWatch.owner_session_hash == owner_hash,
            LiteratureWatch.expires_at > func.clock_timestamp(),
        )
    )
    if watch is None:
        raise AssessmentError("assessment_not_found")
    if watch.source_result_id is not None:
        anchor = _owned_result(session, owner_hash, watch.source_result_id)
        if result_assessment_state(session, anchor.id)["root_result_id"] != state["root_result_id"]:
            raise AssessmentError("assessment_not_found")
    # Applied complete assessments consume pending versions for this root only.
    consumed = set(
        session.scalars(
            select(AssessmentInputSource.version_id)
            .join(AssessmentRequest, AssessmentRequest.run_id == AssessmentInputSource.run_id)
            .join(FindingAssessment, FindingAssessment.run_id == AssessmentRequest.run_id)
            .where(
                AssessmentRequest.root_result_id == _id(state["root_result_id"]),
                AssessmentInputSource.role == "new",
                FindingAssessment.applied.is_(True),
                AssessmentInputSource.version_id.in_(identifiers),
            )
        ).all()
    )
    if consumed:
        raise AssessmentError("assessment_source_already_assessed")
    original_versions = set(
        session.scalars(
            select(EvidenceQuote.version_id).where(
                EvidenceQuote.result_id == _id(state["root_result_id"]),
                EvidenceQuote.version_id.in_(identifiers),
            )
        ).all()
    )
    if original_versions:
        raise AssessmentError("assessment_source_already_cited")
    found = session.execute(
        select(SourceVersion, SourceRetrieval)
        .join(WatchSeenSourceVersion, WatchSeenSourceVersion.version_id == SourceVersion.id)
        .join(SourceRetrieval, SourceRetrieval.id == WatchSeenSourceVersion.retrieval_id)
        .where(
            WatchSeenSourceVersion.watch_id == watch.id,
            SourceVersion.id.in_(identifiers),
        )
    ).all()
    indexed = {version.id: (version, retrieval) for version, retrieval in found}
    if set(indexed) != set(identifiers):
        raise AssessmentError("assessment_not_found")
    basis, inputs = _basis_for_source(session, source, state, [indexed[i] for i in identifiers])
    payload = basis.model_dump(mode="json")
    request = AssessmentRequest(
        run_id=run.id,
        root_result_id=_id(state["root_result_id"]),
        source_result_id=source.id,
        source_sha256=source.content_sha256,
        watch_id=watch.id,
        expected_head_revision=expected_head_revision,
        basis=payload,
        basis_sha256=_hash(_canonical(payload)),
    )
    session.add(request)
    session.flush()
    for ordinal, (version, retrieval, role) in enumerate(inputs):
        session.add(
            AssessmentInputSource(
                run_id=run.id,
                ordinal=ordinal,
                version_id=version.id,
                retrieval_id=retrieval.id,
                role=role,
            )
        )
    session.flush()
    from polymath.scientific_context_store import (
        ContextStoreError,
        bind_assessment_overview_intent,
    )

    try:
        bind_assessment_overview_intent(
            session,
            run=run,
            owner_hash=owner_hash,
            intent=assessment_overview_intent,
        )
    except ContextStoreError as error:
        raise AssessmentError(getattr(error, "code", str(error))) from None
    return request


@dataclass(frozen=True)
class StoredAssessment:
    id: uuid.UUID
    run_id: uuid.UUID
    root_result_id: uuid.UUID
    source_result_id: uuid.UUID
    source_run_id: uuid.UUID
    source_sha256: str
    watch_id: uuid.UUID
    new_source_version_ids: tuple[str, ...]
    expected_head_revision: int
    disposition: str
    applied: bool
    head_revision: int
    candidate_result_id: uuid.UUID | None
    payload: dict
    payload_sha256: str
    basis_sha256: str
    created_at: datetime

    @property
    def artifact_ref(self) -> str:
        return f"assessment:{self.id}"

    def to_response(self) -> dict:
        return {
            "id": str(self.id),
            "run_id": str(self.run_id),
            "root_result_id": str(self.root_result_id),
            "source_result_id": str(self.source_result_id),
            "source_run_id": str(self.source_run_id),
            "source_sha256": self.source_sha256,
            "watch_id": str(self.watch_id),
            "new_source_version_ids": list(self.new_source_version_ids),
            "expected_head_revision": self.expected_head_revision,
            "disposition": self.disposition,
            "applied": self.applied,
            "head_revision": self.head_revision,
            "candidate_result_id": (
                str(self.candidate_result_id) if self.candidate_result_id else None
            ),
            "payload": self.payload,
            "payload_sha256": self.payload_sha256,
            "basis_sha256": self.basis_sha256,
            "created_at": self.created_at.isoformat(),
        }


def _basis(session: Session, run: ResearchRun):
    from polymath.assessment_contracts import AssessmentBasis

    request = session.get(AssessmentRequest, run.id)
    owner = _owner(session, run.id)
    if request is None or run.task_kind != "reassess_finding":
        raise AssessmentError("assessment_not_found")
    source = _owned_result(session, owner, request.source_result_id)
    if (
        source.content_sha256 != request.source_sha256
        or source.created_at >= run.created_at
        or _hash(_canonical(request.basis)) != request.basis_sha256
        or len(_canonical(request.basis).encode("utf-8")) > MAX_ASSESSMENT_BYTES
    ):
        raise AssessmentError("assessment_input_integrity_failed")
    basis = AssessmentBasis.model_validate_json(json.dumps(request.basis))
    original = ResearchResult.model_validate_json(
        json.dumps(EvidenceStore._stored_result(session, source).payload)
    )
    if (
        basis.mode != original.mode
        or basis.prior_finding != original.finding
        or basis.prior_quotes != original.quotes
        or basis.field_selections != original.field_selections
        or basis.prior_overview_binding != original.overview_binding
    ):
        raise AssessmentError("assessment_input_integrity_failed")
    from polymath.scientific_context_store import ContextStoreError, load_scientific_intent

    try:
        intent = load_scientific_intent(session, run)
    except ContextStoreError:
        raise AssessmentError("assessment_input_integrity_failed") from None
    if original.overview_binding is not None and intent is None:
        raise AssessmentError("ask_reassessment_overview_required")
    if (
        basis.prior_reference.result_id,
        basis.prior_reference.run_id,
        basis.prior_reference.content_sha256,
    ) != (str(source.id), str(source.run_id), source.content_sha256):
        raise AssessmentError("assessment_input_integrity_failed")
    rows = session.execute(
        select(AssessmentInputSource, SourceVersion, SourceRetrieval)
        .join(SourceVersion, SourceVersion.id == AssessmentInputSource.version_id)
        .join(SourceRetrieval, SourceRetrieval.id == AssessmentInputSource.retrieval_id)
        .where(AssessmentInputSource.run_id == run.id)
        .order_by(AssessmentInputSource.ordinal)
    ).all()
    if len(rows) != len(basis.sources):
        raise AssessmentError("assessment_input_integrity_failed")
    for ordinal, ((link, version, retrieval), reference) in enumerate(
        zip(rows, basis.sources, strict=True)
    ):
        document = _checked_document(version, retrieval)
        if link.ordinal != ordinal or (
            str(version.id),
            str(retrieval.id),
            version.source_id,
            version.content_sha256,
            version.content_kind,
            retrieval.title,
            retrieval.url,
            link.role,
            document.bibliography,
            document.coverage,
        ) != (
            reference.version_id,
            reference.retrieval_id,
            reference.source_id,
            reference.content_sha256,
            reference.content_kind,
            reference.title,
            reference.url,
            reference.role,
            reference.bibliography,
            reference.coverage,
        ):
            raise AssessmentError("assessment_input_integrity_failed")
    return request, basis


def _observed(session: Session, run_id: uuid.UUID):
    from polymath.assessment_contracts import AssessmentSourceSnapshot

    rows = session.execute(
        select(SourceVersion, SourceRetrieval)
        .join(SourceRetrieval, SourceRetrieval.version_id == SourceVersion.id)
        .join(RunEvidenceSource, RunEvidenceSource.retrieval_id == SourceRetrieval.id)
        .where(RunEvidenceSource.run_id == run_id)
    ).all()
    return {
        str(retrieval.id): AssessmentSourceSnapshot(
            retrieval_id=str(retrieval.id),
            version_id=str(version.id),
            document=_checked_document(version, retrieval),
        )
        for version, retrieval in rows
    }


def _stored(session: Session, row: FindingAssessment) -> StoredAssessment:
    request = session.get(AssessmentRequest, row.run_id)
    if (
        request is None
        or _hash(_canonical(row.payload)) != row.payload_sha256
        or _hash(_canonical(request.basis)) != request.basis_sha256
        or row.payload.get("basis") != request.basis
    ):
        raise AssessmentError("assessment_integrity_failed")
    from polymath.scientific_context_store import ContextStoreError, load_scientific_intent

    try:
        intent = load_scientific_intent(session, session.get(ResearchRun, row.run_id))
    except ContextStoreError:
        raise AssessmentError("assessment_integrity_failed") from None
    if row.payload.get("assessment_overview_intent") != (
        intent.model_dump(mode="json") if intent is not None else None
    ) or ("assessment_overview_intent" in row.payload and intent is None):
        raise AssessmentError("assessment_integrity_failed")
    versions = tuple(
        str(value)
        for value in session.scalars(
            select(AssessmentInputSource.version_id)
            .where(AssessmentInputSource.run_id == row.run_id, AssessmentInputSource.role == "new")
            .order_by(AssessmentInputSource.ordinal)
        ).all()
    )
    return StoredAssessment(
        id=row.id,
        run_id=row.run_id,
        root_result_id=request.root_result_id,
        source_result_id=request.source_result_id,
        source_run_id=_id(request.basis["prior_reference"]["run_id"]),
        source_sha256=request.source_sha256,
        watch_id=request.watch_id,
        new_source_version_ids=versions,
        expected_head_revision=request.expected_head_revision,
        disposition=row.disposition,
        applied=row.applied,
        head_revision=row.head_revision,
        candidate_result_id=row.candidate_result_id,
        payload=row.payload,
        payload_sha256=row.payload_sha256,
        basis_sha256=request.basis_sha256,
        created_at=row.created_at,
    )


class AssessmentStore:
    def __init__(self, engine: Engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("AssessmentStore requires PostgreSQL")
        self.engine = engine

    def load_basis(self, lease: JobLease):
        try:
            with Session(self.engine) as session, session.begin():
                run = _owned(session, lease)
                _, basis = _basis(session, run)
                reference = basis.prior_reference
                if session.get(ResearchMemoryRead, (run.id, _id(reference.result_id))) is None:
                    session.add(
                        ResearchMemoryRead(
                            run_id=run.id,
                            parent_result_id=_id(reference.result_id),
                            parent_run_id=_id(reference.run_id),
                            parent_content_sha256=reference.content_sha256,
                        )
                    )
                return basis
        except (ValueError, TypeError, KeyError, ValidationError, EvidenceError):
            raise AssessmentError("assessment_input_integrity_failed") from None

    def read_source(self, lease: JobLease, retrieval_id) -> SourceDocument:
        retrieval_id = _id(retrieval_id)
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            _, basis = _basis(session, run)
            if str(retrieval_id) not in {ref.retrieval_id for ref in basis.sources}:
                raise AssessmentError("assessment_source_not_found")
            row = session.execute(
                select(SourceVersion, SourceRetrieval)
                .join(SourceRetrieval, SourceRetrieval.version_id == SourceVersion.id)
                .where(SourceRetrieval.id == retrieval_id)
            ).first()
            if row is None:
                raise AssessmentError("assessment_source_not_found")
            document = _checked_document(*row)
        # save_source owns its own fenced transaction: never nest it under a run lock.
        saved = EvidenceStore(self.engine).save_source(lease, document)
        if saved.retrieval_id != retrieval_id:
            raise AssessmentError("assessment_input_integrity_failed")
        return document

    def save(self, lease: JobLease, result) -> StoredAssessment:
        from polymath.assessment_contracts import AssessmentResult, validate_assessment_result
        from polymath.full_text import owned_identity_context

        try:
            result = AssessmentResult.model_validate_json(result.model_dump_json())
            with Session(self.engine) as session, session.begin():
                run = _owned(session, lease)
                request, basis = _basis(session, run)
                from polymath.scientific_context_store import load_scientific_intent

                intent = load_scientific_intent(session, run)
                validate_assessment_result(
                    result,
                    basis,
                    _observed(session, run.id),
                    alias_context=owned_identity_context(session, run.id),
                    expected_overview=intent is not None,
                )
                if set(result.search_purposes) != set(
                    run.checkpoint.get("assessment_search_purposes", [])
                ):
                    raise AssessmentError("assessment_search_provenance_mismatch")
                root_id = request.root_result_id
            candidate = None
            if result.revised_result is not None:
                candidate = EvidenceStore(self.engine).save_result(lease, result.revised_result)
            with Session(self.engine) as session, session.begin():
                # Canonical lock order is Head then Run. No head locks are acquired
                # by load_basis, normal reads, or the earlier candidate transaction.
                session.execute(
                    insert(FindingHead)
                    .values(
                        root_result_id=root_id,
                        current_result_id=root_id,
                        state="supported",
                        revision=0,
                    )
                    .on_conflict_do_nothing(index_elements=[FindingHead.root_result_id])
                )
                head = session.scalar(
                    select(FindingHead)
                    .where(FindingHead.root_result_id == root_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                run = _owned(session, lease)
                request, basis = _basis(session, run)
                intent = load_scientific_intent(session, run)
                observed = _observed(session, run.id)
                validate_assessment_result(
                    result,
                    basis,
                    observed,
                    alias_context=owned_identity_context(session, run.id),
                    expected_overview=intent is not None,
                )
                if set(result.search_purposes) != set(
                    run.checkpoint.get("assessment_search_purposes", [])
                ):
                    raise AssessmentError("assessment_search_provenance_mismatch")
                existing = session.scalar(
                    select(FindingAssessment).where(FindingAssessment.run_id == run.id)
                )
                if existing is not None:
                    raise AssessmentError("assessment_version_conflict")
                applied = (
                    result.status == "completed"
                    and result.disposition != "inconclusive"
                    and head.revision == request.expected_head_revision
                    and head.current_result_id == request.source_result_id
                )
                payload = {
                    "basis": basis.model_dump(mode="json"),
                    "result": result.model_dump(mode="json"),
                    "verified_passages": [quote.model_dump(mode="json") for quote in result.quotes],
                    "observed_sources": [
                        {
                            "retrieval_id": snapshot.retrieval_id,
                            "version_id": snapshot.version_id,
                            "source_id": snapshot.document.source_id,
                            "title": snapshot.document.title,
                            "url": snapshot.document.url,
                            "bibliography": (
                                snapshot.document.bibliography.model_dump(mode="json")
                                if snapshot.document.bibliography
                                else None
                            ),
                            "content_sha256": snapshot.document.content_sha256,
                            "content_kind": snapshot.document.content_kind,
                            **(
                                {"coverage": snapshot.document.coverage.model_dump(mode="json")}
                                if snapshot.document.coverage is not None
                                else {}
                            ),
                        }
                        for snapshot in sorted(
                            observed.values(), key=lambda item: item.retrieval_id
                        )
                    ],
                }
                if intent is not None:
                    payload["assessment_overview_intent"] = intent.model_dump(mode="json")
                # Documents remain in immutable evidence snapshots, not copied into
                # the bounded public conclusion. Exact selected retrievals are retained.
                if payload["result"].get("revised_result") is not None:
                    payload["result"]["revised_result"]["sources"] = []
                encoded = _canonical(payload)
                if len(encoded.encode("utf-8")) > MAX_ASSESSMENT_BYTES:
                    raise AssessmentError("assessment_result_too_large")
                stored = FindingAssessment(
                    run_id=run.id,
                    disposition=result.disposition,
                    applied=applied,
                    head_revision=head.revision + int(applied),
                    candidate_result_id=candidate.id if candidate else None,
                    payload=payload,
                    payload_sha256=_hash(encoded),
                )
                session.add(stored)
                session.flush()
                commit_now = _now(session)
                if applied:
                    head.revision += 1
                    head.last_assessment_id = stored.id
                    head.updated_at = commit_now
                    if result.disposition == "revised":
                        head.current_result_id = candidate.id
                        head.state = "supported"
                    elif result.disposition == "unsupported":
                        head.state = "unsupported"
                    # unchanged preserves an unsupported head; only a revised
                    # independently validated candidate can restore support.
                run.status = "failed" if result.status == "failed" else "succeeded"
                run.outcome = {
                    "assessment_id": str(stored.id),
                    "disposition": result.disposition,
                    "applied": applied,
                    "head_revision": stored.head_revision,
                    "candidate_result_id": str(candidate.id) if candidate else None,
                    "reason": result.reason,
                    "error_code": result.error_code,
                }
                run.evidence_refs = list(run.evidence_refs) + [f"assessment:{stored.id}"]
                if candidate is not None:
                    run.evidence_refs += [candidate.artifact_ref]
                run.finished_at = commit_now
                run.updated_at = run.finished_at
                _clear_lease(run)
                _event(
                    session,
                    run,
                    "assessment_completed",
                    run.status,
                    "Finding reassessment saved"
                    if applied
                    else "Finding reassessment saved without changing the current finding",
                    [f"assessment:{stored.id}"],
                )
                session.flush()
                return _stored(session, stored)
        except (ValueError, TypeError, KeyError, AttributeError, ValidationError, EvidenceError):
            raise AssessmentError("assessment_result_integrity_failed") from None

    def head(self, owner_hash: str, result_id) -> dict:
        with Session(self.engine) as session:
            result = _owned_result(session, owner_hash, result_id)
            state = result_assessment_state(session, result.id)
            current = _owned_result(session, owner_hash, state["current_result_id"])
            return {
                **state,
                "requested_result_id": str(result.id),
                "current_run_id": str(current.run_id),
                "can_generate": state["is_supported"],
            }

    def get(self, owner_hash: str, assessment_id) -> StoredAssessment:
        with Session(self.engine) as session:
            row = session.scalar(
                select(FindingAssessment)
                .join(ResearchRunAccess, ResearchRunAccess.run_id == FindingAssessment.run_id)
                .join(BrowserSession, BrowserSession.token_hash == ResearchRunAccess.session_hash)
                .where(
                    FindingAssessment.id == _id(assessment_id),
                    ResearchRunAccess.session_hash == owner_hash,
                    BrowserSession.expires_at > func.clock_timestamp(),
                )
            )
            if row is None:
                raise AssessmentError("assessment_not_found")
            return _stored(session, row)

    def history(self, owner_hash: str, result_id, *, limit=20, before=None):
        if type(limit) is not int or not 1 <= limit <= 51:
            raise AssessmentError("assessment_invalid_limit")
        with Session(self.engine) as session:
            source = _owned_result(session, owner_hash, result_id)
            state = result_assessment_state(session, source.id)
            query = (
                select(FindingAssessment)
                .join(AssessmentRequest, AssessmentRequest.run_id == FindingAssessment.run_id)
                .join(ResearchRunAccess, ResearchRunAccess.run_id == FindingAssessment.run_id)
                .where(
                    AssessmentRequest.root_result_id == _id(state["root_result_id"]),
                    ResearchRunAccess.session_hash == owner_hash,
                )
            )
            if before is not None:
                anchor = session.scalar(
                    select(FindingAssessment)
                    .join(AssessmentRequest, AssessmentRequest.run_id == FindingAssessment.run_id)
                    .where(
                        FindingAssessment.id == _id(before),
                        AssessmentRequest.root_result_id == _id(state["root_result_id"]),
                    )
                )
                if anchor is None:
                    raise AssessmentError("assessment_not_found")
                query = query.where(
                    tuple_(FindingAssessment.created_at, FindingAssessment.id)
                    < tuple_(anchor.created_at, anchor.id)
                )
            rows = session.scalars(
                query.order_by(
                    FindingAssessment.created_at.desc(), FindingAssessment.id.desc()
                ).limit(limit)
            ).all()
            return tuple(_stored(session, row) for row in rows)
