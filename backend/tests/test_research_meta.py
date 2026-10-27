"""Mock HTTP contracts only. No live API/model capabilities are asserted."""

import json

import httpx
import pytest
from pydantic import SecretStr

from polymath.research import MetaModel, ModelError

SECRET = "fixture-key-not-a-real-credential"
MESSAGES = [{"role": "user", "content": "Synthetic fixture request"}]


def model(handler, *, enabled=True):
    return MetaModel(
        api_key=SecretStr(SECRET),
        model="fixture-meta-model",
        live_enabled=enabled,
        transport=httpx.MockTransport(handler),
    )


def response(content="{}", *, usage=True):
    result = {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
    if usage:
        result["usage"] = {"prompt_tokens": 12, "completion_tokens": 5}
    return result


@pytest.mark.asyncio
async def test_transport_is_fixed_https_bounded_and_usage_is_reported():
    def handler(request):
        assert str(request.url) == "https://api.meta.ai/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        payload = json.loads(request.content)
        assert payload == {
            "model": "fixture-meta-model",
            "messages": MESSAGES,
            "max_tokens": 100,
            "stream": False,
        }
        return httpx.Response(200, json=response('{"kind":"conclude"}'))

    reply = await model(handler).complete(MESSAGES, max_output_tokens=100)
    assert reply.input_tokens == 12
    assert reply.output_tokens == 5


@pytest.mark.asyncio
async def test_live_calls_are_disabled_by_default_and_no_request_is_sent():
    requests = []
    adapter = MetaModel(
        api_key=SecretStr(SECRET),
        model="fixture-meta-model",
        transport=httpx.MockTransport(lambda request: requests.append(request)),
    )
    with pytest.raises(ModelError, match="live_model_disabled"):
        await adapter.complete(MESSAGES, max_output_tokens=100)
    assert not requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [
        (401, "model_access_denied"),
        (403, "model_access_denied"),
        (429, "model_rate_limited"),
        (500, "model_request_failed"),
        (302, "model_request_failed"),
    ],
)
async def test_http_errors_do_not_leak_bodies_or_follow_redirects(status, expected):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status, text=SECRET, headers={"location": "https://example.org/steal"}
        )

    with pytest.raises(ModelError) as error:
        await model(handler).complete(MESSAGES, max_output_tokens=100)
    assert str(error.value) == expected
    assert SECRET not in repr(error.value)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_transport_timeout_has_unknown_outcome_and_no_retry():
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout(SECRET, request=request)

    with pytest.raises(ModelError, match="model_timeout_outcome_unknown") as error:
        await model(handler).complete(MESSAGES, max_output_tokens=100)
    assert SECRET not in repr(error.value)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_missing_usage_is_explicit_not_zero_cost():
    reply = await model(lambda request: httpx.Response(200, json=response(usage=False))).complete(
        MESSAGES, max_output_tokens=100
    )
    assert reply.input_tokens is None
    assert reply.output_tokens is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"error": SECRET},
        {"choices": [1]},
        {"choices": [{"finish_reason": "stop", "message": "invalid"}]},
        {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": []}}]},
    ],
)
async def test_invalid_or_truncated_provider_responses_are_rejected(payload):
    with pytest.raises(ModelError, match="model_invalid_response") as error:
        await model(lambda request: httpx.Response(200, json=payload)).complete(
            MESSAGES, max_output_tokens=100
        )
    assert SECRET not in repr(error.value)


@pytest.mark.asyncio
async def test_response_size_and_output_token_limits_are_enforced():
    with pytest.raises(ModelError, match="model_response_too_large"):
        await model(lambda request: httpx.Response(200, text="x" * 128_001)).complete(
            MESSAGES, max_output_tokens=100
        )
    payload = response()
    payload["usage"]["completion_tokens"] = 101
    with pytest.raises(ModelError, match="model_output_limit_exceeded"):
        await model(lambda request: httpx.Response(200, json=payload)).complete(
            MESSAGES, max_output_tokens=100
        )


def test_no_model_id_or_authentication_is_invented():
    with pytest.raises(ValueError):
        MetaModel(api_key=SecretStr(SECRET), model="")
    with pytest.raises(ValueError):
        MetaModel(api_key=SecretStr(""), model="fixture-meta-model")


@pytest.mark.asyncio
async def test_transport_has_total_wall_clock_deadline_in_addition_to_read_timeout():
    import asyncio

    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=response())

    adapter = MetaModel(
        api_key=SecretStr(SECRET),
        model="fixture-meta-model",
        live_enabled=True,
        transport=httpx.MockTransport(handler),
        request_timeout_seconds=0.01,
    )
    with pytest.raises(ModelError, match="model_timeout_outcome_unknown"):
        await adapter.complete(MESSAGES, max_output_tokens=100)
