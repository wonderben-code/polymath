"""Immutable full-text snapshots and fenced, owned exact-window reads.

An index is metadata, not evidence. Only an exact saved window creates a run
source read. Shared bodies carry no owner's bibliography or retrieval timestamp;
those observations belong to each immutable run index link.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.evidence import EvidenceError, _canonical, _document, _hash, _metadata
from polymath.evidence_models import RunEvidenceSource, SourceRetrieval, SourceVersion
from polymath.full_text_contracts import ExtractedFullText, FullTextIndex
from polymath.full_text_models import FullTextSnapshot, RunFullTextIndex
from polymath.jobs import JobLease, _owned
from polymath.models import ResearchRun
from polymath.research.models import (
    SearchResults,
    SourceBibliography,
    SourceCoverage,
    SourceDocument,
    SourceReference,
)

MAX_RUN_FULL_TEXT_INDEXES = 20
MAX_FULL_TEXT_WINDOW = 8000
MAX_IDENTITY_CONTEXT = 256


class FullTextError(Exception):
    """Static private errors, never paper text, HTML, secrets or foreign IDs."""


def _identifier(value) -> uuid.UUID:
    try:
        parsed = uuid.UUID(str(value))
        if str(parsed) != str(value):
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise FullTextError("full_text_not_found") from None


def _source_id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 200 or "\x00" in value:
        raise FullTextError("full_text_not_found")
    return value


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
        raise FullTextError("full_text_not_found")
    return owner


def _run(session: Session, lease: JobLease):
    run = _owned(session, lease)
    _owner(session, run.id)
    if run.task_kind not in ("research", "reassess_finding"):
        raise FullTextError("full_text_wrong_task_kind")
    return run


def _provenance(extracted: ExtractedFullText) -> dict:
    payload = extracted.model_dump(mode="json")
    payload.pop("retrieved_at")
    payload.pop("text")
    payload["snapshot_sha256"] = extracted.snapshot_sha256
    return payload


def _extracted(snapshot: FullTextSnapshot, retrieved_at) -> ExtractedFullText:
    extracted = ExtractedFullText.model_validate_json(
        json.dumps(
            {
                "arxiv_id": snapshot.arxiv_id,
                "arxiv_version": snapshot.arxiv_version,
                "url": snapshot.url,
                "text": snapshot.source_text,
                "raw_html_sha256": snapshot.raw_html_sha256,
                "retrieved_at": retrieved_at.isoformat(),
                "extractor_version": snapshot.extractor_version,
                "headings": snapshot.headings,
                "headings_truncated": snapshot.headings_truncated,
                "limitations": snapshot.limitations,
            }
        )
    )
    if (
        extracted.snapshot_sha256 != snapshot.snapshot_sha256
        or _hash(_canonical(_provenance(extracted))) != snapshot.provenance_sha256
    ):
        raise FullTextError("full_text_integrity_failed")
    return extracted


def _reference(link: RunFullTextIndex) -> SourceReference:
    if _hash(_canonical(link.reference)) != link.reference_sha256:
        raise FullTextError("full_text_integrity_failed")
    reference = SourceReference.model_validate_json(json.dumps(link.reference))
    if reference.source_id != link.source_id or reference.coverage is not None:
        raise FullTextError("full_text_integrity_failed")
    return reference


def _assert_reference_locator(reference: SourceReference, arxiv_id: str, arxiv_version) -> None:
    from polymath.literature.identity import canonical_arxiv_id

    asserted = []
    if reference.source_id.startswith("arxiv:"):
        asserted.append(reference.source_id.removeprefix("arxiv:"))
    parsed = urlsplit(reference.url)
    if parsed.hostname in ("arxiv.org", "export.arxiv.org"):
        match = re.fullmatch(r"/(?:abs|html|pdf)/(.+)", parsed.path)
        if match is None:
            raise FullTextError("full_text_identity_conflict")
        asserted.append(match.group(1).removesuffix(".pdf"))
    for identity in asserted:
        try:
            base, version = canonical_arxiv_id(identity)
        except ValueError:
            raise FullTextError("full_text_identity_conflict") from None
        if base != arxiv_id or (arxiv_version is not None and version not in (None, arxiv_version)):
            raise FullTextError("full_text_identity_conflict")


def _bibliography(reference: SourceReference, snapshot: FullTextSnapshot) -> SourceBibliography:
    _assert_reference_locator(reference, snapshot.arxiv_id, snapshot.arxiv_version)
    payload = reference.bibliography.model_dump(mode="json") if reference.bibliography else {}
    if payload.get("arxiv_id") not in (None, snapshot.arxiv_id) or payload.get(
        "arxiv_version"
    ) not in (None, snapshot.arxiv_version):
        raise FullTextError("full_text_identity_conflict")
    payload.update(arxiv_id=snapshot.arxiv_id, arxiv_version=snapshot.arxiv_version)
    return SourceBibliography.model_validate_json(json.dumps(payload))


def _index(link: RunFullTextIndex, snapshot: FullTextSnapshot) -> FullTextIndex:
    extracted = _extracted(snapshot, link.retrieved_at)
    reference = _reference(link)
    _bibliography(reference, snapshot)
    return FullTextIndex(
        **{key: value for key, value in extracted.model_dump().items() if key != "text"},
        snapshot_id=str(snapshot.id),
        source=reference,
        snapshot_sha256=snapshot.snapshot_sha256,
        total_chars=len(snapshot.source_text),
    )


def owned_identity_context(session: Session, run_id: uuid.UUID) -> tuple[SourceReference, ...]:
    """Frozen identity metadata only; never source reads or body/range authority."""
    from polymath.literature.identity import paper_aliases

    refs = []
    rows = session.execute(
        select(RunFullTextIndex, FullTextSnapshot)
        .join(FullTextSnapshot, FullTextSnapshot.id == RunFullTextIndex.snapshot_id)
        .where(RunFullTextIndex.run_id == run_id)
        .order_by(RunFullTextIndex.source_id)
        .limit(MAX_RUN_FULL_TEXT_INDEXES + 1)
    ).all()
    if len(rows) > MAX_RUN_FULL_TEXT_INDEXES:
        raise FullTextError("full_text_index_limit")
    for link, snapshot in rows:
        reference = _reference(link)
        refs.append(
            SourceReference(
                source_id=reference.source_id,
                title=reference.title,
                url=reference.url,
                bibliography=_bibliography(reference, snapshot),
            )
        )
    run = session.get(ResearchRun, run_id)
    if run is None:
        raise FullTextError("full_text_not_found")
    observations = run.checkpoint.get("tool_observations", {})
    if not isinstance(observations, dict):
        raise FullTextError("full_text_identity_context_integrity_failed")
    try:
        for observation in observations.values():
            if not isinstance(observation, dict) or observation.get("error"):
                continue
            payload = observation.get("payload")
            if not isinstance(payload, dict) or "sources" not in payload:
                continue
            results = SearchResults.model_validate_json(json.dumps(payload))
            sources = {source.source_id: source for source in results.sources}
            for available in results.full_text:
                if available.status != "candidate" or available.locator is None:
                    continue
                reference = sources[available.source_id]
                locator = available.locator
                _assert_reference_locator(reference, locator.arxiv_id, locator.arxiv_version)
                bibliography = (
                    reference.bibliography.model_dump(mode="json") if reference.bibliography else {}
                )
                if bibliography.get("arxiv_id") not in (None, locator.arxiv_id):
                    raise FullTextError("full_text_identity_context_integrity_failed")
                bibliography["arxiv_id"] = locator.arxiv_id
                if bibliography.get("arxiv_version") is None:
                    bibliography["arxiv_version"] = locator.arxiv_version
                refs.append(
                    SourceReference(
                        source_id=reference.source_id,
                        title=reference.title,
                        url=reference.url,
                        bibliography=SourceBibliography.model_validate_json(
                            json.dumps(bibliography)
                        ),
                    )
                )
    except (ValueError, TypeError, KeyError, ValidationError):
        raise FullTextError("full_text_identity_context_integrity_failed") from None
    distinct = {}
    for reference in refs:
        aliases = paper_aliases(reference)
        distinct.setdefault(aliases, reference)
        if len(distinct) > MAX_IDENTITY_CONTEXT:
            raise FullTextError("full_text_identity_context_limit")
    return tuple(distinct.values())


def _validate_slice(session: Session, document: SourceDocument) -> FullTextSnapshot:
    coverage = document.coverage
    if coverage is None:
        raise FullTextError("full_text_coverage_required")
    snapshot = session.get(FullTextSnapshot, _identifier(coverage.snapshot_id))
    if snapshot is None:
        raise FullTextError("full_text_not_found")
    _extracted(snapshot, document.retrieved_at)
    if (
        coverage.snapshot_sha256 != snapshot.snapshot_sha256
        or coverage.arxiv_id != snapshot.arxiv_id
        or coverage.arxiv_version != snapshot.arxiv_version
        or coverage.total != len(snapshot.source_text)
        or document.text != snapshot.source_text[coverage.start : coverage.end]
        or document.url != snapshot.url
        or document.extraction_method != snapshot.extractor_version
        or document.content_kind != "full_text"
        or document.bibliography is None
        or document.bibliography.arxiv_id != snapshot.arxiv_id
        or document.bibliography.arxiv_version != snapshot.arxiv_version
        or document.source_id != f"arxiv-html:{snapshot.id}:{coverage.start}:{coverage.end}"
    ):
        raise FullTextError("full_text_coverage_integrity_failed")
    return snapshot


def validate_window_source(session: Session, run_id: uuid.UUID, document: SourceDocument) -> None:
    """Authorize new windows by index, or copy one identical owned old retrieval."""
    if document.coverage is None:
        return
    snapshot = _validate_slice(session, document)
    owner = _owner(session, run_id)
    links = (
        session.execute(
            select(RunFullTextIndex).where(
                RunFullTextIndex.run_id == run_id,
                RunFullTextIndex.snapshot_id == snapshot.id,
            )
        )
        .scalars()
        .all()
    )
    for link in links:
        reference = _reference(link)
        if (
            document.title == reference.title
            and document.bibliography == _bibliography(reference, snapshot)
            and document.retrieved_at.astimezone(UTC) == link.retrieved_at.astimezone(UTC)
        ):
            return
    # Copying an existing owned exact reading grants neither an index nor a new
    # character range. Match complete metadata, not merely a source/hash string.
    metadata_hash = _hash(_canonical(_metadata(document)))
    prior = session.scalar(
        select(SourceRetrieval.id)
        .join(SourceVersion, SourceVersion.id == SourceRetrieval.version_id)
        .join(RunEvidenceSource, RunEvidenceSource.retrieval_id == SourceRetrieval.id)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == RunEvidenceSource.run_id)
        .where(
            ResearchRunAccess.session_hash == owner,
            SourceVersion.source_id == document.source_id,
            SourceVersion.content_sha256 == document.content_sha256,
            SourceVersion.content_kind == "full_text",
            SourceRetrieval.metadata_sha256 == metadata_hash,
            SourceRetrieval.full_text_snapshot_id == snapshot.id,
            SourceRetrieval.coverage == document.coverage.model_dump(mode="json"),
        )
        .limit(1)
    )
    if prior is None:
        raise FullTextError("full_text_window_not_authorized")


class FullTextStore:
    def __init__(self, engine: Engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("FullTextStore requires PostgreSQL")
        self.engine = engine

    def owned_identity_context(self, lease: JobLease) -> tuple[SourceReference, ...]:
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            access = session.get(ResearchRunAccess, run.id)
            if access is None and run.task_kind == "research":
                # Old internal research jobs have no browser capability. Preserve
                # that legacy empty-context path without granting full-text access.
                has_index = (
                    session.scalar(
                        select(RunFullTextIndex.source_id)
                        .where(RunFullTextIndex.run_id == run.id)
                        .limit(1)
                    )
                    is not None
                )
                observations = run.checkpoint.get("tool_observations", {})
                has_candidate = not isinstance(observations, dict)
                if isinstance(observations, dict):
                    for key, item in observations.items():
                        payload = item.get("payload") if isinstance(item, dict) else None
                        if str(key).startswith("fulltext-") or (
                            isinstance(payload, dict) and payload.get("full_text")
                        ):
                            has_candidate = True
                if has_index or has_candidate:
                    raise FullTextError("full_text_not_found")
                _owned(session, lease)
                return ()
            _run(session, lease)
            context = owned_identity_context(session, lease.run_id)
            _run(session, lease)
            return context

    def get_index(self, lease: JobLease, source_id: str) -> FullTextIndex | None:
        try:
            with Session(self.engine) as session, session.begin():
                _run(session, lease)
                link = session.get(RunFullTextIndex, (lease.run_id, _source_id(source_id)))
                index = (
                    None
                    if link is None
                    else _index(link, session.get(FullTextSnapshot, link.snapshot_id))
                )
                _run(session, lease)
                return index
        except (ValueError, TypeError, AttributeError, ValidationError, EvidenceError):
            raise FullTextError("full_text_integrity_failed") from None

    def save_snapshot(
        self, lease: JobLease, reference: SourceReference, extracted: ExtractedFullText
    ) -> FullTextIndex:
        try:
            reference = SourceReference.model_validate_json(reference.model_dump_json())
            extracted = ExtractedFullText.model_validate_json(extracted.model_dump_json())
            if reference.coverage is not None:
                raise FullTextError("full_text_original_reference_required")
            with Session(self.engine) as session, session.begin():
                _run(session, lease)
                link = session.get(RunFullTextIndex, (lease.run_id, reference.source_id))
                if link is not None:
                    if link.reference != reference.model_dump(mode="json"):
                        raise FullTextError("full_text_index_binding_conflict")
                    index = _index(link, session.get(FullTextSnapshot, link.snapshot_id))
                    _run(session, lease)
                    return index
                count = session.scalar(
                    select(func.count())
                    .select_from(RunFullTextIndex)
                    .where(RunFullTextIndex.run_id == lease.run_id)
                )
                if count >= MAX_RUN_FULL_TEXT_INDEXES:
                    raise FullTextError("full_text_index_limit")
                provenance = _provenance(extracted)
                digest = _hash(_canonical(provenance))
                session.execute(
                    insert(FullTextSnapshot)
                    .values(
                        id=uuid.uuid4(),
                        arxiv_id=extracted.arxiv_id,
                        arxiv_version=extracted.arxiv_version,
                        url=extracted.url,
                        source_text=extracted.text,
                        snapshot_sha256=extracted.snapshot_sha256,
                        raw_html_sha256=extracted.raw_html_sha256,
                        extractor_version=extracted.extractor_version,
                        headings=[item.model_dump(mode="json") for item in extracted.headings],
                        headings_truncated=extracted.headings_truncated,
                        limitations=list(extracted.limitations),
                        provenance_sha256=digest,
                    )
                    .on_conflict_do_nothing(constraint="uq_full_text_snapshot")
                )
                snapshot = session.scalar(
                    select(FullTextSnapshot).where(
                        FullTextSnapshot.arxiv_id == extracted.arxiv_id,
                        FullTextSnapshot.arxiv_version == extracted.arxiv_version,
                        FullTextSnapshot.extractor_version == extracted.extractor_version,
                        FullTextSnapshot.raw_html_sha256 == extracted.raw_html_sha256,
                        FullTextSnapshot.snapshot_sha256 == extracted.snapshot_sha256,
                    )
                )
                if snapshot.provenance_sha256 != digest or snapshot.source_text != extracted.text:
                    raise FullTextError("full_text_snapshot_conflict")
                _bibliography(reference, snapshot)
                if (
                    session.scalar(
                        select(RunFullTextIndex.source_id).where(
                            RunFullTextIndex.run_id == lease.run_id,
                            RunFullTextIndex.snapshot_id == snapshot.id,
                        )
                    )
                    is not None
                ):
                    raise FullTextError("full_text_snapshot_already_indexed")
                payload = reference.model_dump(mode="json")
                link = RunFullTextIndex(
                    run_id=lease.run_id,
                    source_id=reference.source_id,
                    snapshot_id=snapshot.id,
                    reference=payload,
                    reference_sha256=_hash(_canonical(payload)),
                    retrieved_at=extracted.retrieved_at,
                )
                session.add(link)
                session.flush()
                _run(session, lease)
                return _index(link, snapshot)
        except (ValueError, TypeError, AttributeError, ValidationError, EvidenceError):
            raise FullTextError("full_text_integrity_failed") from None

    def read_window(self, lease: JobLease, snapshot_id, start: int, end: int) -> SourceDocument:
        from polymath.evidence import EvidenceStore

        if (
            type(start) is not int
            or type(end) is not int
            or not (0 <= start < end and end - start <= MAX_FULL_TEXT_WINDOW)
        ):
            raise FullTextError("full_text_invalid_window")
        try:
            with Session(self.engine) as session, session.begin():
                _run(session, lease)
                snapshot_id = _identifier(snapshot_id)
                row = session.execute(
                    select(RunFullTextIndex, FullTextSnapshot)
                    .join(FullTextSnapshot, FullTextSnapshot.id == RunFullTextIndex.snapshot_id)
                    .where(
                        RunFullTextIndex.run_id == lease.run_id,
                        RunFullTextIndex.snapshot_id == snapshot_id,
                    )
                    .order_by(RunFullTextIndex.source_id)
                    .limit(1)
                ).first()
                if row is None:
                    raise FullTextError("full_text_not_found")
                link, snapshot = row
                index = _index(link, snapshot)
                if end > index.total_chars:
                    raise FullTextError("full_text_invalid_window")
                reference = _reference(link)
                document = SourceDocument(
                    source_id=f"arxiv-html:{snapshot.id}:{start}:{end}",
                    title=reference.title,
                    url=snapshot.url,
                    bibliography=_bibliography(reference, snapshot),
                    coverage=SourceCoverage(
                        snapshot_id=str(snapshot.id),
                        snapshot_sha256=snapshot.snapshot_sha256,
                        arxiv_id=snapshot.arxiv_id,
                        arxiv_version=snapshot.arxiv_version,
                        start=start,
                        end=end,
                        total=index.total_chars,
                    ),
                    text=snapshot.source_text[start:end],
                    content_kind="full_text",
                    retrieved_at=link.retrieved_at,
                    extraction_method=snapshot.extractor_version,
                )
            EvidenceStore(self.engine).save_source(lease, document)
            with Session(self.engine) as session, session.begin():
                _run(session, lease)
            return document
        except (ValueError, TypeError, AttributeError, ValidationError, EvidenceError):
            raise FullTextError("full_text_integrity_failed") from None

    def get_saved_window(self, owner_hash: str, run_id, retrieval_id) -> SourceDocument:
        try:
            with Session(self.engine) as session:
                run_id, retrieval_id = _identifier(run_id), _identifier(retrieval_id)
                if _owner(session, run_id) != owner_hash:
                    raise FullTextError("full_text_not_found")
                row = session.execute(
                    select(SourceVersion, SourceRetrieval)
                    .join(SourceRetrieval, SourceRetrieval.version_id == SourceVersion.id)
                    .join(RunEvidenceSource, RunEvidenceSource.retrieval_id == SourceRetrieval.id)
                    .where(
                        RunEvidenceSource.run_id == run_id,
                        SourceRetrieval.id == retrieval_id,
                        SourceRetrieval.full_text_snapshot_id.is_not(None),
                    )
                ).first()
                if row is None:
                    raise FullTextError("full_text_not_found")
                document = _document(*row)
                _validate_slice(session, document)
                if _hash(_canonical(_metadata(document))) != row[1].metadata_sha256:
                    raise FullTextError("full_text_integrity_failed")
                if _owner(session, run_id) != owner_hash:
                    raise FullTextError("full_text_not_found")
                return document
        except (ValueError, TypeError, AttributeError, ValidationError, EvidenceError):
            raise FullTextError("full_text_integrity_failed") from None


def check_window_owner(session: Session, run_id: uuid.UUID) -> None:
    """Recheck active ownership after any shared evidence-row conflict wait."""
    _owner(session, run_id)
