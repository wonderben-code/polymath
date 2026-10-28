"""Small public capability DTO through the real middleware; no provider calls."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_meta_readiness import readiness as readiness_fixture
from test_native_research_worker import native as native_fixture
from test_public_delegations import public_active
from test_run_api import bootstrap
from test_scientific_native_worker import approve_scientific, scientific_client
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.budget import BudgetService
from polymath.budget_models import BudgetReservation
from polymath.meta_research_contracts import SCIENTIFIC_CAPABILITIES
from polymath.models import ResearchRun
from polymath.public_resource_models import PublicRateBucket
from polymath.run_api import COOKIE_NAME, _hash_token

database = database_fixture
readiness = readiness_fixture
native = native_fixture
pytestmark = pytest.mark.integration
PATH = "/api/v1/research/protocols"
EMPTY = {
    "protocol": "polymath-research-protocols-v1",
    "activation_sha256": None,
    "capabilities": [],
}


def counts(engine):
    with Session(engine) as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (BrowserSession, ResearchRun, BudgetReservation)
        )


def private_headers(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert "set-cookie" not in response.headers


def session_reads(engine, owner):
    with Session(engine) as session:
        return session.scalar(
            select(func.coalesce(func.sum(PublicRateBucket.count), 0)).where(
                PublicRateBucket.bucket_kind == "read-session-minute",
                PublicRateBucket.subject_sha256 == owner,
            )
        )


def test_anonymous_protocol_inspection_never_mints_owner_or_creates_jobs(readiness):
    public = public_active(readiness, short_issuer=False)
    with scientific_client(public, public=True) as client:
        before = counts(public.engine)
        response = client.get(PATH)
        assert response.status_code == 401, response.text
        private_headers(response)
        assert not client.cookies and counts(public.engine) == before


def test_public_legacy_then_approved_protocols_use_three_exact_keys_same_sha_and_read_budget(
    readiness,
):
    public = public_active(readiness, short_issuer=False)
    with scientific_client(public, public=True) as visitor:
        bootstrap(visitor)
        owner = _hash_token(visitor.cookies.get(COOKIE_NAME))
        before, reads = counts(public.engine), session_reads(public.engine, owner)
        response = visitor.get(PATH)
        assert response.status_code == 200, response.text
        assert response.json() == {**EMPTY, "activation_sha256": public.public_activation_sha256}
        private_headers(response)
        assert session_reads(public.engine, owner) == reads + 1
        approve_scientific(public, public=True)
        response = visitor.get(PATH)
        assert response.status_code == 200, response.text
        assert response.json() == {
            "protocol": "polymath-research-protocols-v1",
            "activation_sha256": public.public_activation_sha256,
            "capabilities": list(SCIENTIFIC_CAPABILITIES),
        }
        assert set(response.json()) == {"protocol", "activation_sha256", "capabilities"}
        assert session_reads(public.engine, owner) == reads + 2
        assert counts(public.engine) == before
        assert "polymath_operator" not in dict(visitor.cookies)
        assert all(
            value not in response.text
            for value in (
                public.policy.api_key.get_secret_value(),
                "actor_id",
                "credential_binding",
                "task_profiles",
                "provider_account_attestation",
                "controller_sha256",
            )
        )
        private_headers(response)


def test_public_protocols_survive_actual_issuer_logout_then_close_on_account_stop(readiness):
    public = approve_scientific(public_active(readiness, short_issuer=False), public=True)
    with scientific_client(public, public=True) as visitor:
        bootstrap(visitor)
        before = counts(public.engine)
        assert (
            public.issuer_client.post(
                "/api/v1/operator/logout", headers=public.issuer_headers
            ).status_code
            == 200
        )
        response = visitor.get(PATH)
        assert response.status_code == 200, response.text
        assert response.json()["capabilities"] == list(SCIENTIFIC_CAPABILITIES)
        assert response.json()["activation_sha256"] == public.public_activation_sha256
        BudgetService(public.engine).set_kill_switch(public.policy.native_account_id, active=True)
        closed = visitor.get(PATH)
        assert closed.status_code == 200, closed.text
        assert closed.json() == EMPTY
        assert counts(public.engine) == before
        private_headers(closed)


async def test_private_approved_profiles_remain_absent_from_public_protocol_dto(native):
    approve_scientific(native)
    with scientific_client(native) as client:
        before = counts(native.engine)
        response = client.get(PATH)
        assert response.status_code == 200, response.text
        assert response.json() == EMPTY
        assert counts(native.engine) == before
        assert native.activation_sha256 not in response.text
        assert native.policy.api_key.get_secret_value() not in response.text
        private_headers(response)
