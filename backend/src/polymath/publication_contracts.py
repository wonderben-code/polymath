"""Exact human-reviewed publication inputs; no model can grant approval."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from polymath.research.models import (
    ArtifactId,
    ContentHash,
    StrictModel,
    Text,
    _validate_public_text,
)

Destination = Literal["sandbox", "production"]


class PublicationText(StrictModel):
    @model_validator(mode="after")
    def plain_reviewed_text(self) -> PublicationText:
        values = self.model_dump()
        _validate_public_text(values)
        pending = [values]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                if re.search(r"<\s*/?\s*[A-Za-z][^>]*>|https?://", value, re.IGNORECASE):
                    raise ValueError("publication metadata must be plain text")
            elif isinstance(value, dict):
                pending.extend(value.values())
            elif isinstance(value, (list, tuple)):
                pending.extend(value)
        return self


class PublisherIdentity(PublicationText):
    identifier: Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")]
    display_name: Annotated[Text, Field(max_length=200)]


class Creator(PublicationText):
    name: Annotated[Text, Field(max_length=200)]
    affiliation: Annotated[Text, Field(max_length=300)] | None = None
    orcid: (
        Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$")]
        | None
    ) = None

    @field_validator("orcid")
    @classmethod
    def checked_orcid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        digits = value.replace("-", "")
        total = 0
        for digit in digits[:15]:
            total = (total + int(digit)) * 2
        remainder = (12 - total % 11) % 11
        expected = "X" if remainder == 10 else str(remainder)
        if digits[-1] != expected:
            raise ValueError("invalid ORCID checksum")
        return value


class PublicationMetadata(PublicationText):
    title: Annotated[Text, Field(max_length=300)]
    description: Annotated[Text, Field(max_length=5000)]
    creators: Annotated[tuple[Creator, ...], Field(min_length=1, max_length=10)]
    keywords: Annotated[
        tuple[Annotated[Text, Field(max_length=80)], ...], Field(max_length=20)
    ] = ()
    license: Literal["cc-by-4.0", "cc0-1.0"]
    upload_type: Literal["publication"] = "publication"
    publication_type: Literal["preprint"] = "preprint"
    access_right: Literal["open"] = "open"

    @field_validator("creators", "keywords", mode="before")
    @classmethod
    def json_arrays(cls, value):
        # FastAPI validates an already-parsed JSON object, so allow its arrays
        # explicitly while retaining strict scalar and nested-model validation.
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def distinct_keywords(self) -> PublicationMetadata:
        normalized = [" ".join(value.split()).casefold() for value in self.keywords]
        if len(set(normalized)) != len(normalized):
            raise ValueError("publication keywords must be distinct")
        return self


class PublicationManifest(StrictModel):
    protocol: Literal["polymath-publication-v1"] = "polymath-publication-v1"
    artifact_id: ArtifactId
    artifact_run_id: ArtifactId
    markdown_sha256: ContentHash
    markdown_bytes: Annotated[int, Field(ge=1, le=262_144)]
    payload_sha256: ContentHash
    basis_sha256: ContentHash
    file_name: Annotated[str, StringConstraints(max_length=60)]
    media_type: Literal["text/markdown; charset=utf-8"] = "text/markdown; charset=utf-8"
    metadata: PublicationMetadata
    publisher: PublisherIdentity
    destination: Destination
    remote_marker: Annotated[str, StringConstraints(pattern=r"^polymath-[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def exact_filename(self) -> PublicationManifest:
        if self.file_name != f"polymath-{self.artifact_id}.md":
            raise ValueError("publication file name must bind the exact artifact")
        if self.remote_marker != publication_marker(self.model_dump(mode="json")):
            raise ValueError("publication marker must bind the exact manifest contents")
        return self


def publication_marker(manifest_fields: dict) -> str:
    base = {key: value for key, value in manifest_fields.items() if key != "remote_marker"}
    canonical = json.dumps(
        base, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    return "polymath-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PublicationPolicy:
    publisher: PublisherIdentity | None = None
    allowed_destinations: tuple[Destination, ...] = ()
    approval_seconds: int = 1200

    def __post_init__(self) -> None:
        if self.publisher is not None and not isinstance(self.publisher, PublisherIdentity):
            raise ValueError("A validated publisher identity is required")
        if (
            not isinstance(self.allowed_destinations, tuple)
            or any(value not in ("sandbox", "production") for value in self.allowed_destinations)
            or len(set(self.allowed_destinations)) != len(self.allowed_destinations)
        ):
            raise ValueError("Explicit distinct publication destinations are required")
        if type(self.approval_seconds) is not int or not 60 <= self.approval_seconds <= 3600:
            raise ValueError("Approval lifetime must be between 60 and 3600 seconds")

    @property
    def configured(self) -> bool:
        return self.publisher is not None and bool(self.allowed_destinations)
