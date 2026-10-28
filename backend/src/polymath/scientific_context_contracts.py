"""Pure scientific-context contracts; no persistence, truth, or spending authority.

An envelope digest checks its supplied bytes. The integrating store must separately
verify every reference, receipt and applied-assessment claim against owned,
immutable records in its admission transaction. These models do not do that.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from polymath.discovery_contracts import StageCommitReceipt
from polymath.research.models import (
    AnswerClaim,
    AnswerFinding,
    ArtifactId,
    AskOverviewBinding,
    BridgeFinding,
    ClaimId,
    ContentHash,
    MemoryReadReference,
    SourceReference,
    StrictModel,
    Text,
    _validate_public_text,
)

ARTIFACT_CONTEXT_PROTOCOL = "polymath-artifact-scientific-context-v1"
ASK_OVERVIEW_PROTOCOL = "polymath-ask-claim-overview-v1"
ASK_REASSESSMENT_OVERVIEW_PROTOCOL = "polymath-ask-reassessment-overview-v1"
MAX_CONTEXT_BYTES = 262_144
MAX_CONTEXT_NODES = 128
MAX_CONTEXT_EDGES = 512
MAX_APPLIED_ASSESSMENTS = 64
MAX_OVERVIEW_CHARACTERS = 3000


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _tuple(value):
    return tuple(value) if type(value) is list else value


class ScientificModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    @model_validator(mode="after")
    def public_text(self):
        _validate_public_text(self.model_dump(mode="json"))
        return self


class ArtifactScientificContextIntent(ScientificModel):
    protocol: Literal["polymath-artifact-scientific-context-v1"] = ARTIFACT_CONTEXT_PROTOCOL


class AskOverviewIntent(ScientificModel):
    protocol: Literal["polymath-ask-claim-overview-v1"] = ASK_OVERVIEW_PROTOCOL


class AskReassessmentOverviewIntent(ScientificModel):
    protocol: Literal["polymath-ask-reassessment-overview-v1"] = ASK_REASSESSMENT_OVERVIEW_PROTOCOL


def _overview_text(claims: tuple[AnswerClaim, ...] | list[AnswerClaim], ids: tuple[str, ...]):
    by_id = {claim.id: claim for claim in claims}
    if len(by_id) != len(claims):
        raise ValueError("ask_claim_ids_not_unique")
    if len(set(ids)) != len(ids):
        raise ValueError("ask_overview_duplicate_claim")
    if any(identifier not in by_id for identifier in ids):
        raise ValueError("ask_overview_unknown_claim")
    text = "\n\n".join(by_id[identifier].text for identifier in ids)
    if len(text) > MAX_OVERVIEW_CHARACTERS:
        raise ValueError("ask_overview_too_long")
    return text


class BoundAnswerDraft(ScientificModel):
    outcome: Literal["answer"] = "answer"
    question: Annotated[Text, Field(max_length=4000)]
    claims: Annotated[tuple[AnswerClaim, ...], Field(min_length=1, max_length=8)]
    overview_claim_ids: Annotated[tuple[ClaimId, ...], Field(min_length=1, max_length=8)]
    limitations: Annotated[
        tuple[Annotated[Text, Field(max_length=1000)], ...], Field(min_length=1, max_length=8)
    ]
    unanswered_questions: Annotated[
        tuple[Annotated[Text, Field(max_length=1000)], ...], Field(max_length=8)
    ] = ()

    _json_arrays = field_validator(
        "claims", "overview_claim_ids", "limitations", "unanswered_questions", mode="before"
    )(_tuple)

    @model_validator(mode="after")
    def selected_existing_claims(self):
        _overview_text(self.claims, self.overview_claim_ids)
        return self

    def to_answer(self) -> AnswerFinding:
        # Existing nested finding models contain lists. Revalidate their exact
        # current bytes instead of trusting a previously constructed instance.
        value = BoundAnswerDraft.model_validate_json(self.model_dump_json())
        return AnswerFinding(
            outcome="answer",
            question=value.question,
            summary=_overview_text(value.claims, value.overview_claim_ids),
            claims=list(value.claims),
            limitations=list(value.limitations),
            unanswered_questions=list(value.unanswered_questions),
        )

    def overview_binding(self) -> AskOverviewBinding:
        value = BoundAnswerDraft.model_validate_json(self.model_dump_json())
        return AskOverviewBinding(claim_ids=value.overview_claim_ids)


def validate_answer_overview(finding: AnswerFinding, binding: AskOverviewBinding) -> None:
    if not isinstance(finding, AnswerFinding) or not isinstance(binding, AskOverviewBinding):
        raise ValueError("ask_overview_binding_invalid")
    finding = AnswerFinding.model_validate_json(finding.model_dump_json())
    binding = AskOverviewBinding.model_validate_json(binding.model_dump_json())
    if finding.summary != _overview_text(finding.claims, binding.claim_ids):
        raise ValueError("ask_overview_summary_mismatch")


def result_overview_metadata(binding: AskOverviewBinding | None) -> dict:
    if binding is None:
        return {}
    value = AskOverviewBinding.model_validate_json(binding.model_dump_json())
    return {"overview_binding": value.model_dump(mode="json")}


def validate_result_overview(data: Mapping, *, expected: bool | None = None) -> None:
    """Check historical metadata, or a caller-supplied fresh admission expectation.

    ``expected`` is not authority: only the store can determine it from an actual
    immutable intent/profile. This helper neither checks quotes nor verifies
    the semantic truth or classification of any claim.
    """
    if not isinstance(data, Mapping) or (expected is not None and type(expected) is not bool):
        raise ValueError("ask_overview_binding_invalid")
    if "overview_binding" not in data:
        if expected is True and data.get("status") == "answer":
            raise ValueError("ask_overview_binding_required")
        return
    raw = data["overview_binding"]
    if raw is None:
        raise ValueError("ask_overview_binding_invalid")
    if expected is False:
        raise ValueError("ask_overview_not_admitted")
    if data.get("mode") != "ask" or data.get("status") != "answer":
        raise ValueError("ask_overview_wrong_result")
    try:
        finding = data.get("finding")
        if not isinstance(finding, AnswerFinding):
            finding = AnswerFinding.model_validate_json(_canonical(finding))
        binding = (
            raw
            if isinstance(raw, AskOverviewBinding)
            else AskOverviewBinding.model_validate_json(_canonical(raw))
        )
        validate_answer_overview(finding, binding)
    except (TypeError, ValueError):
        raise ValueError("ask_overview_binding_invalid") from None


class ScientificAssessmentCitation(ScientificModel):
    retrieval_id: ArtifactId
    quote: Annotated[str, Field(min_length=10, max_length=4000)]
    supports: Annotated[Text, Field(max_length=1500)]


class ScientificAssessmentStatement(ScientificModel):
    text: Annotated[Text, Field(max_length=2000)]
    citations: Annotated[tuple[ScientificAssessmentCitation, ...], Field(max_length=8)] = ()

    _json_arrays = field_validator("citations", mode="before")(_tuple)


class ScientificAssessmentQuote(ScientificAssessmentCitation):
    version_id: ArtifactId
    source: SourceReference
    source_sha256: ContentHash
    content_kind: Literal["abstract", "full_text"]
    start: Annotated[int, Field(ge=0, lt=20_000)]
    end: Annotated[int, Field(ge=1, le=20_000)]

    @field_validator("source", mode="before")
    @classmethod
    def exact_source_reference(cls, value):
        if isinstance(value, SourceReference):
            return SourceReference.model_validate_json(value.model_dump_json())
        if isinstance(value, Mapping):
            return SourceReference.model_validate_json(_canonical(value))
        return value

    @model_validator(mode="after")
    def exact_span(self):
        if self.end - self.start != len(self.quote):
            raise ValueError("scientific_assessment_quote_span_invalid")
        coverage = self.source.coverage
        if coverage is not None and (
            self.content_kind != "full_text" or self.end > coverage.end - coverage.start
        ):
            raise ValueError("scientific_assessment_quote_coverage_invalid")
        return self


class AppliedAssessmentContext(ScientificModel):
    assessment_id: ArtifactId
    run_id: ArtifactId
    payload_sha256: ContentHash
    applied: Literal[True] = True
    head_revision: Annotated[int, Field(ge=1, le=2**31 - 1)]
    prior_reference: MemoryReadReference
    candidate_reference: MemoryReadReference | None = None
    disposition: Literal["unchanged", "revised", "unsupported"]
    rationale: Annotated[
        tuple[ScientificAssessmentStatement, ...], Field(min_length=1, max_length=8)
    ]
    quotes: Annotated[tuple[ScientificAssessmentQuote, ...], Field(max_length=64)] = ()
    limitations: Annotated[
        tuple[Annotated[Text, Field(max_length=1000)], ...], Field(min_length=1, max_length=8)
    ]

    _json_arrays = field_validator("rationale", "quotes", "limitations", mode="before")(_tuple)

    @field_validator("applied", mode="before")
    @classmethod
    def actual_boolean(cls, value):
        if value is not True:
            raise ValueError("scientific_assessment_not_applied")
        return value

    @model_validator(mode="after")
    def consistent_projection(self):
        if any(not statement.citations for statement in self.rationale):
            raise ValueError("scientific_assessment_rationale_uncited")
        if (self.disposition == "revised") != (self.candidate_reference is not None):
            raise ValueError("scientific_assessment_candidate_invalid")
        if self.candidate_reference is not None and (
            self.candidate_reference.result_id == self.prior_reference.result_id
            or self.candidate_reference.run_id != self.run_id
        ):
            raise ValueError("scientific_assessment_candidate_invalid")
        citations = tuple(item for statement in self.rationale for item in statement.citations)
        if len(citations) != len(self.quotes) or any(
            (citation.retrieval_id, citation.quote, citation.supports)
            != (quote.retrieval_id, quote.quote, quote.supports)
            for citation, quote in zip(citations, self.quotes, strict=True)
        ):
            raise ValueError("scientific_assessment_citation_mismatch")
        return self


class SourceRevisionContext(ScientificModel):
    root_reference: MemoryReadReference
    current_reference: MemoryReadReference
    revision: Annotated[int, Field(ge=0, le=MAX_APPLIED_ASSESSMENTS)]
    state_at_admission: Literal["supported"] = "supported"
    applied_assessments: Annotated[
        tuple[AppliedAssessmentContext, ...], Field(max_length=MAX_APPLIED_ASSESSMENTS)
    ] = ()

    _json_arrays = field_validator("applied_assessments", mode="before")(_tuple)

    @model_validator(mode="after")
    def contiguous_applied_history(self):
        if len(self.applied_assessments) != self.revision:
            raise ValueError("scientific_assessment_history_incomplete")
        cursor, supported = self.root_reference, True
        assessment_ids, run_ids, result_ids = set(), set(), {cursor.result_id}
        for ordinal, assessment in enumerate(self.applied_assessments, start=1):
            if (
                assessment.head_revision != ordinal
                or assessment.prior_reference != cursor
                or assessment.assessment_id in assessment_ids
                or assessment.run_id in run_ids
            ):
                raise ValueError("scientific_assessment_history_invalid")
            assessment_ids.add(assessment.assessment_id)
            run_ids.add(assessment.run_id)
            if assessment.disposition == "revised":
                candidate = assessment.candidate_reference
                if candidate.result_id in result_ids:
                    raise ValueError("scientific_assessment_history_cycle")
                result_ids.add(candidate.result_id)
                cursor, supported = candidate, True
            elif assessment.disposition == "unsupported":
                supported = False
            # An unchanged result preserves an unsupported head in the store.
        if cursor != self.current_reference or supported is not True:
            raise ValueError("scientific_assessment_current_mismatch")
        return self


class StageOriginContext(ScientificModel):
    receipt: StageCommitReceipt
    receipt_sha256: ContentHash
    relationship: Literal["exact_stage_result", "reassessed_descendant"]
    revision_of: MemoryReadReference | None = None

    @field_validator("receipt", mode="before")
    @classmethod
    def exact_receipt_contract(cls, value):
        if isinstance(value, StageCommitReceipt):
            return StageCommitReceipt.model_validate_json(value.model_dump_json())
        if isinstance(value, Mapping):
            return StageCommitReceipt.model_validate_json(_canonical(value))
        return value

    @model_validator(mode="after")
    def exact_receipt(self):
        if _sha(self.receipt.model_dump(mode="json")) != self.receipt_sha256:
            raise ValueError("scientific_stage_receipt_hash_mismatch")
        if self.revision_of is not None and self.revision_of == self.receipt.reference:
            raise ValueError("scientific_stage_revision_self_reference")
        return self


class ScientificAncestor(ScientificModel):
    reference: MemoryReadReference
    finding: BridgeFinding | AnswerFinding
    stage_origin: StageOriginContext | None = None
    overview_binding: AskOverviewBinding | None = None

    @model_validator(mode="after")
    def exact_metadata_scope(self):
        if self.stage_origin is not None and (
            not isinstance(self.finding, BridgeFinding)
            or self.stage_origin.receipt.reference != self.reference
            or self.stage_origin.relationship != "exact_stage_result"
        ):
            raise ValueError("scientific_ancestor_stage_scope_invalid")
        if self.overview_binding is not None:
            validate_answer_overview(self.finding, self.overview_binding)
        return self


class ScientificParentEdge(ScientificModel):
    child: MemoryReadReference
    parent: MemoryReadReference
    ordinal: Annotated[int, Field(ge=0, le=4)]
    kind: Literal["premise", "revision", "reassessment"]
    assessment_id: ArtifactId | None = None
    assessment_sha256: ContentHash | None = None

    @model_validator(mode="after")
    def distinct_and_typed(self):
        if self.child.result_id == self.parent.result_id:
            raise ValueError("scientific_ancestry_self_reference")
        if (self.assessment_id is not None) != (self.assessment_sha256 is not None) or (
            (self.kind == "reassessment") != (self.assessment_id is not None)
        ):
            raise ValueError("scientific_ancestry_assessment_binding_invalid")
        return self


class PriorArtifactContextReference(ScientificModel):
    artifact_id: ArtifactId
    source_reference: MemoryReadReference
    payload_sha256: ContentHash
    basis_sha256: ContentHash
    context_sha256: ContentHash | None = None


class ArtifactScientificContext(ScientificModel):
    protocol: Literal["polymath-artifact-scientific-context-v1"] = ARTIFACT_CONTEXT_PROTOCOL
    source_reference: MemoryReadReference
    source_revision: SourceRevisionContext
    stage_origin: StageOriginContext | None = None
    ancestry_nodes: Annotated[
        tuple[ScientificAncestor, ...], Field(min_length=1, max_length=MAX_CONTEXT_NODES)
    ]
    ancestry_edges: Annotated[
        tuple[ScientificParentEdge, ...], Field(max_length=MAX_CONTEXT_EDGES)
    ] = ()
    prior_formalisation: PriorArtifactContextReference | None = None

    _json_arrays = field_validator("ancestry_nodes", "ancestry_edges", mode="before")(_tuple)

    @model_validator(mode="after")
    def bound_graph(self):
        if self.source_reference != self.source_revision.current_reference:
            raise ValueError("scientific_context_source_mismatch")
        nodes = {item.reference.result_id: item for item in self.ancestry_nodes}
        if len(nodes) != len(self.ancestry_nodes):
            raise ValueError("scientific_ancestry_duplicate_result")
        if self.ancestry_nodes[0].reference != self.source_reference:
            raise ValueError("scientific_ancestry_source_not_first")

        def known(reference):
            node = nodes.get(reference.result_id)
            if node is None or node.reference != reference:
                raise ValueError("scientific_ancestry_reference_mismatch")

        known(self.source_revision.root_reference)
        children = defaultdict(list)
        positions = set()
        for edge in self.ancestry_edges:
            known(edge.child)
            known(edge.parent)
            position = (edge.child.result_id, edge.ordinal)
            if position in positions:
                raise ValueError("scientific_ancestry_duplicate_edge")
            positions.add(position)
            children[edge.child.result_id].append(edge)
        for edges in children.values():
            if tuple(edge.ordinal for edge in edges) != tuple(range(len(edges))):
                raise ValueError("scientific_ancestry_order_invalid")

        visited, active, order = set(), set(), []

        def visit(identifier):
            if identifier in active:
                raise ValueError("scientific_ancestry_cycle")
            if identifier in visited:
                return
            active.add(identifier)
            visited.add(identifier)
            order.append(identifier)
            for edge in children[identifier]:
                visit(edge.parent.result_id)
            active.remove(identifier)

        visit(self.source_reference.result_id)
        if len(visited) != len(nodes):
            raise ValueError("scientific_ancestry_disconnected")
        if tuple(order) != tuple(node.reference.result_id for node in self.ancestry_nodes):
            raise ValueError("scientific_ancestry_order_invalid")
        if tuple(self.ancestry_edges) != tuple(
            edge for identifier in order for edge in children[identifier]
        ):
            raise ValueError("scientific_ancestry_order_invalid")

        origins = [
            (node.reference, node.stage_origin)
            for node in self.ancestry_nodes
            if node.stage_origin is not None
        ]
        root_origin = nodes[self.source_revision.root_reference.result_id].stage_origin
        if (root_origin is not None) != (self.stage_origin is not None):
            raise ValueError("scientific_context_stage_origin_missing")
        if self.stage_origin is not None:
            origin = self.stage_origin
            if origin.receipt.reference != self.source_revision.root_reference or (
                (origin.relationship == "exact_stage_result")
                != (origin.receipt.reference == self.source_reference)
            ):
                raise ValueError("scientific_context_stage_scope_invalid")
            root = nodes[origin.receipt.result_id]
            if root.stage_origin is None or (
                root.stage_origin.receipt != origin.receipt
                or root.stage_origin.receipt_sha256 != origin.receipt_sha256
                or root.stage_origin.revision_of != origin.revision_of
            ):
                raise ValueError("scientific_context_stage_origin_missing")
        for reference, origin in origins:
            expected = [(parent, "premise") for parent in origin.receipt.parent_refs]
            if origin.revision_of is not None:
                expected.append((origin.revision_of, "revision"))
            actual = [(edge.parent, edge.kind) for edge in children[reference.result_id]]
            if actual != expected:
                raise ValueError("scientific_context_stage_parents_mismatch")

        for assessment in self.source_revision.applied_assessments:
            known(assessment.prior_reference)
            if assessment.candidate_reference is not None:
                known(assessment.candidate_reference)
                expected = (
                    assessment.prior_reference,
                    assessment.assessment_id,
                    assessment.payload_sha256,
                )
                actual = [
                    (edge.parent, edge.assessment_id, edge.assessment_sha256)
                    for edge in children[assessment.candidate_reference.result_id]
                    if edge.kind == "reassessment"
                ]
                if actual != [expected]:
                    raise ValueError("scientific_context_assessment_edge_missing")
        if self.prior_formalisation is not None and (
            self.prior_formalisation.source_reference != self.source_reference
        ):
            raise ValueError("scientific_context_formalisation_source_mismatch")
        if len(_canonical(self.model_dump(mode="json")).encode("utf-8")) > MAX_CONTEXT_BYTES:
            raise ValueError("artifact_context_too_large")
        return self


class ArtifactScientificContextBinding(ScientificModel):
    context: ArtifactScientificContext
    context_sha256: ContentHash

    @model_validator(mode="after")
    def exact_context(self):
        if scientific_context_sha256(self.context) != self.context_sha256:
            raise ValueError("scientific_context_hash_mismatch")
        return self


def canonical_scientific_context_json(context: ArtifactScientificContext) -> str:
    value = ArtifactScientificContext.model_validate_json(context.model_dump_json())
    return _canonical(value.model_dump(mode="json"))


def scientific_context_sha256(context: ArtifactScientificContext) -> str:
    return hashlib.sha256(canonical_scientific_context_json(context).encode("utf-8")).hexdigest()


def bind_scientific_context(context: ArtifactScientificContext) -> ArtifactScientificContextBinding:
    return ArtifactScientificContextBinding(
        context=context, context_sha256=scientific_context_sha256(context)
    )


def artifact_context_metadata(binding: ArtifactScientificContextBinding | None) -> dict:
    if binding is None:
        return {}
    value = ArtifactScientificContextBinding.model_validate_json(binding.model_dump_json())
    return {"scientific_context": value.model_dump(mode="json")}


def validate_context_for_finding(
    binding: ArtifactScientificContextBinding,
    reference: MemoryReadReference,
    finding: BridgeFinding | AnswerFinding,
) -> None:
    value = ArtifactScientificContextBinding.model_validate_json(binding.model_dump_json())
    if (
        value.context.source_reference != reference
        or value.context.ancestry_nodes[0].finding != finding
    ):
        raise ValueError("scientific_context_basis_mismatch")


def validate_artifact_context_metadata(data: Mapping, *, expected: bool | None = None) -> None:
    if not isinstance(data, Mapping) or (expected is not None and type(expected) is not bool):
        raise ValueError("scientific_context_binding_invalid")
    if "scientific_context" not in data:
        if expected is True:
            raise ValueError("scientific_context_binding_required")
        return
    raw = data["scientific_context"]
    if raw is None:
        raise ValueError("scientific_context_binding_invalid")
    if expected is False:
        raise ValueError("scientific_context_not_admitted")
    try:
        value = (
            raw
            if isinstance(raw, ArtifactScientificContextBinding)
            else ArtifactScientificContextBinding.model_validate_json(_canonical(raw))
        )
        reference = data.get("reference")
        if not isinstance(reference, MemoryReadReference):
            reference = MemoryReadReference.model_validate_json(_canonical(reference))
        finding = data.get("finding")
        if not isinstance(finding, (BridgeFinding, AnswerFinding)):
            finding_type = AnswerFinding if finding.get("outcome") == "answer" else BridgeFinding
            finding = finding_type.model_validate_json(_canonical(finding))
        validate_context_for_finding(value, reference, finding)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("scientific_context_binding_invalid") from None
