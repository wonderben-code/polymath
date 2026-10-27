"""Sanitized tool errors: provider bodies, URLs, and credentials never appear here."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from polymath.research.models import ProviderFailure

Provider = Literal["openalex", "arxiv"]
FailureCode = Literal[
    "access_denied",
    "rate_limited",
    "timeout",
    "transport_error",
    "invalid_response",
    "response_too_large",
    "source_unavailable",
]


class LiteratureError(Exception):
    """An operation failed; callers must not interpret this as an empty search."""

    def __init__(self, code: str, *, provider_failures: tuple[ProviderFailure, ...] = ()) -> None:
        self.code = code
        self.provider_failures = provider_failures
        super().__init__(code)


class ProviderError(LiteratureError):
    def __init__(self, provider: Provider, code: FailureCode) -> None:
        self.provider = provider
        super().__init__(code)
