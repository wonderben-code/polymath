"""One bounded, read-only literature page from a caller-owned frozen window.

OpenAlex publication dates have day precision; arXiv submittedDate filters have
minute precision. Both filters include their boundaries. The watch repository
must preserve a window while paging and deduplicate overlapping later windows.
Metadata-only records remain explicit discoveries, never invented source text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from xml.etree import ElementTree

import httpx
from pydantic import SecretStr

from polymath.literature.clients import (
    ATOM,
    ArxivClient,
    DiscoveredSource,
    OpenAlexClient,
    parse_arxiv_records,
    parse_openalex_records,
    validate_query,
    validate_result_limit,
)
from polymath.literature.errors import Provider, ProviderError
from polymath.research.models import SourceDocument, SourceReference, _validate_public_text

MAX_CURSOR_CHARACTERS = 2000
MAX_ARXIV_OFFSET = 100_000
OPEN_SEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
OPAQUE_CURSOR = re.compile(r"[A-Za-z0-9_+/=\-]{1,2000}")


def _window(since: datetime, until: datetime) -> tuple[datetime, datetime]:
    if any(
        not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
        for value in (since, until)
    ):
        raise ValueError("invalid_feed_window")
    start, end = since.astimezone(UTC), until.astimezone(UTC)
    if start > end or end - start > timedelta(days=31):
        raise ValueError("invalid_feed_window")
    return start, end


def _cursor(provider: Provider, cursor: str | int | None, *, initial: bool = True):
    if provider == "openalex":
        if initial and cursor in (None, "*"):
            return "*"
        if not isinstance(cursor, str) or OPAQUE_CURSOR.fullmatch(cursor) is None:
            raise ValueError("invalid_feed_cursor")
        return cursor
    if provider == "arxiv":
        if initial and cursor is None:
            return 0
        if type(cursor) is not int or not 0 <= cursor <= MAX_ARXIV_OFFSET:
            raise ValueError("invalid_feed_cursor")
        return cursor
    raise ValueError("invalid_feed_provider")


@dataclass(frozen=True)
class FeedPage:
    provider: Provider
    records: tuple[DiscoveredSource, ...]
    next_cursor: str | int | None
    exhausted: bool
    since: datetime
    until: datetime

    def __post_init__(self) -> None:
        _window(self.since, self.until)
        if self.provider not in ("openalex", "arxiv"):
            raise ValueError("invalid_feed_provider")
        if not isinstance(self.records, tuple) or len(self.records) > 5:
            raise ValueError("invalid_feed_records")
        if type(self.exhausted) is not bool:
            raise ValueError("invalid_feed_page")
        if self.exhausted:
            if self.next_cursor is not None:
                raise ValueError("invalid_feed_cursor")
        else:
            _cursor(self.provider, self.next_cursor, initial=False)
        for record in self.records:
            if not isinstance(record, DiscoveredSource) or not isinstance(
                record.reference, SourceReference
            ):
                raise ValueError("invalid_feed_records")
            if record.document is not None and not isinstance(record.document, SourceDocument):
                raise ValueError("invalid_feed_records")
            _validate_public_text(record.reference.model_dump(mode="json"))
            if record.document is not None:
                _validate_public_text(record.document.model_dump(mode="json"))
            if record.document is not None and any(
                getattr(record.reference, field) != getattr(record.document, field)
                for field in type(record.reference).model_fields
            ):
                raise ValueError("feed_source_identity_mismatch")

    @property
    def sources(self) -> tuple[DiscoveredSource, ...]:
        return self.records

    @property
    def cursor(self) -> str | int | None:
        return self.next_cursor

    @property
    def metadata_only(self) -> int:
        return sum(record.document is None for record in self.records)

    @property
    def unreadable_source_ids(self) -> tuple[str, ...]:
        return tuple(
            record.reference.source_id for record in self.records if record.document is None
        )

    @property
    def window_precision(self) -> Literal["day", "minute"]:
        return "day" if self.provider == "openalex" else "minute"


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _invalid_constant(_value: str):
    raise ValueError


def _json(payload: bytes) -> dict:
    body = json.loads(
        payload.decode("utf-8", errors="strict"),
        object_pairs_hook=_unique_object,
        parse_constant=_invalid_constant,
    )
    if not isinstance(body, dict):
        raise ValueError
    return body


def _xml(payload: bytes):
    text = payload.decode("utf-8", errors="strict")
    if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError
    root = ElementTree.fromstring(text)
    if root.tag != f"{ATOM}feed":
        raise ValueError
    return root


def _xml_integer(root, field: str) -> int | None:
    entries = root.findall(f"{OPEN_SEARCH}{field}")
    if not entries:
        return None
    if len(entries) != 1:
        raise ValueError
    value = entries[0].text or ""
    if re.fullmatch(r"[0-9]{1,10}", value) is None:
        raise ValueError
    return int(value)


def _minute(value: datetime) -> str:
    return f"{value.year:04d}{value.month:02d}{value.day:02d}{value.hour:02d}{value.minute:02d}"


class OpenLiteratureFeed:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        provider_slot=None,
        openalex_api_key: SecretStr | None = None,
    ):
        # These existing clients enforce fixed HTTPS endpoints, wall-clock and
        # byte caps, no redirects and sanitized failures. An optional OpenAlex
        # key is supplied explicitly by the server; clients never look one up.
        self._openalex = OpenAlexClient(
            api_key=openalex_api_key, transport=transport, provider_slot=provider_slot
        )
        self._arxiv = ArxivClient(transport=transport, provider_slot=provider_slot)

    async def fetch_page(
        self,
        provider: Provider,
        query: str,
        *,
        since: datetime,
        until: datetime,
        cursor: str | int | None = None,
        limit: int = 5,
    ) -> FeedPage:
        if provider not in ("openalex", "arxiv"):
            raise ValueError("invalid_feed_provider")
        query = validate_query(query)
        validate_result_limit(limit)
        start, end = _window(since, until)
        current = _cursor(provider, cursor)
        if provider == "openalex":
            payload, retrieved_at = await self._openalex._request(
                {
                    "search": query,
                    "filter": (
                        f"from_publication_date:{start.date().isoformat()},"
                        f"to_publication_date:{end.date().isoformat()}"
                    ),
                    "sort": "publication_date:asc",
                    "per-page": limit,
                    "cursor": current,
                }
            )
            try:
                body = _json(payload)
                metadata = body.get("meta")
                if not isinstance(metadata, dict) or "next_cursor" not in metadata:
                    raise ValueError
                next_cursor = metadata["next_cursor"]
                records = tuple(parse_openalex_records(payload, retrieved_at, limit=limit))
                if next_cursor is not None:
                    _cursor(provider, next_cursor, initial=False)
                    if next_cursor == current or not records:
                        raise ValueError
                return FeedPage(provider, records, next_cursor, next_cursor is None, since, until)
            except (ValueError, TypeError, KeyError, RecursionError):
                raise ProviderError(provider, "invalid_response") from None
        phrase = query.replace('"', " ").replace("\\", " ")
        payload, retrieved_at = await self._arxiv._request(
            {
                "search_query": (
                    f'all:"{phrase}" AND submittedDate:[{_minute(start)} TO {_minute(end)}]'
                ),
                "sortBy": "submittedDate",
                "sortOrder": "ascending",
                "start": current,
                "max_results": limit,
            }
        )
        try:
            root = _xml(payload)
            total = _xml_integer(root, "totalResults")
            offset = _xml_integer(root, "startIndex")
            per_page = _xml_integer(root, "itemsPerPage")
            if offset is not None and offset != current:
                raise ValueError
            if per_page is not None and per_page > limit:
                raise ValueError
            records = tuple(parse_arxiv_records(payload, retrieved_at, limit=limit))
            following = current + len(records)
            if total is not None:
                if records and total < following:
                    raise ValueError
                exhausted = following >= total
            else:
                exhausted = len(records) < limit
            if not exhausted and not records:
                raise ValueError
            next_cursor = None if exhausted else _cursor(provider, following, initial=False)
            return FeedPage(provider, records, next_cursor, exhausted, since, until)
        except (ElementTree.ParseError, ValueError, TypeError, RecursionError):
            raise ProviderError(provider, "invalid_response") from None

    async def fetch(self, provider: Provider, query: str, **arguments) -> FeedPage:
        return await self.fetch_page(provider, query, **arguments)


LiteratureFeed = OpenLiteratureFeed
