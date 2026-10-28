"""Version-bound reassessment contracts; classifications remain model assessments."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from polymath.literature.identity import (
    MAX_IDENTITY_REFERENCES,
    paper_aliases,
    validate_selected_paper_versions,
)
from polymath.research.controller import (
    _verify_answer_evidence,
    _verify_evidence,
    _verify_surprise_fields,
)
from polymath.research.models import (
    AnswerFinding,
    ArtifactId,
    AskOverviewBinding,
    BridgeFinding,
    ContentHash,
    FieldSelection,
    MemoryReadReference,
    ResearchEvent,
    ResearchMode,
    ResearchResult,
    SourceCoverage,
    SourceDocument,
    SourceReference,
    StrictModel,
    Text,
    Usage,
    VerifiedQuote,
    _validate_public_text,
)

Disposition = Literal["unchanged", "revised", "unsupported", "inconclusive"]
SearchPurpose = Literal["discovery", "counterevidence", "prior_work"]


class AssessmentModel(StrictModel):
    @model_validator(mode="after")
    def public_text(self) -> AssessmentModel:
        _validate_public_text(self.model_dump(mode="json"))
        return self


class AssessmentSourceReference(SourceReference):
    retrieval_id: ArtifactId
    version_id: ArtifactId
    content_sha256: ContentHash
    content_kind: Literal["abstract", "full_text"]
    role: Literal["historical", "new"]


class AssessmentSourceSnapshot(AssessmentModel):
    retrieval_id: ArtifactId
    version_id: ArtifactId
    document: SourceDocument


class AssessmentBasis(AssessmentModel):
    prior_reference: MemoryReadReference
    mode: ResearchMode
    prior_state: Literal["supported", "unsupported"] = "supported"
    prior_finding: BridgeFinding | AnswerFinding
    prior_overview_binding: AskOverviewBinding | None = None
    prior_quotes: Annotated[tuple[VerifiedQuote, ...], Field(min_length=1, max_length=64)]
    field_selections: Annotated[tuple[FieldSelection, ...], Field(max_length=3)] = ()
    sources: Annotated[tuple[AssessmentSourceReference, ...], Field(min_length=2, max_length=69)]

    @field_validator("prior_overview_binding", mode="before")
    @classmethod
    def forbid_explicit_null_overview(cls, value):
        if value is None:
            raise ValueError("ask_overview_binding_invalid")
        return value

    @model_serializer(mode="wrap")
    def preserve_absent_overview(self, handler):
        data = handler(self)
        if self.prior_overview_binding is None:
            data.pop("prior_overview_binding", None)
        return data

    @model_validator(mode="after")
    def exact_input(self) -> AssessmentBasis:
        if self.prior_overview_binding is not None:
            from polymath.scientific_context_contracts import validate_answer_overview

            if self.mode != "ask":
                raise ValueError("ask_overview_wrong_result")
            validate_answer_overview(self.prior_finding, self.prior_overview_binding)
        if len({item.retrieval_id for item in self.sources}) != len(self.sources):
            raise ValueError("assessment retrieval handles must be distinct")
        historical = [item for item in self.sources if item.role == "historical"]
        selected = [item for item in self.sources if item.role == "new"]
        if not 1 <= len(historical) <= 64 or not 1 <= len(selected) <= 5:
            raise ValueError("assessment requires historical premises and one to five new versions")
        premises = {
            (item.source_id, item.source_sha256, item.content_kind) for item in self.prior_quotes
        }
        if any(
            (item.source_id, item.content_sha256, item.content_kind) not in premises
            for item in historical
        ):
            raise ValueError("historical handles must bind original quoted source versions")
        if not premises.issubset(
            {(item.source_id, item.content_sha256, item.content_kind) for item in historical}
        ):
            raise ValueError("assessment must preserve every original quoted source version")
        historical_versions = {item.version_id for item in historical}
        if any(item.version_id in historical_versions for item in selected):
            raise ValueError("selected new sources must be different source versions")
        if isinstance(self.prior_finding, AnswerFinding):
            if self.mode != "ask":
                raise ValueError("answer reassessment must retain Ask mode")
            expected = [
                (item.source_id, "answer", claim.id, item.quote, item.supports)
                for claim in self.prior_finding.claims
                for item in claim.evidence
            ]
        elif self.mode == "ask":
            raise ValueError("Ask reassessment requires an answer")
        else:
            expected = [
                (item.source_id, item.side, None, item.quote, item.supports)
                for item in self.prior_finding.evidence
            ]
        if len(expected) != len(self.prior_quotes) or any(
            (quote.source_id, quote.side, quote.claim_id, quote.quote, quote.supports) != item
            or quote.start < 0
            or quote.end - quote.start != len(quote.quote)
            for quote, item in zip(self.prior_quotes, expected, strict=True)
        ):
            raise ValueError("assessment prior quotes must bind the original finding")
        if self.mode != "surprise" and self.field_selections:
            raise ValueError("unexpected prior field selections")
        if self.mode == "surprise":
            _verify_surprise_fields(self.prior_finding, self.field_selections)
        return self


class AssessmentCitation(AssessmentModel):
    retrieval_id: ArtifactId
    quote: Annotated[str, Field(min_length=10, max_length=4000)]
    supports: Annotated[Text, Field(max_length=1500)]


class AssessmentStatement(AssessmentModel):
    text: Annotated[Text, Field(max_length=2000)]
    citations: Annotated[tuple[AssessmentCitation, ...], Field(max_length=8)] = ()


class VerifiedAssessmentCitation(AssessmentCitation):
    source_id: Annotated[Text, Field(max_length=200)]
    source_sha256: ContentHash
    content_kind: Literal["abstract", "full_text"]
    coverage: SourceCoverage | None = None
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=1)]

    @model_serializer(mode="wrap")
    def preserve_absent_coverage(self, handler):
        data = handler(self)
        if self.coverage is None:
            data.pop("coverage", None)
        return data

    @model_validator(mode="after")
    def quote_span(self) -> VerifiedAssessmentCitation:
        if self.end - self.start != len(self.quote):
            raise ValueError("assessment quote offsets must preserve exact Unicode text")
        if self.coverage is not None and (
            self.content_kind != "full_text"
            or self.source_id
            != (f"arxiv-html:{self.coverage.snapshot_id}:{self.coverage.start}:{self.coverage.end}")
            or self.end > self.coverage.end - self.coverage.start
        ):
            raise ValueError("assessment quote must retain its exact full-text excerpt scope")
        return self


class AssessmentResult(AssessmentModel):
    status: Literal["completed", "failed", "limit_reached"]
    disposition: Disposition = "inconclusive"
    rationale: Annotated[tuple[AssessmentStatement, ...], Field(max_length=8)] = ()
    limitations: Annotated[
        tuple[Annotated[Text, Field(max_length=1000)], ...], Field(min_length=1, max_length=8)
    ]
    observed_retrieval_ids: Annotated[tuple[ArtifactId, ...], Field(max_length=200)] = ()
    selected_retrieval_ids: Annotated[tuple[ArtifactId, ...], Field(max_length=200)] = ()
    quotes: Annotated[tuple[VerifiedAssessmentCitation, ...], Field(max_length=64)] = ()
    revised_result: ResearchResult | None = None
    search_purposes: Annotated[tuple[SearchPurpose, ...], Field(max_length=3)] = ()
    events: Annotated[tuple[ResearchEvent, ...], Field(max_length=100)] = ()
    usage: Usage
    error_code: Annotated[str, Field(pattern=r"^[a-z_]{1,100}$")] | None = None
    reason: Annotated[Text, Field(max_length=3000)] | None = None
    validation_scope: Literal["version_bound_quote_integrity"] = "version_bound_quote_integrity"

    @model_validator(mode="after")
    def outcome_shape(self) -> AssessmentResult:
        for identifiers in (
            self.observed_retrieval_ids,
            self.selected_retrieval_ids,
            self.search_purposes,
        ):
            if len(set(identifiers)) != len(identifiers):
                raise ValueError("assessment references and search purposes must be distinct")
        if self.status != "completed" and self.disposition != "inconclusive":
            raise ValueError("unfinished assessments must remain inconclusive")
        if self.status == "completed" and not self.rationale:
            raise ValueError("completed assessments require a rationale")
        if self.disposition == "revised":
            if self.revised_result is None or not self.selected_retrieval_ids:
                raise ValueError("revised assessments require a source-bound positive finding")
        elif self.revised_result is not None or self.selected_retrieval_ids:
            raise ValueError("only revised assessments can supply a replacement finding")
        if any(value < 0 for value in self.usage.model_dump().values()):
            raise ValueError("assessment usage cannot be negative")
        if any(
            event.sequence != index + 1 or len(event.summary) > 2000 or len(event.kind) > 64
            for index, event in enumerate(self.events)
        ):
            raise ValueError("assessment events must be sequential and bounded")
        return self


class ReadAssessmentSourceAction(AssessmentModel):
    kind: Literal["read_source_by_retrieval"]
    retrieval_id: ArtifactId


class ReadAssessmentLiteratureAction(AssessmentModel):
    kind: Literal["read_literature_source"]
    source_id: Annotated[Text, Field(max_length=200)]


class ConcludeAssessmentAction(AssessmentModel):
    kind: Literal["conclude_assessment"]
    disposition: Disposition
    rationale: Annotated[tuple[AssessmentStatement, ...], Field(min_length=1, max_length=8)]
    limitations: Annotated[
        tuple[Annotated[Text, Field(max_length=1000)], ...], Field(min_length=1, max_length=8)
    ]
    selected_retrieval_ids: Annotated[tuple[ArtifactId, ...], Field(max_length=200)] = ()
    revised_finding: BridgeFinding | AnswerFinding | None = None

    @model_validator(mode="after")
    def replacement(self) -> ConcludeAssessmentAction:
        if self.disposition == "revised":
            if self.revised_finding is None or not self.selected_retrieval_ids:
                raise ValueError(
                    "revised assessments require a candidate and exact current versions"
                )
        elif self.revised_finding is not None or self.selected_retrieval_ids:
            raise ValueError("only revised assessments may propose a replacement")
        return self


def verify_assessment_quotes(
    rationale: tuple[AssessmentStatement, ...], observed: Mapping[str, AssessmentSourceSnapshot]
) -> tuple[VerifiedAssessmentCitation, ...]:
    verified = []
    for paragraph in rationale:
        for citation in paragraph.citations:
            snapshot = observed.get(citation.retrieval_id)
            if snapshot is None:
                raise ValueError("assessment_source_not_read")
            source = snapshot.document
            start = source.text.find(citation.quote)
            if start < 0:
                raise ValueError("assessment_quotation_not_found")
            verified.append(
                VerifiedAssessmentCitation(
                    **citation.model_dump(),
                    source_id=source.source_id,
                    source_sha256=source.content_sha256,
                    content_kind=source.content_kind,
                    coverage=source.coverage,
                    start=start,
                    end=start + len(citation.quote),
                )
            )
    return tuple(verified)


def validate_assessment_result(
    result: AssessmentResult,
    basis: AssessmentBasis,
    observed: Mapping[str, AssessmentSourceSnapshot],
    *,
    alias_context: Iterable[SourceReference] = (),
    expected_overview: bool | None = None,
) -> None:
    """Recheck source coverage and historical/original-admission overview semantics.

    ``expected_overview`` is derived by the fresh store/controller from its
    immutable intent, never from a client. None validates historical metadata.
    Scientific interpretation remains model-assessed.
    """
    result = AssessmentResult.model_validate_json(result.model_dump_json())
    basis = AssessmentBasis.model_validate_json(basis.model_dump_json())
    if expected_overview is not None and type(expected_overview) is not bool:
        raise ValueError("ask_overview_binding_invalid")
    if basis.prior_overview_binding is not None and expected_overview is False:
        raise ValueError("ask_reassessment_overview_required")
    if len(observed) > 200 or not set(result.observed_retrieval_ids).issubset(observed):
        raise ValueError("assessment_read_provenance_mismatch")
    for identifier, snapshot in observed.items():
        if identifier != snapshot.retrieval_id:
            raise ValueError("assessment_read_provenance_mismatch")
    expected_quotes = verify_assessment_quotes(result.rationale, observed)
    if result.quotes != expected_quotes:
        raise ValueError("assessment_quote_integrity_failed")
    if any(quote.retrieval_id not in result.observed_retrieval_ids for quote in result.quotes):
        raise ValueError("assessment_source_not_read")
    refs = {item.retrieval_id: item for item in basis.sources}
    for identifier in set(result.observed_retrieval_ids) & refs.keys():
        reference, snapshot = refs[identifier], observed[identifier]
        document = snapshot.document
        if (
            snapshot.version_id != reference.version_id
            or document.source_id != reference.source_id
            or document.title != reference.title
            or document.url != reference.url
            or document.bibliography != reference.bibliography
            or document.coverage != reference.coverage
            or document.content_sha256 != reference.content_sha256
            or document.content_kind != reference.content_kind
        ):
            raise ValueError("assessment_source_version_mismatch")
    if result.status == "completed" and result.disposition != "inconclusive":
        if any(not paragraph.citations for paragraph in result.rationale):
            raise ValueError("assessment_uncited_judgement")
        cited = {item.retrieval_id for item in result.quotes}
        new = {item.retrieval_id for item in basis.sources if item.role == "new"}
        if not new.issubset(set(result.observed_retrieval_ids) & cited):
            raise ValueError("assessment_new_version_coverage_missing")
        historical = {item.retrieval_id for item in basis.sources if item.role == "historical"}
        premises = {
            (item.source_id, item.source_sha256, item.content_kind, item.quote)
            for item in basis.prior_quotes
        }
        if not any(
            item.retrieval_id in historical
            and (item.source_id, item.source_sha256, item.content_kind, item.quote) in premises
            for item in result.quotes
        ):
            raise ValueError("assessment_historical_premise_missing")
    if result.revised_result is None:
        return
    revised = result.revised_result
    from polymath.scientific_context_contracts import validate_result_overview

    validate_result_overview(revised.model_dump(mode="json"), expected=expected_overview)
    if basis.prior_overview_binding is not None and revised.overview_binding is None:
        raise ValueError("ask_overview_binding_required")
    if revised.mode != basis.mode or revised.field_selections != basis.field_selections:
        raise ValueError("assessment_revised_mode_mismatch")
    if revised.memory_reads != (basis.prior_reference,):
        raise ValueError("assessment_prior_provenance_mismatch")
    selected = {}
    for identifier in result.selected_retrieval_ids:
        snapshot = observed.get(identifier)
        if snapshot is None or identifier not in result.observed_retrieval_ids:
            raise ValueError("assessment_source_not_read")
        document = snapshot.document
        if document.source_id in selected:
            raise ValueError("assessment_ambiguous_source_version")
        selected[document.source_id] = document
    if tuple(selected.values()) != revised.sources:
        raise ValueError("assessment_current_versions_mismatch")
    context = {
        paper_aliases(snapshot.document): snapshot.document for snapshot in observed.values()
    }
    try:
        for position, reference in enumerate(alias_context):
            if position >= MAX_IDENTITY_REFERENCES:
                raise ValueError("paper identity reference limit exceeded")
            context.setdefault(paper_aliases(reference), reference)
            if len(context) > MAX_IDENTITY_REFERENCES:
                raise ValueError("paper identity reference limit exceeded")
        validate_selected_paper_versions(selected.values(), context=context.values())
    except ValueError:
        raise ValueError("assessment_ambiguous_paper_version") from None
    context = tuple(context.values())
    purposes = set(result.search_purposes)
    if isinstance(revised.finding, AnswerFinding):
        if basis.mode != "ask" or revised.finding.question != basis.prior_finding.question:
            raise ValueError("assessment_question_mismatch")
        previous = {claim.id: claim.label for claim in basis.prior_finding.claims}
        rank = {"known": 0, "postulated": 1, "speculative": 2}
        if any(
            claim.id in previous and rank[claim.label] < rank[previous[claim.id]]
            for claim in revised.finding.claims
        ):
            raise ValueError("assessment_claim_label_upgrade")
        quotes = _verify_answer_evidence(revised.finding, selected, purposes, alias_context=context)
        if revised.status != "answer":
            raise ValueError("assessment_revised_status_mismatch")
    elif isinstance(revised.finding, BridgeFinding):
        if basis.mode == "ask" or revised.status != "bridge":
            raise ValueError("assessment_revised_mode_mismatch")
        if basis.mode == "surprise":
            _verify_surprise_fields(revised.finding, basis.field_selections)
        quotes = _verify_evidence(revised.finding, selected, purposes, alias_context=context)
    else:
        raise ValueError("assessment_revised_finding_missing")
    if revised.quotes != quotes:
        raise ValueError("assessment_revised_quote_integrity_failed")
