"""Fixed-host metadata clients with explicit limits and no document URL fetching."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx
from pydantic import SecretStr, ValidationError

from polymath.literature.errors import Provider, ProviderError
from polymath.literature.identity import (
    canonical_arxiv_id,
    canonical_doi,
    canonical_openalex_id,
    identifier_from_url,
)
from polymath.provider_pacing import ProviderPacingError, ProviderSlot
from polymath.research.models import (
    SourceAuthor,
    SourceBibliography,
    SourceDocument,
    SourceReference,
)

OPENALEX_URL = "https://api.openalex.org/works"
ARXIV_URL = "https://export.arxiv.org/api/query"
MAX_RESPONSE_BYTES = 512_000
MAX_ABSTRACT_CHARACTERS = 20_000
MAX_ABSTRACT_TOKENS = 4000
REQUEST_TIMEOUT_SECONDS = 15.0
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[A-Za-z0-9][A-Za-z0-9._;()/:+\-]{0,170}")
ARXIV_ID = re.compile(
    r"(?P<base>(?:\d{4}\.\d{4,5}|[a-z]+(?:-[a-z]+)?(?:\.[A-Z]{2})?/\d{7}))"
    r"(?:v[1-9]\d{0,3})?"
)


@dataclass(frozen=True)
class DiscoveredSource:
    reference: SourceReference
    document: SourceDocument | None


def validate_query(query: str) -> str:
    if not isinstance(query, str):
        raise ValueError("invalid_query")
    query = query.strip()
    if not 1 <= len(query) <= 500 or any(ord(character) < 32 for character in query):
        raise ValueError("invalid_query")
    return query


def validate_result_limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError("result_limit must be an integer between 1 and 5")


def _title(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 1000:
        raise ValueError
    if any(ord(character) < 32 and character not in "\n\t\r" for character in value):
        raise ValueError
    return " ".join(value.split())


def _doi(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError
    return canonical_doi(value)


def _identity(source_id: str, url: str, doi: str | None) -> tuple[str, str]:
    # Shared DOI identity prevents counting the same paper from two providers twice.
    if doi is not None:
        return f"doi:{doi}", f"https://doi.org/{doi}"
    return source_id, url


def _record(
    source_id: str,
    url: str,
    title: str,
    abstract: str | None,
    doi: str | None,
    retrieved_at: datetime,
    extraction_method: str,
    bibliography: SourceBibliography | None = None,
) -> DiscoveredSource:
    source_id, url = _identity(source_id, url, doi)
    reference = SourceReference(
        source_id=source_id, url=url, title=title, bibliography=bibliography
    )
    document = None
    if abstract is not None:
        document = SourceDocument(
            **reference.model_dump(),
            text=abstract,
            content_kind="abstract",
            retrieved_at=retrieved_at,
            extraction_method=extraction_method,
        )
    return DiscoveredSource(reference, document)


def reconstruct_openalex_abstract(value: Any) -> str | None:
    """Reconstruct a contiguous validated inverted index without inventing gaps."""
    if value is None or value == {}:
        return None
    if not isinstance(value, dict) or len(value) > MAX_ABSTRACT_TOKENS:
        raise ValueError
    positions: dict[int, str] = {}
    for token, indices in value.items():
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= 500
            or any(character.isspace() or ord(character) < 32 for character in token)
            or not isinstance(indices, list)
            or not 1 <= len(indices) <= MAX_ABSTRACT_TOKENS
        ):
            raise ValueError
        for index in indices:
            if type(index) is not int or not 0 <= index < MAX_ABSTRACT_TOKENS or index in positions:
                raise ValueError
            positions[index] = token
    if not positions or set(positions) != set(range(len(positions))):
        raise ValueError
    abstract = " ".join(positions[index] for index in range(len(positions)))
    if len(abstract) > MAX_ABSTRACT_CHARACTERS:
        raise ValueError
    return abstract


def _optional_text(value: Any, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError
    if any(ord(character) < 32 and character not in "\n\t\r" for character in value):
        raise ValueError
    normalized = " ".join(value.split())
    return normalized or None


def _arxiv_alias(value: Any) -> tuple[str | None, int | None]:
    if value is None:
        return None, None
    if not isinstance(value, str):
        raise ValueError
    if "://" in value:
        alias = identifier_from_url(value)
        if alias is None or not alias.startswith("arxiv:"):
            raise ValueError
        identifier = re.sub(r"^/(?:abs|pdf|html)/", "", urlsplit(value).path).removesuffix(".pdf")
        return canonical_arxiv_id(identifier)
    return canonical_arxiv_id(value)


def _openalex_bibliography(item: dict, source_id: str, doi: str | None) -> SourceBibliography:
    authorships = item.get("authorships", [])
    if not isinstance(authorships, list):
        raise ValueError
    authors = []
    for authorship in authorships[:100]:
        if not isinstance(authorship, dict):
            raise ValueError
        author = authorship.get("author")
        if author is not None and not isinstance(author, dict):
            raise ValueError
        author = author or {}
        name = _optional_text(author.get("display_name"), 300) or _optional_text(
            authorship.get("raw_author_name"), 300
        )
        if name is None:
            raise ValueError
        authors.append(SourceAuthor(name=name, orcid=author.get("orcid")))
    identifiers = item.get("ids") or {}
    if not isinstance(identifiers, dict):
        raise ValueError
    declared_doi = _doi(identifiers.get("doi"))
    if declared_doi is not None and doi is not None and declared_doi != doi:
        raise ValueError
    openalex_id = canonical_openalex_id(source_id.removeprefix("openalex:"))
    if identifiers.get("openalex") is not None:
        if canonical_openalex_id(identifiers["openalex"]) != openalex_id:
            raise ValueError
    arxiv_id, arxiv_version = _arxiv_alias(identifiers.get("arxiv"))
    primary = item.get("primary_location") or {}
    if not isinstance(primary, dict):
        raise ValueError
    venue = primary.get("source") or {}
    if not isinstance(venue, dict):
        raise ValueError
    landing = primary.get("landing_page_url")
    if isinstance(landing, str):
        alias = identifier_from_url(landing)
        if alias is not None and alias.startswith("arxiv:"):
            base, version = _arxiv_alias(landing)
            if arxiv_id is not None and arxiv_id != base:
                raise ValueError
            arxiv_id = base
            arxiv_version = arxiv_version if arxiv_version is not None else version
    raw_date = item.get("publication_date")
    publication_date = None
    if raw_date is not None:
        if (
            not isinstance(raw_date, str)
            or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_date) is None
        ):
            raise ValueError
        publication_date = date.fromisoformat(raw_date)
    return SourceBibliography(
        authors=tuple(authors),
        authors_truncated=len(authorships) > 100,
        publication_year=item.get("publication_year"),
        publication_date=publication_date,
        venue=_optional_text(venue.get("display_name"), 500),
        doi=doi or declared_doi,
        arxiv_id=arxiv_id,
        arxiv_version=arxiv_version,
        openalex_id=openalex_id,
        license=_optional_text(primary.get("license"), 100),
    )


def _arxiv_bibliography(
    entry: ElementTree.Element, arxiv_id: str, version: int | None, doi: str | None
) -> SourceBibliography:
    entries = entry.findall(f"{ATOM}author")
    authors = []
    for author in entries[:100]:
        name = _optional_text(author.findtext(f"{ATOM}name"), 300)
        if name is None:
            raise ValueError
        authors.append(SourceAuthor(name=name, orcid=author.findtext(f"{ARXIV}orcid")))
    published = entry.findtext(f"{ATOM}published")
    publication_date = None
    if published is not None:
        stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
        publication_date = stamp.date()
    return SourceBibliography(
        authors=tuple(authors),
        authors_truncated=len(entries) > 100,
        publication_year=publication_date.year if publication_date else None,
        publication_date=publication_date,
        venue=_optional_text(entry.findtext(f"{ARXIV}journal_ref"), 500),
        doi=doi,
        arxiv_id=arxiv_id,
        arxiv_version=version,
    )


def _parser_boundary(
    payload: bytes, retrieved_at: datetime, limit: int, provider: Provider
) -> None:
    if not isinstance(payload, bytes):
        raise ProviderError(provider, "invalid_response")
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ProviderError(provider, "response_too_large")
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or not isinstance(retrieved_at, datetime)
        or retrieved_at.tzinfo is None
    ):
        raise ProviderError(provider, "invalid_response")


def _json_payload(payload: bytes):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError

    return json.loads(
        payload.decode("utf-8", errors="strict"),
        object_pairs_hook=unique_object,
        parse_constant=invalid_constant,
    )


def parse_openalex_records(
    payload: bytes, retrieved_at: datetime, limit: int = 5
) -> list[DiscoveredSource]:
    """Parse a bounded provider page without making a request or changing discovery state."""
    _parser_boundary(payload, retrieved_at, limit, "openalex")
    try:
        body = _json_payload(payload)
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ValueError
        if len(body["results"]) > limit:
            raise ValueError
        records = []
        for item in body["results"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValueError
            match = re.fullmatch(r"https://openalex\.org/(W[1-9][0-9]{0,18})", item["id"])
            if match is None:
                raise ValueError
            source_id = f"openalex:{match.group(1)}"
            doi = _doi(item.get("doi"))
            records.append(
                _record(
                    source_id,
                    item["id"],
                    _title(item.get("display_name")),
                    reconstruct_openalex_abstract(item.get("abstract_inverted_index")),
                    doi,
                    retrieved_at,
                    "openalex_abstract_inverted_index/v1",
                    _openalex_bibliography(item, source_id, doi),
                )
            )
        return records
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError, ValidationError):
        raise ProviderError("openalex", "invalid_response") from None


def parse_arxiv_records(
    payload: bytes, retrieved_at: datetime, limit: int = 5
) -> list[DiscoveredSource]:
    """Parse a bounded Atom page, retaining abstract scope and declared version metadata."""
    _parser_boundary(payload, retrieved_at, limit, "arxiv")
    try:
        xml = payload.decode("utf-8", errors="strict")
        if "\x00" in xml or "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
            raise ValueError
        feed = ElementTree.fromstring(xml)
        if feed.tag != f"{ATOM}feed":
            raise ValueError
        entries = feed.findall(f"{ATOM}entry")
        if len(entries) > limit:
            raise ValueError
        records = []
        for entry in entries:
            raw_id = entry.findtext(f"{ATOM}id", "")
            id_match = re.fullmatch(r"https?://arxiv\.org/abs/(.+)", raw_id)
            if id_match is None:
                raise ValueError
            arxiv_id, version = canonical_arxiv_id(id_match.group(1))
            title = _title(entry.findtext(f"{ATOM}title"))
            summary = entry.find(f"{ATOM}summary")
            abstract = None
            if summary is not None:
                abstract = " ".join("".join(summary.itertext()).split()) or None
                if abstract is not None and len(abstract) > MAX_ABSTRACT_CHARACTERS:
                    raise ValueError
            doi = _doi(entry.findtext(f"{ARXIV}doi"))
            records.append(
                _record(
                    f"arxiv:{arxiv_id}",
                    f"https://arxiv.org/abs/{arxiv_id}",
                    title,
                    abstract,
                    doi,
                    retrieved_at,
                    "arxiv_atom_summary/v1",
                    _arxiv_bibliography(entry, arxiv_id, version, doi),
                )
            )
        return records
    except (
        ElementTree.ParseError,
        ValueError,
        TypeError,
        RecursionError,
        UnicodeError,
        ValidationError,
    ):
        raise ProviderError("arxiv", "invalid_response") from None


class _BoundedClient:
    provider: Provider
    endpoint: str

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        provider_slot: ProviderSlot | None = None,
    ) -> None:
        self._transport = transport
        self._provider_slot = provider_slot

    async def _request(self, params: dict[str, str | int]) -> tuple[bytes, datetime]:
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                if self._provider_slot is not None:
                    await self._provider_slot(self.provider)
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=httpx.Timeout(10.0, connect=5.0),
                    follow_redirects=False,
                ) as client:
                    async with client.stream(
                        "GET",
                        self.endpoint,
                        params=params,
                        headers={
                            "User-Agent": "Polymath/0.1 (open-literature research)",
                            "Accept-Encoding": "identity",
                        },
                    ) as response:
                        if response.status_code in (401, 403):
                            raise ProviderError(self.provider, "access_denied")
                        if response.status_code == 429:
                            raise ProviderError(self.provider, "rate_limited")
                        if response.status_code in (404, 410):
                            raise ProviderError(self.provider, "source_unavailable")
                        if response.status_code != 200:
                            raise ProviderError(self.provider, "invalid_response")
                        if response.headers.get("content-encoding", "identity") != "identity":
                            raise ProviderError(self.provider, "invalid_response")
                        length = response.headers.get("content-length")
                        if length is not None:
                            try:
                                too_large = int(length) > MAX_RESPONSE_BYTES
                            except ValueError:
                                raise ProviderError(self.provider, "invalid_response") from None
                            if too_large:
                                raise ProviderError(self.provider, "response_too_large")
                        payload = bytearray()
                        async for chunk in response.aiter_bytes():
                            payload.extend(chunk)
                            if len(payload) > MAX_RESPONSE_BYTES:
                                raise ProviderError(self.provider, "response_too_large")
                        return bytes(payload), datetime.now(UTC)
        except ProviderPacingError as error:
            code = "rate_limited" if error.code == "rate_limited" else "transport_error"
            raise ProviderError(self.provider, code) from None
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderError(self.provider, "timeout") from None
        except httpx.HTTPError:
            raise ProviderError(self.provider, "transport_error") from None


class OpenAlexClient(_BoundedClient):
    provider: Provider = "openalex"
    endpoint = OPENALEX_URL

    def __init__(
        self,
        *,
        api_key: SecretStr | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        provider_slot: ProviderSlot | None = None,
    ) -> None:
        super().__init__(transport=transport, provider_slot=provider_slot)
        if api_key is not None:
            # OpenAlex's query-key route is not enabled: httpx request logs can
            # include full URLs. Verify a safe authentication route before use.
            raise ValueError("OpenAlex credential transport is not verified")

    async def search(self, query: str, *, limit: int = 5) -> list[DiscoveredSource]:
        query = validate_query(query)
        validate_result_limit(limit)
        params: dict[str, str | int] = {"search": query, "per-page": limit}
        payload, retrieved_at = await self._request(params)
        return parse_openalex_records(payload, retrieved_at, limit)


class ArxivClient(_BoundedClient):
    provider: Provider = "arxiv"
    endpoint = ARXIV_URL

    async def search(self, query: str, *, limit: int = 5) -> list[DiscoveredSource]:
        query = validate_query(query)
        validate_result_limit(limit)
        # Quote user input as a phrase; it cannot become URL parameters or API operators.
        phrase = query.replace('"', " ").replace("\\", " ")
        params: dict[str, str | int] = {
            "search_query": f'all:"{phrase}"',
            "start": 0,
            "max_results": limit,
        }
        payload, retrieved_at = await self._request(params)
        return parse_arxiv_records(payload, retrieved_at, limit)
