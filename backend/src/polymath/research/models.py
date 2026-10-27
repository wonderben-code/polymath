"""Typed contracts for the experimental research loop, not yet a public API."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
SourceId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SourceReference(StrictModel):
    source_id: SourceId
    title: Annotated[Text, Field(max_length=1000)]
    url: Annotated[str, StringConstraints(pattern=r"^https://[^\s]+$", max_length=2000)]


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
        if self.field_left.casefold() == self.field_right.casefold():
            raise ValueError("bridge fields must be distinct")
        return self


class InsufficientFinding(StrictModel):
    outcome: Literal["insufficient_evidence"]
    reason: Annotated[Text, Field(max_length=3000)]


Finding = Annotated[BridgeFinding | InsufficientFinding, Field(discriminator="outcome")]


class ConcludeAction(StrictModel):
    kind: Literal["conclude"]
    conclusion: Finding


Action = Annotated[SearchAction | ReadAction | ConcludeAction, Field(discriminator="kind")]
ACTION_ADAPTER = TypeAdapter(Action)


class VerifiedQuote(StrictModel):
    """Exact substring match; this does not verify scientific interpretation."""

    source_id: SourceId
    side: Literal["left", "right"]
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
    status: Literal["bridge", "insufficient_evidence", "limit_reached", "failed"]
    finding: BridgeFinding | InsufficientFinding | None = None
    quotes: tuple[VerifiedQuote, ...] = ()
    sources: tuple[SourceDocument, ...] = ()
    events: tuple[ResearchEvent, ...] = ()
    usage: Usage
    error_code: str | None = None
    validation_scope: Literal["source_identity_and_quote_integrity"] = (
        "source_identity_and_quote_integrity"
    )
