"""Explicitly gated Meta transport. Live protocol compatibility remains unverified."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import SecretStr

META_COMPLETIONS_URL = "https://api.meta.ai/v1/chat/completions"


class ModelError(Exception):
    """A sanitized error code; never retains provider bodies or credentials."""


@dataclass(frozen=True)
class ModelReply:
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ResearchModel(Protocol):
    async def complete(
        self, messages: list[dict[str, str]], *, max_output_tokens: int
    ) -> ModelReply: ...


class MetaModel:
    """No model default, endpoint override, automatic retries, or redirects.

    This implements the repository's proposed chat-completion wire format. Tests
    exercise a mock transport; they do not establish actual Meta API support.
    Explicit live_enabled opt-in is required after pricing/account limits are
    verified. The public application must not expose this before durable budget
    reservation and job controls are implemented.
    """

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        live_enabled: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
        request_timeout_seconds: float = 45.0,
    ) -> None:
        if not model.strip() or len(model) > 200:
            raise ValueError("An explicit verified Meta model ID is required")
        if not api_key.get_secret_value().strip():
            raise ValueError("Meta authentication is not configured")
        if (
            type(request_timeout_seconds) not in (int, float)
            or not math.isfinite(request_timeout_seconds)
            or not 0.001 <= request_timeout_seconds <= 60
        ):
            raise ValueError("request_timeout_seconds must be between 0.001 and 60")
        self._request_timeout_seconds = request_timeout_seconds
        self._api_key = api_key
        self._model = model
        self._live_enabled = live_enabled
        self._transport = transport

    async def complete(
        self, messages: list[dict[str, str]], *, max_output_tokens: int
    ) -> ModelReply:
        if not self._live_enabled:
            raise ModelError("live_model_disabled")
        if type(max_output_tokens) is not int or not 16 <= max_output_tokens <= 4096:
            raise ValueError("max_output_tokens must be between 16 and 4096")
        if sum(len(message["content"]) for message in messages) > 90_000:
            raise ModelError("model_context_limit")
        try:
            async with asyncio.timeout(self._request_timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=httpx.Timeout(30.0, connect=10.0),
                    follow_redirects=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        META_COMPLETIONS_URL,
                        headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
                        json={
                            "model": self._model,
                            "messages": messages,
                            "max_tokens": max_output_tokens,
                            "stream": False,
                        },
                    ) as response:
                        if response.status_code in (401, 403):
                            raise ModelError("model_access_denied")
                        if response.status_code == 429:
                            raise ModelError("model_rate_limited")
                        if response.status_code != 200:
                            raise ModelError("model_request_failed")
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 128_000:
                                raise ModelError("model_response_too_large")
        except (TimeoutError, httpx.TimeoutException):
            raise ModelError("model_timeout_outcome_unknown") from None
        except httpx.HTTPError:
            raise ModelError("model_transport_error") from None
        try:
            body = json.loads(data)
            choices = body["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            choice = choices[0]
            if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
                raise ValueError
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError
            content = message["content"]
            if not isinstance(content, str) or not 1 <= len(content) <= 16_000:
                raise ValueError
            usage = body.get("usage")
            input_tokens = output_tokens = None
            if isinstance(usage, dict):
                prompt = usage.get("prompt_tokens")
                completion = usage.get("completion_tokens")
                if (
                    type(prompt) is int
                    and type(completion) is int
                    and prompt >= 0
                    and completion >= 0
                ):
                    input_tokens, output_tokens = prompt, completion
            if output_tokens is not None and output_tokens > max_output_tokens:
                raise ModelError("model_output_limit_exceeded")
            return ModelReply(content, input_tokens, output_tokens)
        except (ValueError, TypeError, KeyError, IndexError):
            raise ModelError("model_invalid_response") from None
