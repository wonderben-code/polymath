"""Explore metadata closure over actual ordinary memory receipts.

Leads are not stage parents or new primary evidence. No source body, external
URL, model, clock, browser session, grant, event or mutable head is read here.
Fresh-use head checks remain the existing EvidenceStore memory validator.
"""

from __future__ import annotations

from datetime import UTC

from sqlalchemy import select

from polymath.access_models import ResearchRunAccess
from polymath.assessment_eligibility import completed_result_condition
from polymath.assessment_models import FindingAssessment
from polymath.evidence import EvidenceError, _canonical, _hash
from polymath.evidence_models import EvidenceQuote, EvidenceResult, SourceRetrieval, SourceVersion
from polymath.literature.identity import paper_aliases
from polymath.memory_models import ResearchMemoryRead
from polymath.models import ResearchRun
from polymath.research.models import SourceReference


class StageMemoryError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _bad():
    raise StageMemoryError("stage_memory_integrity_failed")


def _owned_memory_identity_references(session, run, *, identity_limit=256):
    if type(identity_limit) is not int or not 1 <= identity_limit <= 4096:
        _bad()
    owner = session.scalar(
        select(ResearchRunAccess.session_hash).where(ResearchRunAccess.run_id == run.id)
    )
    if owner is None:
        _bad()
    reads = session.scalars(
        select(ResearchMemoryRead).where(ResearchMemoryRead.run_id == run.id).limit(11)
    ).all()
    if len(reads) > 10:
        _bad()
    references = {}
    for read in reads:
        parent = session.scalar(
            select(EvidenceResult)
            .join(ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id)
            .where(
                EvidenceResult.id == read.parent_result_id,
                ResearchRunAccess.session_hash == owner,
                EvidenceResult.run_id != run.id,
                EvidenceResult.created_at < run.created_at,
                completed_result_condition(),
            )
        )
        if (
            parent is None
            or parent.run_id != read.parent_run_id
            or parent.status not in ("bridge", "answer")
            or parent.run_id == run.id
            or read.run_id != run.id
            or parent.created_at >= run.created_at
            or parent.content_sha256 != read.parent_content_sha256
            or _hash(_canonical(parent.payload)) != parent.content_sha256
        ):
            _bad()
        parent_run = session.get(ResearchRun, parent.run_id)
        if parent_run is None:
            _bad()
        if parent_run.task_kind == "reassess_finding":
            applied = session.scalars(
                select(FindingAssessment)
                .where(
                    FindingAssessment.candidate_result_id == parent.id,
                    FindingAssessment.applied.is_(True),
                    FindingAssessment.disposition == "revised",
                    FindingAssessment.run_id == parent.run_id,
                )
                .limit(2)
            ).all()
            if (
                len(applied) != 1
                or _hash(_canonical(applied[0].payload)) != applied[0].payload_sha256
            ):
                _bad()
        elif parent_run.task_kind != "research":
            _bad()
        saved = parent.payload["source_artifacts"]
        if not isinstance(saved, list) or not 1 <= len(saved) <= 200:
            _bad()
        pairs = {(item["version_id"], item["retrieval_id"]) for item in saved}
        if len(pairs) != len(saved):
            _bad()
        # Select only metadata and version identifiers. No source_text column is
        # read merely because the model revisited a finding as a conceptual lead.
        rows = session.execute(
            select(
                SourceVersion.id.label("version_id"),
                SourceVersion.source_id,
                SourceVersion.content_kind,
                SourceRetrieval.id.label("retrieval_id"),
                SourceRetrieval.title,
                SourceRetrieval.url,
                SourceRetrieval.bibliography,
                SourceRetrieval.coverage,
                SourceRetrieval.full_text_snapshot_id,
                SourceRetrieval.retrieved_at,
                SourceRetrieval.extraction_method,
                SourceRetrieval.metadata_sha256,
            )
            .select_from(EvidenceQuote)
            .join(SourceVersion, SourceVersion.id == EvidenceQuote.version_id)
            .join(SourceRetrieval, SourceRetrieval.id == EvidenceQuote.retrieval_id)
            .where(EvidenceQuote.result_id == parent.id, EvidenceQuote.run_id == parent.run_id)
            .order_by(EvidenceQuote.ordinal)
            .limit(65)
        ).all()
        if not 1 <= len(rows) <= 64:
            _bad()
        for row in rows:
            if (str(row.version_id), str(row.retrieval_id)) not in pairs:
                _bad()
            value = {"source_id": row.source_id, "title": row.title, "url": row.url}
            if row.bibliography is not None:
                value["bibliography"] = row.bibliography
            if row.coverage is not None:
                value["coverage"] = row.coverage
            reference = SourceReference.model_validate_json(_canonical(value))
            metadata = {
                "title": row.title,
                "url": row.url,
                "retrieved_at": row.retrieved_at.astimezone(UTC).isoformat(),
                "extraction_method": row.extraction_method,
            }
            if reference.bibliography is not None:
                metadata["bibliography"] = reference.bibliography.model_dump(mode="json")
            if reference.coverage is not None:
                metadata["coverage"] = reference.coverage.model_dump(mode="json")
                if (
                    row.content_kind != "full_text"
                    or str(row.full_text_snapshot_id) != reference.coverage.snapshot_id
                ):
                    _bad()
            elif row.full_text_snapshot_id is not None:
                _bad()
            if _hash(_canonical(metadata)) != row.metadata_sha256:
                _bad()
            references.setdefault(paper_aliases(reference), reference)
            if len(references) > identity_limit:
                raise StageMemoryError("source_identity_context_limit")
    return tuple(references.values())


def owned_memory_identity_references(session, run, *, identity_limit=256):
    try:
        return _owned_memory_identity_references(session, run, identity_limit=identity_limit)
    except StageMemoryError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, EvidenceError):
        raise StageMemoryError("stage_memory_integrity_failed") from None
