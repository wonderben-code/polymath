"""Source-bound conjecture and writing contracts, never a scientific verifier.

The model supplies prose and citation ordinals. Source identities, quotations,
export bytes, and finite-check results come from trusted application boundaries.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, field_validator, model_serializer, model_validator

from polymath.math_checks import FiniteCheckResult, FiniteConjecture, validate_check
from polymath.research.models import (
    AnswerFinding,
    ArtifactId,
    BridgeFinding,
    ClaimId,
    ContentHash,
    MemoryReadReference,
    ResearchEvent,
    SourceReference,
    StrictModel,
    Text,
    Usage,
    _validate_public_text,
)
from polymath.scientific_context_contracts import (
    ArtifactScientificContextBinding,
    validate_context_for_finding,
)

ArtifactKind = Literal["formalise", "write_summary", "write_paper"]
ClaimLabel = Literal["known", "postulated", "speculative"]
QuoteOrdinal = Annotated[int, Field(ge=0, le=63)]


class ArtifactModel(StrictModel):
    @model_validator(mode="after")
    def require_public_text(self) -> ArtifactModel:
        _validate_public_text(self.model_dump())
        return self


class ArtifactPassage(SourceReference):
    ordinal: QuoteOrdinal
    content_sha256: ContentHash
    content_kind: Literal["abstract", "full_text"]
    quote: Annotated[str, Field(min_length=10, max_length=4000)]
    supports: Annotated[Text, Field(max_length=1500)]
    claim_id: ClaimId | None = None

    @model_validator(mode="after")
    def require_public_text(self) -> ArtifactPassage:
        _validate_public_text(self.model_dump())
        return self


class CitedText(ArtifactModel):
    text: Annotated[Text, Field(max_length=2000)]
    label: ClaimLabel
    citation_ordinals: Annotated[tuple[QuoteOrdinal, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def distinct_citations(self) -> CitedText:
        if len(set(self.citation_ordinals)) != len(self.citation_ordinals):
            raise ValueError("citation ordinals must be distinct")
        return self


class DocumentSection(ArtifactModel):
    heading: Annotated[Text, Field(max_length=100)]
    paragraphs: Annotated[tuple[CitedText, ...], Field(min_length=1, max_length=4)]


class DocumentDraft(ArtifactModel):
    kind: Literal["summary", "paper"]
    title: Annotated[Text, Field(max_length=300)]
    abstract: Annotated[tuple[CitedText, ...], Field(min_length=1, max_length=3)]
    sections: Annotated[tuple[DocumentSection, ...], Field(min_length=2, max_length=8)]
    limitations: Annotated[
        list[Annotated[Text, Field(max_length=1000)]], Field(min_length=1, max_length=8)
    ]

    @model_validator(mode="after")
    def require_complete_paper(self) -> DocumentDraft:
        headings = {" ".join(section.heading.split()).casefold() for section in self.sections}
        if len(headings) != len(self.sections):
            raise ValueError("document section headings must be distinct")
        if self.kind == "paper" and not {"background", "findings", "evidence", "limitations"} <= (
            headings
        ):
            raise ValueError("papers require Background, Findings, Evidence, and Limitations")
        return self


class Definition(ArtifactModel):
    symbol: Annotated[Text, Field(max_length=80)]
    meaning: CitedText


class FormalisationDraft(ArtifactModel):
    kind: Literal["formalisation"] = "formalisation"
    title: Annotated[Text, Field(max_length=300)]
    definitions: Annotated[tuple[Definition, ...], Field(min_length=1, max_length=8)]
    assumptions: Annotated[tuple[CitedText, ...], Field(min_length=1, max_length=8)]
    conjecture: CitedText
    proof_attempt: Annotated[tuple[CitedText, ...], Field(min_length=1, max_length=8)]
    limitations: Annotated[
        list[Annotated[Text, Field(max_length=1000)]], Field(min_length=1, max_length=8)
    ]
    finite_conjecture: FiniteConjecture | None = None

    @model_validator(mode="after")
    def distinguish_conjecture_from_fact(self) -> FormalisationDraft:
        if self.conjecture.label == "known":
            raise ValueError("a generated conjecture cannot be labelled known")
        if any(attempt.label == "known" for attempt in self.proof_attempt):
            raise ValueError("a generated proof attempt cannot be labelled known")
        normalized = [" ".join(item.symbol.split()).casefold() for item in self.definitions]
        if len(set(normalized)) != len(normalized):
            raise ValueError("definition symbols must be distinct")
        return self


ArtifactDraft = Annotated[DocumentDraft | FormalisationDraft, Field(discriminator="kind")]


class ArtifactBasis(ArtifactModel):
    reference: MemoryReadReference
    finding: BridgeFinding | AnswerFinding
    passages: Annotated[tuple[ArtifactPassage, ...], Field(min_length=1, max_length=64)]
    formalisation_id: ArtifactId | None = None
    formalisation: FormalisationDraft | None = None
    formalisation_checks: Annotated[tuple[FiniteCheckResult, ...], Field(max_length=3)] = ()
    scientific_context: ArtifactScientificContextBinding | None = None

    @field_validator("scientific_context", mode="before")
    @classmethod
    def reject_explicit_null_context(cls, value):
        if value is None:
            raise ValueError("scientific_context_binding_invalid")
        return value

    @model_serializer(mode="wrap")
    def preserve_legacy_basis(self, handler):
        value = handler(self)
        if self.scientific_context is None:
            value.pop("scientific_context", None)
        return value

    @model_validator(mode="after")
    def require_exact_basis(self) -> ArtifactBasis:
        if len({item.ordinal for item in self.passages}) != len(self.passages):
            raise ValueError("basis passage ordinals must be distinct")
        expected = (
            [(claim.id, item) for claim in self.finding.claims for item in claim.evidence]
            if isinstance(self.finding, AnswerFinding)
            else [(None, item) for item in self.finding.evidence]
        )
        if {item.ordinal for item in self.passages} != set(range(len(expected))):
            raise ValueError("basis passages must preserve every original quote ordinal")
        for passage in self.passages:
            claim_id, evidence = expected[passage.ordinal]
            if (passage.claim_id, passage.source_id, passage.quote, passage.supports) != (
                claim_id,
                evidence.source_id,
                evidence.quote,
                evidence.supports,
            ):
                raise ValueError("basis passages must match the original finding evidence")
        if (self.formalisation_id is None) != (self.formalisation is None):
            raise ValueError("a prior formalisation requires its exact artifact identifier")
        if self.formalisation is None and self.formalisation_checks:
            raise ValueError("prior checks require their exact formalisation artifact")
        for check in self.formalisation_checks:
            validate_check(check)
        if self.formalisation is not None and self.formalisation.finite_conjecture is not None:
            if not any(
                check.conjecture == self.formalisation.finite_conjecture
                for check in self.formalisation_checks
            ):
                raise ValueError("prior finite conjecture must retain its actual checks")
        if isinstance(self.finding, AnswerFinding):
            claims = {claim.id for claim in self.finding.claims}
            if any(passage.claim_id not in claims for passage in self.passages):
                raise ValueError("answer passages must bind a saved claim")
        elif any(passage.claim_id is not None for passage in self.passages):
            raise ValueError("bridge passages cannot claim answer bindings")
        if self.scientific_context is not None:
            validate_context_for_finding(self.scientific_context, self.reference, self.finding)
            prior = self.scientific_context.context.prior_formalisation
            if (prior is None) != (self.formalisation_id is None) or (
                prior is not None and prior.artifact_id != self.formalisation_id
            ):
                raise ValueError("scientific_context_formalisation_mismatch")
        return self


class ArtifactResult(ArtifactModel):
    kind: ArtifactKind
    status: Literal["ready", "insufficient_evidence", "failed", "limit_reached"]
    draft: ArtifactDraft | None = None
    checks: Annotated[tuple[FiniteCheckResult, ...], Field(max_length=3)] = ()
    events: Annotated[tuple[ResearchEvent, ...], Field(max_length=100)] = ()
    usage: Usage
    error_code: Annotated[str, Field(pattern=r"^[a-z_]{1,100}$")] | None = None
    reason: Annotated[Text, Field(max_length=3000)] | None = None
    validation_scope: Literal["citation_integrity_and_bounded_checks"] = (
        "citation_integrity_and_bounded_checks"
    )

    @model_validator(mode="after")
    def require_matching_outcome(self) -> ArtifactResult:
        if self.status == "ready":
            if self.draft is None or not draft_matches_kind(self.draft, self.kind):
                raise ValueError("ready artifacts require a draft matching their task kind")
        elif self.draft is not None:
            raise ValueError("non-ready artifact outcomes cannot contain a draft")
        if self.status == "insufficient_evidence" and self.reason is None:
            raise ValueError("insufficient evidence requires an explanation")
        if self.kind != "formalise" and self.checks:
            raise ValueError("writing tasks cannot declare new finite checks")
        if any(value < 0 for value in self.usage.model_dump().values()):
            raise ValueError("reported artifact usage cannot be negative")
        if any(
            event.sequence != index + 1 or len(event.kind) > 64 or len(event.summary) > 2000
            for index, event in enumerate(self.events)
        ):
            raise ValueError("artifact events must be bounded and sequential")
        return self


class CheckConjectureAction(ArtifactModel):
    kind: Literal["check_conjecture"]
    conjecture: FiniteConjecture


class ConcludeArtifactAction(ArtifactModel):
    kind: Literal["conclude"]
    draft: ArtifactDraft


class InsufficientArtifactAction(ArtifactModel):
    kind: Literal["insufficient"]
    reason: Annotated[Text, Field(max_length=3000)]


ArtifactAction = Annotated[
    CheckConjectureAction | ConcludeArtifactAction | InsufficientArtifactAction,
    Field(discriminator="kind"),
]
ARTIFACT_ACTION_ADAPTER = TypeAdapter(ArtifactAction)


def draft_matches_kind(draft: DocumentDraft | FormalisationDraft, kind: ArtifactKind) -> bool:
    if kind == "formalise":
        return isinstance(draft, FormalisationDraft)
    return isinstance(draft, DocumentDraft) and draft.kind == (
        "summary" if kind == "write_summary" else "paper"
    )


def iter_cited_texts(draft: DocumentDraft | FormalisationDraft) -> Iterator[CitedText]:
    if isinstance(draft, DocumentDraft):
        yield from draft.abstract
        for section in draft.sections:
            yield from section.paragraphs
    else:
        for definition in draft.definitions:
            yield definition.meaning
        yield from draft.assumptions
        yield draft.conjecture
        yield from draft.proof_attempt


def validate_artifact_result(result: ArtifactResult, basis: ArtifactBasis) -> None:
    """Validate references and persisted checks, not the prose's scientific meaning."""
    if not isinstance(result, ArtifactResult) or not isinstance(basis, ArtifactBasis):
        raise ValueError("invalid_artifact_contract")
    result = ArtifactResult.model_validate_json(result.model_dump_json())
    basis = ArtifactBasis.model_validate_json(basis.model_dump_json())
    for check in result.checks:
        validate_check(check)
    if result.draft is None:
        return
    available = {passage.ordinal: passage for passage in basis.passages}
    inherited = (
        {claim.id: claim.label for claim in basis.finding.claims}
        if isinstance(basis.finding, AnswerFinding)
        else {}
    )
    rank = {"known": 0, "postulated": 1, "speculative": 2}
    for paragraph in iter_cited_texts(result.draft):
        for ordinal in paragraph.citation_ordinals:
            passage = available.get(ordinal)
            if passage is None:
                raise ValueError("unknown_artifact_citation")
            label = inherited.get(passage.claim_id)
            if label is not None and rank[paragraph.label] < rank[label]:
                raise ValueError("citation_label_upgrade")
    if isinstance(basis.finding, BridgeFinding):
        cited_ordinals = {
            ordinal
            for paragraph in iter_cited_texts(result.draft)
            for ordinal in paragraph.citation_ordinals
        }
        cited_sides = {basis.finding.evidence[ordinal].side for ordinal in cited_ordinals}
        if not {"left", "right"}.issubset(cited_sides):
            raise ValueError("bridge_citation_coverage_missing")
    if isinstance(result.draft, FormalisationDraft) and result.draft.finite_conjecture is not None:
        encoded = result.draft.finite_conjecture.model_dump(mode="json")
        if not any(check.conjecture.model_dump(mode="json") == encoded for check in result.checks):
            raise ValueError("finite_conjecture_not_checked")
