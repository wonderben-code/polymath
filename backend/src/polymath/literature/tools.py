"""Per-investigation discovery boundary and explicit partial provider failures."""

from __future__ import annotations

import asyncio

import httpx
from pydantic import SecretStr

from polymath.literature.clients import (
    ArxivClient,
    DiscoveredSource,
    OpenAlexClient,
    validate_query,
    validate_result_limit,
)
from polymath.literature.errors import LiteratureError, ProviderError
from polymath.research.models import ProviderFailure, SearchResults, SourceDocument


class OpenLiteratureTools:
    """Search both providers; read only abstract snapshots discovered by this instance.

    Instantiate for each research run. Reading never dereferences a paper URL.
    Missing text is an explicit error, not fabricated text or a full-text claim.
    """

    def __init__(
        self,
        *,
        openalex_api_key: SecretStr | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        results_per_provider: int = 5,
    ) -> None:
        validate_result_limit(results_per_provider)
        self._openalex = OpenAlexClient(api_key=openalex_api_key, transport=transport)
        self._arxiv = ArxivClient(transport=transport)
        self._limit = results_per_provider
        self._discovered: dict[str, DiscoveredSource] = {}

    async def search(self, query: str, purpose: str) -> SearchResults:
        try:
            query = validate_query(query)
        except ValueError:
            raise LiteratureError("invalid_query") from None
        if purpose not in ("discovery", "counterevidence", "prior_work"):
            raise LiteratureError("invalid_purpose")
        providers = (self._openalex, self._arxiv)
        responses = await asyncio.gather(
            *(provider.search(query, limit=self._limit) for provider in providers),
            return_exceptions=True,
        )
        failures = []
        found: dict[str, DiscoveredSource] = {}
        for provider, response in zip(providers, responses, strict=True):
            if isinstance(response, BaseException):
                if not isinstance(response, Exception):
                    raise response
                code = response.code if isinstance(response, ProviderError) else "transport_error"
                failures.append(ProviderFailure(provider=provider.provider, code=code))
                continue
            for record in response:
                prior = found.get(record.reference.source_id)
                if prior is None or (prior.document is None and record.document is not None):
                    found[record.reference.source_id] = record
        if len(failures) == len(providers):
            raise LiteratureError("all_providers_unavailable", provider_failures=tuple(failures))
        if len(set(self._discovered) | set(found)) > 200:
            raise LiteratureError("discovery_limit")
        # Freeze the first readable snapshot in a run. A repeated search must not
        # silently swap the source text already supplied to the research model.
        for source_id, record in found.items():
            prior = self._discovered.get(source_id)
            if prior is None or (prior.document is None and record.document is not None):
                self._discovered[source_id] = record
        return SearchResults(
            sources=tuple(self._discovered[source_id].reference for source_id in found),
            provider_failures=tuple(failures),
        )

    async def read(self, source_id: str) -> SourceDocument:
        if not isinstance(source_id, str) or source_id not in self._discovered:
            raise LiteratureError("source_not_discovered")
        document = self._discovered[source_id].document
        if document is None:
            raise LiteratureError("source_text_unavailable")
        return document
