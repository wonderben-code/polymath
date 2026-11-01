"""Sign in or continue as a guest, against real PostgreSQL and fake providers.

No request reaches ORCID, GitHub or Google.
"""

import os
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_artifact_api import artifacts_api as artifacts_api_fixture
from test_run_api import bootstrap

from polymath.access_models import BrowserSession
from polymath.account_models import Account
from polymath.accounts import AccountPolicy, Provider, open_home, plain_name, seal_home
from polymath.accounts_api import create_accounts_router
from polymath.run_api import COOKIE_NAME, _hash_token, create_run_router

artifacts_api = artifacts_api_fixture
KEY = os.urandom(32)
SECRET = "synthetic-provider-secret-never-returned"
PROVIDER_TOKEN = "synthetic-provider-access-token"


def provider(name, token_url, userinfo=None, scope=""):
    return Provider(
        name,
        f"client-{name}",
        SecretStr(SECRET),
        f"https://{name}.test/authorize",
        token_url,
        scope,
        userinfo,
    )


def policy():
    return AccountPolicy(
        key=KEY,
        site_origin="https://polymath.test",
        providers={
            "orcid": provider("orcid", "https://orcid.test/oauth/token", scope="/authenticate"),
            "github": provider("github", "https://github.test/token", "https://github.test/user"),
            "google": provider(
                "google",
                "https://google.test/token",
                "https://google.test/userinfo",
                "openid profile",
            ),
        },
    )


# Pure checks -------------------------------------------------------------


def test_names_become_short_plain_text():
    assert plain_name("  Ada\u0000 <b>Lovelace</b> https://evil.test ") == "Ada Lovelace"
    assert plain_name("x" * 300) == "x" * 120
    assert plain_name(None) is None and plain_name("<i></i>") is None


def test_the_home_session_is_sealed_to_its_account():
    sealed = seal_home(KEY, "account-a", "t" * 43)
    assert b"t" * 43 not in sealed
    assert open_home(KEY, "account-a", sealed) == "t" * 43
    assert open_home(KEY, "account-b", sealed) is None
    assert open_home(os.urandom(32), "account-a", sealed) is None


def test_a_provider_is_offered_only_with_a_key_and_the_site_origin():
    assert policy().available == ("orcid", "github", "google")
    assert AccountPolicy().available == ()
    assert AccountPolicy(providers=policy().providers).available == ()


# A fake set of providers --------------------------------------------------


class Providers:
    def __init__(self):
        self.identity = {"github": {"id": 4242, "login": "ada", "name": "Ada <b>Lovelace</b>"}}
        self.forms = []
        self.refuse = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            form = parse_qs(request.content.decode())
            self.forms.append(form)
            if self.refuse:
                return httpx.Response(400, json={"error": "bad_verification_code"})
            body = {"access_token": PROVIDER_TOKEN, "token_type": "bearer"}
            if request.url.host == "orcid.test":
                body |= {"orcid": "0000-0002-1825-0097", "name": "Josiah Carberry"}
            return httpx.Response(200, json=body)
        assert request.headers["authorization"] == f"Bearer {PROVIDER_TOKEN}"
        host = request.url.host.split(".")[0]
        return httpx.Response(200, json=self.identity.get(host, {"sub": "g-77", "name": "Grace"}))


@pytest.fixture
def accounts(artifacts_api):
    engine, _ = artifacts_api
    fake = Providers()
    app = FastAPI()
    app.include_router(create_run_router(engine, lambda: True))
    app.include_router(
        create_accounts_router(engine, policy(), transport=httpx.MockTransport(fake.handler))
    )

    opened = []

    def device():
        client = TestClient(app)
        client.__enter__()
        opened.append(client)
        return client, bootstrap(client)

    yield engine, app, fake, device
    for client in opened:
        client.__exit__(None, None, None)


def sign_in(client, headers, name="github"):
    started = client.post("/api/v1/account/sign-in", headers=headers, json={"provider": name})
    assert started.status_code == 200, started.text
    query = parse_qs(urlsplit(started.json()["authorize_url"]).query)
    assert query["redirect_uri"] == ["https://polymath.test/account/callback"]
    return client.post(
        "/api/v1/account/complete",
        headers=headers,
        json={"code": "synthetic-code", "state": query["state"][0]},
    )


def expiry(engine, token):
    with Session(engine) as session:
        row = session.get(BrowserSession, _hash_token(token))
        now = session.scalar(select(func.clock_timestamp()))
        return row.expires_at - now


@pytest.mark.integration
def test_a_guest_signs_in_and_their_work_simply_carries_on(accounts):
    engine, _, fake, device = accounts
    client, headers = device()
    guest = client.cookies[COOKIE_NAME]
    assert client.get("/api/v1/account").json()["signed_in"] is False
    done = sign_in(client, headers)
    assert done.status_code == 200 and done.json()["result"] == "created"
    assert client.cookies[COOKIE_NAME] == guest
    status = client.get("/api/v1/account")
    assert status.json()["account"] == {
        "display_name": "Ada Lovelace",
        "provider": "github",
        "orcid": None,
    }
    assert PROVIDER_TOKEN not in status.text and SECRET not in status.text
    assert expiry(engine, guest) > timedelta(days=360)
    with Session(engine) as session:
        account = session.scalar(select(Account))
        assert account.home_session_hash == _hash_token(guest)
        assert guest.encode() not in account.home_ciphertext
    assert fake.forms[0]["redirect_uri"] == ["https://polymath.test/account/callback"]


@pytest.mark.integration
def test_another_device_opens_the_same_work_and_gets_its_own_back_on_sign_out(accounts):
    engine, _, _, device = accounts
    first, first_headers = device()
    sign_in(first, first_headers)
    home = first.cookies[COOKIE_NAME]
    second, second_headers = device()
    own = second.cookies[COOKIE_NAME]
    done = sign_in(second, second_headers)
    assert done.json()["result"] == "switched"
    assert second.cookies[COOKIE_NAME] == home
    assert second.get("/api/v1/account").json()["signed_in"] is True
    headers = bootstrap(second)
    out = second.post("/api/v1/account/sign-out", headers=headers)
    assert out.json() == {"signed_out": True, "guest_work_restored": True}
    assert second.cookies[COOKIE_NAME] == own
    assert second.get("/api/v1/account").json()["signed_in"] is False
    # The first device is still signed in; signing out there starts afresh.
    out = first.post("/api/v1/account/sign-out", headers=first_headers)
    assert out.json() == {"signed_out": True, "guest_work_restored": False}
    assert COOKIE_NAME not in first.cookies
    with Session(engine) as session:
        assert session.get(BrowserSession, _hash_token(home)) is not None


@pytest.mark.integration
def test_the_sign_in_link_is_one_time_and_bound_to_its_browser(accounts):
    _, _, _, device = accounts
    first, first_headers = device()
    second, second_headers = device()
    started = first.post(
        "/api/v1/account/sign-in", headers=first_headers, json={"provider": "github"}
    )
    state = parse_qs(urlsplit(started.json()["authorize_url"]).query)["state"][0]
    stolen = second.post(
        "/api/v1/account/complete",
        headers=second_headers,
        json={"code": "synthetic-code", "state": state},
    )
    assert stolen.status_code == 400 and stolen.json()["detail"] == "account_state_invalid"
    again = first.post(
        "/api/v1/account/complete",
        headers=first_headers,
        json={"code": "synthetic-code", "state": state},
    )
    # A stolen link is refused without using it up; the rightful browser finishes.
    assert again.status_code == 200
    reused = first.post(
        "/api/v1/account/complete",
        headers=first_headers,
        json={"code": "synthetic-code", "state": state},
    )
    assert reused.status_code == 400 and reused.json()["detail"] == "account_state_invalid"


@pytest.mark.integration
def test_orcid_fills_the_researchers_identifier_and_google_uses_its_subject(accounts):
    engine, _, _, device = accounts
    client, headers = device()
    sign_in(client, headers, "orcid")
    assert client.get("/api/v1/account").json()["account"] == {
        "display_name": "Josiah Carberry",
        "provider": "orcid",
        "orcid": "0000-0002-1825-0097",
    }
    other, other_headers = device()
    sign_in(other, other_headers, "google")
    with Session(engine) as session:
        subjects = sorted(session.scalars(select(Account.subject)))
    assert subjects == ["0000-0002-1825-0097", "g-77"]


@pytest.mark.integration
def test_a_browser_holding_one_account_must_sign_out_before_another(accounts):
    _, _, _, device = accounts
    client, headers = device()
    sign_in(client, headers, "github")
    refused = sign_in(client, headers, "google")
    assert refused.status_code == 409 and refused.json()["detail"] == "account_sign_out_first"


@pytest.mark.integration
def test_a_refused_sign_in_changes_nothing(accounts):
    engine, _, fake, device = accounts
    client, headers = device()
    guest = client.cookies[COOKIE_NAME]
    fake.refuse = True
    refused = sign_in(client, headers)
    assert refused.status_code == 400 and refused.json()["detail"] == "account_sign_in_refused"
    assert client.cookies[COOKIE_NAME] == guest
    with Session(engine) as session:
        assert session.scalar(select(Account)) is None


@pytest.mark.integration
def test_forgetting_the_account_keeps_this_browsers_work_as_a_guest(accounts):
    engine, _, _, device = accounts
    client, headers = device()
    sign_in(client, headers)
    token = client.cookies[COOKIE_NAME]
    gone = client.post("/api/v1/account/delete", headers=headers)
    assert gone.status_code == 200 and gone.json()["signed_in"] is False
    assert expiry(engine, token) <= timedelta(days=7)
    with Session(engine) as session:
        assert session.scalar(select(Account)) is None


@pytest.mark.integration
def test_writes_need_csrf(accounts):
    _, _, _, device = accounts
    client, _ = device()
    assert client.post("/api/v1/account/sign-in", json={"provider": "github"}).status_code == 403
    assert client.post("/api/v1/account/sign-out").status_code == 403


def test_without_accounts_the_site_says_so_without_a_session():
    class Engine:
        dialect = type("Dialect", (), {"name": "postgresql"})()

    app = FastAPI()
    app.include_router(create_accounts_router(Engine(), AccountPolicy()))
    with TestClient(app) as client:
        assert client.get("/api/v1/account").json() == {
            "available": False,
            "providers": [],
            "signed_in": False,
            "account": None,
        }
