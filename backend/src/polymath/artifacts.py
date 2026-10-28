"""Versioned model drafts, exact citations, and immutable export bytes.

This layer validates provenance, classifications, and bounded check integrity.
It does not establish that generated prose or mathematical encodings are true.
All source documents originate from the exact pinned finding's quoted versions.
"""

from __future__ import annotations

import html
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urlsplit

from pydantic import ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.artifact_contracts import (
    ArtifactBasis,
    ArtifactPassage,
    ArtifactResult,
    CitedText,
    DocumentDraft,
    FormalisationDraft,
    iter_cited_texts,
    validate_artifact_result,
)
from polymath.artifact_models import ArtifactRequest, GeneratedArtifact
from polymath.assessment_eligibility import (
    EligibilityError,
    current_supported_condition,
    require_current_supported,
)
from polymath.evidence import EvidenceError, EvidenceStore, _canonical, _document, _hash, _metadata
from polymath.evidence_models import (
    EvidenceQuote,
    EvidenceResult,
    RunEvidenceSource,
    SourceRetrieval,
    SourceVersion,
)
from polymath.jobs import JobLease, _owned
from polymath.math_checks import FiniteCheckResult
from polymath.models import ResearchRun
from polymath.research.models import (
    AnswerFinding,
    BridgeFinding,
    MemoryReadReference,
    SourceDocument,
)

KINDS = ("formalise", "write_summary", "write_paper")
MAX_ARTIFACT_BYTES = 262_144


class ArtifactError(Exception):
    """Static error code, never input, source text, draft content, or credentials."""


@dataclass(frozen=True)
class StoredArtifact:
    id: uuid.UUID
    run_id: uuid.UUID
    kind: str
    status: str
    payload: dict
    basis: dict
    payload_sha256: str
    basis_sha256: str
    markdown: str
    markdown_sha256: str
    created_at: datetime

    @property
    def artifact_ref(self) -> str:
        return f"artifact:{self.id}"

    def to_response(self) -> dict:
        value = {
            "id": str(self.id),
            "run_id": str(self.run_id),
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "source_result_id": self.basis["reference"]["result_id"],
            "source_run_id": self.basis["reference"]["run_id"],
            "source_sha256": self.basis["reference"]["content_sha256"],
            "formalisation_id": self.basis["formalisation_id"],
            "formalisation_checks": self.basis.get("formalisation_checks", []),
            "passages": self.basis["passages"],
            "payload": self.payload,
            "markdown": self.markdown,
            "markdown_sha256": self.markdown_sha256,
            "payload_sha256": self.payload_sha256,
            "basis_sha256": self.basis_sha256,
        }
        if "scientific_context" in self.basis:
            value["scientific_context"] = self.basis["scientific_context"]
        return value


def _identifier(value: str) -> uuid.UUID:
    try:
        identifier = uuid.UUID(value)
        if str(identifier) != value:
            raise ValueError
        return identifier
    except (ValueError, TypeError, AttributeError):
        raise ArtifactError("artifact_source_not_found") from None


def _owner(session: Session, run_id: uuid.UUID) -> str | None:
    return session.scalar(
        select(ResearchRunAccess.session_hash)
        .join(BrowserSession, BrowserSession.token_hash == ResearchRunAccess.session_hash)
        .where(
            ResearchRunAccess.run_id == run_id,
            BrowserSession.expires_at > func.clock_timestamp(),
        )
    )


def source_for_request(
    session: Session, owner_hash: str, source_result_id: str, kind: str
) -> EvidenceResult:
    if kind not in KINDS:
        raise ArtifactError("invalid_artifact_kind")
    identifier = _identifier(source_result_id)
    row = session.scalar(
        select(EvidenceResult)
        .join(ResearchRun, ResearchRun.id == EvidenceResult.run_id)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id)
        .join(BrowserSession, BrowserSession.token_hash == ResearchRunAccess.session_hash)
        .where(
            EvidenceResult.id == identifier,
            EvidenceResult.status.in_(("bridge", "answer")),
            current_supported_condition(),
            ResearchRun.task_kind.in_(("research", "reassess_finding")),
            ResearchRunAccess.session_hash == owner_hash,
            BrowserSession.expires_at > func.clock_timestamp(),
        )
    )
    if row is None or (kind == "formalise" and row.status != "bridge"):
        raise ArtifactError("artifact_source_not_found")
    try:
        require_current_supported(session, owner_hash, row.id)
    except EligibilityError:
        raise ArtifactError("artifact_source_not_found") from None
    return row


def _formalisation(
    session: Session,
    run: ResearchRun,
    owner_hash: str,
    source: EvidenceResult,
    identifier: uuid.UUID,
) -> GeneratedArtifact:
    row = session.scalar(
        select(GeneratedArtifact)
        .join(ResearchRun, ResearchRun.id == GeneratedArtifact.run_id)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == GeneratedArtifact.run_id)
        .where(
            GeneratedArtifact.id == identifier,
            GeneratedArtifact.kind == "formalise",
            GeneratedArtifact.status == "ready",
            GeneratedArtifact.created_at < run.created_at,
            ResearchRun.status == "succeeded",
            ResearchRunAccess.session_hash == owner_hash,
        )
    )
    if row is None:
        raise ArtifactError("artifact_source_not_found")
    stored = _stored(row)
    reference = stored.basis["reference"]
    if (
        reference["result_id"] != str(source.id)
        or reference["run_id"] != str(source.run_id)
        or reference["content_sha256"] != source.content_sha256
    ):
        raise ArtifactError("artifact_source_not_found")
    return row


def bind_request(
    session: Session,
    run: ResearchRun,
    owner_hash: str,
    kind: str,
    source_result_id: str,
    formalisation_id: str | None = None,
    *,
    scientific_context_intent=None,
) -> ArtifactRequest:
    """Pin exact input versions inside the caller's enqueue transaction."""
    if (
        run.status != "queued"
        or run.task_kind != kind
        or _owner(session, run.id) != owner_hash
        or session.get(ArtifactRequest, run.id) is not None
    ):
        raise ArtifactError("artifact_source_not_found")
    source = source_for_request(session, owner_hash, source_result_id, kind)
    source_run = session.get(ResearchRun, source.run_id)
    if (
        source.run_id == run.id
        or source.created_at >= run.created_at
        or source_run.mode != run.mode
    ):
        raise ArtifactError("artifact_source_not_found")
    formalisation = None
    if formalisation_id is not None:
        if kind == "formalise":
            raise ArtifactError("invalid_artifact_kind")
        formalisation = _formalisation(
            session, run, owner_hash, source, _identifier(formalisation_id)
        )
    request = ArtifactRequest(
        run_id=run.id,
        kind=kind,
        source_result_id=source.id,
        source_run_id=source.run_id,
        source_sha256=source.content_sha256,
        formalisation_id=formalisation.id if formalisation else None,
        formalisation_sha256=formalisation.payload_sha256 if formalisation else None,
    )
    session.add(request)
    session.flush()
    if scientific_context_intent is not None:
        from polymath.scientific_context_store import bind_artifact_context

        bind_artifact_context(
            session, run=run, owner_hash=owner_hash, intent=scientific_context_intent
        )
    # Reject an unusable input in the same transaction as enqueue. It must not
    # consume an active-job slot or reach the model before size/integrity checks.
    basis, _ = _context(session, run)
    if scientific_context_intent is not None:
        preflight_artifact_context(kind, basis)
    return request


def preflight_artifact_context(kind, basis, *, max_context_characters=90_000):
    """Measure the exact new initial messages before the enqueue transaction commits."""
    from polymath.artifact_controller import artifact_initial_messages

    if (
        basis.scientific_context is not None
        and sum(len(message["content"]) for message in artifact_initial_messages(kind, basis))
        > max_context_characters
    ):
        raise ArtifactError("artifact_context_too_large")


def _context_unchecked(
    session: Session, run: ResearchRun
) -> tuple[ArtifactBasis, dict[uuid.UUID, SourceDocument]]:
    request = session.get(ArtifactRequest, run.id)
    owner_hash = _owner(session, run.id)
    if request is None or owner_hash is None or request.kind != run.task_kind:
        raise ArtifactError("artifact_source_not_found")
    source = source_for_request(session, owner_hash, str(request.source_result_id), request.kind)
    if (
        source.run_id != request.source_run_id
        or source.content_sha256 != request.source_sha256
        or source.run_id == run.id
        or source.created_at >= run.created_at
        or _hash(_canonical(source.payload)) != source.content_sha256
    ):
        raise ArtifactError("artifact_input_integrity_failed")
    try:
        raw_finding = source.payload["result"]["finding"]
        finding_type = BridgeFinding if source.status == "bridge" else AnswerFinding
        finding = finding_type.model_validate_json(json.dumps(raw_finding))
        refs = {
            (uuid.UUID(ref["version_id"]), uuid.UUID(ref["retrieval_id"]))
            for ref in source.payload["source_artifacts"]
        }
    except (ValueError, KeyError, TypeError, ValidationError):
        raise ArtifactError("artifact_input_integrity_failed") from None
    rows = session.execute(
        select(EvidenceQuote, SourceVersion, SourceRetrieval)
        .join(SourceVersion, SourceVersion.id == EvidenceQuote.version_id)
        .join(SourceRetrieval, SourceRetrieval.id == EvidenceQuote.retrieval_id)
        .where(EvidenceQuote.result_id == source.id, EvidenceQuote.run_id == source.run_id)
        .order_by(EvidenceQuote.ordinal)
        .limit(65)
    ).all()
    if not 1 <= len(rows) <= 64:
        raise ArtifactError("artifact_input_integrity_failed")
    passages, documents = [], {}
    for ordinal, (evidence, version, retrieval) in enumerate(rows):
        document = _document(version, retrieval)
        if (
            evidence.ordinal != ordinal
            or (version.id, retrieval.id) not in refs
            or document.content_sha256 != version.content_sha256
            or _hash(_canonical(_metadata(document))) != retrieval.metadata_sha256
            or not 0 <= evidence.start_offset < evidence.end_offset <= len(document.text)
            or document.text[evidence.start_offset : evidence.end_offset] != evidence.quote
        ):
            raise ArtifactError("artifact_input_integrity_failed")
        documents[retrieval.id] = document
        passages.append(
            ArtifactPassage(
                ordinal=ordinal,
                source_id=version.source_id,
                title=retrieval.title,
                url=retrieval.url,
                bibliography=document.bibliography,
                coverage=document.coverage,
                content_sha256=version.content_sha256,
                content_kind=version.content_kind,
                quote=evidence.quote,
                supports=evidence.supports,
                claim_id=evidence.claim_id,
            )
        )
    formalisation_draft = None
    formalisation_checks = ()
    if request.formalisation_id is not None:
        formalisation = _formalisation(session, run, owner_hash, source, request.formalisation_id)
        if formalisation.payload_sha256 != request.formalisation_sha256:
            raise ArtifactError("artifact_input_integrity_failed")
        previous = ArtifactResult.model_validate_json(json.dumps(formalisation.payload))
        if not isinstance(previous.draft, FormalisationDraft):
            raise ArtifactError("artifact_input_integrity_failed")
        formalisation_draft = previous.draft
        formalisation_checks = previous.checks
    from polymath.scientific_context_store import load_pinned_artifact_context

    context = load_pinned_artifact_context(session, owner_hash=owner_hash, run_id=run.id)
    basis = ArtifactBasis(
        reference=MemoryReadReference(
            result_id=str(source.id),
            run_id=str(source.run_id),
            content_sha256=source.content_sha256,
        ),
        finding=finding,
        passages=tuple(passages),
        formalisation_id=str(request.formalisation_id) if request.formalisation_id else None,
        formalisation=formalisation_draft,
        formalisation_checks=formalisation_checks,
        **({"scientific_context": context} if context is not None else {}),
    )
    if len(_canonical(basis.model_dump(mode="json")).encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise ArtifactError("artifact_size_limit")
    return basis, documents


def _context(
    session: Session, run: ResearchRun
) -> tuple[ArtifactBasis, dict[uuid.UUID, SourceDocument]]:
    try:
        return _context_unchecked(session, run)
    except (ValueError, TypeError, KeyError, AttributeError, EvidenceError):
        raise ArtifactError("artifact_input_integrity_failed") from None


def _text(value: str) -> str:
    """Render model/source strings as text, never executable Markdown or HTML."""
    escaped = html.escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~\-])", r"\\\1", escaped)


def _link(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        if any(ord(character) < 33 for character in url):
            raise ValueError
    except ValueError:
        return "Source URL omitted: invalid HTTPS reference"
    return "<" + quote(url, safe=":/?#@!$&*+,;=%") + ">"


def _paragraph(value: CitedText) -> str:
    citations = " ".join(f"[{ordinal + 1}]" for ordinal in value.citation_ordinals)
    return f"**{value.label.title()} (model assessment).** {_text(value.text)} {citations}"


def _check_section(heading: str, checks: tuple[FiniteCheckResult, ...]) -> list[str]:
    if not checks:
        return []
    lines = [
        "## " + heading,
        "",
        "These recorded checks apply only to the model's explicit finite integer encoding. "
        "They do not verify that encoding against the scientific prose or prove a general claim. "
        "An unverified proof attempt remains unverified.",
        "",
    ]
    for index, check in enumerate(checks, 1):
        lines += [
            f"### Check {index}: {check.status.replace('_', ' ')}",
            "",
            f"Encoding SHA-256: `{check.conjecture_sha256}`",
            f"Assignments tested: {check.assignments_tested}; "
            f"applicable cases: {check.applicable_cases}; "
            f"domain size: {check.total_assignments}.",
            "",
        ]
        if check.witness is not None:
            lines += ["Counterexample: `" + json.dumps(check.witness, sort_keys=True) + "`."]
            lines += [f"Exact left/right values: `{check.lhs}` / `{check.rhs}`.", ""]
        lines += ["```json", check.conjecture.model_dump_json(indent=2), "```", ""]
    return lines


def render_artifact(result: ArtifactResult, basis: ArtifactBasis) -> str:
    title = result.draft.title if result.draft else "Research artifact outcome"
    lines = [
        "# " + _text(title),
        "",
        "Model-generated research draft. Citation integrity does not verify scientific truth.",
        "",
        f"Source finding: `{basis.reference.result_id}`",
        f"Source version SHA-256: `{basis.reference.content_sha256}`",
        "",
    ]
    if basis.formalisation_id:
        lines += [f"Prior conjecture artifact: `{basis.formalisation_id}` (unverified).", ""]
    if isinstance(result.draft, DocumentDraft):
        lines += ["## Abstract", ""]
        for paragraph in result.draft.abstract:
            lines += [_paragraph(paragraph), ""]
        for section in result.draft.sections:
            lines += ["## " + _text(section.heading), ""]
            for paragraph in section.paragraphs:
                lines += [_paragraph(paragraph), ""]
    elif isinstance(result.draft, FormalisationDraft):
        lines += [
            "Status: open conjecture; proof attempt unverified. No general proof is certified.",
            "",
            "## Definitions",
            "",
        ]
        for definition in result.draft.definitions:
            lines += [f"**{_text(definition.symbol)}.** " + _paragraph(definition.meaning), ""]
        lines += ["## Assumptions", ""]
        for assumption in result.draft.assumptions:
            lines += [_paragraph(assumption), ""]
        lines += ["## Conjecture", "", _paragraph(result.draft.conjecture), ""]
        lines += ["## Proof attempt — unverified", ""]
        for paragraph in result.draft.proof_attempt:
            lines += [_paragraph(paragraph), ""]
    else:
        lines += ["Outcome: " + result.status.replace("_", " "), ""]
        if result.reason:
            lines += [_text(result.reason), ""]
        if result.error_code:
            lines += [f"Recorded failure: `{result.error_code}`", ""]
    if result.draft is not None:
        lines += ["## Research limitations", ""]
        for limitation in result.draft.limitations:
            lines += ["- " + _text(limitation)]
        lines += [""]
    lines += _check_section("Bounded mathematical checks", result.checks)
    lines += _check_section("Checks on the incorporated conjecture", basis.formalisation_checks)
    used = (
        {
            ordinal
            for paragraph in iter_cited_texts(result.draft)
            for ordinal in paragraph.citation_ordinals
        }
        if result.draft is not None
        else set()
    )
    lines += ["## References and exact source passages", ""]
    for passage in basis.passages:
        if passage.ordinal not in used:
            continue
        scope = "FULL TEXT EXCERPT" if passage.coverage else passage.content_kind.replace("_", " ")
        lines += [
            f"### [{passage.ordinal + 1}] {_text(passage.title)}",
            "",
            _link(passage.url),
            "",
            f"Read scope: {scope}. "
            f"Source: `{_text(passage.source_id)}`. SHA-256: `{passage.content_sha256}`.",
            "",
        ]
        if passage.coverage is not None:
            coverage = passage.coverage
            lines += [
                f"Exact normalized character window [{coverage.start}, {coverage.end}) "
                f"of {coverage.total}; arXiv `{coverage.arxiv_id}v{coverage.arxiv_version}`. "
                "This excerpt does not indicate that the whole paper was read.",
                "",
                f"Full snapshot SHA-256: `{coverage.snapshot_sha256}`. "
                f"Snapshot ID: `{coverage.snapshot_id}`.",
                "",
                "Extracted HTML text may omit equations, figures, and tables. "
                "A heading index is metadata; only the cited excerpt was read.",
                "",
            ]
        if passage.bibliography is not None:
            bibliography = passage.bibliography
            if bibliography.authors:
                authors = "; ".join(_text(author.name) for author in bibliography.authors)
                suffix = " (additional authors omitted)" if bibliography.authors_truncated else ""
                lines += [f"Authors: {authors}{suffix}.", ""]
            if bibliography.publication_year is not None:
                lines += [f"Publication year: {bibliography.publication_year}.", ""]
            if bibliography.venue is not None:
                lines += [f"Venue: {_text(bibliography.venue)}.", ""]
            if bibliography.doi is not None:
                lines += [f"DOI: {_text(bibliography.doi)}.", ""]
            if bibliography.arxiv_id is not None:
                version = f"v{bibliography.arxiv_version}" if bibliography.arxiv_version else ""
                lines += [f"arXiv: {_text(bibliography.arxiv_id)}{version}.", ""]
        lines += ["> " + _text(line) for line in passage.quote.splitlines()]
        lines += [""]
    rendered = "\n".join(lines).rstrip() + "\n"
    if basis.scientific_context is not None:
        from polymath.artifact_context_rendering import render_artifact_scientific_context

        rendered += "\n" + render_artifact_scientific_context(basis, text=_text, link=_link)
    return rendered


def _stored(row: GeneratedArtifact) -> StoredArtifact:
    try:
        payload_text, basis_text = _canonical(row.payload), _canonical(row.basis)
        if (
            _hash(payload_text) != row.payload_sha256
            or _hash(basis_text) != row.basis_sha256
            or _hash(row.markdown) != row.markdown_sha256
        ):
            raise ValueError
        payload = ArtifactResult.model_validate_json(payload_text)
        basis = ArtifactBasis.model_validate_json(basis_text)
        validate_artifact_result(payload, basis)
        if row.kind != payload.kind or row.status != payload.status:
            raise ValueError
    except (ValueError, TypeError, ValidationError):
        raise ArtifactError("artifact_integrity_failed") from None
    return StoredArtifact(
        id=row.id,
        run_id=row.run_id,
        kind=row.kind,
        status=row.status,
        payload=payload.model_dump(mode="json"),
        basis=basis.model_dump(mode="json"),
        payload_sha256=row.payload_sha256,
        basis_sha256=row.basis_sha256,
        markdown=row.markdown,
        markdown_sha256=row.markdown_sha256,
        created_at=row.created_at,
    )


class ArtifactStore:
    def __init__(self, engine: Engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("ArtifactStore requires PostgreSQL transaction semantics")
        self.engine = engine

    def load_basis(self, lease: JobLease) -> ArtifactBasis:
        with Session(self.engine) as session, session.begin():
            basis, documents = _context(session, _owned(session, lease))
        # Source writes own their transactions and recheck this lease. Never
        # hold its run lock while attempting a second connection's source write.
        store = EvidenceStore(self.engine)
        for retrieval_id, document in documents.items():
            saved = store.save_source(lease, document)
            if saved.retrieval_id != retrieval_id:
                raise ArtifactError("artifact_input_integrity_failed")
        with Session(self.engine) as session, session.begin():
            latest, _ = _context(session, _owned(session, lease))
            if latest != basis:
                raise ArtifactError("artifact_input_integrity_failed")
        return basis

    def save(self, lease: JobLease, result: ArtifactResult) -> StoredArtifact:
        try:
            result = ArtifactResult.model_validate_json(result.model_dump_json())
        except (ValueError, TypeError, AttributeError, ValidationError):
            raise ArtifactError("invalid_artifact_result") from None
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            basis, documents = _context(session, run)
            if result.kind != run.task_kind:
                raise ArtifactError("artifact_kind_mismatch")
            try:
                validate_artifact_result(result, basis)
            except (ValueError, TypeError, ValidationError):
                raise ArtifactError("artifact_validation_failed") from None
            for retrieval_id in documents:
                if session.get(RunEvidenceSource, (run.id, retrieval_id)) is None:
                    raise ArtifactError("artifact_source_not_observed")
            payload, basis_json = result.model_dump(mode="json"), basis.model_dump(mode="json")
            encoded, basis_encoded = _canonical(payload), _canonical(basis_json)
            markdown = render_artifact(result, basis)
            if any(
                len(value.encode("utf-8")) > MAX_ARTIFACT_BYTES
                for value in (encoded, basis_encoded, markdown)
            ):
                raise ArtifactError("artifact_size_limit")
            digest = _hash(encoded)
            basis_digest, markdown_digest = _hash(basis_encoded), _hash(markdown)
            prior = session.scalar(
                select(GeneratedArtifact).where(GeneratedArtifact.run_id == run.id)
            )
            if prior is not None:
                if (prior.payload_sha256, prior.basis_sha256, prior.markdown_sha256) != (
                    digest,
                    basis_digest,
                    markdown_digest,
                ):
                    raise ArtifactError("artifact_version_conflict")
                return _stored(prior)
            artifact = GeneratedArtifact(
                run_id=run.id,
                kind=result.kind,
                status=result.status,
                payload=payload,
                payload_sha256=digest,
                basis=basis_json,
                basis_sha256=basis_digest,
                markdown=markdown,
                markdown_sha256=markdown_digest,
            )
            session.add(artifact)
            session.flush()
            return _stored(artifact)

    def get(self, run_id: uuid.UUID, artifact_id: uuid.UUID) -> StoredArtifact:
        with Session(self.engine) as session:
            row = session.scalar(
                select(GeneratedArtifact).where(
                    GeneratedArtifact.id == artifact_id, GeneratedArtifact.run_id == run_id
                )
            )
            if row is None:
                raise ArtifactError("artifact_not_found")
            return _stored(row)
