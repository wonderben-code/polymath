"""Build pinned scientific context from exact owned, immutable records.

This reads only previously saved scientific records. It never fetches a paper,
creates a read receipt, calls a model, mutates a head, or acquires a budget grant.
The returned historical hypotheses are context snapshots, not current support
claims. The admission caller owns locking, immutable pinning and native intent
binding; this module only reads and validates previously saved records.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.artifact_models import GeneratedArtifact
from polymath.assessment_contracts import AssessmentBasis, AssessmentResult
from polymath.assessment_eligibility import (
    completed_result_condition,
    require_current_supported,
)
from polymath.assessment_models import AssessmentRequest, FindingAssessment, FindingHead
from polymath.discovery_contracts import StageContext
from polymath.discovery_models import StageCommit, StageContextRecord, StageParent
from polymath.discovery_store import DiscoveryError, _receipt
from polymath.evidence import EvidenceError, _canonical, _document, _hash, _metadata
from polymath.evidence_models import (
    EvidenceQuote,
    EvidenceResult,
    RunEvidenceSource,
    SourceRetrieval,
    SourceVersion,
)
from polymath.lineage_models import ResearchRunParent
from polymath.models import ResearchRun
from polymath.research.models import MemoryReadReference, ResearchResult, SourceReference
from polymath.scientific_context_contracts import (
    ARTIFACT_CONTEXT_PROTOCOL,
    MAX_APPLIED_ASSESSMENTS,
    MAX_CONTEXT_BYTES,
    MAX_CONTEXT_EDGES,
    MAX_CONTEXT_NODES,
    AppliedAssessmentContext,
    ArtifactScientificContext,
    AskOverviewBinding,
    PriorArtifactContextReference,
    ScientificAncestor,
    ScientificAssessmentQuote,
    ScientificAssessmentStatement,
    ScientificParentEdge,
    SourceRevisionContext,
    StageOriginContext,
    validate_result_overview,
)


class ScientificContextError(ValueError):
    """Static codes only; no foreign record, stored prose or provider body."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _id(value):
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        raise ScientificContextError("artifact_context_not_available")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise ScientificContextError("artifact_context_not_available") from None
    if str(parsed) != value:
        raise ScientificContextError("artifact_context_not_available")
    return parsed


def _typed(cls, data):
    return cls.model_validate_json(_canonical(data))


def _reference(row):
    return MemoryReadReference(
        result_id=str(row.id), run_id=str(row.run_id), content_sha256=row.content_sha256
    )


def _integrity():
    raise ScientificContextError("artifact_context_integrity_failed")


class _Builder:
    def __init__(self, session, owner):
        self.session = session
        self.owner = owner
        self.results = {}
        self.runs = {}
        self.packets = {}
        self.origins = {}
        self.parents = {}
        self.nodes = []
        self.edges = {}
        self.active = set()
        self.visited = set()
        self.assessments = {}

    def run(self, identifier):
        if identifier not in self.runs:
            row = self.session.scalar(
                select(ResearchRun)
                .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
                .where(
                    ResearchRun.id == identifier,
                    ResearchRunAccess.session_hash == self.owner,
                )
            )
            if row is None:
                raise ScientificContextError("artifact_context_not_available")
            self.runs[identifier] = row
        return self.runs[identifier]

    def result(self, identifier):
        if identifier in self.results:
            return self.results[identifier]
        if len(self.results) >= MAX_CONTEXT_NODES:
            raise ScientificContextError("artifact_context_too_large")
        row = self.session.scalar(
            select(EvidenceResult)
            .join(ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id)
            .where(
                EvidenceResult.id == identifier,
                ResearchRunAccess.session_hash == self.owner,
                EvidenceResult.status.in_(("bridge", "answer")),
                completed_result_condition(),
            )
        )
        if row is None:
            raise ScientificContextError("artifact_context_not_available")
        run = self.run(row.run_id)
        if (
            run.task_kind not in ("research", "reassess_finding")
            or _hash(_canonical(row.payload)) != row.content_sha256
        ):
            _integrity()
        # Succeeded reassessment alone does not establish that its candidate won
        # the compare-and-swap. Only the immutable applied record authorizes it.
        if run.task_kind == "reassess_finding":
            self.applied_for_candidate(row)
        self.results[identifier] = row
        self.packet(row)
        return row

    def document(self, run_id, retrieval_id, version_id=None):
        query = (
            select(SourceVersion, SourceRetrieval)
            .join(SourceRetrieval, SourceRetrieval.version_id == SourceVersion.id)
            .join(RunEvidenceSource, RunEvidenceSource.retrieval_id == SourceRetrieval.id)
            .where(
                RunEvidenceSource.run_id == run_id,
                SourceRetrieval.id == _id(retrieval_id),
            )
        )
        if version_id is not None:
            query = query.where(SourceVersion.id == _id(version_id))
        rows = self.session.execute(query.limit(2)).all()
        if len(rows) != 1:
            _integrity()
        version, retrieval = rows[0]
        document = _document(version, retrieval)
        if (
            document.content_sha256 != version.content_sha256
            or _hash(_canonical(_metadata(document))) != retrieval.metadata_sha256
        ):
            _integrity()
        return version, retrieval, document

    def packet(self, row):
        if row.id in self.packets:
            return self.packets[row.id]
        raw = row.payload["result"]
        validate_result_overview(raw)
        payload = dict(raw)
        refs = row.payload["source_artifacts"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 200:
            _integrity()
        references = set()
        for reference in refs:
            if not isinstance(reference, dict) or set(reference) != {"retrieval_id", "version_id"}:
                _integrity()
            key = (_id(reference["version_id"]), _id(reference["retrieval_id"]))
            if key in references:
                _integrity()
            references.add(key)
        # Check every saved handle's run binding without loading uncited text.
        # Only actual finding quotations justify reading an existing body here.
        bound = set(
            self.session.execute(
                select(SourceVersion.id, SourceRetrieval.id)
                .join(SourceRetrieval, SourceRetrieval.version_id == SourceVersion.id)
                .join(RunEvidenceSource, RunEvidenceSource.retrieval_id == SourceRetrieval.id)
                .where(
                    RunEvidenceSource.run_id == row.run_id,
                    SourceRetrieval.id.in_([key[1] for key in references]),
                )
                .limit(201)
            )
        )
        if bound != references:
            _integrity()
        # Stored positive envelopes deliberately omit bodies. Only immutable,
        # run-bound source artifacts are allowed to reconstruct this check.
        if payload.get("sources") != []:
            _integrity()
        packet = _typed(ResearchResult, payload)
        if packet.status != row.status or packet.mode != self.run(row.run_id).mode:
            _integrity()
        expected = (
            [(item, item.side, None) for item in packet.finding.evidence]
            if row.status == "bridge"
            else [
                (item, "answer", claim.id)
                for claim in packet.finding.claims
                for item in claim.evidence
            ]
        )
        quotes = self.session.scalars(
            select(EvidenceQuote)
            .where(EvidenceQuote.result_id == row.id)
            .order_by(EvidenceQuote.ordinal)
            .limit(65)
        ).all()
        if (
            not 1 <= len(quotes) <= 64
            or len(quotes) != len(packet.quotes)
            or len(quotes) != len(expected)
        ):
            _integrity()
        documents = {}
        for ordinal, (saved, quote, (evidence, side, claim_id)) in enumerate(
            zip(quotes, packet.quotes, expected, strict=True)
        ):
            if (saved.version_id, saved.retrieval_id) not in references:
                _integrity()
            version, retrieval, document = self.document(
                row.run_id, saved.retrieval_id, saved.version_id
            )
            previous = documents.setdefault(document.source_id, document)
            if (
                saved.ordinal != ordinal
                or saved.run_id != row.run_id
                or saved.version_id != version.id
                or saved.retrieval_id != retrieval.id
                or (
                    saved.side,
                    saved.claim_id,
                    saved.quote,
                    saved.supports,
                    saved.start_offset,
                    saved.end_offset,
                )
                != (quote.side, quote.claim_id, quote.quote, quote.supports, quote.start, quote.end)
                or (quote.source_id, quote.side, quote.claim_id, quote.quote, quote.supports)
                != (evidence.source_id, side, claim_id, evidence.quote, evidence.supports)
                or quote.source_sha256 != document.content_sha256
                or quote.content_kind != document.content_kind
                or not 0 <= quote.start < quote.end <= len(document.text)
                or document.text[quote.start : quote.end] != quote.quote
                or previous != document
                or quote.source_id != document.source_id
            ):
                _integrity()
        packet = packet.model_copy(update={"sources": tuple(documents.values())})
        self.packets[row.id] = packet
        return packet

    def applied_for_candidate(self, row):
        rows = self.session.scalars(
            select(FindingAssessment)
            .where(
                FindingAssessment.candidate_result_id == row.id,
                FindingAssessment.applied.is_(True),
            )
            .limit(2)
        ).all()
        if len(rows) != 1 or rows[0].run_id != row.run_id or rows[0].disposition != "revised":
            _integrity()
        return rows[0]

    def assessment(self, row):
        if row.id in self.assessments:
            return self.assessments[row.id]
        if len(self.assessments) >= MAX_APPLIED_ASSESSMENTS:
            raise ScientificContextError("artifact_context_too_large")
        request = self.session.get(AssessmentRequest, row.run_id)
        run = self.run(row.run_id)
        if (
            request is None
            or run.task_kind != "reassess_finding"
            or run.status != "succeeded"
            or not row.applied
            or row.disposition not in ("unchanged", "revised", "unsupported")
            or _hash(_canonical(row.payload)) != row.payload_sha256
            or _hash(_canonical(request.basis)) != request.basis_sha256
            or row.payload.get("basis") != request.basis
            or request.expected_head_revision + 1 != row.head_revision
        ):
            _integrity()
        basis = _typed(AssessmentBasis, request.basis)
        result = _typed(AssessmentResult, row.payload["result"])
        prior = self.result(request.source_result_id)
        prior_packet = self.packet(prior)
        prior_run = self.run(prior.run_id)
        prior_request = (
            self.session.get(AssessmentRequest, prior.run_id)
            if prior_run.task_kind == "reassess_finding"
            else None
        )
        expected_root = prior_request.root_result_id if prior_request is not None else prior.id
        if (
            basis.prior_reference != _reference(prior)
            or basis.prior_finding != prior_packet.finding
            or basis.prior_quotes != prior_packet.quotes
            or basis.prior_overview_binding != prior_packet.overview_binding
            or basis.field_selections != prior_packet.field_selections
            or request.root_result_id != expected_root
            or request.source_sha256 != prior.content_sha256
            or prior.created_at >= run.created_at
            or result.status != "completed"
            or result.disposition != row.disposition
            or row.payload.get("verified_passages")
            != [q.model_dump(mode="json") for q in result.quotes]
        ):
            _integrity()
        candidate_reference = None
        if row.disposition == "revised":
            candidate = self.result(row.candidate_result_id)
            if candidate.run_id != run.id or result.revised_result is None:
                _integrity()
            raw_candidate = dict(candidate.payload["result"])
            if raw_candidate != result.revised_result.model_dump(mode="json"):
                _integrity()
            candidate_reference = _reference(candidate)
        elif row.candidate_result_id is not None or result.revised_result is not None:
            _integrity()
        quotes = []
        observed = set(result.observed_retrieval_ids)
        observed_metadata = row.payload.get("observed_sources")
        if not isinstance(observed_metadata, list) or len(observed_metadata) > 200:
            _integrity()
        metadata_by_id = {item["retrieval_id"]: item for item in observed_metadata}
        if len(metadata_by_id) != len(observed_metadata):
            _integrity()
        for quote in result.quotes:
            version, retrieval, document = self.document(run.id, quote.retrieval_id)
            expected_metadata = {
                "retrieval_id": str(retrieval.id),
                "version_id": str(version.id),
                "source_id": document.source_id,
                "title": document.title,
                "url": document.url,
                "bibliography": (
                    document.bibliography.model_dump(mode="json")
                    if document.bibliography is not None
                    else None
                ),
                "content_sha256": document.content_sha256,
                "content_kind": document.content_kind,
                **(
                    {"coverage": document.coverage.model_dump(mode="json")}
                    if document.coverage is not None
                    else {}
                ),
            }
            if (
                quote.retrieval_id not in observed
                or metadata_by_id.get(quote.retrieval_id) != expected_metadata
                or quote.source_id != document.source_id
                or quote.source_sha256 != document.content_sha256
                or quote.content_kind != document.content_kind
                or quote.coverage != document.coverage
                or not 0 <= quote.start < quote.end <= len(document.text)
                or document.text[quote.start : quote.end] != quote.quote
            ):
                _integrity()
            quotes.append(
                ScientificAssessmentQuote(
                    retrieval_id=str(retrieval.id),
                    version_id=str(version.id),
                    source=SourceReference.model_validate_json(
                        _canonical(
                            {
                                key: value
                                for key, value in document.model_dump(mode="json").items()
                                if key in ("source_id", "title", "url", "bibliography", "coverage")
                            }
                        )
                    ),
                    source_sha256=quote.source_sha256,
                    content_kind=quote.content_kind,
                    quote=quote.quote,
                    supports=quote.supports,
                    start=quote.start,
                    end=quote.end,
                )
            )
        value = AppliedAssessmentContext(
            assessment_id=str(row.id),
            run_id=str(row.run_id),
            payload_sha256=row.payload_sha256,
            head_revision=row.head_revision,
            prior_reference=_reference(prior),
            candidate_reference=candidate_reference,
            disposition=row.disposition,
            rationale=tuple(
                _typed(ScientificAssessmentStatement, item.model_dump(mode="json"))
                for item in result.rationale
            ),
            quotes=tuple(quotes),
            limitations=result.limitations,
        )
        self.assessments[row.id] = value
        return value

    def origin(self, row):
        if row.id in self.origins:
            return self.origins[row.id]
        stages = self.session.scalars(
            select(StageCommit).where(StageCommit.result_id == row.id).limit(2)
        ).all()
        if not stages:
            self.origins[row.id] = None
            return None
        if len(stages) != 1:
            _integrity()
        stage = stages[0]
        receipt = _receipt(stage)
        from polymath.stage_protocols import resolve_stage_protocol

        protocol = resolve_stage_protocol(self.session, self.run(row.run_id))
        context = self.session.get(StageContextRecord, (row.run_id, stage.context_ordinal))
        if (
            protocol is None
            or context is None
            or receipt.reference != _reference(row)
            or receipt.action_ordinal != stage.action_ordinal
            or receipt.model_request_id != stage.model_request_id
            or _hash(_canonical(context.context)) != context.context_sha256
        ):
            _integrity()
        scope = _typed(StageContext, context.context)
        if scope.ordinal != stage.context_ordinal or scope.parent_refs != receipt.parent_refs:
            _integrity()
        packet = self.packet(row)
        if protocol.scientific_sha256(scope, packet) != stage.scientific_sha256:
            _integrity()
        parents = self.session.scalars(
            select(StageParent)
            .where(StageParent.stage_id == stage.id)
            .order_by(StageParent.ordinal)
            .limit(6)
        ).all()
        actual, references, revision = [], [], None
        for ordinal, parent in enumerate(parents):
            value = self.result(parent.parent_result_id)
            reference = _reference(value)
            if (
                parent.ordinal != ordinal
                or str(parent.parent_run_id) != reference.run_id
                or parent.parent_sha256 != reference.content_sha256
                or parent.kind not in ("support", "revision")
            ):
                _integrity()
            parent_stage = self.session.scalar(
                select(StageCommit).where(StageCommit.result_id == value.id)
            )
            if value.run_id == row.run_id:
                if (
                    parent_stage is None
                    or parent.parent_stage_id != parent_stage.id
                    or parent_stage.ordinal >= stage.ordinal
                ):
                    _integrity()
            elif (
                parent.parent_stage_id is not None
                or value.created_at >= self.run(row.run_id).created_at
            ):
                _integrity()
            if parent.kind == "revision":
                if revision is not None or ordinal != len(parents) - 1:
                    _integrity()
                revision = reference
            else:
                references.append(reference)
            actual.append((reference, "premise" if parent.kind == "support" else "revision", None))
        if tuple(references) != receipt.parent_refs:
            _integrity()
        protocol.validate_saved_stage(receipt, scope, packet, revision_of=revision)
        self.parents[row.id] = actual
        value = StageOriginContext(
            receipt=receipt,
            receipt_sha256=stage.receipt_sha256,
            relationship="exact_stage_result",
            revision_of=revision,
        )
        self.origins[row.id] = value
        return value

    def outgoing(self, row):
        origin = self.origin(row)
        if origin is not None:
            return self.parents[row.id]
        run = self.run(row.run_id)
        if run.task_kind == "reassess_finding":
            assessment = self.assessment(self.applied_for_candidate(row))
            return [(assessment.prior_reference, "reassessment", assessment)]
        links = self.session.scalars(
            select(ResearchRunParent)
            .where(ResearchRunParent.run_id == row.run_id)
            .order_by(ResearchRunParent.ordinal)
            .limit(5)
        ).all()
        if links and (run.mode != "explore" or not 2 <= len(links) <= 4):
            _integrity()
        values = []
        for ordinal, link in enumerate(links):
            parent = self.result(link.parent_result_id)
            reference = _reference(parent)
            if (
                link.ordinal != ordinal
                or parent.status != "bridge"
                or link.parent_run_id != parent.run_id
                or link.content_sha256 != parent.content_sha256
                or parent.run_id == row.run_id
                or parent.created_at >= run.created_at
                or reference not in self.packet(row).memory_reads
            ):
                _integrity()
            values.append((reference, "premise", None))
        return values

    def visit(self, identifier):
        if identifier in self.active:
            _integrity()
        if identifier in self.visited:
            return
        row = self.result(identifier)
        self.active.add(identifier)
        self.visited.add(identifier)
        raw_binding = row.payload["result"].get("overview_binding")
        self.nodes.append(
            ScientificAncestor(
                reference=_reference(row),
                finding=self.packet(row).finding,
                stage_origin=self.origin(row),
                overview_binding=_typed(AskOverviewBinding, raw_binding)
                if raw_binding is not None
                else None,
            )
        )
        edges = []
        for ordinal, (parent, kind, assessment) in enumerate(self.outgoing(row)):
            if sum(len(value) for value in self.edges.values()) + len(edges) >= MAX_CONTEXT_EDGES:
                raise ScientificContextError("artifact_context_too_large")
            edge = ScientificParentEdge(
                child=_reference(row),
                parent=parent,
                ordinal=ordinal,
                kind=kind,
                assessment_id=assessment.assessment_id if assessment else None,
                assessment_sha256=assessment.payload_sha256 if assessment else None,
            )
            edges.append(edge)
            self.visit(_id(parent.result_id))
        self.edges[identifier] = edges
        self.active.remove(identifier)

    def revision(self, source):
        run = self.run(source.run_id)
        request = (
            self.session.get(AssessmentRequest, source.run_id)
            if run.task_kind == "reassess_finding"
            else None
        )
        root = self.result(request.root_result_id) if request is not None else source
        head = self.session.get(FindingHead, root.id, populate_existing=True)
        rows = self.session.scalars(
            select(FindingAssessment)
            .join(AssessmentRequest, AssessmentRequest.run_id == FindingAssessment.run_id)
            .where(AssessmentRequest.root_result_id == root.id, FindingAssessment.applied.is_(True))
            .order_by(FindingAssessment.head_revision)
            .limit(MAX_APPLIED_ASSESSMENTS + 1)
        ).all()
        if len(rows) > MAX_APPLIED_ASSESSMENTS:
            raise ScientificContextError("artifact_context_too_large")
        assessments = tuple(self.assessment(row) for row in rows)
        if head is None:
            if assessments or source.id != root.id:
                _integrity()
            revision = 0
        else:
            if (
                head.current_result_id != source.id
                or head.state != "supported"
                or head.revision != len(rows)
                or head.last_assessment_id != (rows[-1].id if rows else None)
            ):
                _integrity()
            revision = head.revision
        return SourceRevisionContext(
            root_reference=_reference(root),
            current_reference=_reference(source),
            revision=revision,
            applied_assessments=assessments,
        )

    def formalisation(self, identifier, source):
        from polymath.artifacts import ArtifactError
        from polymath.artifacts import _stored as _stored_artifact

        if identifier is None:
            return None
        row = self.session.scalar(
            select(GeneratedArtifact)
            .join(ResearchRun, ResearchRun.id == GeneratedArtifact.run_id)
            .join(ResearchRunAccess, ResearchRunAccess.run_id == GeneratedArtifact.run_id)
            .where(
                GeneratedArtifact.id == _id(identifier),
                GeneratedArtifact.kind == "formalise",
                GeneratedArtifact.status == "ready",
                ResearchRun.status == "succeeded",
                ResearchRunAccess.session_hash == self.owner,
            )
        )
        if row is None:
            raise ScientificContextError("artifact_context_not_available")
        try:
            stored = _stored_artifact(row)
        except ArtifactError:
            _integrity()
        if stored.basis["reference"] != _reference(source).model_dump(mode="json"):
            _integrity()
        # The prior artifact's saved basis is authoritative for its original
        # context. Never substitute the requested source's newer admission state.
        context = stored.basis.get("scientific_context")
        return PriorArtifactContextReference(
            artifact_id=str(row.id),
            source_reference=_reference(source),
            payload_sha256=stored.payload_sha256,
            basis_sha256=stored.basis_sha256,
            context_sha256=context["context_sha256"] if context else None,
        )


def build_artifact_scientific_context(
    session: Session, *, owner_hash: str, source_result_id, formalisation_id=None
) -> ArtifactScientificContext:
    """Build a bounded read-only snapshot; caller owns transaction/admission.

    Historical ancestors are exact saved hypotheses, even if later unsupported.
    Only the requested source must be current-supported. No fresh eligibility is
    conferred on any ancestor, no confidence is copied to a revised descendant,
    and this function performs no sidecar write or native activation binding.
    """
    if (
        not isinstance(session, Session)
        or not isinstance(owner_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", owner_hash)
    ):
        raise ScientificContextError("artifact_context_not_available")
    try:
        with session.no_autoflush:
            live = session.scalar(
                select(BrowserSession.token_hash).where(
                    BrowserSession.token_hash == owner_hash,
                    BrowserSession.expires_at > func.clock_timestamp(),
                )
            )
            if live is None:
                raise ScientificContextError("artifact_context_not_available")
            builder = _Builder(session, owner_hash)
            source = builder.result(_id(source_result_id))
            require_current_supported(session, owner_hash, source.id)
            revision = builder.revision(source)
            builder.visit(source.id)
            original = builder.origin(builder.result(_id(revision.root_reference.result_id)))
            origin = (
                original.model_copy(update={"relationship": "reassessed_descendant"})
                if original is not None and source.id != _id(revision.root_reference.result_id)
                else original
            )
            edges = tuple(
                edge
                for node in builder.nodes
                for edge in builder.edges[_id(node.reference.result_id)]
            )
            prior_formalisation = builder.formalisation(formalisation_id, source)
            raw_context = {
                "protocol": ARTIFACT_CONTEXT_PROTOCOL,
                "source_reference": _reference(source).model_dump(mode="json"),
                "source_revision": revision.model_dump(mode="json"),
                "stage_origin": origin.model_dump(mode="json") if origin else None,
                "ancestry_nodes": [node.model_dump(mode="json") for node in builder.nodes],
                "ancestry_edges": [edge.model_dump(mode="json") for edge in edges],
                "prior_formalisation": (
                    prior_formalisation.model_dump(mode="json") if prior_formalisation else None
                ),
            }
            if len(_canonical(raw_context).encode("utf-8")) > MAX_CONTEXT_BYTES:
                raise ScientificContextError("artifact_context_too_large")
            context = _typed(ArtifactScientificContext, raw_context)
            require_current_supported(session, owner_hash, source.id)
            # The caller still must pin this inside its admission transaction.
            # A concurrent unchanged assessment can keep the same source current
            # while advancing its relevant scientific history; detect that too.
            latest_head = session.get(
                FindingHead, _id(revision.root_reference.result_id), populate_existing=True
            )
            if latest_head is not None and (
                latest_head.current_result_id != source.id
                or latest_head.state != "supported"
                or latest_head.revision != revision.revision
                or latest_head.last_assessment_id
                != (
                    _id(revision.applied_assessments[-1].assessment_id)
                    if revision.applied_assessments
                    else None
                )
            ):
                raise ScientificContextError("artifact_context_changed")
            return context
    except ScientificContextError:
        raise
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RecursionError,
        DiscoveryError,
        EvidenceError,
    ):
        raise ScientificContextError("artifact_context_integrity_failed") from None


def validated_owned_result_packet(
    session: Session, *, owner_hash: str, result_id
) -> ResearchResult:
    """Read exact owned historical evidence without fresh scientific authority.

    No browser-expiry, current-head or supported-state check belongs here: saved
    unsupported/superseded findings remain valid historical inputs. Uncommitted
    ladder positives and unapplied reassessment candidates are rejected. Only
    cited saved bodies are reconstructed, and no read receipt or write occurs.
    """
    if (
        not isinstance(session, Session)
        or not isinstance(owner_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", owner_hash)
    ):
        raise ScientificContextError("artifact_context_not_available")
    try:
        with session.no_autoflush:
            builder = _Builder(session, owner_hash)
            row = builder.result(_id(result_id))
            stage = session.scalar(select(StageCommit).where(StageCommit.result_id == row.id))
            if stage is not None and _receipt(stage).reference != _reference(row):
                _integrity()
            if builder.run(row.run_id).task_kind == "reassess_finding":
                # Check the actual applied proof and its exact candidate bytes;
                # a succeeded assessment run alone is not scientific authority.
                builder.assessment(builder.applied_for_candidate(row))
            return builder.packet(row)
    except ScientificContextError:
        raise
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        RecursionError,
        DiscoveryError,
        EvidenceError,
    ):
        raise ScientificContextError("artifact_context_integrity_failed") from None
