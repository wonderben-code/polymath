"""Bounded arXiv HTML extraction/index contracts; native compatibility unverified.

Snapshot hashes cover the entire extracted Unicode text encoded as UTF-8. Window
source hashes cover only the selected text. Indexes do not authorize a source read
or constitute evidence; an owned, saved exact window is required for quotation.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from polymath.research.models import (
    MAX_FULL_TEXT_CHARACTERS,
    MAX_FULL_TEXT_HEADINGS,
    MAX_FULL_TEXT_WINDOW,
    ArtifactId,
    ArxivFullTextLocator,
    ContentHash,
    FullTextAvailability,
    SourceReference,
    StrictModel,
    Text,
    _validate_public_text,
)

__all__ = [
    "MAX_FULL_TEXT_CHARACTERS",
    "MAX_FULL_TEXT_HEADINGS",
    "MAX_FULL_TEXT_WINDOW",
    "ArxivFullTextLocator",
    "ExtractedFullText",
    "FullTextAvailability",
    "FullTextHeading",
    "FullTextIndex",
]

EXTRACTOR_VERSION = "arxiv_html_latexml/v1"


class FullTextHeading(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    level: Annotated[int, Field(ge=1, le=6)]
    start: Annotated[int, Field(ge=0, lt=MAX_FULL_TEXT_CHARACTERS)]
    end: Annotated[int, Field(ge=1, le=MAX_FULL_TEXT_CHARACTERS)]

    @model_validator(mode="after")
    def exact_span(self) -> FullTextHeading:
        _validate_public_text(self.title)
        if self.end - self.start != len(self.title):
            raise ValueError("full-text heading offsets must match their exact title")
        return self


class _FullTextProvenance(ArxivFullTextLocator):
    arxiv_version: Annotated[int, Field(ge=1, le=9999)]
    url: Annotated[str, Field(max_length=2000)]
    raw_html_sha256: ContentHash
    retrieved_at: datetime
    extractor_version: Literal["arxiv_html_latexml/v1"] = EXTRACTOR_VERSION
    headings: Annotated[tuple[FullTextHeading, ...], Field(max_length=MAX_FULL_TEXT_HEADINGS)]
    headings_truncated: bool = False
    limitations: Annotated[
        tuple[Annotated[Text, Field(max_length=1000)], ...], Field(min_length=1, max_length=8)
    ]

    @model_validator(mode="after")
    def exact_provenance(self) -> _FullTextProvenance:
        _validate_public_text(self.model_dump(mode="json"))
        if self.url != f"https://arxiv.org/html/{self.arxiv_id}v{self.arxiv_version}":
            raise ValueError("full-text provenance requires its exact versioned arXiv HTML URL")
        if self.retrieved_at.tzinfo is None:
            raise ValueError("full-text retrieval time must have a timezone")
        if any(
            previous.end > following.start
            for previous, following in zip(self.headings, self.headings[1:], strict=False)
        ):
            raise ValueError("full-text headings must be ordered non-overlapping spans")
        if self.headings_truncated and len(self.headings) != MAX_FULL_TEXT_HEADINGS:
            raise ValueError("truncated heading indexes must retain their bounded prefix")
        return self


class ExtractedFullText(_FullTextProvenance):
    text: Annotated[str, Field(min_length=1, max_length=MAX_FULL_TEXT_CHARACTERS)]

    @model_validator(mode="after")
    def exact_headings(self) -> ExtractedFullText:
        if any(self.text[item.start : item.end] != item.title for item in self.headings):
            raise ValueError("full-text heading titles must be exact snapshot substrings")
        return self

    @property
    def snapshot_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def total_chars(self) -> int:
        return len(self.text)


class FullTextIndex(_FullTextProvenance):
    snapshot_id: ArtifactId
    source: SourceReference
    snapshot_sha256: ContentHash
    total_chars: Annotated[int, Field(ge=1, le=MAX_FULL_TEXT_CHARACTERS)]

    @model_validator(mode="after")
    def bounded_index(self) -> FullTextIndex:
        if any(item.end > self.total_chars for item in self.headings):
            raise ValueError("full-text heading index exceeds its immutable snapshot")
        if self.source.coverage is not None:
            raise ValueError("full-text indexes require the original discovered source reference")
        bibliography = self.source.bibliography
        if bibliography is not None and (
            (bibliography.arxiv_id is not None and bibliography.arxiv_id != self.arxiv_id)
            or (
                bibliography.arxiv_version is not None
                and bibliography.arxiv_version != self.arxiv_version
            )
        ):
            raise ValueError("full-text indexes must preserve the source's declared arXiv identity")
        return self
