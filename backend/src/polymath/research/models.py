"""Typed contracts for the experimental research loop, not yet a public API."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_serializer,
    model_validator,
)

Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
SourceId = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
ClaimId = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
ArtifactId = Annotated[
    str,
    StringConstraints(
        strict=True, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
]
ContentHash = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
ResearchMode = Literal["explore", "ask", "surprise"]
MAX_BIBLIOGRAPHY_BYTES = 60_000
MAX_FULL_TEXT_CHARACTERS = 250_000
MAX_FULL_TEXT_WINDOW = 8_000
MAX_FULL_TEXT_HEADINGS = 80


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


_DOI = re.compile(r"10\.[0-9]{4,9}/[A-Za-z0-9][A-Za-z0-9._;()/:+\-]{0,170}")
_ARXIV = re.compile(
    r"(?P<base>(?:[0-9]{4}\.[0-9]{4,5}|[a-z]+(?:-[a-z]+)?(?:\.[A-Z]{2})?/[0-9]{7}))"
    r"(?:v(?P<version>[1-9][0-9]{0,3}))?"
)


def _canonical_doi(value: str) -> str:
    if not isinstance(value, str) or any(not 33 <= ord(character) <= 126 for character in value):
        raise ValueError("invalid DOI")
    if "://" in value:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or parsed.netloc.lower() not in ("doi.org", "dx.doi.org")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid DOI")
        value = parsed.path.removeprefix("/")
    if _DOI.fullmatch(value) is None:
        raise ValueError("invalid DOI")
    return value.lower()


def _canonical_arxiv(value: str) -> tuple[str, int | None]:
    if not isinstance(value, str):
        raise ValueError("invalid arXiv identifier")
    match = _ARXIV.fullmatch(value)
    if match is None:
        raise ValueError("invalid arXiv identifier")
    base = match.group("base")
    serial = base.rsplit("/", 1)[-1].split(".", 1)[0]
    if not 1 <= int(serial[2:4]) <= 12:
        raise ValueError("invalid arXiv identifier")
    return base, int(match.group("version")) if match.group("version") else None


def _canonical_openalex(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid OpenAlex identifier")
    if value.startswith("https://openalex.org/"):
        value = value.removeprefix("https://openalex.org/")
    if re.fullmatch(r"W[1-9][0-9]{0,18}", value) is None:
        raise ValueError("invalid OpenAlex identifier")
    return value


def _canonical_orcid(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid ORCID")
    if value.startswith("https://orcid.org/"):
        value = value.removeprefix("https://orcid.org/")
    if re.fullmatch(r"[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]", value) is None:
        raise ValueError("invalid ORCID")
    digits = value.replace("-", "")
    total = 0
    for digit in digits[:15]:
        total = (total + int(digit)) * 2
    checksum = (12 - total % 11) % 11
    if digits[-1] != ("X" if checksum == 10 else str(checksum)):
        raise ValueError("invalid ORCID")
    return value


class SourceAuthor(StrictModel):
    name: Annotated[Text, Field(max_length=300)]
    orcid: Annotated[str, Field(max_length=80)] | None = None

    @field_validator("orcid", mode="before")
    @classmethod
    def canonical_orcid(cls, value: str | None) -> str | None:
        return _canonical_orcid(value) if value is not None else None

    @model_validator(mode="after")
    def public_author(self) -> SourceAuthor:
        _validate_public_text(self.model_dump())
        return self


class SourceBibliography(StrictModel):
    """Provider-declared bibliography/aliases, not complete identity resolution."""

    authors: Annotated[tuple[SourceAuthor, ...], Field(max_length=100)] = ()
    authors_truncated: bool = False
    publication_year: Annotated[int, Field(ge=1000, le=2100)] | None = None
    publication_date: date | None = None
    venue: Annotated[Text, Field(max_length=500)] | None = None
    doi: Annotated[str, Field(max_length=200)] | None = None
    arxiv_id: Annotated[str, Field(max_length=100)] | None = None
    arxiv_version: Annotated[int, Field(ge=1, le=9999)] | None = None
    openalex_id: Annotated[str, Field(max_length=40)] | None = None
    license: Annotated[Text, Field(max_length=100)] | None = None

    @field_validator("doi", mode="before")
    @classmethod
    def canonical_doi(cls, value: str | None) -> str | None:
        return _canonical_doi(value) if value is not None else None

    @field_validator("arxiv_id")
    @classmethod
    def canonical_arxiv(cls, value: str | None) -> str | None:
        if value is None:
            return None
        base, version = _canonical_arxiv(value)
        if version is not None:
            raise ValueError("arXiv base identity and version must be separate")
        return base

    @field_validator("openalex_id", mode="before")
    @classmethod
    def canonical_openalex(cls, value: str | None) -> str | None:
        return _canonical_openalex(value) if value is not None else None

    @model_validator(mode="after")
    def consistent_bibliography(self) -> SourceBibliography:
        serialized = self.model_dump(mode="json")
        _validate_public_text(serialized)
        # Leave headroom below the database's65KiB JSONB textual bound, including
        # separator/escaping overhead and multibyte author names.
        if len(json.dumps(serialized, ensure_ascii=False, sort_keys=True).encode("utf-8")) > (
            MAX_BIBLIOGRAPHY_BYTES
        ):
            raise ValueError("source bibliography exceeds its UTF-8 byte limit")
        if self.arxiv_version is not None and self.arxiv_id is None:
            raise ValueError("arXiv versions require their paper identity")
        if self.authors_truncated and len(self.authors) != 100:
            raise ValueError("truncated author lists must preserve the bounded prefix")
        if self.publication_date is not None and not 1000 <= self.publication_date.year <= 2100:
            raise ValueError("publication date is outside the supported range")
        return self


class ArxivFullTextLocator(StrictModel):
    arxiv_id: Annotated[str, Field(max_length=100)]
    arxiv_version: Annotated[int, Field(ge=1, le=9999)] | None = None

    @field_validator("arxiv_id")
    @classmethod
    def base_identity(cls, value: str) -> str:
        base, version = _canonical_arxiv(value)
        if version is not None:
            raise ValueError("arXiv locator requires separate base and version")
        return base


class SourceCoverage(ArxivFullTextLocator):
    snapshot_id: ArtifactId
    snapshot_sha256: ContentHash
    arxiv_version: Annotated[int, Field(ge=1, le=9999)]
    start: Annotated[int, Field(ge=0, lt=MAX_FULL_TEXT_CHARACTERS)]
    end: Annotated[int, Field(ge=1, le=MAX_FULL_TEXT_CHARACTERS)]
    total: Annotated[int, Field(ge=1, le=MAX_FULL_TEXT_CHARACTERS)]

    @model_validator(mode="after")
    def bounded_window(self) -> SourceCoverage:
        if not self.start < self.end <= self.total or self.end - self.start > MAX_FULL_TEXT_WINDOW:
            raise ValueError("full-text coverage must identify a bounded exact window")
        return self


class SourceReference(StrictModel):
    source_id: SourceId
    title: Annotated[Text, Field(max_length=1000)]
    url: Annotated[str, StringConstraints(pattern=r"^https://[^\s]+$", max_length=2000)]
    bibliography: SourceBibliography | None = None
    coverage: SourceCoverage | None = None

    @model_validator(mode="after")
    def full_text_identity(self) -> SourceReference:
        coverage = self.coverage
        if coverage is not None and (
            self.source_id != f"arxiv-html:{coverage.snapshot_id}:{coverage.start}:{coverage.end}"
            or self.url != f"https://arxiv.org/html/{coverage.arxiv_id}v{coverage.arxiv_version}"
            or self.bibliography is None
            or self.bibliography.arxiv_id != coverage.arxiv_id
            or self.bibliography.arxiv_version != coverage.arxiv_version
        ):
            raise ValueError("full-text coverage requires its exact locator and paper aliases")
        return self

    @model_serializer(mode="wrap")
    def preserve_absent_bibliography(self, handler):
        # Historical source/retrieval/artifact hashes must not acquire a new
        # null field merely because the reader understands richer metadata.
        data = handler(self)
        if self.bibliography is None:
            data.pop("bibliography", None)
        if self.coverage is None:
            data.pop("coverage", None)
        return data


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


class FullTextAvailability(StrictModel):
    source_id: SourceId
    provider: Literal["arxiv_html"] = "arxiv_html"
    status: Literal["candidate", "ambiguous"]
    locator: ArxivFullTextLocator | None = None

    @model_validator(mode="after")
    def candidate_locator(self) -> FullTextAvailability:
        if (self.status == "candidate") != (self.locator is not None):
            raise ValueError("full-text candidates require one unambiguous validated locator")
        return self


class SearchResults(StrictModel):
    sources: Annotated[tuple[SourceReference, ...], Field(max_length=10)]
    provider_failures: Annotated[tuple[ProviderFailure, ...], Field(max_length=2)] = ()
    full_text: Annotated[tuple[FullTextAvailability, ...], Field(max_length=10)] = ()

    @model_validator(mode="after")
    def known_full_text_sources(self) -> SearchResults:
        identifiers = {source.source_id for source in self.sources}
        candidates = [candidate.source_id for candidate in self.full_text]
        if len(set(candidates)) != len(candidates) or not set(candidates).issubset(identifiers):
            raise ValueError("full-text availability must bind distinct discovered sources")
        return self

    @model_serializer(mode="wrap")
    def omit_absent_full_text(self, handler):
        data = handler(self)
        if not self.full_text:
            data.pop("full_text", None)
        return data


class SourceDocument(SourceReference):
    text: Annotated[str, Field(min_length=1, max_length=20_000)]
    content_kind: Literal["abstract", "full_text"]
    retrieved_at: datetime
    extraction_method: Annotated[Text, Field(max_length=100)]

    @model_validator(mode="after")
    def require_timezone(self) -> SourceDocument:
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must include its timezone")
        if self.coverage is not None and (
            self.content_kind != "full_text"
            or self.coverage.end - self.coverage.start != len(self.text)
        ):
            raise ValueError("full-text window text must match its exact coverage")
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


class OpenFullTextAction(StrictModel):
    kind: Literal["open_full_text"]
    source_id: SourceId


class ReadFullTextWindowAction(StrictModel):
    kind: Literal["read_full_text_window"]
    snapshot_id: ArtifactId
    start: Annotated[int, Field(ge=0, lt=MAX_FULL_TEXT_CHARACTERS)]
    end: Annotated[int, Field(ge=1, le=MAX_FULL_TEXT_CHARACTERS)]

    @model_validator(mode="after")
    def bounded_window(self) -> ReadFullTextWindowAction:
        if not 0 < self.end - self.start <= MAX_FULL_TEXT_WINDOW:
            raise ValueError("full-text reads require a window of at most8000 characters")
        return self


class SearchMemoryAction(StrictModel):
    kind: Literal["search_memory"]
    query: Annotated[Text, Field(max_length=200)]

    @model_validator(mode="after")
    def validate_query(self) -> SearchMemoryAction:
        _validate_public_text(self.query)
        if any(ord(character) < 32 for character in self.query):
            raise ValueError("memory query cannot contain control characters")
        return self


class ReadMemoryAction(StrictModel):
    kind: Literal["read_memory_finding"]
    result_id: ArtifactId


class ReadMemorySourceAction(StrictModel):
    kind: Literal["read_memory_source"]
    result_id: ArtifactId
    retrieval_id: ArtifactId


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


class AskOverviewBinding(StrictModel):
    """Exact selected claim IDs; ownership/admission are verified by the store."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    protocol: Literal["polymath-ask-claim-overview-v1"] = "polymath-ask-claim-overview-v1"
    claim_ids: Annotated[tuple[ClaimId, ...], Field(min_length=1, max_length=8)]

    @field_validator("claim_ids", mode="before")
    @classmethod
    def json_claim_ids(cls, value):
        return tuple(value) if type(value) is list else value

    @model_validator(mode="after")
    def distinct_ids(self):
        if len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("ask_overview_duplicate_claim")
        return self


class MemoryReadReference(StrictModel):
    """An immutable finding read by this run, not independent scientific evidence."""

    result_id: ArtifactId
    run_id: ArtifactId
    content_sha256: ContentHash


class MemoryFindingReference(MemoryReadReference):
    title: Annotated[Text, Field(max_length=4000)]
    summary: Annotated[Text, Field(max_length=1000)]
    outcome: Literal["bridge", "answer"]

    @model_validator(mode="after")
    def validate_public_reference(self) -> MemoryFindingReference:
        _validate_public_text(self.model_dump())
        return self


class MemorySearchResults(StrictModel):
    findings: Annotated[tuple[MemoryFindingReference, ...], Field(max_length=10)]
    truncated: bool

    @model_validator(mode="after")
    def distinct_findings(self) -> MemorySearchResults:
        if len({item.result_id for item in self.findings}) != len(self.findings):
            raise ValueError("memory search findings must be distinct")
        return self


class MemorySourceReference(StrictModel):
    retrieval_id: ArtifactId
    source: SourceReference
    content_sha256: ContentHash
    content_kind: Literal["abstract", "full_text"]


class MemoryFinding(StrictModel):
    reference: MemoryFindingReference
    finding: BridgeFinding | AnswerFinding
    overview_binding: AskOverviewBinding | None = None
    sources: Annotated[tuple[MemorySourceReference, ...], Field(max_length=64)]

    @field_validator("overview_binding", mode="before")
    @classmethod
    def forbid_explicit_null_overview(cls, value):
        if value is None:
            raise ValueError("ask_overview_binding_invalid")
        return value

    @model_serializer(mode="wrap")
    def preserve_absent_overview(self, handler):
        data = handler(self)
        if self.overview_binding is None:
            data.pop("overview_binding", None)
        return data

    @model_validator(mode="after")
    def consistent_reference(self) -> MemoryFinding:
        if self.overview_binding is not None:
            from polymath.scientific_context_contracts import validate_answer_overview

            validate_answer_overview(self.finding, self.overview_binding)
        _validate_public_text(self.model_dump())
        if self.reference.outcome != self.finding.outcome:
            raise ValueError("memory finding outcome must match its reference")
        if len({item.retrieval_id for item in self.sources}) != len(self.sources):
            raise ValueError("memory source retrievals must be distinct")
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
    SearchAction
    | ReadAction
    | OpenFullTextAction
    | ReadFullTextWindowAction
    | SearchMemoryAction
    | ReadMemoryAction
    | ReadMemorySourceAction
    | SelectFieldsAction
    | ConcludeAction,
    Field(discriminator="kind"),
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
    overview_binding: AskOverviewBinding | None = None
    field_selections: Annotated[tuple[FieldSelection, ...], Field(max_length=3)] = ()
    memory_reads: Annotated[tuple[MemoryReadReference, ...], Field(max_length=10)] = ()
    quotes: tuple[VerifiedQuote, ...] = ()
    sources: tuple[SourceDocument, ...] = ()
    events: tuple[ResearchEvent, ...] = ()
    usage: Usage
    error_code: str | None = None
    validation_scope: Literal["source_identity_and_quote_integrity"] = (
        "source_identity_and_quote_integrity"
    )

    @field_validator("overview_binding", mode="before")
    @classmethod
    def forbid_explicit_null_overview(cls, value):
        if value is None:
            raise ValueError("ask_overview_binding_invalid")
        return value

    @model_serializer(mode="wrap")
    def preserve_absent_overview(self, handler):
        data = handler(self)
        if self.overview_binding is None:
            data.pop("overview_binding", None)
        return data

    @model_validator(mode="after")
    def exact_overview_binding(self):
        if self.overview_binding is not None:
            # Local import keeps the legacy research/discovery action adapters
            # independent of the optional scientific-context contract module.
            from polymath.scientific_context_contracts import validate_answer_overview

            if self.mode != "ask" or self.status != "answer":
                raise ValueError("ask_overview_wrong_result")
            validate_answer_overview(self.finding, self.overview_binding)
        return self
