"""Per-investigation discovery boundary and explicit partial provider failures."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

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
from polymath.provider_pacing import ProviderSlot
from polymath.research.models import (
    ArxivFullTextLocator,
    FullTextAvailability,
    ProviderFailure,
    SearchResults,
    SourceDocument,
    SourceReference,
)

if TYPE_CHECKING:
    from polymath.full_text_contracts import ExtractedFullText


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
        provider_slot: ProviderSlot | None = None,
    ) -> None:
        from polymath.literature.full_text import ArxivHtmlClient

        validate_result_limit(results_per_provider)
        self._openalex = OpenAlexClient(
            api_key=openalex_api_key, transport=transport, provider_slot=provider_slot
        )
        self._arxiv = ArxivClient(transport=transport, provider_slot=provider_slot)
        self._html = ArxivHtmlClient(transport=transport, provider_slot=provider_slot)
        self._limit = results_per_provider
        self._discovered: dict[str, DiscoveredSource] = {}
        self._full_text: dict[str, FullTextAvailability] = {}
        self._restored: set[str] = set()

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
        locators: dict[str, set[tuple[str, int | None]]] = {}
        for provider, response in zip(providers, responses, strict=True):
            if isinstance(response, BaseException):
                if not isinstance(response, Exception):
                    raise response
                code = response.code if isinstance(response, ProviderError) else "transport_error"
                failures.append(ProviderFailure(provider=provider.provider, code=code))
                continue
            for record in response:
                bibliography = record.reference.bibliography
                if bibliography is not None and bibliography.arxiv_id is not None:
                    locators.setdefault(record.reference.source_id, set()).add(
                        (bibliography.arxiv_id, bibliography.arxiv_version)
                    )
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
                if source_id in self._restored and prior is not None:
                    if prior.reference == record.reference:
                        self._discovered[source_id] = record
                else:
                    self._discovered[source_id] = record
        for source_id, values in locators.items():
            bases = {base for base, _ in values}
            versions = {version for _, version in values if version is not None}
            locator = (
                ArxivFullTextLocator(
                    arxiv_id=next(iter(bases)), arxiv_version=next(iter(versions), None)
                )
                if len(bases) == 1 and len(versions) <= 1
                else None
            )
            self._retain_availability(
                FullTextAvailability(
                    source_id=source_id,
                    status="candidate" if locator else "ambiguous",
                    locator=locator,
                )
            )
        return SearchResults(
            sources=tuple(self._discovered[source_id].reference for source_id in found),
            provider_failures=tuple(failures),
            full_text=tuple(
                self._full_text[source_id] for source_id in found if source_id in self._full_text
            ),
        )

    def _retain_availability(self, candidate: FullTextAvailability) -> None:
        prior = self._full_text.get(candidate.source_id)
        if prior is None or prior == candidate:
            self._full_text[candidate.source_id] = candidate
            return
        if prior.status == "candidate" and candidate.status == "candidate":
            left, right = prior.locator, candidate.locator
            if left.arxiv_id == right.arxiv_id and (
                left.arxiv_version is None or right.arxiv_version is None
            ):
                # Retain the exact first locator; resolving it is a separate,
                # journalled tool operation rather than a search-side refresh.
                return
        self._full_text[candidate.source_id] = FullTextAvailability(
            source_id=candidate.source_id, status="ambiguous"
        )

    def restore_discovery(self, result: SearchResults) -> None:
        """Restore an authoritative saved discovery/locator without any HTTP refresh."""
        result = SearchResults.model_validate_json(result.model_dump_json())
        if len(set(self._discovered) | {source.source_id for source in result.sources}) > 200:
            raise LiteratureError("discovery_limit")
        for source in result.sources:
            prior = self._discovered.get(source.source_id)
            # A free provider refresh may warm a not-yet-recorded abstract read.
            # Its body is usable only when EVERY reference field matches the
            # saved discovery. The saved locator remains authoritative even if
            # the refreshed provider has advanced to another arXiv version.
            if prior is None or prior.reference != source:
                self._discovered[source.source_id] = DiscoveredSource(source, None)
            self._restored.add(source.source_id)
            self._full_text.pop(source.source_id, None)
        for availability in result.full_text:
            self._full_text[availability.source_id] = availability

    def get_discovered_reference(self, source_id: str) -> SourceReference:
        if not isinstance(source_id, str) or source_id not in self._discovered:
            raise LiteratureError("source_not_discovered")
        return self._discovered[source_id].reference

    def get_full_text_availability(self, source_id: str) -> FullTextAvailability | None:
        self.get_discovered_reference(source_id)
        return self._full_text.get(source_id)

    def _locator(self, source_id: str) -> ArxivFullTextLocator:
        availability = self.get_full_text_availability(source_id)
        if availability is None:
            raise LiteratureError("full_text_locator_unavailable")
        if availability.status != "candidate":
            raise LiteratureError("full_text_locator_ambiguous")
        return availability.locator

    async def resolve_full_text_locator(self, source_id: str) -> ArxivFullTextLocator:
        return await self._html.resolve(self._locator(source_id))

    async def fetch_full_text(
        self, source_id: str, *, locator: ArxivFullTextLocator
    ) -> ExtractedFullText:
        """Fetch only an explicit locator already resolved/journalled by the caller."""
        allowed = self._locator(source_id)
        locator = ArxivFullTextLocator.model_validate_json(locator.model_dump_json())
        if (
            locator.arxiv_version is None
            or locator.arxiv_id != allowed.arxiv_id
            or (
                allowed.arxiv_version is not None and locator.arxiv_version != allowed.arxiv_version
            )
        ):
            raise LiteratureError("full_text_locator_mismatch")
        return await self._html.fetch(locator)

    async def read(self, source_id: str) -> SourceDocument:
        if not isinstance(source_id, str) or source_id not in self._discovered:
            raise LiteratureError("source_not_discovered")
        document = self._discovered[source_id].document
        if document is None:
            raise LiteratureError("source_text_unavailable")
        return document
