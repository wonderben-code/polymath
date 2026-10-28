"""Real PostgreSQL operator HTTP controls; no native model or provider calls."""

import copy
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_meta_readiness_contracts import proposal_data
from test_operator_auth import POLICY, login
from test_worker import database as database_fixture

from polymath.budget_admin_api import create_budget_admin_router
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.meta_probe_api import create_meta_probe_router
from polymath.meta_probe_contracts import MetaProbePolicy
from polymath.meta_probe_models import MetaProbeApproval, MetaProbeManifestRecord, MetaProbeReceipt
from polymath.meta_readiness_contracts import ReadinessPolicy
from polymath.models import ResearchRun
from polymath.operator_auth import create_operator_router
from polymath.run_api import create_run_router

database = database_fixture
pytestmark = pytest.mark.integration
PREFIX = "/api/v1/budget/verification"
READY = ReadinessPolicy(preparation_enabled=True, allowed_actor_ids=(POLICY.actor_id,))
PROBE = MetaProbePolicy(
    paid_probe_enabled=True,
    native_account_id="fixture-account",
    model_id="fixture-attested-model",
    api_key=SecretStr("Synthetic_fixture_no_native_key_or_calls"),
)


def app_for(engine, *, readiness=READY, probe=PROBE, operator=POLICY):
    app = FastAPI()
    app.include_router(create_run_router(engine))
    app.include_router(create_operator_router(engine, operator))
    app.include_router(
        create_budget_admin_router(engine, readiness_policy=readiness, operator_policy=operator)
    )
    app.include_router(
        create_meta_probe_router(
            engine, probe_policy=probe, readiness_policy=readiness, operator_policy=operator
        )
    )
    return app


def prepared(client):
    headers, _ = login(client)
    data = proposal_data(now=datetime.now(UTC))
    preview = client.post(
        "/api/v1/budget/readiness/preview", headers=headers, json={"proposal": data}
    )
    assert preview.status_code == 200, preview.text
    quote = preview.json()
    response = client.post(
        "/api/v1/budget/readiness",
        headers=headers,
        json={"proposal": quote["proposal"], "expected_proposal_sha256": quote["proposal_sha256"]},
    )
    assert response.status_code == 201, response.text
    now = datetime.now(UTC)
    attestation = {
        "provider_billing_account_id": "fixture-billing-scope",
        "model_id": data["model_id"],
        "currency": "USD",
        "usage_contract": "prompt_completion_inclusive_v1",
        "observed_at": (now - timedelta(minutes=1)).isoformat(),
        "valid_until": (now + timedelta(minutes=30)).isoformat(),
        "available_credit_microdollars": 150000000,
        "remaining_provider_allowance_microdollars": 150000000,
        "provider_hard_cap_microdollars": 150000000,
        "evidence": [
            {"url": "https://example.org/synthetic-credit-evidence", "content_sha256": "d" * 64}
        ],
    }
    return headers, {
        "readiness_id": response.json()["id"],
        "provider_account_attestation": attestation,
    }


def preview(client, headers, body):
    response = client.post(PREFIX + "/preview", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def arm(client, headers, value):
    return client.post(
        PREFIX + "/jobs",
        headers=headers,
        json={"manifest": value["manifest"], "expected_manifest_sha256": value["manifest_sha256"]},
    )


def count(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def headers_private(response):
    for name, value in {
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        "referrer-policy": "no-referrer",
        "x-robots-tag": "noindex, nofollow",
    }.items():
        assert response.headers[name] == value


def test_capabilities_are_session_free_no_db_and_never_expose_native_secrets():
    # Even an engine without connection methods suffices: this route is pure policy.
    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    app = FastAPI()
    app.include_router(create_meta_probe_router(engine))
    with TestClient(app) as client:
        response = client.get(PREFIX + "/capabilities")
        assert response.json() == {
            "verification_available": False,
            "inspection_available": False,
            "cancel_available": False,
            "finalization_available": False,
            "research_execution_available": False,
            "reason": "meta_probe_unavailable",
        }
        headers_private(response)
        assert not client.cookies


def test_exact_preview_creates_no_permission_or_work_and_arm_is_one_idempotent_job(database):
    with TestClient(app_for(database)) as client:
        headers, body = prepared(client)
        value = preview(client, headers, body)
        assert value["research_execution_available"] is False
        assert value["manifest"]["clear_kill_switch"] is True
        assert "credential_binding_sha256" not in body["provider_account_attestation"]
        assert PROBE.api_key.get_secret_value() not in str(value)
        assert count(database, ResearchRun) == 0
        assert count(database, MetaProbeManifestRecord) == 0
        with Session(database) as session:
            assert session.get(BudgetAccount, "fixture-account").kill_switch is True
        response = arm(client, headers, value)
        assert response.status_code == 201, response.text
        job = response.json()
        assert job["task_kind"] == "verify_meta" and job["status"] == "queued"
        assert job["manifest"] == value["manifest"]
        assert job["manifest_sha256"] == value["manifest_sha256"]
        assert job["receipt"] is None and job["research_execution_available"] is False
        assert job["can_cancel"] is True and job["can_finalize"] is False
        assert arm(client, headers, value).json()["id"] == job["id"]
        assert count(database, ResearchRun) == count(database, MetaProbeApproval) == 1
        assert count(database, BudgetReservation) == count(database, MetaProbeReceipt) == 0
        with Session(database) as session:
            account = session.get(BudgetAccount, "fixture-account")
            assert account.kill_switch is False
            assert account.execution_enabled is account.pricing_verified is False
        headers_private(response)


@pytest.mark.parametrize("missing", ["browser", "operator", "origin"])
def test_preview_requires_each_live_csrf_domain_and_same_origin(database, missing):
    with TestClient(app_for(database)) as client:
        headers, body = prepared(client)
        omitted = {
            "browser": "x-csrf-token",
            "operator": "x-operator-csrf-token",
            "origin": "origin",
        }[missing]
        edited = {key: value for key, value in headers.items() if key.lower() != omitted}
        if missing == "origin":
            edited["Origin"] = "https://foreign.example"
        response = client.post(PREFIX + "/preview", headers=edited, json=body)
        assert response.status_code == 403
        headers_private(response)
        assert count(database, ResearchRun) == 0


@pytest.mark.parametrize("target", ["model", "message", "reservation", "hash"])
def test_no_caller_can_substitute_a_model_prompt_price_or_approval_hash(database, target):
    with TestClient(app_for(database)) as client:
        headers, body = prepared(client)
        value = preview(client, headers, body)
        changed = copy.deepcopy(value)
        if target == "model":
            changed["manifest"]["request"]["payload"]["model"] = "different-caller-model"
        elif target == "message":
            changed["manifest"]["request"]["payload"]["messages"][0]["content"] = (
                "Secret prompt must never echo"
            )
        elif target == "reservation":
            changed["manifest"]["reservation_microdollars"] = 1
        else:
            changed["manifest_sha256"] = "f" * 64
        response = arm(client, headers, changed)
        assert response.status_code in (409, 422)
        assert "Secret prompt" not in response.text
        headers_private(response)
        assert count(database, ResearchRun) == 0


def test_authorized_history_read_cancel_and_free_finalization_never_send_or_release_money(database):
    with TestClient(app_for(database)) as client:
        headers, body = prepared(client)
        value = preview(client, headers, body)
        job = arm(client, headers, value).json()
        listing = client.get(PREFIX + "/jobs?limit=1").json()
        assert listing == {"jobs": [job], "next_cursor": None}
        assert client.get(PREFIX + "/jobs/" + job["id"]).json() == job
        response = client.post(
            PREFIX + "/jobs/" + job["id"] + "/finalize", headers=headers, json={}
        )
        assert (
            response.status_code == 409
            and response.json()["detail"] == "meta_probe_outcome_unknown"
        )
        cancelled = client.post(PREFIX + "/jobs/" + job["id"] + "/cancel", headers=headers, json={})
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["approval"]["valid"] is False
        assert arm(client, headers, value).json()["id"] == job["id"]
        with Session(database) as session:
            account = session.get(BudgetAccount, "fixture-account")
            assert account.kill_switch is True
        assert count(database, BudgetReservation) == count(database, MetaProbeReceipt) == 0
        headers_private(cancelled)


def test_ordinary_browser_and_operator_outside_budget_allowlist_cannot_read_or_arm(database):
    with TestClient(app_for(database)) as owner:
        headers, body = prepared(owner)
        job = arm(owner, headers, preview(owner, headers, body)).json()
    with TestClient(app_for(database)) as browser:
        browser.get("/api/v1/session")
        response = browser.get(PREFIX + "/jobs/" + job["id"])
        assert response.status_code == 401
        headers_private(response)
    other_role = replace(READY, allowed_actor_ids=("different.budget.actor",))
    with TestClient(app_for(database, readiness=other_role)) as publisher:
        login(publisher)
        response = publisher.get(PREFIX + "/jobs/" + job["id"])
        assert response.status_code == 403
        assert response.json()["detail"] == "meta_probe_actor_forbidden"
        assert job["manifest_sha256"] not in response.text


def test_paid_flag_off_blocks_fresh_preview_but_leaves_inspection_and_cancel_available(database):
    with TestClient(app_for(database)) as client:
        headers, body = prepared(client)
        value = preview(client, headers, body)
        job = arm(client, headers, value).json()
        cookies = dict(client.cookies)
    with TestClient(app_for(database, probe=replace(PROBE, paid_probe_enabled=False))) as disabled:
        disabled.cookies.update(cookies)
        caps = disabled.get(PREFIX + "/capabilities").json()
        assert caps["verification_available"] is False and caps["inspection_available"] is True
        response = disabled.post(PREFIX + "/preview", headers=headers, json=body)
        assert response.status_code == 503
        assert disabled.get(PREFIX + "/jobs/" + job["id"]).status_code == 200
        assert (
            disabled.post(
                PREFIX + "/jobs/" + job["id"] + "/cancel", headers=headers, json={}
            ).status_code
            == 200
        )


def test_removed_budget_role_returns_distinct_loss_of_authority_for_private_reads(database):
    with TestClient(app_for(database)) as client:
        headers, body = prepared(client)
        job = arm(client, headers, preview(client, headers, body)).json()
        cookies = dict(client.cookies)
    with TestClient(app_for(database, readiness=replace(READY, allowed_actor_ids=()))) as removed:
        removed.cookies.update(cookies)
        response = removed.get(PREFIX + "/jobs/" + job["id"])
        assert response.status_code == 503
        assert response.json() == {"detail": "readiness_role_unavailable"}
        assert job["manifest_sha256"] not in response.text
        headers_private(response)


def test_control_bodies_cannot_forge_a_receipt_or_submit_usage_and_validation_never_echoes(
    database,
):
    with TestClient(app_for(database)) as client:
        headers, body = prepared(client)
        job = arm(client, headers, preview(client, headers, body)).json()
        response = client.post(
            PREFIX + "/jobs/" + job["id"] + "/finalize",
            headers=headers,
            json={"receipt": "SECRET-never-echo", "input_tokens": 0, "actual_microdollars": 0},
        )
        assert response.status_code == 422
        assert response.json() == {"detail": "meta_probe_invalid_request"}
        assert "SECRET" not in response.text
        assert count(database, MetaProbeReceipt) == 0
        headers_private(response)
        missing = client.get(PREFIX + "/receipts/" + str(uuid.uuid4()))
        assert missing.status_code == 404 and missing.json()["detail"] == "meta_probe_not_found"
        headers_private(missing)
