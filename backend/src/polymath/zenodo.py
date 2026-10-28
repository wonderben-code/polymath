"""Explicitly gated, injectable Zenodo deposit transport.

The legacy deposition/bucket contract was compared with the official developer
documentation source at zenodo/developers.zenodo.org commit9c277efbbff8022f.
It uses both unsubmitted and inprogress for unpublished drafts and documents
top-level record_id/record_url/doi fields. Documentation review and mock HTTP
tests do not verify native sandbox compatibility. No client is constructed by default, no
request retries, and a credential alone never enables a request. Human approval
and durable mutation/reconciliation records belong to the publication worker.

A remote marker can find candidate deposits after a lost response; it is not a
provider idempotency guarantee. Absence, truncation, or duplicates cannot justify
blindly repeating a mutating request. Remote MD5 metadata is never a SHA-256
verification of the actual uploaded bytes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import date
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

import httpx
from pydantic import Field, SecretStr, ValidationError

from polymath.publication_contracts import (
    Creator,
    Destination,
    PublicationManifest,
    PublicationMetadata,
)
from polymath.research.models import StrictModel, _validate_public_text

MAX_JSON_BYTES = 512_000
MAX_FILE_BYTES = 262_144
REQUEST_TIMEOUT_SECONDS = 20.0
PAGE_SIZE = 10
MAX_PAGES = 2
MAX_DEPOSIT_ID = 2**63 - 1
FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,59}")
MARKER = re.compile(r"polymath-[0-9a-f]{64}")
SHA256 = re.compile(r"[0-9a-f]{64}")
DOI = re.compile(r"10\.[0-9]{4,9}/[A-Za-z0-9][A-Za-z0-9._;()/:+\-]{0,180}")
BUCKET = re.compile(r"/api/files/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CHECKSUM = re.compile(r"(?:md5:)?[0-9a-f]{32}|sha256:[0-9a-f]{64}")
REMOTE_METADATA_KEYS = frozenset(
    {
        "title",
        "description",
        "creators",
        "keywords",
        "license",
        "upload_type",
        "publication_type",
        "access_right",
    }
)
SERVER_METADATA_KEYS = frozenset({"doi", "prereserve_doi", "publication_date"})


class ZenodoError(Exception):
    """Static code only; raw URLs, headers and provider bodies are never retained."""

    def __init__(self, code: str, *, outcome_unknown: bool = False):
        super().__init__(code)
        self.code = code
        self.outcome_unknown = outcome_unknown


class RemoteFile(StrictModel):
    filename: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$")]
    size: Annotated[int, Field(ge=0, le=MAX_FILE_BYTES)]
    checksum: Annotated[str, Field(max_length=71)] | None = None


class ZenodoDeposit(StrictModel):
    id: Annotated[int, Field(ge=1, le=MAX_DEPOSIT_ID)]
    destination: Destination
    submitted: bool
    state: Literal["unsubmitted", "done", "inprogress", "error"]
    metadata: dict
    bucket_url: str | None = None
    record_id: Annotated[int, Field(ge=1, le=MAX_DEPOSIT_ID)] | None = None
    record_url: str | None = None
    doi: str | None = None
    files: Annotated[tuple[RemoteFile, ...], Field(max_length=20)] = ()

    @property
    def published(self) -> bool:
        return bool(
            self.submitted
            and self.state == "done"
            and self.doi
            and self.record_id
            and self.record_url
        )

    @property
    def doi_url(self) -> str | None:
        return "https://doi.org/" + quote(self.doi, safe="/") if self.doi else None


def _manifest(manifest: PublicationManifest) -> PublicationManifest:
    try:
        if not isinstance(manifest, PublicationManifest):
            raise ValueError
        return PublicationManifest.model_validate_json(manifest.model_dump_json())
    except (ValueError, TypeError, ValidationError):
        raise ZenodoError("invalid_publication_manifest") from None


def canonical_remote_metadata(manifest: PublicationManifest) -> dict:
    """The exact reviewed fields plus one hash-bound server reconciliation keyword."""
    manifest = _manifest(manifest)
    metadata = manifest.metadata.model_dump(mode="json", exclude_none=True)
    metadata["keywords"] = [*metadata["keywords"], manifest.remote_marker]
    if len(set(metadata["keywords"])) != len(metadata["keywords"]):
        raise ZenodoError("invalid_publication_manifest")
    return metadata


def metadata_matches(deposit: ZenodoDeposit, manifest: PublicationManifest) -> bool:
    """Compare approved metadata/destination, not a provider's fuzzy search hit."""
    try:
        return deposit.destination == manifest.destination and deposit.metadata == (
            canonical_remote_metadata(manifest)
        )
    except (AttributeError, ZenodoError):
        return False


def _deposit_id(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_DEPOSIT_ID:
        raise ZenodoError("invalid_zenodo_deposit_id")
    return value


def _filename(value: str) -> str:
    if not isinstance(value, str) or FILE_NAME.fullmatch(value) is None:
        raise ZenodoError("invalid_publication_file_name")
    return value


def _plain(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or value != value.strip():
        raise ValueError
    _validate_public_text(value)
    if re.search(r"<\s*/?\s*[A-Za-z][^>]*>|https?://", value, re.IGNORECASE):
        raise ValueError
    return value


def _metadata(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) - REMOTE_METADATA_KEYS - SERVER_METADATA_KEYS:
        raise ValueError
    keywords = raw.get("keywords", [])
    if not isinstance(keywords, list) or len(keywords) > 21:
        raise ValueError
    for keyword in keywords:
        _plain(keyword, 80)
    if len({" ".join(item.split()).casefold() for item in keywords}) != len(keywords):
        raise ValueError
    creators = raw.get("creators")
    if not isinstance(creators, list) or not 1 <= len(creators) <= 10:
        raise ValueError
    normalized_creators = []
    for item in creators:
        if not isinstance(item, dict):
            raise ValueError
        creator = Creator.model_validate_json(json.dumps(item)).model_dump(
            mode="json", exclude_none=True
        )
        if creator != {key: value for key, value in item.items() if value is not None}:
            raise ValueError
        normalized_creators.append(creator)
    projection = {key: raw[key] for key in REMOTE_METADATA_KEYS - {"keywords"} if key in raw}
    projection["creators"] = normalized_creators
    # The marker makes at most21 remote keywords; validate the reviewed scalar
    # fields separately with their existing strict plain-text contract.
    checked = PublicationMetadata.model_validate_json(
        json.dumps({**projection, "keywords": []})
    ).model_dump(mode="json", exclude_none=True)
    checked.pop("keywords")
    if projection != checked:
        raise ValueError
    if raw.get("publication_date") is not None:
        value = raw["publication_date"]
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
            raise ValueError
        date.fromisoformat(value)
    if raw.get("doi") is not None:
        _doi(raw["doi"])
    if raw.get("prereserve_doi") is not None:
        reserved = raw["prereserve_doi"]
        if not isinstance(reserved, dict) or set(reserved) - {"doi", "recid"}:
            raise ValueError
        if "doi" in reserved:
            _doi(reserved["doi"])
        if "recid" in reserved:
            _deposit_id(reserved["recid"])
    return {**projection, "keywords": list(keywords)}


def _doi(raw: object) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or DOI.fullmatch(raw) is None:
        raise ValueError
    return raw


def _remote_file(raw: object) -> RemoteFile:
    if not isinstance(raw, dict):
        raise ValueError
    filename = raw.get("filename", raw.get("key"))
    if "filename" in raw and "key" in raw and raw["filename"] != raw["key"]:
        raise ValueError
    filename = _filename(filename)
    size = raw.get("filesize", raw.get("size"))

    def bounded_size(value):
        # The official legacy quickstart shows a digit-string filesize while
        # the resource table/new bucket API specifies an integer.
        if isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]{0,5}", value):
            value = int(value)
        if type(value) is not int or not 0 <= value <= MAX_FILE_BYTES:
            raise ValueError
        return value

    size = bounded_size(size)
    if "filesize" in raw and "size" in raw:
        if bounded_size(raw["filesize"]) != bounded_size(raw["size"]):
            raise ValueError
    checksum = raw.get("checksum")
    if checksum is not None and (
        not isinstance(checksum, str) or CHECKSUM.fullmatch(checksum) is None
    ):
        raise ValueError
    return RemoteFile(filename=filename, size=size, checksum=checksum)


class ZenodoClient:
    def __init__(
        self,
        destination: Destination,
        token: SecretStr,
        *,
        live_enabled: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if destination not in ("sandbox", "production") or type(live_enabled) is not bool:
            raise ValueError("Explicit Zenodo destination and boolean execution gate are required")
        if not isinstance(token, SecretStr):
            raise ValueError("Zenodo authentication must be configured as a secret")
        value = token.get_secret_value()
        if not 1 <= len(value) <= 4096 or any(not 33 <= ord(item) <= 126 for item in value):
            raise ValueError("Zenodo authentication is not configured safely")
        self.destination = destination
        self._host = "sandbox.zenodo.org" if destination == "sandbox" else "zenodo.org"
        self._origin = "https://" + self._host
        self._token, self._enabled, self._transport = token, live_enabled, transport

    def _url_path(self, value: object) -> str:
        if not isinstance(value, str) or len(value) > 2000:
            raise ValueError
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc != self._host
            or parsed.query
            or parsed.fragment
            or "%" in parsed.path
            or "\\" in parsed.path
            or any(ord(item) < 33 or ord(item) > 126 for item in value)
        ):
            raise ValueError
        return parsed.path

    def _bucket(self, value: object) -> str:
        path = self._url_path(value)
        if BUCKET.fullmatch(path) is None:
            raise ValueError
        return self._origin + path

    def _parse_deposit(self, raw: object, *, expected_id: int | None = None) -> ZenodoDeposit:
        if not isinstance(raw, dict):
            raise ValueError
        identifier = _deposit_id(raw.get("id"))
        if expected_id is not None and identifier != expected_id:
            raise ValueError
        submitted, state = raw.get("submitted"), raw.get("state")
        if type(submitted) is not bool or state not in (
            "unsubmitted",
            "done",
            "inprogress",
            "error",
        ):
            raise ValueError
        metadata = _metadata(raw.get("metadata"))
        links = raw.get("links")
        if not isinstance(links, dict):
            raise ValueError
        bucket = self._bucket(links["bucket"]) if links.get("bucket") is not None else None
        if "self" in links:
            if self._url_path(links["self"]) != f"/api/deposit/depositions/{identifier}":
                raise ValueError
        record_id = _deposit_id(raw["record_id"]) if raw.get("record_id") is not None else None
        record_url = raw.get("record_url") or links.get("record_html")
        for candidate in (raw.get("record_url"), links.get("record_html")):
            if candidate is None:
                continue
            match = re.fullmatch(
                r"/(?:records|record)/([1-9][0-9]{0,18})", self._url_path(candidate)
            )
            if match is None:
                raise ValueError
            actual_record_id = _deposit_id(int(match.group(1)))
            if record_id is not None and record_id != actual_record_id:
                raise ValueError
            # A legacy record_html path still contains the actual returned
            # record identifier. Never invent it from the deposition ID.
            record_id = actual_record_id
        files = raw.get("files", [])
        if not isinstance(files, list) or len(files) > 20:
            raise ValueError
        parsed_files = tuple(_remote_file(item) for item in files)
        if len({item.filename for item in parsed_files}) != len(parsed_files):
            raise ValueError
        # Reserved draft DOI values are not published DOI evidence.
        doi = _doi(raw.get("doi") if raw.get("doi") is not None else raw["metadata"].get("doi"))
        if raw.get("doi") is not None and raw["metadata"].get("doi") is not None:
            if raw["doi"] != raw["metadata"]["doi"]:
                raise ValueError
        if raw.get("doi_url") is not None:
            value = raw["doi_url"]
            if (
                doi is None
                or not isinstance(value, str)
                or value
                not in (
                    "https://doi.org/" + doi,
                    "https://doi.org/" + quote(doi, safe="/"),
                )
            ):
                raise ValueError
        return ZenodoDeposit(
            id=identifier,
            destination=self.destination,
            submitted=submitted,
            state=state,
            metadata=metadata,
            bucket_url=bucket,
            record_id=record_id,
            record_url=record_url,
            doi=doi,
            files=parsed_files,
        )

    def _validated_deposit(self, deposit: ZenodoDeposit, *, writable=False) -> ZenodoDeposit:
        try:
            if not isinstance(deposit, ZenodoDeposit):
                raise ValueError
            checked = ZenodoDeposit.model_validate_json(deposit.model_dump_json())
            if checked.destination != self.destination:
                raise ValueError
            if checked.bucket_url is not None:
                self._bucket(checked.bucket_url)
            if writable and (
                checked.submitted or checked.state not in ("unsubmitted", "inprogress")
            ):
                raise ValueError
            return checked
        except (ValueError, TypeError, ValidationError):
            raise ZenodoError("invalid_zenodo_deposit") from None

    async def _request(
        self,
        method: str,
        url: str,
        *,
        statuses: tuple[int, ...],
        mutation: bool = False,
        params: dict | None = None,
        body: dict | None = None,
        content: bytes | None = None,
        limit: int = MAX_JSON_BYTES,
    ) -> bytes:
        if not self._enabled:
            raise ZenodoError("zenodo_execution_disabled")
        try:
            self._url_path(url)
        except ValueError:
            raise ZenodoError("invalid_zenodo_url") from None
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=httpx.Timeout(15.0, connect=5.0),
                    follow_redirects=False,
                ) as client:
                    headers = {
                        "Authorization": "Bearer " + self._token.get_secret_value(),
                        "Accept": "application/json" if limit == MAX_JSON_BYTES else "*/*",
                        "Accept-Encoding": "identity",
                    }
                    if content is not None:
                        headers["Content-Type"] = "text/markdown; charset=utf-8"
                    async with client.stream(
                        method,
                        url,
                        params=params,
                        json=body,
                        content=content,
                        headers=headers,
                    ) as response:
                        status = response.status_code
                        if status in (401, 403):
                            raise ZenodoError("zenodo_access_denied")
                        if status == 429:
                            raise ZenodoError("zenodo_rate_limited")
                        if status in (404, 410):
                            raise ZenodoError("zenodo_deposit_not_found")
                        if status == 409:
                            raise ZenodoError("zenodo_state_conflict")
                        if 400 <= status < 500:
                            raise ZenodoError("zenodo_request_rejected")
                        if status not in statuses:
                            raise ZenodoError("zenodo_request_failed", outcome_unknown=mutation)
                        if response.headers.get("content-encoding", "identity") != "identity":
                            raise ZenodoError("zenodo_invalid_response", outcome_unknown=mutation)
                        size = response.headers.get("content-length")
                        if size is not None:
                            try:
                                parsed_size = int(size)
                                if parsed_size < 0:
                                    raise ValueError
                            except ValueError:
                                raise ZenodoError(
                                    "zenodo_invalid_response", outcome_unknown=mutation
                                ) from None
                            if parsed_size > limit:
                                raise ZenodoError(
                                    "zenodo_response_too_large", outcome_unknown=mutation
                                )
                        payload = bytearray()
                        async for part in response.aiter_bytes():
                            payload.extend(part)
                            if len(payload) > limit:
                                raise ZenodoError(
                                    "zenodo_response_too_large", outcome_unknown=mutation
                                )
                        return bytes(payload)
        except (TimeoutError, httpx.TimeoutException):
            raise ZenodoError("zenodo_timeout", outcome_unknown=mutation) from None
        except httpx.HTTPError:
            raise ZenodoError("zenodo_transport_error", outcome_unknown=mutation) from None

    @staticmethod
    def _json(payload: bytes, *, mutation=False):
        def unique_object(pairs):
            output = {}
            for key, value in pairs:
                if key in output:
                    raise ValueError
                output[key] = value
            return output

        def invalid_constant(value):
            raise ValueError

        try:
            value = json.loads(
                payload.decode("utf-8", errors="strict"),
                object_pairs_hook=unique_object,
                parse_constant=invalid_constant,
            )
            _validate_public_text(value)
            return value
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ZenodoError("zenodo_invalid_response", outcome_unknown=mutation) from None

    def _parsed(self, raw: object, *, mutation=False, expected_id=None) -> ZenodoDeposit:
        try:
            return self._parse_deposit(raw, expected_id=expected_id)
        except (ValueError, TypeError, KeyError, ValidationError, ZenodoError):
            raise ZenodoError("zenodo_invalid_response", outcome_unknown=mutation) from None

    async def create_draft(self, manifest: PublicationManifest) -> ZenodoDeposit:
        manifest = _manifest(manifest)
        if manifest.destination != self.destination:
            raise ZenodoError("zenodo_destination_mismatch")
        metadata = canonical_remote_metadata(manifest)
        payload = await self._request(
            "POST",
            self._origin + "/api/deposit/depositions",
            statuses=(201,),
            mutation=True,
            body={"metadata": metadata},
        )
        deposit = self._parsed(self._json(payload, mutation=True), mutation=True)
        if (
            deposit.submitted
            or deposit.state not in ("unsubmitted", "inprogress")
            or deposit.bucket_url is None
        ):
            raise ZenodoError("zenodo_unexpected_state", outcome_unknown=True)
        if not metadata_matches(deposit, manifest):
            raise ZenodoError("zenodo_metadata_mismatch", outcome_unknown=True)
        return deposit

    async def get_deposit(self, identifier: int) -> ZenodoDeposit:
        identifier = _deposit_id(identifier)
        payload = await self._request(
            "GET", self._origin + f"/api/deposit/depositions/{identifier}", statuses=(200,)
        )
        return self._parsed(self._json(payload), expected_id=identifier)

    async def upload_file(
        self,
        deposit: ZenodoDeposit,
        filename: str,
        content: bytes,
        expected_sha256: str,
    ) -> RemoteFile:
        deposit = self._validated_deposit(deposit, writable=True)
        filename = _filename(filename)
        if (
            not isinstance(content, bytes)
            or not 1 <= len(content) <= MAX_FILE_BYTES
            or not isinstance(expected_sha256, str)
            or SHA256.fullmatch(expected_sha256) is None
            or hashlib.sha256(content).hexdigest() != expected_sha256
        ):
            raise ZenodoError("invalid_publication_file")
        try:
            content.decode("utf-8", errors="strict")
        except UnicodeError:
            raise ZenodoError("invalid_publication_file") from None
        if deposit.bucket_url is None:
            raise ZenodoError("invalid_zenodo_deposit")
        payload = await self._request(
            "PUT",
            deposit.bucket_url + "/" + filename,
            statuses=(200, 201),
            mutation=True,
            content=content,
        )
        try:
            remote = _remote_file(self._json(payload, mutation=True))
        except (ValueError, TypeError, ValidationError, ZenodoError):
            raise ZenodoError("zenodo_invalid_response", outcome_unknown=True) from None
        if remote.filename != filename or remote.size != len(content):
            raise ZenodoError("zenodo_file_mismatch", outcome_unknown=True)
        if remote.checksum is not None:
            checksum = (
                "sha256:" + expected_sha256
                if remote.checksum.startswith("sha256:")
                else hashlib.md5(content, usedforsecurity=False).hexdigest()
            )
            if remote.checksum.removeprefix("md5:") != checksum:
                raise ZenodoError("zenodo_file_mismatch", outcome_unknown=True)
        return remote

    async def get_file_bytes(
        self, deposit: ZenodoDeposit, filename: str, maxbytes: int = MAX_FILE_BYTES
    ) -> bytes:
        deposit = self._validated_deposit(deposit)
        filename = _filename(filename)
        if type(maxbytes) is not int or not 1 <= maxbytes <= MAX_FILE_BYTES:
            raise ZenodoError("invalid_publication_file_limit")
        if deposit.bucket_url is None:
            raise ZenodoError("invalid_zenodo_deposit")
        return await self._request(
            "GET", deposit.bucket_url + "/" + filename, statuses=(200,), limit=maxbytes
        )

    async def publish(self, deposit_id: int) -> ZenodoDeposit:
        deposit_id = _deposit_id(deposit_id)
        payload = await self._request(
            "POST",
            self._origin + f"/api/deposit/depositions/{deposit_id}/actions/publish",
            statuses=(200, 201, 202),
            mutation=True,
        )
        return self._parsed(
            self._json(payload, mutation=True), mutation=True, expected_id=deposit_id
        )

    async def find_deposits(self, remote_marker: str) -> tuple[ZenodoDeposit, ...]:
        if not isinstance(remote_marker, str) or MARKER.fullmatch(remote_marker) is None:
            raise ZenodoError("invalid_publication_marker")
        found: dict[int, ZenodoDeposit] = {}
        for page in range(1, MAX_PAGES + 1):
            payload = await self._request(
                "GET",
                self._origin + "/api/deposit/depositions",
                statuses=(200,),
                params={
                    "q": f'keywords:"{remote_marker}"',
                    "size": PAGE_SIZE,
                    "page": page,
                    "sort": "mostrecent",
                },
            )
            entries = self._json(payload)
            if not isinstance(entries, list):
                raise ZenodoError("zenodo_invalid_response")
            if len(entries) > PAGE_SIZE:
                raise ZenodoError("zenodo_lookup_truncated")
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("metadata"), dict):
                    raise ZenodoError("zenodo_invalid_response")
                keywords = entry["metadata"].get("keywords", [])
                if not isinstance(keywords, list) or not all(
                    isinstance(item, str) for item in keywords
                ):
                    raise ZenodoError("zenodo_invalid_response")
                if remote_marker not in keywords:
                    continue
                candidate = self._parsed(entry)
                if candidate.id in found and found[candidate.id] != candidate:
                    raise ZenodoError("zenodo_lookup_conflict")
                found[candidate.id] = candidate
            if len(entries) < PAGE_SIZE:
                return tuple(found.values())
        raise ZenodoError("zenodo_lookup_truncated")
