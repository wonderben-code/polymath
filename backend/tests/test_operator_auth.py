"""Real PostgreSQL human-role checks with explicit local fixture credentials."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_run_api import bootstrap
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.operator_auth import (
    OPERATOR_COOKIE_NAME,
    OperatorPolicy,
    create_operator_router,
    require_operator,
)
from polymath.operator_models import OperatorSession
from polymath.run_api import COOKIE_NAME, create_run_router

database = database_fixture
pytestmark = pytest.mark.integration
TOKEN = "Synthetic_fixture_only_0123456789_ABCDEFGHIJKLMNOPQRSTUVWXYZ"
OTHER_TOKEN = "Different_fixture_only_9876543210_abcdefghijklmnopqrstuvwxyz"
POLICY = OperatorPolicy(
    bootstrap_token=SecretStr(TOKEN), actor_id="fixture-publisher", display_name="Fixture Publisher"
)


@pytest.fixture
def operators(database):
    with ExitStack() as clients:

        def factory(policy=POLICY, *, base_url="http://testserver"):
            app = FastAPI()
            app.include_router(create_run_router(database))
            app.include_router(create_operator_router(database, policy))

            @app.post("/api/v1/operator-test")
            def privileged(request: Request):
                with Session(database) as session, session.begin():
                    operator = require_operator(session, request, policy=policy)
                    return {"actor_id": operator.actor_id, "display_name": operator.display_name}

            @app.get("/api/v1/operator-test-read")
            def read_role(request: Request):
                with Session(database) as session:
                    operator = require_operator(session, request, write=False, policy=policy)
                    return {"actor_id": operator.actor_id}

            @app.post("/api/v1/operator-test-no-policy")
            def no_policy(request: Request):
                with Session(database) as session:
                    require_operator(session, request)
                return {"unexpected": True}

            return clients.enter_context(TestClient(app, base_url=base_url))

        yield database, factory


def login(client, *, token=TOKEN):
    browser = bootstrap(client)
    response = client.post("/api/v1/operator/session", headers=browser, json={"token": token})
    assert response.status_code == 200
    return {**browser, "X-Operator-CSRF-Token": response.json()["csrf_token"]}, response


def cookie_headers(browser, operator):
    return {"Cookie": f"{COOKIE_NAME}={browser}; {OPERATOR_COOKIE_NAME}={operator}"}


def test_disabled_policy_and_environment_credential_alone_grant_nothing(operators, monkeypatch):
    engine, factory = operators
    monkeypatch.setenv("OPERATOR_BOOTSTRAP_TOKEN", TOKEN)
    for policy in (None, OperatorPolicy()):
        client = factory(policy)
        assert client.get("/api/v1/operator/session").json() == {
            "configured": False,
            "authenticated": False,
            "actor_id": None,
            "display_name": None,
            "expires_at": None,
            "csrf_token": None,
        }
        response = client.post(
            "/api/v1/operator/session", headers=bootstrap(client), json={"token": TOKEN}
        )
        assert response.status_code == 503
        assert response.json() == {"detail": "operator_unavailable"}
        assert response.headers["cache-control"] == "no-store"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(OperatorSession)) == 0


def test_login_hashes_cookie_and_never_stores_or_returns_bootstrap_secret(operators):
    engine, factory = operators
    client = factory()
    headers, response = login(client)
    operator = client.cookies.get(OPERATOR_COOKIE_NAME)
    browser = client.cookies.get(COOKIE_NAME)
    assert operator not in (TOKEN, browser)
    assert TOKEN not in response.text and TOKEN not in repr(POLICY)
    assert operator not in response.text and browser not in response.text
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert "Path=/api/v1" in response.headers["set-cookie"]
    assert "Secure" not in response.headers["set-cookie"]
    assert response.json()["actor_id"] == "fixture-publisher"
    assert response.json()["display_name"] == "Fixture Publisher"
    assert headers["X-Operator-CSRF-Token"] != headers["X-CSRF-Token"]
    assert client.get("/api/v1/operator/session").json() == response.json()
    assert client.post("/api/v1/operator-test", headers=headers).status_code == 200
    with Session(engine) as session:
        row = session.scalar(select(OperatorSession))
        assert (
            row.token_hash == hashlib.sha256(("operator-session:" + operator).encode()).hexdigest()
        )
        assert row.browser_session_hash == hashlib.sha256(browser.encode()).hexdigest()
        assert row.credential_fingerprint == POLICY.credential_fingerprint
        assert row.actor_id == "fixture-publisher" and row.revoked_at is None
        assert TOKEN not in str(row.__dict__) and operator not in str(row.__dict__)


def test_https_role_cookie_is_secure(operators):
    _, factory = operators
    client = factory(base_url="https://testserver")
    headers, response = login(client)
    assert "Secure" in response.headers["set-cookie"]
    assert client.post("/api/v1/operator-test", headers=headers).status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"X-CSRF-Token": "wrong"},
        {"Origin": "https://foreign.example"},
        {"Origin": "http://[invalid"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_login_requires_browser_csrf_and_origin(operators, change):
    engine, factory = operators
    client = factory()
    response = client.post(
        "/api/v1/operator/session", headers={**bootstrap(client), **change}, json={"token": TOKEN}
    )
    assert response.status_code == 403
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(OperatorSession)) == 0


def test_missing_or_expired_browser_cannot_login(operators):
    engine, factory = operators
    client = factory()
    assert client.get("/api/v1/operator/session").json()["authenticated"] is False
    assert client.post("/api/v1/operator/session", json={"token": TOKEN}).status_code == 403
    headers = bootstrap(client)
    with engine.begin() as connection:
        connection.execute(
            update(BrowserSession).values(expires_at=datetime.now(UTC) - timedelta(1))
        )
    assert (
        client.post("/api/v1/operator/session", headers=headers, json={"token": TOKEN}).status_code
        == 401
    )


@pytest.mark.parametrize("token", [OTHER_TOKEN, "wrong", TOKEN + " ", "\ud800"])
def test_invalid_credential_is_sanitized_and_creates_no_role(operators, token):
    engine, factory = operators
    client = factory()
    response = client.post(
        "/api/v1/operator/session",
        headers={**bootstrap(client), "Content-Type": "application/json"},
        content=json.dumps({"token": token}).encode("ascii"),
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_operator_credential"}
    assert response.headers["cache-control"] == "no-store"
    assert "Set-Cookie" not in response.headers
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(OperatorSession)) == 0


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"token": True},
        {"token": "x" * 257},
        {"token": TOKEN, "actor_id": "forged-actor"},
    ],
)
def test_validation_never_echoes_secret_or_client_identity(operators, body):
    _, factory = operators
    client = factory()
    response = client.post("/api/v1/operator/session", headers=bootstrap(client), json=body)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert TOKEN not in response.text and "forged-actor" not in response.text
    assert "x" * 257 not in response.text


def test_login_compares_the_secret_with_constant_time_helper(operators, monkeypatch):
    import polymath.operator_auth as auth

    _, factory = operators
    client = factory()
    original, compared = auth.hmac.compare_digest, []

    def compare(left, right):
        compared.append((left, right))
        return original(left, right)

    monkeypatch.setattr(auth.hmac, "compare_digest", compare)
    login(client)
    assert (TOKEN.encode(), TOKEN.encode()) in compared


@pytest.mark.parametrize(
    "change",
    [
        {"X-Operator-CSRF-Token": "wrong"},
        {"X-Operator-CSRF-Token": ""},
        {"X-CSRF-Token": "wrong"},
        {"Origin": "https://foreign.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_privileged_writes_and_logout_need_both_csrf_domains(operators, change):
    engine, factory = operators
    client = factory()
    headers, _ = login(client)
    for route in ("/api/v1/operator-test", "/api/v1/operator/logout"):
        response = client.post(route, headers={**headers, **change})
        assert response.status_code == 403
    with Session(engine) as session:
        assert session.scalar(select(OperatorSession)).revoked_at is None


def test_role_cookie_cannot_transfer_to_another_browser(operators):
    _, factory = operators
    owner, other = factory(), factory()
    headers, _ = login(owner)
    other_headers = bootstrap(other)
    transferred = {
        **cookie_headers(other.cookies.get(COOKIE_NAME), owner.cookies.get(OPERATOR_COOKIE_NAME)),
        **other_headers,
        "X-Operator-CSRF-Token": headers["X-Operator-CSRF-Token"],
    }
    snapshot = other.get("/api/v1/operator/session", headers=transferred)
    assert snapshot.json()["authenticated"] is False and snapshot.json()["actor_id"] is None
    assert "Max-Age=0" in snapshot.headers["set-cookie"]
    assert other.post("/api/v1/operator-test", headers=transferred).status_code == 401
    assert owner.post("/api/v1/operator-test", headers=headers).status_code == 200


@pytest.mark.parametrize("expired", ["operator", "browser"])
def test_expiry_denies_role_reads_writes_and_session_snapshot(operators, expired):
    engine, factory = operators
    client = factory()
    headers, _ = login(client)
    with engine.begin() as connection:
        table = OperatorSession if expired == "operator" else BrowserSession
        connection.execute(
            update(table).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert client.get("/api/v1/operator-test-read").status_code == 401
    assert client.post("/api/v1/operator-test", headers=headers).status_code == 401
    snapshot = client.get("/api/v1/operator/session")
    assert snapshot.json()["authenticated"] is False and snapshot.json()["csrf_token"] is None
    assert "Max-Age=0" in snapshot.headers["set-cookie"]


@pytest.mark.parametrize(
    "replacement",
    [
        None,
        OperatorPolicy(),
        replace(POLICY, bootstrap_token=SecretStr(OTHER_TOKEN)),
        replace(POLICY, actor_id="different-publisher"),
        replace(POLICY, display_name="Different"),
        replace(POLICY, session_seconds=600),
    ],
)
def test_current_policy_removal_or_rotation_invalidates_existing_roles(operators, replacement):
    _, factory = operators
    original = factory()
    headers, _ = login(original)
    changed = factory(replacement)
    cookies = cookie_headers(
        original.cookies.get(COOKIE_NAME), original.cookies.get(OPERATOR_COOKIE_NAME)
    )
    assert changed.get("/api/v1/operator/session", headers=cookies).json()["authenticated"] is False
    result = changed.post("/api/v1/operator-test", headers={**cookies, **headers})
    assert result.status_code == (503 if replacement is None or not replacement.configured else 401)


def test_helper_default_policy_does_not_trust_previously_authenticated_cookie(operators):
    _, factory = operators
    client = factory()
    headers, _ = login(client)
    assert client.post("/api/v1/operator-test-no-policy", headers=headers).status_code == 503


def test_logout_revokes_role_but_preserves_browser_session(operators):
    engine, factory = operators
    client = factory()
    headers, _ = login(client)
    browser = client.cookies.get(COOKIE_NAME)
    old = cookie_headers(browser, client.cookies.get(OPERATOR_COOKIE_NAME))
    response = client.post("/api/v1/operator/logout", headers=headers)
    assert response.status_code == 200 and response.json()["authenticated"] is False
    assert response.json()["csrf_token"] is None and response.json()["configured"] is True
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.cookies.get(COOKIE_NAME) == browser
    assert client.post("/api/v1/operator-test", headers={**old, **headers}).status_code == 401
    with Session(engine) as session:
        assert session.scalar(select(OperatorSession)).revoked_at is not None
        assert session.scalar(select(func.count()).select_from(BrowserSession)) == 1


def test_reauthentication_rotates_cookie_and_revokes_prior_role(operators):
    engine, factory = operators
    client = factory()
    previous_headers, _ = login(client)
    previous = client.cookies.get(OPERATOR_COOKIE_NAME)
    cookies = cookie_headers(client.cookies.get(COOKIE_NAME), previous)
    headers, _ = login(client)
    assert client.cookies.get(OPERATOR_COOKIE_NAME) != previous
    assert client.post("/api/v1/operator-test", headers=headers).status_code == 200
    assert (
        client.post("/api/v1/operator-test", headers={**cookies, **previous_headers}).status_code
        == 401
    )
    with Session(engine) as session:
        rows = session.scalars(select(OperatorSession)).all()
        assert len(rows) == 2 and sum(row.revoked_at is None for row in rows) == 1


def test_operator_expiry_is_capped_by_browser_expiry(operators):
    engine, factory = operators
    client = factory()
    headers = bootstrap(client)
    expires = datetime.now(UTC) + timedelta(seconds=20)
    with engine.begin() as connection:
        connection.execute(update(BrowserSession).values(expires_at=expires))
    response = client.post("/api/v1/operator/session", headers=headers, json={"token": TOKEN})
    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["expires_at"]) == expires


@pytest.mark.parametrize("action", ["login", "privileged_write"])
def test_browser_expiring_during_authentication_lock_wait_cannot_grant_role(
    operators, monkeypatch, action
):
    import polymath.operator_auth as auth

    engine, factory = operators
    client = factory()
    headers = bootstrap(client) if action == "login" else login(client)[0]
    authenticated = Event()
    original = auth._authenticated

    def watched(session, request):
        browser = original(session, request)
        authenticated.set()
        return browser

    monkeypatch.setattr(auth, "_authenticated", watched)
    locked = BrowserSession if action == "login" else OperatorSession
    with ThreadPoolExecutor(max_workers=1) as executor:
        with engine.begin() as blocker:
            blocker.execute(select(locked).with_for_update())
            if action == "login":
                future = executor.submit(
                    client.post, "/api/v1/operator/session", headers=headers, json={"token": TOKEN}
                )
            else:
                future = executor.submit(client.post, "/api/v1/operator-test", headers=headers)
            assert authenticated.wait(timeout=5)
            blocker.execute(
                update(BrowserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        response = future.result(timeout=10)
    assert response.status_code == 401
    assert response.json() == {"detail": "research_session_required"}
    if action == "login":
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(OperatorSession)) == 0


def test_storage_failure_is_sanitized_without_cookie(operators, monkeypatch):
    import polymath.operator_auth as auth

    _, factory = operators
    client = factory()
    headers = bootstrap(client)

    def fail(*args, **kwargs):
        raise SQLAlchemyError("private-database-fixture")

    monkeypatch.setattr(auth, "_now", fail)
    response = client.post("/api/v1/operator/session", headers=headers, json={"token": TOKEN})
    assert response.status_code == 503
    assert response.json() == {"detail": "operator_storage_unavailable"}
    assert "private-database" not in response.text and "Set-Cookie" not in response.headers


@pytest.mark.parametrize(
    "change",
    [
        {"bootstrap_token": "not-wrapped"},
        {"bootstrap_token": SecretStr("short")},
        {"bootstrap_token": SecretStr("a" * 64)},
        {"bootstrap_token": SecretStr(TOKEN + " bad")},
        {"actor_id": "actor\nforged"},
        {"display_name": "Name\nforged"},
        {"display_name": "\ud800"},
        {"session_seconds": True},
        {"session_seconds": 0},
        {"session_seconds": 3601},
    ],
)
def test_policy_rejects_weak_or_unbounded_configuration(change):
    with pytest.raises(ValueError):
        replace(POLICY, **change)


def test_downgrade_does_not_discard_authenticated_audit_history(operators):
    _, factory = operators
    login(factory())
    with pytest.raises(Exception, match="Cannot discard saved operator authentication sessions"):
        command.downgrade(Config("backend/alembic.ini"), "0010_artifact_jobs")
