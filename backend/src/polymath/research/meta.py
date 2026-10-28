"""Explicitly gated Meta transport. Live protocol compatibility remains unverified."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import httpx
from pydantic import SecretStr

if TYPE_CHECKING:
    from polymath.meta_probe_contracts import MetaObservation, MetaProbeRequest
    from polymath.meta_research_contracts import MetaResearchRequest

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

    async def observe(self, request: MetaProbeRequest) -> MetaObservation:
        """One bound probe observation before action/finish semantics.

        Complete response usage is retained even when its action is invalid.
        Raw response/error bodies never enter the observation. Cancellation is
        allowed to propagate so the committed grant remains an unknown hold.
        The historical complete() path below is intentionally unchanged.
        """
        from polymath.meta_probe_contracts import (
            MetaProbeRequest,
            probe_request_sha256,
        )

        if not self._live_enabled:
            raise ModelError("live_model_disabled")
        if not isinstance(request, MetaProbeRequest):
            raise ModelError("model_probe_request_invalid")
        try:
            request = MetaProbeRequest.model_validate_json(request.model_dump_json())
        except ValueError:
            raise ModelError("model_probe_request_invalid") from None
        if request.payload.model != self._model:
            raise ModelError("model_probe_request_binding_mismatch")
        return await self._observe_request(request, request_sha256=probe_request_sha256(request))

    async def observe_research(self, request: MetaResearchRequest) -> MetaObservation:
        """Observe one immutable scientific-controller request using the same wire boundary."""
        from polymath.meta_research_contracts import (
            MetaResearchRequest,
            meta_research_request_sha256,
        )

        if not self._live_enabled:
            raise ModelError("live_model_disabled")
        if not isinstance(request, MetaResearchRequest):
            raise ModelError("model_research_request_invalid")
        try:
            request = MetaResearchRequest.model_validate_json(request.model_dump_json())
        except ValueError:
            raise ModelError("model_research_request_invalid") from None
        if request.payload.model != self._model:
            raise ModelError("model_research_request_binding_mismatch")
        return await self._observe_request(
            request, request_sha256=meta_research_request_sha256(request)
        )

    async def _observe_request(self, request, *, request_sha256: str) -> MetaObservation:
        """Shared bounded native HTTP observation; callers first validate their exact DTO."""
        from polymath.meta_probe_contracts import MetaObservation

        started = datetime.now(UTC)
        status = None
        data = bytearray()
        complete = False
        code = None
        try:
            async with asyncio.timeout(self._request_timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=httpx.Timeout(30.0, connect=10.0),
                    follow_redirects=False,
                ) as client:
                    async with client.stream(
                        request.method,
                        request.endpoint,
                        headers={
                            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                            "Accept": "application/json",
                            "Accept-Encoding": "identity",
                        },
                        json=request.payload.model_dump(mode="json"),
                    ) as response:
                        status = response.status_code
                        if (
                            response.headers.get("content-encoding", "identity").lower()
                            != "identity"
                        ):
                            raise ModelError("model_encoded_response")
                        length = response.headers.get("content-length")
                        if length is not None and (
                            len(length) > 10 or not length.isascii() or not length.isdigit()
                        ):
                            raise ModelError("model_invalid_response_length")
                        if length is not None and int(length) > 128000:
                            raise ModelError("model_response_too_large")
                        async for chunk in response.aiter_bytes(chunk_size=16384):
                            if len(data) + len(chunk) > 128000:
                                data.extend(chunk[: 128000 - len(data)])
                                raise ModelError("model_response_too_large")
                            data.extend(chunk)
                        complete = True
                        if status != 200:
                            code = {
                                401: "model_access_denied",
                                403: "model_access_denied",
                                429: "model_rate_limited",
                            }.get(status, "model_request_failed")
        except (TimeoutError, httpx.TimeoutException):
            code = "model_timeout_outcome_unknown"
        except ModelError as error:
            code = (
                str(error)
                if str(error)
                in {
                    "model_encoded_response",
                    "model_invalid_response_length",
                    "model_response_too_large",
                }
                else "model_transport_error"
            )
        except Exception:
            code = "model_transport_error"

        observed = finish = content = content_hash = None
        input_tokens = output_tokens = None
        if complete:
            try:

                def unique(pairs):
                    values = {}
                    for key, value in pairs:
                        if key in values:
                            raise ValueError
                        values[key] = value
                    return values

                def invalid_constant(_value):
                    raise ValueError

                text = bytes(data).decode("utf-8", errors="strict")
                depth = 0
                quoted = escaped = False
                for character in text:
                    if quoted:
                        if escaped:
                            escaped = False
                        elif character == "\\":
                            escaped = True
                        elif character == '"':
                            quoted = False
                    elif character == '"':
                        quoted = True
                    elif character in "[{":
                        depth += 1
                        if depth > 128:
                            raise ValueError
                    elif character in "]}":
                        depth -= 1
                body = json.loads(
                    text,
                    object_pairs_hook=unique,
                    parse_constant=invalid_constant,
                )
                if not isinstance(body, dict):
                    raise ValueError
                value = body.get("model")
                if (
                    isinstance(value, str)
                    and 1 <= len(value) <= 200
                    and all(
                        character.isascii() and (character.isalnum() or character in "_.:/-")
                        for character in value
                    )
                    and self._api_key.get_secret_value() not in value
                ):
                    observed = value
                usage = body.get("usage")
                if isinstance(usage, dict):
                    first, second = usage.get("prompt_tokens"), usage.get("completion_tokens")
                    if (
                        type(first) is int
                        and type(second) is int
                        and 0 <= first <= 2**63 - 1
                        and 0 <= second <= 2**63 - 1
                    ):
                        input_tokens, output_tokens = first, second
                if input_tokens is None:
                    code = code or "model_usage_missing_or_invalid"
                choices = body.get("choices")
                if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
                    choice = choices[0]
                    value = choice.get("finish_reason")
                    if (
                        isinstance(value, str)
                        and 1 <= len(value) <= 80
                        and self._api_key.get_secret_value() not in value
                        and all(
                            character.isascii() and (character.isalnum() or character in "_.:-")
                            for character in value
                        )
                    ):
                        finish = value
                    message = choice.get("message")
                    value = message.get("content") if isinstance(message, dict) else None
                    if isinstance(value, str) and status == 200:
                        try:
                            content_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
                            # Never journal an echoed credential as generated text.
                            if (
                                len(value) <= 16000
                                and self._api_key.get_secret_value() not in value
                            ):
                                content = value
                            else:
                                code = code or "model_content_unavailable"
                        except UnicodeError:
                            code = code or "model_content_invalid"
                else:
                    code = code or "model_choices_invalid"
            except (ValueError, TypeError, UnicodeError, RecursionError):
                code = code or "model_response_invalid_json"
        return MetaObservation(
            request_sha256=request_sha256,
            requested_model_id=self._model,
            observed_model_id=observed,
            http_status=status,
            finish_reason=finish,
            content=content,
            content_sha256=content_hash,
            body_sha256=hashlib.sha256(data).hexdigest() if data or complete else None,
            body_complete=complete,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error_code=code,
            started_at=started,
            finished_at=datetime.now(UTC),
        )

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
