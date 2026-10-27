"""Fixed-host metadata clients with explicit limits and no document URL fetching."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree

import httpx
from pydantic import SecretStr, ValidationError

from polymath.literature.errors import Provider, ProviderError
from polymath.research.models import SourceDocument, SourceReference

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
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
    ):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    if not DOI_PATTERN.fullmatch(value):
        raise ValueError
    return value.lower()


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
) -> DiscoveredSource:
    source_id, url = _identity(source_id, url, doi)
    reference = SourceReference(source_id=source_id, url=url, title=title)
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


class _BoundedClient:
    provider: Provider
    endpoint: str

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def _request(self, params: dict[str, str | int]) -> tuple[bytes, datetime]:
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
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
    ) -> None:
        super().__init__(transport=transport)
        if api_key is not None:
            # OpenAlex's query-key route is not enabled: httpx request logs can
            # include full URLs. Verify a safe authentication route before use.
            raise ValueError("OpenAlex credential transport is not verified")

    async def search(self, query: str, *, limit: int = 5) -> list[DiscoveredSource]:
        query = validate_query(query)
        validate_result_limit(limit)
        params: dict[str, str | int] = {"search": query, "per-page": limit}
        payload, retrieved_at = await self._request(params)
        try:
            body = json.loads(payload)
            if not isinstance(body, dict) or not isinstance(body.get("results"), list):
                raise ValueError
            if len(body["results"]) > limit:
                raise ValueError
            records = []
            for item in body["results"]:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise ValueError
                match = re.fullmatch(r"https://openalex\.org/(W[1-9]\d{0,18})", item["id"])
                if match is None:
                    raise ValueError
                source_id = f"openalex:{match.group(1)}"
                records.append(
                    _record(
                        source_id,
                        item["id"],
                        _title(item.get("display_name")),
                        reconstruct_openalex_abstract(item.get("abstract_inverted_index")),
                        _doi(item.get("doi")),
                        retrieved_at,
                        "openalex_abstract_inverted_index/v1",
                    )
                )
            return records
        except (ValueError, TypeError, KeyError, RecursionError, ValidationError):
            raise ProviderError(self.provider, "invalid_response") from None


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
                match = ARXIV_ID.fullmatch(id_match.group(1)) if id_match else None
                if match is None:
                    raise ValueError
                arxiv_id = match.group("base")
                serial = arxiv_id.rsplit("/", 1)[-1].split(".", 1)[0]
                if not 1 <= int(serial[2:4]) <= 12:
                    raise ValueError
                title = _title(entry.findtext(f"{ATOM}title"))
                summary = entry.find(f"{ATOM}summary")
                abstract = None
                if summary is not None:
                    abstract = " ".join("".join(summary.itertext()).split())
                    if not abstract:
                        abstract = None
                    elif len(abstract) > MAX_ABSTRACT_CHARACTERS:
                        raise ValueError
                records.append(
                    _record(
                        f"arxiv:{arxiv_id}",
                        f"https://arxiv.org/abs/{arxiv_id}",
                        title,
                        abstract,
                        _doi(entry.findtext(f"{ARXIV}doi")),
                        retrieved_at,
                        "arxiv_atom_summary/v1",
                    )
                )
            return records
        except (ElementTree.ParseError, ValueError, TypeError, ValidationError):
            raise ProviderError(self.provider, "invalid_response") from None
