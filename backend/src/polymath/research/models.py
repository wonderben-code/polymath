"""Typed contracts for the experimental research loop, not yet a public API."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
SourceId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
ClaimId = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
ResearchMode = Literal["explore", "ask", "surprise"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _validate_public_text(value: object) -> None:
    """Reject output that cannot be safely stored/rendered before it leaves a repair loop."""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("research output must contain valid Unicode text") from None
        if any(ord(character) < 32 and character not in "\n\t\r" for character in value):
            raise ValueError("research output cannot contain unsupported control characters")
    elif isinstance(value, dict):
        for item in value.values():
            _validate_public_text(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_public_text(item)


class SourceReference(StrictModel):
    source_id: SourceId
    title: Annotated[Text, Field(max_length=1000)]
    url: Annotated[str, StringConstraints(pattern=r"^https://[^\s]+$", max_length=2000)]


class ProviderFailure(StrictModel):
    provider: Literal["openalex", "arxiv"]
    code: Literal[
        "access_denied",
        "rate_limited",
        "timeout",
        "transport_error",
        "invalid_response",
        "response_too_large",
        "source_unavailable",
    ]


class SearchResults(StrictModel):
    sources: Annotated[tuple[SourceReference, ...], Field(max_length=10)]
    provider_failures: Annotated[tuple[ProviderFailure, ...], Field(max_length=2)] = ()


class SourceDocument(SourceReference):
    text: Annotated[str, Field(min_length=1, max_length=20_000)]
    content_kind: Literal["abstract", "full_text"]
    retrieved_at: datetime
    extraction_method: Annotated[Text, Field(max_length=100)]

    @model_validator(mode="after")
    def require_timezone(self) -> SourceDocument:
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must include its timezone")
        return self

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


class SearchAction(StrictModel):
    kind: Literal["search_literature"]
    query: Annotated[Text, Field(max_length=500)]
    purpose: Literal["discovery", "counterevidence", "prior_work"]


class ReadAction(StrictModel):
    kind: Literal["read_source"]
    source_id: SourceId


class FieldSelection(StrictModel):
    """A concise public research direction chosen by the model, never a fixed pair."""

    fields: Annotated[
        tuple[Annotated[Text, Field(max_length=100)], ...], Field(min_length=2, max_length=4)
    ]
    rationale: Annotated[Text, Field(max_length=1000)]

    @model_validator(mode="after")
    def distinct_fields(self) -> FieldSelection:
        _validate_public_text(self.model_dump())
        if len({" ".join(name.split()).casefold() for name in self.fields}) != len(self.fields):
            raise ValueError("selected fields must be distinct")
        if any(any(ord(character) < 32 for character in name) for name in self.fields):
            raise ValueError("selected fields cannot contain control characters")
        return self


class SelectFieldsAction(FieldSelection):
    kind: Literal["select_fields"]


class Mapping(StrictModel):
    left: Annotated[Text, Field(max_length=1000)]
    right: Annotated[Text, Field(max_length=1000)]
    assumption: Annotated[Text, Field(max_length=1500)]


class EvidenceQuote(StrictModel):
    source_id: SourceId
    side: Literal["left", "right"]
    quote: Annotated[str, Field(min_length=10, max_length=4000)]
    supports: Annotated[Text, Field(max_length=1500)]


class BridgeFinding(StrictModel):
    outcome: Literal["bridge"]
    title: Annotated[Text, Field(max_length=300)]
    field_left: Annotated[Text, Field(max_length=200)]
    field_right: Annotated[Text, Field(max_length=200)]
    explanation: Annotated[Text, Field(max_length=3000)]
    mappings: Annotated[list[Mapping], Field(min_length=1, max_length=8)]
    open_question: Annotated[Text, Field(max_length=1500)]
    strongest_objection: Annotated[Text, Field(max_length=2000)]
    objection_verdict: Annotated[Text, Field(max_length=2000)]
    novelty_limitations: Annotated[Text, Field(max_length=2000)]
    evidence: Annotated[list[EvidenceQuote], Field(min_length=2, max_length=12)]

    @model_validator(mode="after")
    def distinct_fields(self) -> BridgeFinding:
        if (
            " ".join(self.field_left.split()).casefold()
            == " ".join(self.field_right.split()).casefold()
        ):
            raise ValueError("bridge fields must be distinct")
        return self


class InsufficientFinding(StrictModel):
    outcome: Literal["insufficient_evidence"]
    reason: Annotated[Text, Field(max_length=3000)]


class ClaimQuote(StrictModel):
    source_id: SourceId
    quote: Annotated[str, Field(min_length=10, max_length=4000)]
    supports: Annotated[Text, Field(max_length=1500)]


class AnswerClaim(StrictModel):
    id: ClaimId
    text: Annotated[Text, Field(max_length=2000)]
    label: Literal["known", "postulated", "speculative"]
    evidence: Annotated[list[ClaimQuote], Field(min_length=1, max_length=8)]
    derivation: Annotated[Text, Field(max_length=2000)] | None = None
    caveats: Annotated[list[Annotated[Text, Field(max_length=1000)]], Field(max_length=6)] = []

    @model_validator(mode="after")
    def explain_inference_and_uncertainty(self) -> AnswerClaim:
        if self.label == "postulated" and self.derivation is None:
            raise ValueError("postulated claims require an explicit derivation")
        if self.label == "speculative" and not self.caveats:
            raise ValueError("speculative claims require an explicit caveat")
        return self


class AnswerFinding(StrictModel):
    outcome: Literal["answer"]
    question: Annotated[Text, Field(max_length=4000)]
    summary: Annotated[Text, Field(max_length=3000)]
    claims: Annotated[list[AnswerClaim], Field(min_length=1, max_length=8)]
    limitations: Annotated[
        list[Annotated[Text, Field(max_length=1000)]], Field(min_length=1, max_length=8)
    ]
    unanswered_questions: Annotated[
        list[Annotated[Text, Field(max_length=1000)]], Field(max_length=8)
    ] = []

    @model_validator(mode="after")
    def distinct_claim_ids(self) -> AnswerFinding:
        _validate_public_text(self.model_dump())
        if len({claim.id for claim in self.claims}) != len(self.claims):
            raise ValueError("answer claim IDs must be unique")
        return self


Finding = Annotated[
    BridgeFinding | AnswerFinding | InsufficientFinding, Field(discriminator="outcome")
]


class ConcludeAction(StrictModel):
    kind: Literal["conclude"]
    conclusion: Finding

    @model_validator(mode="after")
    def validate_public_conclusion(self) -> ConcludeAction:
        _validate_public_text(self.conclusion.model_dump())
        return self


Action = Annotated[
    SearchAction | ReadAction | SelectFieldsAction | ConcludeAction, Field(discriminator="kind")
]
ACTION_ADAPTER = TypeAdapter(Action)


class VerifiedQuote(StrictModel):
    """Exact substring match; this does not verify scientific interpretation."""

    source_id: SourceId
    side: Literal["left", "right", "answer"]
    claim_id: ClaimId | None = None
    quote: str
    supports: str
    start: int
    end: int
    source_sha256: str
    content_kind: Literal["abstract", "full_text"]


class ResearchEvent(StrictModel):
    sequence: int
    kind: str
    summary: str


class Usage(StrictModel):
    model_calls: int = 0
    input_tokens_reported: int = 0
    output_tokens_reported: int = 0
    calls_without_usage: int = 0


class ResearchResult(StrictModel):
    status: Literal["bridge", "answer", "insufficient_evidence", "limit_reached", "failed"]
    mode: ResearchMode = "explore"
    finding: BridgeFinding | AnswerFinding | InsufficientFinding | None = None
    field_selections: Annotated[tuple[FieldSelection, ...], Field(max_length=3)] = ()
    quotes: tuple[VerifiedQuote, ...] = ()
    sources: tuple[SourceDocument, ...] = ()
    events: tuple[ResearchEvent, ...] = ()
    usage: Usage
    error_code: str | None = None
    validation_scope: Literal["source_identity_and_quote_integrity"] = (
        "source_identity_and_quote_integrity"
    )
