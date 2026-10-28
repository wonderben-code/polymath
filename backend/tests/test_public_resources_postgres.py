"""Real multi-process-style resource state, without model/provider connections."""

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_memory import owner
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.ingestion_models import IngestionProviderGate
from polymath.literature.clients import ArxivClient
from polymath.literature.errors import ProviderError
from polymath.provider_pacing import DatabaseProviderPacer
from polymath.public_research_contracts import PublicResourceLimits
from polymath.public_resource_controls import (
    PublicRequestIdentity,
    PublicResourceControls,
    PublicResourceError,
    PublicResourcePolicy,
    derive_network_key,
)
from polymath.public_resource_middleware import PublicResourceMiddleware
from polymath.public_resource_models import PublicRateBucket, PublicStreamLease
from polymath.run_api import create_run_router

database = database_fixture
pytestmark = pytest.mark.integration


def controls(engine, *, reviewed=None, **limits):
    return PublicResourceControls(
        engine,
        PublicResourcePolicy(
            enabled=True,
            network_key=derive_network_key("synthetic-resource-secret" * 2),
            limits=PublicResourceLimits(**limits),
        ),
        limits_resolver=(lambda _: reviewed) if reviewed else None,
    )


def identity(engine, instance, label="synthetic-owner", peer="192.0.2.1"):
    digest = owner(engine, label)
    with Session(engine) as session:
        expiry = session.get(BrowserSession, digest).expires_at
    return PublicRequestIdentity(instance.network_bucket(peer), digest, expiry)


def test_concurrent_last_attempt_is_shared_between_independent_controls(database, monkeypatch):
    first = controls(database, submit_session_per_minute=2)
    second = controls(database, submit_session_per_minute=2)
    requester = identity(database, first)
    with Session(database) as session:
        now = session.scalar(select(func.clock_timestamp()))
    monkeypatch.setattr("polymath.public_resource_controls._now", lambda _: now)
    first.consume("submit", requester)
    barrier = Barrier(2)

    def attempt(instance):
        barrier.wait(timeout=5)
        try:
            instance.consume("submit", requester)
            return "accepted"
        except PublicResourceError as error:
            assert error.retry_after_seconds is not None
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (first, second)))
    assert sorted(results) == ["accepted", "public_request_rate_limited"]
    with Session(database) as session:
        row = session.scalar(
            select(PublicRateBucket).where(PublicRateBucket.bucket_kind == "submit-session-minute")
        )
        assert row.count == 3
    # The exhausted admission bucket cannot disable the separate stop control.
    second.consume("control", requester)


def test_exhausted_service_gate_cannot_allocate_unbounded_peer_rows(database):
    instance = controls(database, submit_service_per_minute=1)
    instance.consume("submit", PublicRequestIdentity(instance.network_bucket("192.0.2.1")))
    for suffix in range(2, 40):
        with pytest.raises(PublicResourceError, match="public_request_rate_limited"):
            instance.consume(
                "submit", PublicRequestIdentity(instance.network_bucket(f"192.0.2.{suffix}"))
            )
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(PublicRateBucket)) == 2
        assert (
            session.scalar(
                select(PublicRateBucket.count).where(
                    PublicRateBucket.bucket_kind == "submit-service-minute"
                )
            )
            == 2
        )


def test_reviewed_limits_only_lower_current_server_limits(database):
    instance = controls(
        database,
        reviewed=PublicResourceLimits(submit_session_per_minute=1),
        submit_session_per_minute=2,
    )
    requester = identity(database, instance)
    instance.consume("submit", requester)
    with pytest.raises(PublicResourceError):
        instance.consume("submit", requester)
    assert instance.current_limits().submit_session_per_minute == 1


def test_expired_window_changes_bucket_without_rewriting_earlier_attempts(database, monkeypatch):
    instance = controls(database, read_session_per_minute=1)
    requester = identity(database, instance)
    with Session(database) as session:
        now = session.scalar(select(func.clock_timestamp()))
    monkeypatch.setattr("polymath.public_resource_controls._now", lambda _: now)
    instance.consume("read", requester)
    with pytest.raises(PublicResourceError):
        instance.consume("read", requester)
    monkeypatch.setattr(
        "polymath.public_resource_controls._now", lambda _: now + timedelta(minutes=1)
    )
    instance.consume("read", requester)
    with Session(database) as session:
        rows = session.scalars(
            select(PublicRateBucket)
            .where(PublicRateBucket.bucket_kind == "read-session-minute")
            .order_by(PublicRateBucket.window_start)
        ).all()
        assert [row.count for row in rows] == [2, 1]


def test_session_mint_gate_runs_before_actual_cookie_creation(database):
    instance = controls(database, mint_network_per_hour=1)
    app = FastAPI()
    app.include_router(create_run_router(database))
    app.add_middleware(PublicResourceMiddleware, controls=instance)
    with TestClient(app, base_url="https://fixture.test") as first:
        response = first.get("/api/v1/session")
        assert response.status_code == 200
        assert first.get("/api/v1/session").status_code == 200
    with TestClient(app, base_url="https://fixture.test") as second:
        response = second.get("/api/v1/session", headers={"X-Forwarded-For": "203.0.113.99"})
        assert response.status_code == 429 and not response.cookies
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(BrowserSession)) == 1


def test_invalid_actual_task_bodies_consume_attempts_but_not_stop_controls(database):
    instance = controls(database, submit_session_per_minute=2)
    app = FastAPI()
    app.include_router(create_run_router(database))
    app.add_middleware(PublicResourceMiddleware, controls=instance)
    with TestClient(app, base_url="https://fixture.test") as client:
        csrf = client.get("/api/v1/session").json()["csrf_token"]
        for _ in range(2):
            response = client.post("/api/v1/runs", content="{synthetic-private-invalid-json")
            assert response.status_code == 422
            assert "synthetic-private" not in response.text
        response = client.post("/api/v1/runs", json={})
        assert response.status_code == 429

        def unavailable_manifest(_):
            raise ValueError("Synthetic current funding cannot be inspected")

        instance.limits_resolver = unavailable_manifest
        response = client.post(
            "/api/v1/runs/00000000-0000-0000-0000-000000000000/cancel",
            headers={"X-CSRF-Token": csrf, "Origin": "https://fixture.test"},
        )
        assert response.status_code == 404


def test_stream_slots_are_shared_expire_and_release_without_renewing(database):
    first = controls(
        database, sse_session_connections=1, sse_network_connections=2, sse_service_connections=2
    )
    second = controls(
        database, sse_session_connections=1, sse_network_connections=2, sse_service_connections=2
    )
    one, two, three = (identity(database, first, label=f"synthetic-stream-{i}") for i in range(3))
    original = first.acquire_stream(one)
    with pytest.raises(PublicResourceError, match="public_stream_limit"):
        second.acquire_stream(one)
    other = second.acquire_stream(two)
    with pytest.raises(PublicResourceError, match="public_stream_limit"):
        first.acquire_stream(three)
    with Session(database) as session, session.begin():
        assert session.get(PublicStreamLease, original.id).expires_at == original.expires_at
        session.execute(
            update(PublicStreamLease)
            .where(PublicStreamLease.id == original.id)
            .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )
    replacement = second.acquire_stream(three)
    second.release_stream(replacement.id)
    first.release_stream(other.id)
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(PublicStreamLease)) == 0


async def test_actual_sse_timeout_preserves_cursor_and_frees_slot(database):
    instance = controls(database, sse_max_seconds=1)
    token = "S" * 43
    owner(database, token)
    app = FastAPI()

    @app.get("/api/v1/runs/synthetic/events")
    async def events():
        async def messages():
            yield "id: 7\nevent: activity\ndata: {}\n\n"
            await asyncio.Event().wait()

        return StreamingResponse(messages(), media_type="text/event-stream")

    app.add_middleware(PublicResourceMiddleware, controls=instance)
    transport = httpx.ASGITransport(app=app, client=("192.0.2.8", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="https://fixture.test") as client:
        response = await client.get(
            "/api/v1/runs/synthetic/events",
            headers={"Cookie": "polymath_session=" + token, "Last-Event-ID": "6"},
        )
    assert response.status_code == 200 and "id: 7" in response.text
    assert "event: end" not in response.text
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(PublicStreamLease)) == 0
        digest = hashlib.sha256(token.encode()).hexdigest()
        assert session.get(BrowserSession, digest) is not None


async def test_research_and_feed_share_one_actual_provider_gate_without_double_acquisition(
    database,
):
    from test_ingestion import start

    repository, _, _, lease = start(database)
    assert repository.acquire_provider_slot(lease, "arxiv") == 0
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(200, content=b"synthetic metadata")

    pacer = DatabaseProviderPacer(database, max_wait_seconds=0.01)
    client = ArxivClient(transport=httpx.MockTransport(transport), provider_slot=pacer)
    with pytest.raises(ProviderError, match="rate_limited"):
        await client._request({"max_results": 1})
    assert requests == []
    with Session(database) as session, session.begin():
        gate = session.get(IngestionProviderGate, "arxiv")
        assert gate.sequence == 1
        gate.next_allowed_at = session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
    await client._request({"max_results": 1})
    assert len(requests) == 1
    assert 0 < repository.acquire_provider_slot(lease, "arxiv") <= 3
    with Session(database) as session:
        assert session.get(IngestionProviderGate, "arxiv").sequence == 2
