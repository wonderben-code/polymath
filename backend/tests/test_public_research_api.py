"""Real PG HTTP ownership and durable-delegation boundaries; no paid calls."""

from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_meta_readiness import readiness as readiness_fixture
from test_public_delegations import public_active, public_preview
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.budget_models import BudgetReservation
from polymath.meta_research_errors import MetaResearchError
from polymath.models import ResearchRun
from polymath.operator_auth import create_operator_router
from polymath.public_research_api import create_public_research_router
from polymath.public_research_quota import PublicQuotaError
from polymath.research.meta import MetaModel
from polymath.run_api import create_run_router

pytestmark = pytest.mark.integration
database = database_fixture
readiness = readiness_fixture
PREFIX = "/api/v1/budget/research/public"


def private(response):
    for header, value in {
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
        "x-robots-tag": "noindex, nofollow",
    }.items():
        assert response.headers[header] == value
    assert "set-cookie" not in response.headers


def count(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def app(value):
    application = FastAPI()
    application.include_router(create_run_router(value.engine))
    application.include_router(create_operator_router(value.engine, value.operator_policy))
    application.include_router(
        create_public_research_router(
            value.engine,
            native_store=value.parent,
            research_policy=value.policy,
            readiness_policy=value.readiness_policy,
            operator_policy=value.operator_policy,
            public_policy=value.public_policy,
            resource_controls=value.parent.resource_controls,
        )
    )
    return application


@pytest.fixture
def api(readiness, monkeypatch):
    value = public_preview(readiness, short_issuer=False)
    monkeypatch.setattr(
        MetaModel, "__init__", lambda *a, **k: pytest.fail("HTTP constructed a model")
    )
    with ExitStack() as clients:

        def client(owned=True):
            browser = clients.enter_context(TestClient(app(value)))
            if owned:
                browser.cookies.update(value.issuer_client.cookies)
            return browser

        yield value, client


def body(value):
    manifest = value.public_preview.manifest.model_dump(mode="json")
    return {
        "readiness_id": manifest["readiness_id"],
        "canary_receipt_id": manifest["canary"]["receipt_id"],
        "review": manifest["review"],
        "provider_account_attestation": manifest["provider_account_attestation"],
        "public_limits": manifest["public_limits"],
        "valid_until": manifest["valid_until"],
    }


def test_default_capabilities_never_query_or_bootstrap(database, monkeypatch):
    application = FastAPI()
    application.include_router(create_public_research_router(database))
    monkeypatch.setattr(database, "connect", lambda *a, **k: pytest.fail("queried DB"))
    with TestClient(application) as visitor:
        response = visitor.get(PREFIX + "/capabilities")
        assert response.json() == {
            "activation_available": False,
            "inspection_available": False,
            "revoke_available": False,
            "audience": "public_sessions",
            "reason": "public_research_unavailable",
        }
        private(response)
        assert not visitor.cookies


def test_exact_preview_activate_read_history_stop_do_not_create_scientific_work(api):
    value, clients = api
    owner = clients()
    before = {model: count(value.engine, model) for model in (ResearchRun, BudgetReservation)}
    response = owner.post(PREFIX + "/preview", headers=value.issuer_headers, json=body(value))
    assert response.status_code == 200, response.text
    private(response)
    preview = response.json()
    assert preview["manifest"]["continues_after_operator_logout"] is True
    assert preview["manifest"]["valid_until"] == body(value)["valid_until"]
    arm = {"manifest": preview["manifest"], "expected_manifest_sha256": preview["manifest_sha256"]}
    response = owner.post(PREFIX + "/activate", headers=value.issuer_headers, json=arm)
    assert response.status_code == 201, response.text
    private(response)
    record = response.json()
    assert record["delegation_current"]
    assert datetime.fromisoformat(record["approval"]["expires_at"]) == datetime.fromisoformat(
        preview["manifest"]["valid_until"]
    )
    detail = owner.get(PREFIX + "/activations/" + record["id"])
    assert detail.json() == record
    assert owner.get(PREFIX + "/current").json()["activation"] == record
    page = owner.get(PREFIX + "/activations", params={"limit": 1}).json()
    assert page == {"activations": [record], "next_cursor": None}
    stopped = owner.post(
        PREFIX + "/activations/" + record["id"] + "/revoke", headers=value.issuer_headers, json={}
    )
    assert stopped.status_code == 200, stopped.text
    assert not stopped.json()["delegation_current"]
    assert stopped.json()["approval"]["revoked_at"] is not None
    repeated = owner.post(PREFIX + "/activate", headers=value.issuer_headers, json=arm)
    assert repeated.status_code == 201 and repeated.json()["id"] == record["id"]
    assert not repeated.json()["delegation_current"]
    assert {model: count(value.engine, model) for model in before} == before
    assert value.policy.api_key.get_secret_value() not in response.text + detail.text + stopped.text


def test_access_never_bootstraps_and_never_leaks_private_approval_to_foreign_owner(api):
    value, clients = api
    anonymous = clients(False)
    before = count(value.engine, BrowserSession)
    response = anonymous.get("/api/v1/research/access")
    assert response.status_code == 401
    private(response)
    assert not anonymous.cookies and count(value.engine, BrowserSession) == before
    foreign = clients(False)
    foreign.get("/api/v1/session")
    response = foreign.get("/api/v1/research/access")
    assert response.status_code == 200, response.text
    assert response.json()["audience"] is None
    assert response.json()["expected_activation_sha256"] is None
    private(response)
    owner = clients()
    response = owner.get("/api/v1/research/access")
    assert response.json()["audience"] == "operator_browser"
    assert response.json()["reason"] == "operator_required"
    assert response.json()["limits"] is None


def test_public_visitor_access_survives_issuer_logout_and_discloses_only_own_limits(readiness):
    value = public_active(readiness, short_issuer=False)
    with TestClient(app(value)) as visitor, TestClient(app(value)) as issuer:
        visitor.get("/api/v1/session")
        issuer.cookies.update(value.issuer_client.cookies)
        response = visitor.get("/api/v1/research/access")
        assert response.status_code == 200, response.text
        access = response.json()
        assert access["audience"] == "public_sessions" and access["available"]
        assert access["expected_activation_sha256"] == value.public_activation_sha256
        assert set(access["limits"]) == {
            "session_total_cap_microdollars",
            "session_daily_cap_microdollars",
            "max_jobs_per_session_day",
            "max_active_jobs_per_session",
            "task_kinds",
            "discovery_intent",
        }
        private(response)
        assert "actor_id" not in response.text and "credential_binding" not in response.text
        assert (
            issuer.post("/api/v1/operator/logout", headers=value.issuer_headers).status_code == 200
        )
        continued = visitor.get("/api/v1/research/access")
        assert continued.status_code == 200 and continued.json()["available"]
        assert (
            continued.json()["expected_activation_sha256"] == access["expected_activation_sha256"]
        )
        assert continued.json()["expires_at"] == access["expires_at"]


@pytest.mark.parametrize("change", ["missing_browser", "missing_operator", "foreign_origin"])
@pytest.mark.parametrize("route", ["preview", "activate", "revoke"])
def test_every_admin_mutation_requires_both_csrf_domains_and_same_origin(api, route, change):
    value, clients = api
    owner = clients()
    record = value.parent.public_activate(
        value.request, value.public_preview.manifest, value.public_preview.manifest_sha256
    ).to_response()
    headers = dict(value.issuer_headers)
    if change == "missing_browser":
        headers = {key: val for key, val in headers.items() if key.lower() != "x-csrf-token"}
    elif change == "missing_operator":
        headers = {
            key: val for key, val in headers.items() if key.lower() != "x-operator-csrf-token"
        }
    else:
        headers["Origin"] = "https://foreign.example.test"
    target = "/activations/" + record["id"] + "/revoke" if route == "revoke" else "/" + route
    payload = (
        body(value)
        if route == "preview"
        else {
            "manifest": value.public_preview.manifest.model_dump(mode="json"),
            "expected_manifest_sha256": value.public_preview.manifest_sha256,
        }
        if route == "activate"
        else {}
    )
    response = owner.post(PREFIX + target, headers=headers, json=payload)
    assert response.status_code == 403, response.text
    private(response)


def test_public_flag_off_keeps_authorized_inspection_and_stop(api):
    value, clients = api
    owner = clients()
    record = value.parent.public_activate(
        value.request, value.public_preview.manifest, value.public_preview.manifest_sha256
    ).to_response()
    value.parent.public_policy = replace(value.public_policy, enabled=False)
    assert owner.get(PREFIX + "/activations/" + record["id"]).status_code == 200
    stopped = owner.post(
        PREFIX + "/activations/" + record["id"] + "/revoke", headers=value.issuer_headers, json={}
    )
    assert stopped.status_code == 200 and not stopped.json()["delegation_current"]


def test_redacted_validation_storage_and_bounded_quota_retry(api, monkeypatch):
    value, clients = api
    owner = clients()
    malformed = owner.post(
        PREFIX + "/preview", headers=value.issuer_headers, json={"api_key": "never-echo"}
    )
    assert malformed.status_code == 422 and "never-echo" not in malformed.text
    private(malformed)
    for fault, expected, retry in (
        (RuntimeError("sensitive storage details"), "meta_research_storage_unavailable", None),
        (MetaResearchError("invented-sensitive-code"), "meta_research_storage_unavailable", None),
        (
            PublicQuotaError("public_session_job_day_limit", 17),
            "public_session_job_day_limit",
            "17",
        ),
        (
            PublicQuotaError("public_session_job_day_limit", 99999999),
            "public_session_job_day_limit",
            None,
        ),
    ):

        def fail(*args, fault=fault, **kwargs):
            raise fault

        monkeypatch.setattr(value.parent, "public_preview", fail)
        response = owner.post(PREFIX + "/preview", headers=value.issuer_headers, json=body(value))
        assert response.json() == {"detail": expected}
        assert response.headers.get("retry-after") == retry
        private(response)


def test_expired_browser_clears_access_ownership_instead_of_funding_status(api):
    value, clients = api
    owner = clients()
    with Session(value.engine) as session, session.begin():
        session.execute(
            update(BrowserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    response = owner.get("/api/v1/research/access")
    assert response.status_code == 401
    private(response)
