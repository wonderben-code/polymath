"""Actual assembled API/resource behavior with synthetic saved funding only."""

import hashlib
import json
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_memory import assign
from test_meta_readiness import readiness as readiness_fixture
from test_operator_auth import TOKEN
from test_public_research_quota import public_delegation
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.config import Settings
from polymath.jobs import JobRepository
from polymath.main import create_app
from polymath.meta_research_models import MetaResearchManifestRecord
from polymath.models import ResearchRun
from polymath.operator_models import OperatorSession
from polymath.public_research_contracts import (
    PublicLimits,
    PublicResearchPolicy,
    PublicResourceLimits,
)
from polymath.public_resource_controls import (
    PublicResourceControls,
    PublicResourcePolicy,
    derive_network_key,
)
from polymath.research.meta import MetaModel

database = database_fixture
readiness = readiness_fixture
pytestmark = pytest.mark.integration


def limits(**resources):
    return PublicLimits(
        public_total_cap_microdollars=10_000_000,
        public_daily_cap_microdollars=8_000_000,
        session_total_cap_microdollars=5_000_000,
        session_daily_cap_microdollars=4_000_000,
        resources=PublicResourceLimits(**resources),
    )


def application(public, *, injected=None, research_policy=None, public_policy=None):
    return create_app(
        Settings(
            database_url=public.engine.url.render_as_string(hide_password=False),
            operator_bootstrap_token=TOKEN,
            public_resource_limits_enabled=True,
            _env_file=None,
        ),
        research_policy=research_policy or public.active.policy,
        readiness_policy=public.active.readiness_policy,
        operator_policy=public.active.operator_policy,
        public_policy=public_policy or PublicResearchPolicy(enabled=True),
        resource_controls=injected,
    )


@pytest.mark.parametrize("custom", ["absent", "looser", "stricter"])
def test_actual_app_always_combines_injected_limits_with_reviewed_bounds(readiness, custom):
    public = public_delegation(
        readiness, limits(submit_session_per_minute=2 if custom == "stricter" else 1)
    )
    resolver = (
        None
        if custom == "absent"
        else lambda _: PublicResourceLimits(
            submit_session_per_minute=1 if custom == "stricter" else 6
        )
    )
    injected = PublicResourceControls(
        public.engine,
        PublicResourcePolicy(
            enabled=True, network_key=derive_network_key(TOKEN), limits=PublicResourceLimits()
        ),
        limits_resolver=resolver,
    )
    app = application(public, injected=injected)
    assert (
        injected.limits_resolver is resolver
    )  # Assembly cannot mutate another application's resolver.
    assert app.state.public_resource_controls.current_limits().submit_session_per_minute == 1
    with TestClient(app, base_url="https://fixture.test") as client:
        assert client.get("/api/v1/session").status_code == 200
        first = client.post("/api/v1/runs", content="{synthetic-private-invalid-json")
        second = client.post("/api/v1/runs", content="{synthetic-private-invalid-json")
        assert first.status_code == 422
        assert second.status_code == 429 and int(second.headers["Retry-After"]) >= 1
        assert "synthetic-private" not in second.text


def test_saved_read_limits_survive_removed_keys_and_expired_issuer(readiness, monkeypatch):
    public = public_delegation(readiness, limits(read_session_per_minute=2))
    with Session(public.engine) as session, session.begin():
        account = session.get(BudgetAccount, public.account_id)
        account.native_scope = None
        account.kill_switch = True
        account.frozen = True
        session.execute(
            update(OperatorSession).values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )
        issuer_hash = session.scalar(select(OperatorSession.browser_session_hash))
        session.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == issuer_hash)
            .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )
    policy = replace(public.active.policy, paid_research_enabled=False, api_key=None, model_id=None)
    monkeypatch.setattr(MetaModel, "__init__", lambda *a, **k: pytest.fail("Read constructed Meta"))
    app = application(public, research_policy=policy)
    assert app.state.public_resource_controls.current_limits().read_session_per_minute == 2
    with TestClient(app, base_url="https://fixture.test") as client:
        assert client.get("/api/v1/session").status_code == 200
        assert client.get("/api/v1/knowledge").status_code == 200
        assert client.get("/api/v1/knowledge").status_code == 200
        assert client.get("/api/v1/knowledge").status_code == 429


def test_owned_access_get_never_bootstraps_cookie_or_scientific_work(readiness, monkeypatch):
    public = public_delegation(readiness)
    monkeypatch.setattr(
        MetaModel, "__init__", lambda *a, **k: pytest.fail("Access constructed Meta")
    )
    app = application(public)
    with Session(public.engine) as session:
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in (ResearchRun, BudgetReservation, BrowserSession)
        }
    with TestClient(app, base_url="https://fixture.test") as client:
        missing = client.get("/api/v1/research/access")
        assert (
            missing.status_code == 401
            and "set-cookie" not in missing.headers
            and not client.cookies
        )
        assert client.get("/api/v1/session").status_code == 200
        owned = client.get("/api/v1/research/access")
        assert owned.status_code == 200 and "set-cookie" not in owned.headers
        assert owned.json()["protocol"] == "polymath-research-access-v1"
        assert public.account_id not in owned.text and "fixture-publisher" not in owned.text
        assert public.active.policy.api_key.get_secret_value() not in owned.text
    with Session(public.engine) as session:
        for model in (ResearchRun, BudgetReservation):
            assert session.scalar(select(func.count()).select_from(model)) == before[model]
        assert (
            session.scalar(select(func.count()).select_from(BrowserSession))
            == before[BrowserSession] + 1
        )


def test_corrupt_historical_manifest_falls_back_to_server_read_caps(readiness):
    public = public_delegation(readiness, limits(read_session_per_minute=2))
    with Session(public.engine) as session, session.begin():
        original = session.get(MetaResearchManifestRecord, public.manifest_id)
        raw = json.loads(json.dumps(original.manifest))
        raw["public_limits"]["resources"]["read_session_per_minute"] = False
        digest = hashlib.sha256(
            json.dumps(
                raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode()
        ).hexdigest()
        session.add(
            MetaResearchManifestRecord(
                readiness_id=original.readiness_id,
                canary_receipt_id=original.canary_receipt_id,
                account_id=original.account_id,
                manifest_sha256=digest,
                manifest=raw,
            )
        )
        session.get(BudgetAccount, public.account_id).native_manifest_sha256 = digest
    assert (
        application(public).state.public_resource_controls.current_limits().read_session_per_minute
        == 60
    )


async def test_actual_main_sse_chain_closes_at_resource_deadline_without_false_terminal_event(
    readiness,
):
    public = public_delegation(readiness)
    app = application(
        public,
        public_policy=PublicResearchPolicy(
            enabled=True, resources=PublicResourceLimits(sse_max_seconds=1)
        ),
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="https://fixture.test"
        ) as client:
            assert (await client.get("/api/v1/session")).status_code == 200
            token = client.cookies.get("polymath_session")
            digest = hashlib.sha256(token.encode("ascii")).hexdigest()
            run = JobRepository(public.engine).create(mode="explore", goal="Synthetic owned SSE")
            assign(public.engine, run.id, digest)
            response = await client.get(f"/api/v1/runs/{run.id}/events")
        assert response.status_code == 200
        assert "event: activity" in response.text
        assert "event: end" not in response.text
        from polymath.public_resource_models import PublicStreamLease

        with Session(public.engine) as session:
            assert session.scalar(select(func.count()).select_from(PublicStreamLease)) == 0
            assert session.get(ResearchRun, run.id).status == "queued"
    finally:
        app.state.engine.dispose()


def test_injected_server_limits_cannot_enlarge_configured_policy_before_any_delegation(database):
    injected = PublicResourceControls(
        database,
        PublicResourcePolicy(
            enabled=True, network_key=derive_network_key(TOKEN), limits=PublicResourceLimits()
        ),
    )
    app = create_app(
        Settings(database_url=database.url.render_as_string(hide_password=False), _env_file=None),
        public_policy=PublicResearchPolicy(
            resources=PublicResourceLimits(submit_session_per_minute=1)
        ),
        resource_controls=injected,
    )
    assert injected.policy.limits.submit_session_per_minute == 6
    assert app.state.public_resource_controls.current_limits().submit_session_per_minute == 1
    with TestClient(app, base_url="https://fixture.test") as client:
        assert client.get("/api/v1/session").status_code == 200
        assert client.post("/api/v1/runs", json={}).status_code == 422
        assert client.post("/api/v1/runs", json={}).status_code == 429
