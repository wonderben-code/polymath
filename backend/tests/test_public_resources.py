"""Resource boundaries use synthetic addresses and never make native HTTP calls."""

import json
from types import SimpleNamespace

import httpx
import pytest
from test_full_text import LOCATOR, html
from test_literature import atom_entry, atom_feed

from polymath.literature.clients import ArxivClient
from polymath.literature.errors import ProviderError
from polymath.literature.full_text import ArxivHtmlClient, FullTextError
from polymath.provider_pacing import ProviderPacingError
from polymath.public_research_contracts import PublicResourceLimits
from polymath.public_resource_controls import (
    PublicResourceControls,
    PublicResourcePolicy,
    derive_network_key,
)
from polymath.public_resource_middleware import PublicResourceMiddleware, _operation
from polymath.worker import ExecutionStopped


def controls():
    return PublicResourceControls(
        SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        PublicResourcePolicy(
            enabled=True, network_key=derive_network_key("s" * 40), limits=PublicResourceLimits()
        ),
    )


def test_network_identity_is_canonical_hmac_with_ipv6_prefix_and_mapped_ipv4():
    instance = controls()
    assert instance.network_bucket("192.0.2.9") == instance.network_bucket("::ffff:192.0.2.9")
    assert instance.network_bucket("2001:db8:1:2::1") == instance.network_bucket(
        "2001:db8:1:2::ffff"
    )
    assert instance.network_bucket("2001:db8:1:3::1") != instance.network_bucket("2001:db8:1:2::1")
    assert instance.network_bucket("192.0.2.8") != instance.network_bucket("192.0.2.9")
    assert instance.network_bucket(None) == instance.network_bucket("not-an-address")
    assert instance.network_bucket("192.0.2.9").isalnum()
    assert "192.0.2" not in instance.network_bucket("192.0.2.9")
    other = PublicResourceControls(
        instance.engine,
        PublicResourcePolicy(
            enabled=True, network_key=derive_network_key("t" * 40), limits=PublicResourceLimits()
        ),
    )
    assert instance.network_bucket("192.0.2.9") != other.network_bucket("192.0.2.9")
    assert "ssss" not in repr(instance.policy)


def test_policy_revalidates_forged_ceiling_instead_of_trusting_model_copy():
    with pytest.raises(ValueError):
        PublicResourcePolicy(
            enabled=True,
            network_key=b"k" * 32,
            limits=PublicResourceLimits().model_copy(update={"sse_max_seconds": 301}),
        )


@pytest.mark.parametrize(
    "path,operation",
    [
        ("/api/v1/runs", "submit"),
        ("/api/v1/runs/not-a-uuid/resume", "submit"),
        ("/api/v1/artifacts/jobs", "submit"),
        ("/api/v1/assessments/jobs", "submit"),
        ("/api/v1/knowledge/watches", "free_write"),
        ("/api/v1/knowledge/watches/example/check", "free_write"),
        ("/api/v1/knowledge/watches/example/resume", "free_write"),
        ("/api/v1/runs/example/cancel", "control"),
        ("/api/v1/knowledge/watches/example/pause", "control"),
        ("/api/v1/budget/accounts/example/disable", "control"),
    ],
)
def test_preparse_paths_separate_attempts_free_work_and_stops(path, operation):
    assert _operation("POST", path, True)[0] == operation


async def test_disabled_middleware_never_looks_up_owner_or_touches_database():
    observed = []

    async def app(scope, receive, send):
        observed.append(await receive())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    instance = PublicResourceControls(SimpleNamespace())
    middleware = PublicResourceMiddleware(app, controls=instance)
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"x" * 70_000}

    async def send(message):
        messages.append(message)

    await middleware({"type": "http", "method": "POST", "path": "/api/v1/runs"}, receive, send)
    assert len(observed[0]["body"]) == 70_000
    assert messages[0]["status"] == 200


async def test_metadata_and_html_requests_use_one_callback_per_actual_http_request():
    order = []

    async def slot(provider):
        order.append(("slot", provider))

    def transport(request):
        order.append(("http", request.url.host))
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, text=atom_feed(atom_entry()))
        return httpx.Response(
            200,
            content=html("<p>Synthetic original text.</p>"),
            headers={"Content-Type": "text/html"},
        )

    client = ArxivHtmlClient(transport=httpx.MockTransport(transport), provider_slot=slot)
    resolved = await client.resolve(LOCATOR.model_copy(update={"arxiv_version": None}))
    await client.fetch(resolved)
    assert order == [
        ("slot", "arxiv"),
        ("http", "export.arxiv.org"),
        ("slot", "arxiv"),
        ("http", "arxiv.org"),
    ]


@pytest.mark.parametrize("html_client", [False, True])
async def test_denied_pacing_never_sends_and_retains_sanitized_provider_failure(html_client):
    calls = []

    async def slot(_):
        raise ProviderPacingError("rate_limited")

    def transport(request):
        calls.append(request)
        return httpx.Response(200, content=b"must never send")

    if html_client:
        with pytest.raises(FullTextError, match="^full_text_rate_limited$"):
            await ArxivHtmlClient(
                transport=httpx.MockTransport(transport), provider_slot=slot
            ).fetch(LOCATOR)
    else:
        with pytest.raises(ProviderError, match="^rate_limited$"):
            await ArxivClient(
                transport=httpx.MockTransport(transport), provider_slot=slot
            )._request({"max_results": 1})
    assert calls == []


async def test_pacing_control_stop_is_not_converted_to_an_ordinary_provider_failure():
    from polymath.literature import OpenLiteratureTools

    async def stopped(_):
        raise ExecutionStopped("source_authority_interrupted")

    def transport(_):
        pytest.fail("No HTTP follows a control stop")

    with pytest.raises(ExecutionStopped, match="source_authority_interrupted"):
        await OpenLiteratureTools(
            transport=httpx.MockTransport(transport), provider_slot=stopped
        ).search("synthetic control stop", "discovery")


async def test_preparse_body_limit_counts_attempt_and_ignores_forwarding_headers():
    class FakeControls:
        policy = SimpleNamespace(enabled=True, limits=PublicResourceLimits(body_max_bytes=32))
        limits_resolver = None

        def __init__(self):
            self.peers, self.operations = [], []

        def identify(self, peer, cookie):
            self.peers.append(peer)
            return SimpleNamespace(owner_hash=None, network_bucket="n" * 64)

        def consume(self, operation, identity):
            self.operations.append(operation)

    seen = []

    async def app(scope, receive, send):
        seen.append(True)

    gate = FakeControls()
    transport = httpx.ASGITransport(
        app=PublicResourceMiddleware(app, controls=gate), client=("192.0.2.8", 1234)
    )

    async def chunks():
        yield b"private-synthetic-input-" * 2
        yield b"never-echo"

    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.post(
            "/api/v1/runs",
            content=chunks(),
            headers={
                "X-Forwarded-For": "203.0.113.9",
                "Forwarded": "for=203.0.113.9",
                "Content-Length": "1",
            },
        )
    assert response.status_code == 413
    assert response.json() == {"detail": "public_request_body_too_large"}
    assert gate.peers == ["192.0.2.8"] and gate.operations == ["submit"] and seen == []
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "private" not in json.dumps(response.json())
