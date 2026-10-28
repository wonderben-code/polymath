"""Strict public-only content DTOs; no private source/read capability is included."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import date
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from polymath.literature.identity import (
    canonical_arxiv_id,
    canonical_doi,
    canonical_openalex_id,
    canonical_orcid,
)
from polymath.research.models import (
    ContentHash,
    ResearchMode,
    StrictModel,
    Text,
    _validate_public_text,
)

PUBLIC_PROTOCOL = "polymath-public-finding-v1"
MAX_PUBLIC_BYTES = 262_144
MAX_PUBLIC_EXCERPT = 280
MAX_PUBLIC_PAPER_EXCERPTS = 1000
PublicPaperId = Annotated[str, Field(pattern=r"^paper-[1-9][0-9]?$", max_length=8)]
PublicCitationId = Annotated[str, Field(pattern=r"^citation-[1-9][0-9]?$", max_length=11)]
PublicClaimId = Annotated[str, Field(pattern=r"^claim-[1-8]$")]


class PublicModel(StrictModel):
    @model_validator(mode="after")
    def public_text(self):
        _validate_public_text(self.model_dump(mode="json"))
        return self


class PublicAuthor(PublicModel):
    name: Annotated[Text, Field(max_length=300)]
    orcid: Annotated[str, Field(max_length=19)] | None = None

    @field_validator("orcid")
    @classmethod
    def validated_orcid(cls, value):
        return canonical_orcid(value) if value is not None else None


class PublicBibliography(PublicModel):
    authors: Annotated[tuple[PublicAuthor, ...], Field(max_length=20)] = ()
    authors_truncated: bool = False
    publication_year: Annotated[int, Field(ge=1000, le=2100)] | None = None
    publication_date: date | None = None
    venue: Annotated[Text, Field(max_length=500)] | None = None

    @model_validator(mode="after")
    def bounded_bibliography(self):
        if self.authors_truncated and len(self.authors) != 20:
            raise ValueError("public truncated authors require their bounded prefix")
        if self.publication_date is not None and not 1000 <= self.publication_date.year <= 2100:
            raise ValueError("public publication date is outside its bound")
        return self


def canonical_public_doi(value: str) -> str:
    """Preserve DOI identity only when browsers preserve its literal URL path.

    Private bibliography accepts historical DOI suffixes containing dot segments.
    A public resolver URL must not silently navigate to a different identifier.
    """
    identifier = canonical_doi(value)
    if any(segment in (".", "..") for segment in identifier.split("/")):
        raise ValueError("public DOI links cannot contain URL dot segments")
    return identifier


class PublicIdentifiers(PublicModel):
    doi: Annotated[str, Field(max_length=200)] | None = None
    arxiv_id: Annotated[str, Field(max_length=100)] | None = None
    arxiv_version: Annotated[int, Field(ge=1, le=9999)] | None = None
    openalex_id: Annotated[str, Field(max_length=40)] | None = None

    @field_validator("doi")
    @classmethod
    def valid_doi(cls, value):
        return canonical_public_doi(value) if value is not None else None

    @field_validator("openalex_id")
    @classmethod
    def valid_openalex(cls, value):
        return canonical_openalex_id(value) if value is not None else None

    @field_validator("arxiv_id")
    @classmethod
    def valid_arxiv(cls, value):
        if value is None:
            return None
        base, version = canonical_arxiv_id(value)
        if version is not None:
            raise ValueError("public arXiv base and version must be separate")
        return base

    @model_validator(mode="after")
    def separate_version(self):
        if self.arxiv_version is not None and self.arxiv_id is None:
            raise ValueError("public arXiv version requires its base identifier")
        return self

    @model_serializer(mode="wrap")
    def omit_absent(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class PublicScholarlyLink(PublicModel):
    kind: Literal["doi", "arxiv", "openalex"]
    url: Annotated[str, Field(max_length=2000)]

    @model_validator(mode="after")
    def scholarly_url(self):
        if self.kind == "doi":
            valid = self.url == "https://doi.org/" + canonical_public_doi(self.url)
        elif self.kind == "openalex":
            prefix = "https://openalex.org/"
            valid = self.url.startswith(prefix) and self.url == prefix + canonical_openalex_id(
                self.url[len(prefix) :]
            )
        else:
            prefix = next(
                (
                    item
                    for item in ("https://arxiv.org/abs/", "https://arxiv.org/html/")
                    if self.url.startswith(item)
                ),
                None,
            )
            if prefix is None:
                valid = False
            else:
                base, version = canonical_arxiv_id(self.url[len(prefix) :])
                valid = self.url == prefix + base + (f"v{version}" if version is not None else "")
                if prefix.endswith("html/") and version is None:
                    valid = False
        if not valid:
            raise ValueError("public links require reconstructed scholarly identifiers")
        return self


class PublicPaper(PublicModel):
    id: PublicPaperId
    title: Annotated[Text, Field(max_length=1000)]
    bibliography: PublicBibliography | None = None
    identifiers: PublicIdentifiers
    links: Annotated[tuple[PublicScholarlyLink, ...], Field(max_length=3)]

    @model_validator(mode="after")
    def identifier_links(self):
        expected = {
            kind
            for kind, value in (
                ("doi", self.identifiers.doi),
                ("arxiv", self.identifiers.arxiv_id),
                ("openalex", self.identifiers.openalex_id),
            )
            if value is not None
        }
        if {link.kind for link in self.links} != expected or len(self.links) != len(expected):
            raise ValueError("public scholarly links must match their declared identifiers")
        for link in self.links:
            if link.kind == "doi" and link.url != "https://doi.org/" + self.identifiers.doi:
                raise ValueError("public DOI link differs from its identifier")
            if (
                link.kind == "openalex"
                and link.url != "https://openalex.org/" + self.identifiers.openalex_id
            ):
                raise ValueError("public OpenAlex link differs from its identifier")
            if link.kind == "arxiv":
                suffix = self.identifiers.arxiv_id + (
                    f"v{self.identifiers.arxiv_version}"
                    if self.identifiers.arxiv_version is not None
                    else ""
                )
                if link.url not in (
                    "https://arxiv.org/abs/" + suffix,
                    "https://arxiv.org/html/" + suffix,
                ):
                    raise ValueError("public arXiv link differs from its identifier")
        return self

    @model_serializer(mode="wrap")
    def omit_absent_bibliography(self, handler):
        data = handler(self)
        if self.bibliography is None:
            data.pop("bibliography", None)
        return data


class PublicCoverage(PublicModel):
    # These are the only allowed locator fields. Private snapshot UUIDs are absent.
    arxiv_id: Annotated[str, Field(max_length=100)]
    arxiv_version: Annotated[int, Field(ge=1, le=9999)]
    snapshot_sha256: ContentHash
    start: Annotated[int, Field(ge=0, lt=250000)]
    end: Annotated[int, Field(ge=1, le=250000)]
    total: Annotated[int, Field(ge=1, le=250000)]

    @field_validator("arxiv_id")
    @classmethod
    def canonical_base(cls, value):
        base, version = canonical_arxiv_id(value)
        if version is not None:
            raise ValueError("public coverage requires a separate arXiv base and version")
        return base

    @model_validator(mode="after")
    def bounded_window(self):
        if not self.start < self.end <= self.total or self.end - self.start > 8000:
            raise ValueError("public coverage requires an exact bounded window")
        return self


class PublicQuoteExcerpt(PublicModel):
    excerpt: Annotated[str, Field(min_length=10, max_length=MAX_PUBLIC_EXCERPT)] | None
    excerpted: bool
    excerpt_start: Annotated[int, Field(ge=0, lt=20000)] | None
    excerpt_end: Annotated[int, Field(ge=1, le=20000)] | None
    full_quote_start: Annotated[int, Field(ge=0, lt=20000)]
    full_quote_end: Annotated[int, Field(ge=1, le=20000)]
    full_quote_chars: Annotated[int, Field(ge=10, le=4000)]
    full_quote_sha256: ContentHash
    excerpt_omission_reason: Literal["paper_limit"] | None

    @model_validator(mode="after")
    def exact_offsets(self):
        if self.full_quote_end - self.full_quote_start != self.full_quote_chars:
            raise ValueError("public full quote offsets must match exact Unicode length")
        if self.excerpt is None:
            if (
                self.excerpt_start is not None
                or self.excerpt_end is not None
                or not self.excerpted
                or self.excerpt_omission_reason != "paper_limit"
            ):
                raise ValueError("public omitted excerpts require explicit paper-limit metadata")
        else:
            if (
                self.excerpt_start != self.full_quote_start
                or self.excerpt_end != self.full_quote_start + len(self.excerpt)
                or len(self.excerpt) > self.full_quote_chars
                or self.excerpted != (len(self.excerpt) < self.full_quote_chars)
                or self.excerpt_omission_reason is not None
            ):
                raise ValueError("public excerpt offsets must preserve an exact quote prefix")
            if (
                not self.excerpted
                and hashlib.sha256(self.excerpt.encode()).hexdigest() != self.full_quote_sha256
            ):
                raise ValueError("public full quote hash differs from its complete excerpt")
        return self


class PublicCitation(PublicModel):
    id: PublicCitationId
    paper_id: PublicPaperId
    side: Literal["left", "right", "answer"]
    claim_id: PublicClaimId | None
    supports: Annotated[Text, Field(max_length=1500)]
    source_kind: Literal["abstract", "full_text"]
    source_sha256: ContentHash
    quote: PublicQuoteExcerpt
    coverage: PublicCoverage | None = None

    @model_validator(mode="after")
    def citation_shape(self):
        if (self.side == "answer") != (self.claim_id is not None):
            raise ValueError("public citation claims must match their answer side")
        if self.coverage is not None and (
            self.source_kind != "full_text"
            or self.quote.full_quote_end > self.coverage.end - self.coverage.start
        ):
            raise ValueError("public citation offsets exceed its exact full-text window")
        return self

    @model_serializer(mode="wrap")
    def omit_absent_coverage(self, handler):
        data = handler(self)
        if self.coverage is None:
            data.pop("coverage", None)
        return data


class PublicMapping(PublicModel):
    left: Annotated[Text, Field(max_length=1000)]
    right: Annotated[Text, Field(max_length=1000)]
    assumption: Annotated[Text, Field(max_length=1500)]


class PublicBridge(PublicModel):
    kind: Literal["bridge"] = "bridge"
    title: Annotated[Text, Field(max_length=300)]
    field_left: Annotated[Text, Field(max_length=200)]
    field_right: Annotated[Text, Field(max_length=200)]
    explanation: Annotated[Text, Field(max_length=3000)]
    mappings: Annotated[tuple[PublicMapping, ...], Field(min_length=1, max_length=8)]
    open_question: Annotated[Text, Field(max_length=1500)]
    strongest_objection: Annotated[Text, Field(max_length=2000)]
    objection_verdict: Annotated[Text, Field(max_length=2000)]
    novelty_limitations: Annotated[Text, Field(max_length=2000)]
    citation_ids: Annotated[tuple[PublicCitationId, ...], Field(min_length=2, max_length=12)]


class PublicClaim(PublicModel):
    id: PublicClaimId
    text: Annotated[Text, Field(max_length=2000)]
    label: Literal["known", "postulated", "speculative"]
    derivation: Annotated[Text, Field(max_length=2000)] | None
    caveats: Annotated[tuple[Annotated[Text, Field(max_length=1000)], ...], Field(max_length=6)]
    citation_ids: Annotated[tuple[PublicCitationId, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def uncertainty(self):
        if self.label == "postulated" and self.derivation is None:
            raise ValueError("public postulated claims retain their derivation")
        if self.label == "speculative" and not self.caveats:
            raise ValueError("public speculative claims retain their caveats")
        return self


class PublicAnswer(PublicModel):
    kind: Literal["answer"] = "answer"
    question: Annotated[Text, Field(max_length=4000)]
    summary: Annotated[Text, Field(max_length=3000)]
    claims: Annotated[tuple[PublicClaim, ...], Field(min_length=1, max_length=8)]
    limitations: Annotated[
        tuple[Annotated[Text, Field(max_length=1000)], ...], Field(min_length=1, max_length=8)
    ]
    unanswered_questions: Annotated[
        tuple[Annotated[Text, Field(max_length=1000)], ...], Field(max_length=8)
    ]


class PublicOverviewBinding(PublicModel):
    protocol: Literal["polymath-ask-claim-overview-v1"] = "polymath-ask-claim-overview-v1"
    claim_ids: Annotated[tuple[PublicClaimId, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def distinct_claims(self):
        if len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError("public_overview_duplicate_claim")
        return self


def classified_answer_navigation_summary(labels: Iterable[str]) -> str:
    """Describe an answer record without turning a clipped claim into an overview."""
    counts = dict.fromkeys(("known", "postulated", "speculative"), 0)
    count = 0
    for label in labels:
        if count >= 8 or type(label) is not str or label not in counts:
            raise ValueError("invalid_navigation_claims")
        counts[label] += 1
        count += 1
    if count == 0:
        raise ValueError("invalid_navigation_claims")
    return (
        f"Saved Ask answer with {count} classified claims "
        f"(known: {counts['known']}, postulated: {counts['postulated']}, "
        f"speculative: {counts['speculative']}). Open for exact claims and citations."
    )


class PublicFieldSelection(PublicModel):
    fields: Annotated[
        tuple[Annotated[Text, Field(max_length=100)], ...], Field(min_length=2, max_length=4)
    ]
    rationale: Annotated[Text, Field(max_length=1000)]


class PublicFindingProjection(PublicModel):
    protocol: Literal["polymath-public-finding-v1"] = PUBLIC_PROTOCOL
    mode: ResearchMode
    validation_scope: Literal["model_assessment_and_saved_quote_integrity"] = (
        "model_assessment_and_saved_quote_integrity"
    )
    finding: Annotated[PublicBridge | PublicAnswer, Field(discriminator="kind")]
    papers: Annotated[tuple[PublicPaper, ...], Field(min_length=1, max_length=64)]
    citations: Annotated[tuple[PublicCitation, ...], Field(min_length=1, max_length=64)]
    field_selection: PublicFieldSelection | None = None
    overview_binding: PublicOverviewBinding | None = None

    @field_validator("overview_binding", mode="before")
    @classmethod
    def present_binding(cls, value):
        if value is None:
            raise ValueError("public_overview_binding_null")
        if isinstance(value, dict):
            return PublicOverviewBinding.model_validate_json(json.dumps(value))
        return value

    @model_validator(mode="after")
    def complete_associations(self):
        if [item.id for item in self.papers] != [
            f"paper-{index + 1}" for index in range(len(self.papers))
        ] or [item.id for item in self.citations] != [
            f"citation-{index + 1}" for index in range(len(self.citations))
        ]:
            raise ValueError("public paper/citation IDs must follow deterministic local ordinals")
        papers = {item.id for item in self.papers}
        if {item.paper_id for item in self.citations} != papers:
            raise ValueError("public papers must match exactly the cited local references")
        first_cited = list(dict.fromkeys(item.paper_id for item in self.citations))
        if first_cited != [item.id for item in self.papers]:
            raise ValueError("public papers must follow their first citation ordinal")
        totals = dict.fromkeys(papers, 0)
        for citation in self.citations:
            expected_chars = min(
                MAX_PUBLIC_EXCERPT,
                citation.quote.full_quote_chars,
                MAX_PUBLIC_PAPER_EXCERPTS - totals[citation.paper_id],
            )
            actual_chars = len(citation.quote.excerpt or "")
            if (expected_chars < 10 and citation.quote.excerpt is not None) or (
                expected_chars >= 10 and actual_chars != expected_chars
            ):
                raise ValueError("public excerpts must follow their deterministic paper budget")
            totals[citation.paper_id] += len(citation.quote.excerpt or "")
        if any(total > MAX_PUBLIC_PAPER_EXCERPTS for total in totals.values()):
            raise ValueError("public paper excerpt budget exceeded")
        if isinstance(self.finding, PublicAnswer):
            if self.mode != "ask" or [claim.id for claim in self.finding.claims] != [
                f"claim-{index + 1}" for index in range(len(self.finding.claims))
            ]:
                raise ValueError("public Ask claims must use deterministic local ordinals")
            if self.overview_binding is not None:
                by_id = {claim.id: claim for claim in self.finding.claims}
                if any(identifier not in by_id for identifier in self.overview_binding.claim_ids):
                    raise ValueError("public_overview_unknown_claim")
                overview = "\n\n".join(
                    by_id[identifier].text for identifier in self.overview_binding.claim_ids
                )
                if self.finding.summary != overview:
                    raise ValueError("public_overview_summary_mismatch")
            associated = []
            for claim in self.finding.claims:
                associated.extend(claim.citation_ids)
                if any(
                    item.claim_id != claim.id or item.side != "answer"
                    for item in self.citations
                    if item.id in claim.citation_ids
                ):
                    raise ValueError("public citations must retain their exact claim association")
        else:
            if self.overview_binding is not None:
                raise ValueError("public_overview_wrong_finding")
            if self.mode == "ask" or any(item.side == "answer" for item in self.citations):
                raise ValueError("public bridges require bridge citation sides")
            if {item.side for item in self.citations} != {"left", "right"}:
                raise ValueError("public bridges must preserve evidence for both sides")
            if self.finding.field_left.casefold() == self.finding.field_right.casefold():
                raise ValueError("public bridges require distinct fields")
            associated = list(self.finding.citation_ids)
        if associated != [item.id for item in self.citations]:
            raise ValueError("every original public citation association must be retained in order")
        if (self.mode == "surprise") != (self.field_selection is not None):
            raise ValueError("only Surprise exposes its final field selection")
        if self.field_selection is not None:
            fields = {" ".join(value.split()).casefold() for value in self.field_selection.fields}
            if len(fields) != len(self.field_selection.fields) or not {
                " ".join(self.finding.field_left.split()).casefold(),
                " ".join(self.finding.field_right.split()).casefold(),
            }.issubset(fields):
                raise ValueError("public Surprise fields must retain its final bridge selection")
        if (
            len(
                json.dumps(
                    self.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
            > MAX_PUBLIC_BYTES
        ):
            raise ValueError("public_projection_too_large")
        return self

    @model_serializer(mode="wrap")
    def omit_absent_selection(self, handler):
        data = handler(self)
        if self.field_selection is None:
            data.pop("field_selection", None)
        if self.overview_binding is None:
            data.pop("overview_binding", None)
        return data
