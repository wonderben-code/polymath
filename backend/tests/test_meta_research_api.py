"""Actual operator HTTP activation boundaries; no model or provider calls."""

from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_meta_probes import attestation_data
from test_meta_readiness import readiness as readiness_fixture
from test_meta_research import active_research, review_data
from test_operator_auth import POLICY, login
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.meta_research import MetaResearchStore
from polymath.meta_research_api import create_meta_research_router
from polymath.meta_research_models import MetaResearchManifestRecord, NativeResearchRunBinding
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.operator_auth import create_operator_router
from polymath.operator_models import OperatorSession
from polymath.research.meta import MetaModel
from polymath.run_api import create_run_router

database = database_fixture
readiness = readiness_fixture
pytestmark = pytest.mark.integration
PREFIX = "/api/v1/budget/research"


def count(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert "set-cookie" not in response.headers


@pytest.fixture
def research_api(readiness, monkeypatch):
    active = active_research(readiness)
    _, _, _, original, headers = readiness

    def forbidden(*args, **kwargs):
        pytest.fail("Activation HTTP attempted to construct a model")

    monkeypatch.setattr(MetaModel, "__init__", forbidden)
    with ExitStack() as clients:

        def factory(*, enabled=True, owned=True, readiness_policy=None, operator_policy=POLICY):
            app = FastAPI()
            app.include_router(create_run_router(active.engine))
            app.include_router(create_operator_router(active.engine, operator_policy))
            app.include_router(
                create_meta_research_router(
                    active.engine,
                    research_policy=replace(active.policy, paid_research_enabled=enabled),
                    readiness_policy=readiness_policy or active.readiness_policy,
                    operator_policy=operator_policy,
                )
            )
            client = clients.enter_context(TestClient(app))
            if owned:
                client.cookies.update(original.cookies)
            return client

        yield active, factory, headers


def preview_body(active):
    return {
        "readiness_id": str(active.saved.id),
        "canary_receipt_id": str(active.canary.id),
        "review": review_data(),
        "provider_account_attestation": attestation_data(active.proposal.model_id),
    }


def arm_body(active):
    return {
        "manifest": active.preview.manifest.model_dump(mode="json"),
        "expected_manifest_sha256": active.activation_sha256,
    }


def test_default_capabilities_are_database_and_session_free(database, monkeypatch):
    app = FastAPI()
    app.include_router(create_meta_research_router(database))
    with monkeypatch.context() as scope:
        scope.setattr(database, "connect", lambda *a, **k: pytest.fail("Capabilities queried DB"))
        scope.setattr(MetaModel, "__init__", lambda *a, **k: pytest.fail("Constructed Meta"))
        with TestClient(app) as client:
            response = client.get(PREFIX + "/capabilities")
            assert response.json() == {
                "activation_available": False,
                "inspection_available": False,
                "revoke_available": False,
                "audience": "operator_browser",
                "public_research_available": False,
                "reason": "meta_research_unavailable",
            }
            private(response)
            assert not client.cookies
    assert count(database, BrowserSession) == count(database, OperatorSession) == 0


def test_exact_http_preview_activation_current_history_and_revoke_create_no_scientific_work(
    research_api,
):
    active, factory, headers = research_api
    client = factory()
    baseline = {
        model: count(active.engine, model)
        for model in (
            ResearchRun,
            ResearchExternalRequest,
            BudgetReservation,
            NativeResearchRunBinding,
        )
    }
    response = client.post(PREFIX + "/preview", headers=headers, json=preview_body(active))
    assert response.status_code == 200, response.text
    private(response)
    preview = response.json()
    assert preview["research_execution_available"] is False
    assert preview["manifest"]["audience"] == "operator_browser"
    assert len(preview["manifest"]["task_profiles"]) == 5
    assert count(active.engine, MetaResearchManifestRecord) == 1
    body = {"manifest": preview["manifest"], "expected_manifest_sha256": preview["manifest_sha256"]}
    response = client.post(PREFIX + "/activations", headers=headers, json=body)
    assert response.status_code == 201, response.text
    private(response)
    record = response.json()
    assert record["eligible_for_current_browser"] and record["research_execution_available"]
    assert client.get(PREFIX + "/activations/" + record["id"]).json() == record
    current = client.get(PREFIX + "/current")
    private(current)
    assert current.json()["activation"]["manifest_sha256"] == preview["manifest_sha256"]
    assert current.json()["activation"]["research_execution_available"] is True
    page = client.get(PREFIX + "/activations", params={"limit": 1}).json()
    assert [item["id"] for item in page["activations"]] == [record["id"]]
    assert page["next_cursor"] == record["id"]
    earlier = client.get(PREFIX + "/activations", params={"limit": 1, "before": record["id"]})
    assert [item["id"] for item in earlier.json()["activations"]] == [str(active.activation.id)]
    response = client.post(
        PREFIX + "/activations/" + record["id"] + "/revoke", headers=headers, json={}
    )
    assert response.status_code == 200, response.text
    private(response)
    assert not response.json()["research_execution_available"]
    with Session(active.engine) as session:
        account = session.get(BudgetAccount, active.policy.native_account_id)
        epoch = account.native_control_epoch
        assert (
            account.kill_switch and not account.execution_enabled and not account.pricing_verified
        )
    repeated = client.post(PREFIX + "/activations", headers=headers, json=body)
    assert repeated.status_code == 201 and repeated.json()["id"] == record["id"]
    assert repeated.json()["research_execution_available"] is False
    assert client.get(PREFIX + "/current").json()["activation"] is None
    with Session(active.engine) as session:
        assert (
            session.get(BudgetAccount, active.policy.native_account_id).native_control_epoch
            == epoch
        )
    assert {model: count(active.engine, model) for model in baseline} == baseline
    assert (
        active.policy.api_key.get_secret_value() not in response.text + current.text + repeated.text
    )


@pytest.mark.parametrize("action", ["preview", "activate", "revoke"])
@pytest.mark.parametrize(
    "changed",
    [
        {"X-CSRF-Token": "0" * 64},
        {"X-Operator-CSRF-Token": "0" * 64},
        {"Origin": "https://foreign.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_every_activation_mutation_requires_both_csrf_domains_and_same_origin(
    research_api, action, changed
):
    active, factory, headers = research_api
    client = factory()
    routes = {
        "preview": ("/preview", preview_body(active)),
        "activate": ("/activations", arm_body(active)),
        "revoke": (f"/activations/{active.activation.id}/revoke", {}),
    }
    path, body = routes[action]
    with Session(active.engine) as session:
        epoch = session.get(BudgetAccount, active.policy.native_account_id).native_control_epoch
    response = client.post(PREFIX + path, headers={**headers, **changed}, json=body)
    assert response.status_code == 403, response.text
    private(response)
    assert count(active.engine, MetaResearchManifestRecord) == 1
    with Session(active.engine) as session:
        assert (
            session.get(BudgetAccount, active.policy.native_account_id).native_control_epoch
            == epoch
        )


def test_current_context_requires_live_role_and_is_null_for_a_different_operator_browser(
    research_api,
):
    active, factory, _ = research_api
    anonymous = factory(owned=False)
    for path in ("/current", "/activations", f"/activations/{active.activation.id}"):
        response = anonymous.get(PREFIX + path)
        assert response.status_code == 401
        private(response)
    foreign = factory(owned=False)
    login(foreign)
    response = foreign.get(PREFIX + "/current")
    assert response.json() == {"activation": None, "reason": "meta_research_activation_required"}
    assert active.activation_sha256 not in response.text
    assert str(active.activation.id) not in response.text
    private(response)
    assert count(active.engine, NativeResearchRunBinding) == 0


def test_disabling_paid_configuration_keeps_inspection_and_explicit_revoke_available(research_api):
    active, factory, headers = research_api
    client = factory(enabled=False)
    capabilities = client.get(PREFIX + "/capabilities").json()
    assert not capabilities["activation_available"]
    assert capabilities["inspection_available"] and capabilities["revoke_available"]
    assert client.get(PREFIX + "/current").json()["activation"] is None
    assert client.get(PREFIX + "/activations").status_code == 200
    preview = client.post(PREFIX + "/preview", headers=headers, json=preview_body(active))
    assert preview.status_code == 503 and preview.json() == {"detail": "meta_research_unavailable"}
    response = client.post(
        f"{PREFIX}/activations/{active.activation.id}/revoke", headers=headers, json={}
    )
    assert response.status_code == 200 and not response.json()["research_execution_available"]
    private(response)


def test_role_removal_and_operator_revocation_cannot_read_or_reuse_activation(research_api):
    active, factory, headers = research_api
    no_role = factory(
        readiness_policy=replace(active.readiness_policy, allowed_actor_ids=("other",))
    )
    response = no_role.get(PREFIX + "/activations")
    assert response.status_code == 403 and response.json() == {
        "detail": "meta_research_actor_forbidden"
    }
    private(response)
    with Session(active.engine) as session, session.begin():
        session.execute(update(OperatorSession).values(revoked_at=func.clock_timestamp()))
    client = factory()
    for method, path, body in (
        ("GET", "/current", None),
        ("POST", "/activations", arm_body(active)),
    ):
        response = client.request(method, PREFIX + path, headers=headers, json=body)
        assert response.status_code == 401
        private(response)


@pytest.mark.parametrize("expired", [BrowserSession, OperatorSession])
def test_expired_browser_or_operator_cannot_preview_or_reuse_activation(research_api, expired):
    active, factory, headers = research_api
    with Session(active.engine) as session, session.begin():
        session.execute(update(expired).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    client = factory()
    for method, path, body in (
        ("GET", "/current", None),
        ("POST", "/preview", preview_body(active)),
        ("POST", "/activations", arm_body(active)),
    ):
        response = client.request(method, PREFIX + path, headers=headers, json=body)
        assert response.status_code == 401
        private(response)
    assert count(active.engine, MetaResearchManifestRecord) == 1


def test_invalid_payloads_and_storage_errors_are_static_and_private(research_api, monkeypatch):
    active, factory, headers = research_api
    client = factory()
    secret = "synthetic-private-input-must-not-echo"
    malformed = client.post(
        PREFIX + "/activations", headers=headers, json={**arm_body(active), "api_key": secret}
    )
    assert malformed.status_code == 422
    assert malformed.json() == {"detail": "meta_research_invalid_request"}
    assert secret not in malformed.text
    private(malformed)

    def broken(*args, **kwargs):
        raise SQLAlchemyError(secret)

    monkeypatch.setattr(MetaResearchStore, "current", broken)
    response = client.get(PREFIX + "/current")
    assert response.status_code == 503
    assert response.json() == {"detail": "meta_research_storage_unavailable"}
    assert secret not in response.text
    private(response)
