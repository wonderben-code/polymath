"""Actual operator/ledger HTTP preparation with synthetic, unverified Meta attestations."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_operator_auth import OTHER_TOKEN, POLICY, TOKEN, login
from test_run_api import bootstrap
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.budget import BudgetService
from polymath.budget_admin_api import create_budget_admin_router
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.jobs import JobRepository
from polymath.meta_readiness import MetaReadinessStore
from polymath.meta_readiness_contracts import ReadinessPolicy
from polymath.models import ResearchRun
from polymath.operator_auth import OPERATOR_COOKIE_NAME, create_operator_router
from polymath.readiness_models import MetaReadinessAudit, MetaReadinessProposalRecord
from polymath.run_api import COOKIE_NAME, create_run_router

database = database_fixture
pytestmark = pytest.mark.integration
PREFIX = "/api/v1/budget"
ADMIN = replace(POLICY, actor_id="fixture-budget-admin", display_name="Fixture Budget Admin")
PREPARATION = ReadinessPolicy(preparation_enabled=True, allowed_actor_ids=(ADMIN.actor_id,))


@pytest.fixture
def budget_admin(database):
    with ExitStack() as clients:

        def factory(policy=None, *, operator_policy=ADMIN):
            app = FastAPI()
            app.include_router(create_run_router(database))
            app.include_router(create_operator_router(database, operator_policy))
            app.include_router(
                create_budget_admin_router(
                    database, readiness_policy=policy, operator_policy=operator_policy
                )
            )
            return clients.enter_context(TestClient(app))

        yield database, factory


def proposal_data(account_id="synthetic-meta-account"):
    now = datetime.now(UTC)

    def evidence(name):
        return [
            {"url": f"https://developers.meta.com/synthetic/{name}", "content_sha256": "a" * 64}
        ]

    return {
        "protocol": "polymath-meta-readiness-v1",
        "model_id": "synthetic-unverified-meta-model",
        "endpoint": "https://api.meta.ai/v1/chat/completions",
        "currency": "USD",
        "usage_contract": "prompt_completion_inclusive_v1",
        "observed_at": (now - timedelta(minutes=1)).isoformat(),
        "valid_until": (now + timedelta(hours=1)).isoformat(),
        "billable_bounds": {
            "input_tokens_absolute": 300000,
            "output_tokens_absolute": 4096,
            "evidence": evidence("absolute-bounds"),
        },
        "pricing": {
            "input_microdollars_per_million": 1000000,
            "output_microdollars_per_million": 2000000,
            "evidence": evidence("pricing"),
        },
        "usage_evidence": evidence("wire-and-inclusive-billing"),
        "application_limits": {
            "max_output_tokens": 4096,
            "max_context_characters": 90000,
            "max_model_calls": 12,
            "request_timeout_seconds": 40.0,
        },
        "account": {
            "account_id": account_id,
            "hard_cap_microdollars": 150000000,
            "daily_cap_microdollars": 10000000,
            "run_cap_microdollars": 5000000,
            "max_in_flight": 1,
        },
        "native_status": "not_run",
        "execution_available": False,
    }


def count(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def assert_private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert "set-cookie" not in response.headers


def preview(client, body=None, *, headers=None):
    if headers is None:
        headers, _ = login(client)
    return client.post(
        PREFIX + "/readiness/preview", headers=headers, json={"proposal": body or proposal_data()}
    )


def saved(client, data=None, *, headers=None):
    if headers is None:
        headers, _ = login(client)
    data = data or proposal_data()
    result = preview(client, data, headers=headers)
    assert result.status_code == 200, result.text
    response = client.post(
        PREFIX + "/readiness",
        headers=headers,
        json={"proposal": data, "expected_proposal_sha256": result.json()["proposal_sha256"]},
    )
    assert response.status_code == 201, response.text
    return headers, result.json(), response.json()


def legacy_account(engine, account_id="synthetic-meta-account", *, with_holds=False):
    service = BudgetService(engine)
    service.create_account(
        account_id, hard_cap_microdollars=150000000, daily_cap_microdollars=10000000
    )
    service.authorize_execution(account_id, pricing_reference="synthetic-fixture-only-not-native")
    if with_holds:
        run = JobRepository(engine).create(mode="explore", goal="Synthetic prior budget ledger")
        service.configure_run(account_id, run.id, cap_microdollars=5000000, max_calls=10)
        first = service.reserve(
            account_id, run.id, call_key="synthetic-known", estimated_microdollars=400000
        )
        service.claim_dispatch(first.id)
        service.settle(
            first.id,
            settlement_key="synthetic-settlement",
            actual_microdollars=250000,
            prompt_tokens=10,
            completion_tokens=5,
        )
        unknown = service.reserve(
            account_id, run.id, call_key="synthetic-unknown", estimated_microdollars=300000
        )
        service.claim_dispatch(unknown.id)
        service.mark_unknown(unknown.id, reason="timeout")
        with Session(engine) as session, session.begin():
            row = session.get(BudgetAccount, account_id)
            row.frozen, row.frozen_reason = True, "reconciliation_conflict"
    return service


def test_capabilities_default_has_no_db_session_or_actor_list_leak(budget_admin, monkeypatch):
    engine, factory = budget_admin
    client = factory()
    monkeypatch.setattr(
        engine, "connect", lambda *args, **kwargs: pytest.fail("capabilities queried DB")
    )
    response = client.get(PREFIX + "/capabilities")
    assert response.json() == {
        "preparation_available": False,
        "inspection_available": False,
        "disable_available": False,
        "native_status": "not_run",
        "execution_available": False,
        "reason": "readiness_unavailable",
    }
    assert ADMIN.actor_id not in response.text and TOKEN not in response.text
    assert_private(response)
    assert not client.cookies


@pytest.mark.parametrize("enabled", [False, True])
def test_allowed_role_configuration_only_controls_local_preparation(budget_admin, enabled):
    _, factory = budget_admin
    client = factory(replace(PREPARATION, preparation_enabled=enabled))
    result = client.get(PREFIX + "/capabilities").json()
    assert result["inspection_available"] and result["disable_available"]
    assert result["preparation_available"] is enabled and result["execution_available"] is False
    assert result["reason"] == (None if enabled else "preparation_disabled")
    assert not client.cookies


def test_publisher_cookie_and_model_key_alone_grant_no_budget_authority(budget_admin, monkeypatch):
    engine, factory = budget_admin
    monkeypatch.setenv("META_API_KEY", "SYNTHETIC-SECRET-ENV-KEY")
    publisher = factory(PREPARATION, operator_policy=POLICY)
    headers, _ = login(publisher)
    response = publisher.get(PREFIX + "/accounts")
    assert response.status_code == 403 and response.json() == {
        "detail": "readiness_actor_forbidden"
    }
    assert preview(publisher, headers=headers).status_code == 403
    assert count(engine, BudgetAccount) == count(engine, MetaReadinessProposalRecord) == 0
    unconfigured = factory()
    login(unconfigured)
    assert unconfigured.get(PREFIX + "/accounts").status_code == 503


def test_live_browser_and_current_operator_are_required_even_for_reads(budget_admin):
    _, factory = budget_admin
    client = factory(PREPARATION)
    assert client.get(PREFIX + "/accounts").status_code == 401
    browser_headers = bootstrap(client)
    missing = client.get(PREFIX + "/accounts")
    assert missing.status_code == 401 and missing.json() == {"detail": "operator_session_required"}
    assert (
        client.post(
            PREFIX + "/readiness/preview",
            headers=browser_headers,
            json={"proposal": proposal_data()},
        ).status_code
        == 401
    )
    assert_private(missing)


def test_exact_preview_save_idempotence_and_disabled_unverified_account(budget_admin):
    engine, factory = budget_admin
    client = factory(PREPARATION)
    headers, _ = login(client)
    data = proposal_data()
    response = preview(client, data, headers=headers)
    assert response.status_code == 200, response.text
    quoted = response.json()
    assert quoted["input_reservation_microdollars"] == 300000
    assert quoted["output_reservation_microdollars"] == 8192
    assert quoted["per_call_reservation_microdollars"] == 308192
    assert quoted["max_run_reservation_microdollars"] == 3698304
    assert quoted["full_run_reservation_fits"] and quoted["budget_limited_stop_notice"] is None
    assert quoted["native_status"] == "not_run" and quoted["execution_available"] is False
    assert count(engine, BudgetAccount) == count(engine, MetaReadinessProposalRecord) == 0
    body = {"proposal": data, "expected_proposal_sha256": quoted["proposal_sha256"]}
    created = client.post(PREFIX + "/readiness", headers=headers, json=body)
    assert created.status_code == 201, created.text
    assert_private(created)
    assert client.post(PREFIX + "/readiness", headers=headers, json=body).json() == created.json()
    record = created.json()
    assert record["actor_id"] == ADMIN.actor_id and record["temporal_status"] == "current"
    assert record["quote"] == quoted and record["proposal_sha256"] == quoted["proposal_sha256"]
    inspected = client.get(PREFIX + "/accounts/" + data["account"]["account_id"])
    assert inspected.status_code == 200
    account = inspected.json()
    assert (
        account["kill_switch"]
        and not account["execution_enabled"]
        and not account["pricing_verified"]
    )
    assert account["pricing_reference"] is None and not account["frozen"]
    assert account["native_status"] == "not_run" and account["execution_available"] is False
    assert account["usage"]["reservations"] == 0
    assert count(engine, MetaReadinessProposalRecord) == 1 and count(engine, BudgetReservation) == 0
    assert count(engine, ResearchRun) == 0
    for secret in (
        TOKEN,
        client.cookies.get(COOKIE_NAME),
        client.cookies.get(OPERATOR_COOKIE_NAME),
        headers["X-CSRF-Token"],
        headers["X-Operator-CSRF-Token"],
    ):
        assert secret not in created.text + inspected.text


@pytest.mark.parametrize("action", ["preview", "save", "disable"])
@pytest.mark.parametrize("failure", ["browser_csrf", "operator_csrf", "origin", "cross_site"])
def test_all_post_routes_require_both_csrf_tokens_and_origin(budget_admin, action, failure):
    engine, factory = budget_admin
    client = factory(PREPARATION)
    headers, quoted, record = saved(client)
    changed = dict(headers)
    if failure == "browser_csrf":
        changed.pop("X-CSRF-Token")
    elif failure == "operator_csrf":
        changed.pop("X-Operator-CSRF-Token")
    elif failure == "origin":
        changed["Origin"] = "https://foreign.invalid"
    else:
        changed["Sec-Fetch-Site"] = "cross-site"
    if action == "disable":
        path, body = f"{PREFIX}/accounts/{record['account_id']}/disable", {}
    else:
        path = PREFIX + ("/readiness/preview" if action == "preview" else "/readiness")
        body = {
            "proposal": quoted["proposal"],
            **({"expected_proposal_sha256": quoted["proposal_sha256"]} if action == "save" else {}),
        }
    response = client.post(path, headers=changed, json=body)
    assert response.status_code == 403
    assert_private(response)
    assert count(engine, MetaReadinessProposalRecord) == 1


@pytest.mark.parametrize(
    "field",
    [
        "api_key",
        "pricing_verified",
        "execution_available",
        "native_status",
        "float_rate",
        "bool_bound",
        "endpoint",
    ],
)
def test_proposal_validation_rejects_secrets_authority_and_nonexact_facts(budget_admin, field):
    engine, factory = budget_admin
    client = factory(PREPARATION)
    headers, _ = login(client)
    data = proposal_data()
    if field == "api_key":
        data["api_key"] = TOKEN
    elif field == "pricing_verified":
        data["pricing_verified"] = True
    elif field == "execution_available":
        data["execution_available"] = True
    elif field == "native_status":
        data["native_status"] = "verified"
    elif field == "float_rate":
        data["pricing"]["input_microdollars_per_million"] = 1.5
    elif field == "bool_bound":
        data["billable_bounds"]["input_tokens_absolute"] = True
    else:
        data["endpoint"] = "https://wrong.example?api_key=" + TOKEN
    response = preview(client, data, headers=headers)
    assert response.status_code == 422 and response.json() == {
        "detail": "readiness_invalid_request"
    }
    assert TOKEN not in response.text
    assert_private(response)
    assert count(engine, BudgetAccount) == count(engine, MetaReadinessProposalRecord) == 0


def test_hash_mismatch_and_fake_disable_fields_cannot_change_account(budget_admin):
    engine, factory = budget_admin
    client = factory(PREPARATION)
    headers, quoted, record = saved(client)
    wrong = client.post(
        PREFIX + "/readiness",
        headers=headers,
        json={"proposal": quoted["proposal"], "expected_proposal_sha256": "0" * 64},
    )
    assert wrong.status_code == 409 and wrong.json() == {"detail": "readiness_proposal_changed"}
    disable = client.post(
        f"{PREFIX}/accounts/{record['account_id']}/disable",
        headers=headers,
        json={"execution_enabled": True, "actor_id": "forged-actor", "secret": TOKEN},
    )
    assert disable.status_code == 422 and TOKEN not in disable.text
    assert count(engine, MetaReadinessProposalRecord) == 1


def test_preparation_off_preserves_history_inspection_and_safe_disable(budget_admin):
    _, factory = budget_admin
    client = factory(PREPARATION)
    headers, quoted, record = saved(client)
    disabled = factory(replace(PREPARATION, preparation_enabled=False))
    disabled.cookies.update(client.cookies)
    for path in (
        PREFIX + "/readiness",
        PREFIX + "/readiness/" + record["id"],
        PREFIX + "/accounts",
        PREFIX + "/accounts/" + record["account_id"],
    ):
        response = disabled.get(path)
        assert response.status_code == 200, response.text
        assert_private(response)
    assert preview(disabled, quoted["proposal"], headers=headers).status_code == 503
    assert (
        disabled.post(
            PREFIX + "/readiness",
            headers=headers,
            json={
                "proposal": quoted["proposal"],
                "expected_proposal_sha256": quoted["proposal_sha256"],
            },
        ).status_code
        == 503
    )
    assert (
        disabled.post(
            f"{PREFIX}/accounts/{record['account_id']}/disable", headers=headers, json={}
        ).status_code
        == 200
    )


def test_safe_disable_preserves_real_counted_spend_unknown_holds_and_flags(budget_admin):
    engine, factory = budget_admin
    legacy_account(engine, with_holds=True)
    client = factory(replace(PREPARATION, preparation_enabled=False))
    headers, _ = login(client)
    before = client.get(PREFIX + "/accounts/synthetic-meta-account").json()
    assert before["execution_enabled"] and before["pricing_verified"] and before["frozen"]
    assert before["usage"]["counted_spend_microdollars"] == "250000"
    assert before["usage"]["open_hold_microdollars"] == "300000"
    assert before["usage"]["unknown_hold_microdollars"] == "300000"
    response = client.post(
        PREFIX + "/accounts/synthetic-meta-account/disable", headers=headers, json={}
    )
    assert response.status_code == 200, response.text
    after = response.json()
    assert not after["execution_enabled"] and after["kill_switch"]
    assert after["usage"] == before["usage"]
    for field in (
        "hard_cap_microdollars",
        "daily_cap_microdollars",
        "max_in_flight",
        "frozen",
        "frozen_reason",
        "pricing_verified",
        "pricing_reference",
    ):
        assert after[field] == before[field]
    with Session(engine) as session:
        assert set(session.scalars(select(BudgetReservation.status))) == {"settled", "unknown"}
    assert count(engine, BudgetReservation) == 2 and count(engine, ResearchRun) == 1
    assert count(engine, MetaReadinessProposalRecord) == 0


def test_existing_enabled_account_is_not_reconfigured_by_preparation(budget_admin):
    engine, factory = budget_admin
    legacy_account(engine)
    client = factory(PREPARATION)
    response = preview(client)
    assert response.status_code == 409 and response.json() == {
        "detail": "readiness_account_enabled"
    }
    with Session(engine) as session:
        account = session.get(BudgetAccount, "synthetic-meta-account")
        assert account.execution_enabled and account.pricing_verified and not account.kill_switch
    assert count(engine, MetaReadinessProposalRecord) == 0


def test_legacy_slash_account_is_addressable_without_creating_new_proposal(budget_admin):
    engine, factory = budget_admin
    legacy_account(engine, "legacy/team")
    client = factory(replace(PREPARATION, preparation_enabled=False))
    headers, _ = login(client)
    assert client.get(PREFIX + "/accounts/legacy%2Fteam").json()["account_id"] == "legacy/team"
    response = client.post(PREFIX + "/accounts/legacy%2Fteam/disable", headers=headers, json={})
    assert response.status_code == 200 and not response.json()["execution_enabled"]


def test_proposal_and_account_lists_are_bounded_and_paginated(budget_admin):
    _, factory = budget_admin
    client = factory(PREPARATION)
    headers, _ = login(client)
    ids = []
    for index in range(3):
        _, _, record = saved(client, proposal_data(f"synthetic-account-{index}"), headers=headers)
        ids.append(record["id"])
    proposals, accounts, proposal_cursor, account_cursor = [], [], None, None
    for _ in range(3):
        response = client.get(
            PREFIX + "/readiness",
            params={"limit": 1, **({"before": proposal_cursor} if proposal_cursor else {})},
        ).json()
        listed = client.get(
            PREFIX + "/accounts",
            params={"limit": 1, **({"before": account_cursor} if account_cursor else {})},
        ).json()
        proposals.extend(row["id"] for row in response["proposals"])
        accounts.extend(row["account_id"] for row in listed["accounts"])
        proposal_cursor, account_cursor = response["next_cursor"], listed["next_cursor"]
    assert set(proposals) == set(ids) and len(set(proposals)) == 3
    assert accounts == [f"synthetic-account-{index}" for index in range(3)]
    assert proposal_cursor is account_cursor is None
    assert (
        len(
            client.get(PREFIX + "/readiness", params={"account_id": accounts[1]}).json()[
                "proposals"
            ]
        )
        == 1
    )
    assert client.get(PREFIX + "/readiness", params={"limit": 21}).status_code == 422


def test_operator_revocation_rotation_and_browser_binding_deny_administration(budget_admin):
    _, factory = budget_admin
    client = factory(PREPARATION)
    headers, _, record = saved(client)
    foreign = factory(PREPARATION)
    bootstrap(foreign)
    foreign.cookies.set(OPERATOR_COOKIE_NAME, client.cookies.get(OPERATOR_COOKIE_NAME))
    assert foreign.get(PREFIX + "/readiness").status_code == 401
    rotated = factory(
        PREPARATION, operator_policy=replace(ADMIN, bootstrap_token=SecretStr(OTHER_TOKEN))
    )
    rotated.cookies.update(client.cookies)
    assert rotated.get(PREFIX + "/accounts").status_code == 401
    assert client.post("/api/v1/operator/logout", headers=headers).status_code == 200
    assert client.get(PREFIX + "/readiness/" + record["id"]).status_code == 401
    login(client)
    assert client.get(PREFIX + "/readiness/" + record["id"]).status_code == 200


def test_browser_expiry_after_account_lock_wait_denies_disable_without_mutation(budget_admin):
    engine, factory = budget_admin
    legacy_account(engine)
    client = factory(PREPARATION)
    headers, _ = login(client)
    with Session(engine) as session:
        browser_hash = session.scalar(select(BrowserSession.token_hash))
    waiting = Event()

    def blocked(connection, cursor, statement, parameters, context, executemany):
        if "budget_accounts" in statement and "FOR UPDATE" in statement:
            waiting.set()

    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            select(BudgetAccount)
            .where(BudgetAccount.id == "synthetic-meta-account")
            .with_for_update()
        )
        event.listen(engine, "before_cursor_execute", blocked)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    client.post,
                    PREFIX + "/accounts/synthetic-meta-account/disable",
                    headers=headers,
                    json={},
                )
                try:
                    assert waiting.wait(timeout=10)
                    with engine.begin() as expires:
                        expires.execute(
                            update(BrowserSession)
                            .where(BrowserSession.token_hash == browser_hash)
                            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
                        )
                finally:
                    transaction.rollback()
                response = future.result(timeout=20)
        finally:
            event.remove(engine, "before_cursor_execute", blocked)
    assert response.status_code == 401 and response.json() == {
        "detail": "research_session_required"
    }
    with Session(engine) as session:
        account = session.get(BudgetAccount, "synthetic-meta-account")
        assert account.execution_enabled and not account.kill_switch
    assert count(engine, MetaReadinessAudit) == 0


def test_no_activation_probe_reset_or_factory_surface_exists(budget_admin, monkeypatch):
    engine, factory = budget_admin
    client = factory(PREPARATION)
    headers, _ = login(client)

    def forbidden(*args, **kwargs):
        pytest.fail("Readiness called an activation/dispatch/factory path")

    monkeypatch.setattr(BudgetService, "authorize_execution", forbidden)
    monkeypatch.setattr(BudgetService, "configure_run", forbidden)
    monkeypatch.setattr(BudgetService, "reserve", forbidden)
    monkeypatch.setattr(JobRepository, "create", forbidden)
    _, _, record = saved(client, headers=headers)
    for action in ("activate", "probe", "reconcile", "reset", "enable"):
        response = client.post(
            f"{PREFIX}/accounts/{record['account_id']}/{action}", headers=headers, json={}
        )
        assert response.status_code in (404, 405)
    assert count(engine, BudgetReservation) == count(engine, ResearchRun) == 0


def test_storage_and_validation_errors_are_static_and_private(budget_admin, monkeypatch):
    _, factory = budget_admin
    client = factory(PREPARATION)
    login(client)

    def broken(*args, **kwargs):
        raise SQLAlchemyError("PRIVATE DATABASE/SECRET INPUT")

    monkeypatch.setattr(MetaReadinessStore, "inspect_account", broken)
    response = client.get(PREFIX + "/accounts/synthetic-meta-account")
    assert response.status_code == 503 and response.json() == {
        "detail": "readiness_storage_unavailable"
    }
    assert_private(response)
    assert "PRIVATE" not in response.text
    invalid = client.get(PREFIX + "/readiness/" + uuid.uuid4().hex)
    assert invalid.status_code == 422 and invalid.json() == {"detail": "readiness_invalid_request"}
