"""Connect Zenodo for every visitor, against real PostgreSQL and a mock Zenodo.

No request reaches sandbox.zenodo.org or zenodo.org; no real DOI is made.
"""

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from test_publication import artifacts_api as artifacts_api_fixture
from test_publication import publication as publication_fixture
from test_run_api import bootstrap

from polymath.access_models import BrowserSession
from polymath.artifact_models import GeneratedArtifact
from polymath.publication_contracts import PublicationMetadata
from polymath.run_api import create_run_router
from polymath.visitor_zenodo import (
    OAuthClient,
    VisitorZenodoError,
    VisitorZenodoPolicy,
    VisitorZenodoStore,
    parse_token_response,
    seal,
    unseal,
)
from polymath.visitor_zenodo_api import create_visitor_zenodo_router
from polymath.visitor_zenodo_models import ZenodoConnection, ZenodoOAuthState
from polymath.visitor_zenodo_worker import VisitorZenodoWorker
from polymath.zenodo import ZenodoClient

artifacts_api = artifacts_api_fixture
publication = publication_fixture
KEY = os.urandom(32)
OWNER = "a" * 64
ACCESS = "synthetic-visitor-access-token"
SECRET = "synthetic-client-secret-never-returned"
BUCKET = "c3333333-3333-4333-8333-333333333333"
METADATA = {
    "title": "A synthetic shared structure",
    "description": "Synthetic visitor publication fixture, not a scientific discovery.",
    "creators": [{"name": "Synthetic Visitor"}],
    "keywords": ["synthetic"],
    "license": "cc-by-4.0",
}


def policy(*destinations, daily=10):
    return VisitorZenodoPolicy(
        key=KEY,
        site_origin="https://polymath.test",
        clients={
            name: OAuthClient(f"client-{name}", SecretStr(SECRET))
            for name in destinations or ("sandbox", "production")
        },
        daily_publications=daily,
    )


def token_body(**changes):
    return {
        "access_token": ACCESS,
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": "synthetic-refresh-token",
        "scope": "deposit:write deposit:actions",
        "user": {"id": "4242"},
        **changes,
    }


# Pure checks -------------------------------------------------------------


def test_tokens_are_sealed_to_their_session_destination_and_kind():
    sealed = seal(KEY, OWNER, "sandbox", "access", SecretStr(ACCESS))
    assert ACCESS.encode() not in sealed
    assert unseal(KEY, OWNER, "sandbox", "access", sealed).get_secret_value() == ACCESS
    for owner, destination, kind, key in (
        ("b" * 64, "sandbox", "access", KEY),
        (OWNER, "production", "access", KEY),
        (OWNER, "sandbox", "refresh", KEY),
        (OWNER, "sandbox", "access", os.urandom(32)),
    ):
        with pytest.raises(VisitorZenodoError, match="zenodo_reconnect_required"):
            unseal(key, owner, destination, kind, sealed)


def test_token_response_needs_both_deposit_permissions_and_a_bearer_token():
    now = datetime.now(UTC)
    tokens = parse_token_response(json.dumps(token_body()).encode(), now)
    assert tokens.access.get_secret_value() == ACCESS and tokens.user_id == "4242"
    assert (tokens.expires_at - now).total_seconds() == 3540
    for changes, code in (
        ({"scope": "deposit:write"}, "zenodo_permission_missing"),
        ({"token_type": "mac"}, "zenodo_connection_failed"),
        ({"access_token": "has space"}, "zenodo_connection_failed"),
        ({"expires_in": True}, "zenodo_connection_failed"),
    ):
        with pytest.raises(VisitorZenodoError, match=code):
            parse_token_response(json.dumps(token_body(**changes)).encode(), now)


def test_a_destination_is_offered_only_with_its_own_credentials_and_a_key():
    assert policy("sandbox").destinations == ("sandbox",)
    assert VisitorZenodoPolicy().destinations == ()
    assert VisitorZenodoPolicy(clients=policy().clients).destinations == ()
    with pytest.raises(ValueError):
        VisitorZenodoPolicy(key=b"short")


# A stateful mock Zenodo --------------------------------------------------


class FakeZenodo:
    def __init__(self, destination="sandbox"):
        self.host = (
            "https://sandbox.zenodo.org" if destination == "sandbox" else ("https://zenodo.org")
        )
        self.deposits, self.calls, self.tokens = {}, [], []
        self.lose_create_response = False
        self.reject_token = False

    def body(self, identifier):
        deposit = self.deposits[identifier]
        body = {
            "id": identifier,
            "submitted": deposit["published"],
            "state": "done" if deposit["published"] else "unsubmitted",
            "metadata": dict(deposit["metadata"]),
            "links": {
                "self": f"{self.host}/api/deposit/depositions/{identifier}",
                "bucket": f"{self.host}/api/files/{BUCKET}",
            },
            "files": [
                {
                    "filename": name,
                    "filesize": len(content),
                    "checksum": "md5:" + hashlib.md5(content, usedforsecurity=False).hexdigest(),
                }
                for name, content in deposit["files"].items()
            ],
        }
        if deposit["published"]:
            body["metadata"]["doi"] = f"10.5072/zenodo.{identifier}"
            body["links"]["record_html"] = f"{self.host}/records/{identifier}"
        return body

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append(f"{method} {path}")
        if path == "/oauth/token":
            form = parse_qs(request.content.decode())
            self.tokens.append(form)
            if self.reject_token:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json=token_body())
        assert request.headers["authorization"] == "Bearer " + ACCESS
        if path == "/api/deposit/depositions" and method == "POST":
            identifier = 100 + len(self.deposits)
            self.deposits[identifier] = {
                "metadata": json.loads(request.content)["metadata"],
                "files": {},
                "published": False,
            }
            if self.lose_create_response:
                raise httpx.ReadTimeout("lost", request=request)
            return httpx.Response(201, json=self.body(identifier))
        if path == "/api/deposit/depositions" and method == "GET":
            marker = request.url.params["q"].split('"')[1]
            found = [
                self.body(identifier)
                for identifier, deposit in self.deposits.items()
                if marker in deposit["metadata"]["keywords"]
            ]
            return httpx.Response(200, json=found)
        if path.startswith("/api/deposit/depositions/"):
            identifier = int(path.split("/")[4])
            if path.endswith("/actions/publish"):
                self.deposits[identifier]["published"] = True
                return httpx.Response(202, json=self.body(identifier))
            return httpx.Response(200, json=self.body(identifier))
        if path.startswith(f"/api/files/{BUCKET}/"):
            name = path.rsplit("/", 1)[1]
            (identifier,) = list(self.deposits)[-1:]
            if method == "PUT":
                self.deposits[identifier]["files"][name] = request.content
                content = request.content
                return httpx.Response(
                    201,
                    json={
                        "key": name,
                        "size": len(content),
                        "checksum": "md5:"
                        + hashlib.md5(content, usedforsecurity=False).hexdigest(),
                    },
                )
            return httpx.Response(200, content=self.deposits[identifier]["files"][name])
        return httpx.Response(404)

    def transport(self):
        return httpx.MockTransport(self.handler)


@pytest.fixture
def visitor(publication):
    fake = FakeZenodo()
    app = FastAPI()
    app.include_router(create_run_router(publication.engine, lambda: True))
    app.include_router(
        create_visitor_zenodo_router(publication.engine, policy(), token_transport=fake.transport())
    )
    client = TestClient(app)
    client.cookies = publication.client.cookies
    store = VisitorZenodoStore(publication.engine, policy())
    worker = VisitorZenodoWorker(
        publication.engine,
        store,
        client_factory=lambda destination, token: ZenodoClient(
            destination, token, live_enabled=True, transport=fake.transport()
        ),
        token_transport=fake.transport(),
    )
    with client:
        yield SimpleNamespace(
            **vars(publication),
            http=client,
            headers=bootstrap(client),
            fake=fake,
            worker=worker,
            zstore=store,
        )


def connect(case, destination="sandbox"):
    started = case.http.post(
        "/api/v1/zenodo/connect", headers=case.headers, json={"destination": destination}
    )
    assert started.status_code == 200
    url = urlsplit(started.json()["authorize_url"])
    query = parse_qs(url.query)
    assert query["scope"] == ["deposit:write deposit:actions"]
    assert query["redirect_uri"] == ["https://polymath.test/zenodo/callback"]
    done = case.http.post(
        "/api/v1/zenodo/complete",
        headers=case.headers,
        json={"code": "synthetic-code", "state": query["state"][0]},
    )
    assert done.status_code == 200, done.text
    return query["state"][0]


def preview(case, destination="sandbox"):
    response = case.http.post(
        "/api/v1/zenodo/publications",
        headers=case.headers,
        json={
            "artifact_id": str(case.artifact.id),
            "destination": destination,
            "metadata": METADATA,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def publish(case, record, **changes):
    return case.http.post(
        f"/api/v1/zenodo/publications/{record['id']}/publish",
        headers=case.headers,
        json={"manifest_sha256": record["manifest_sha256"], **changes},
    )


def run(case):
    return asyncio.run(case.worker.run_once("synthetic-worker"))


# Integration -------------------------------------------------------------


@pytest.mark.integration
def test_a_visitor_connects_publishes_their_own_record_and_no_token_leaves(visitor):
    status = visitor.http.get("/api/v1/zenodo/status").json()
    assert status["available"] and not any(row["connected"] for row in status["destinations"])
    state = connect(visitor)
    exchange = visitor.fake.tokens[0]
    assert exchange["grant_type"] == ["authorization_code"]
    assert exchange["redirect_uri"] == ["https://polymath.test/zenodo/callback"]
    status = visitor.http.get("/api/v1/zenodo/status")
    assert ACCESS not in status.text and SECRET not in status.text
    assert [row["connected"] for row in status.json()["destinations"]] == [True, False]
    with Session(visitor.engine) as session:
        sealed = session.scalar(select(ZenodoConnection.access_ciphertext))
    assert ACCESS.encode() not in sealed
    # The state is one-time.
    again = visitor.http.post(
        "/api/v1/zenodo/complete",
        headers=visitor.headers,
        json={"code": "synthetic-code", "state": state},
    )
    assert again.status_code == 400 and again.json()["detail"] == "zenodo_state_invalid"

    record = preview(visitor)
    assert record["status"] == "preview" and record["creators"][0]["name"] == "Synthetic Visitor"
    assert visitor.fake.calls == ["POST /oauth/token"]  # a preview sends nothing
    queued = publish(visitor, record)
    assert queued.status_code == 200 and queued.json()["status"] == "queued"
    assert run(visitor) == "published"
    done = visitor.http.get(f"/api/v1/zenodo/publications/{record['id']}").json()
    assert done["status"] == "published"
    assert done["doi"] == "10.5072/zenodo.100"
    assert done["record_url"] == "https://sandbox.zenodo.org/records/100"
    assert [call for call in visitor.fake.calls if call.startswith("POST /api/deposit")] == [
        "POST /api/deposit/depositions",
        "POST /api/deposit/depositions/100/actions/publish",
    ]
    with Session(visitor.engine) as session:
        markdown = session.get(GeneratedArtifact, visitor.artifact.id).markdown
    files = visitor.fake.deposits[100]["files"]
    assert list(files.values()) == [markdown.encode()]
    assert run(visitor) == "idle"


@pytest.mark.integration
def test_permanent_records_need_explicit_confirmation(visitor):
    connect(visitor, "production")
    record = preview(visitor, "production")
    refused = publish(visitor, record)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "zenodo_permanent_confirmation_required"
    assert publish(visitor, record, confirm_permanent=True).json()["status"] == "queued"


@pytest.mark.integration
def test_nothing_is_offered_without_a_connection_and_disconnect_deletes_tokens(visitor):
    response = visitor.http.post(
        "/api/v1/zenodo/publications",
        headers=visitor.headers,
        json={
            "artifact_id": str(visitor.artifact.id),
            "destination": "sandbox",
            "metadata": METADATA,
        },
    )
    assert response.status_code == 409 and response.json()["detail"] == "zenodo_not_connected"
    connect(visitor)
    record = preview(visitor)
    publish(visitor, record)
    gone = visitor.http.delete("/api/v1/zenodo/connections/sandbox", headers=visitor.headers)
    assert gone.status_code == 200 and not gone.json()["destinations"][0]["connected"]
    with Session(visitor.engine) as session:
        assert session.scalar(select(ZenodoConnection)) is None
    assert run(visitor) == "reconnect"
    assert [call for call in visitor.fake.calls if "/api/" in call] == []


@pytest.mark.integration
def test_writes_need_csrf_and_another_session_sees_nothing(visitor):
    blocked = visitor.http.post("/api/v1/zenodo/connect", json={"destination": "sandbox"})
    assert blocked.status_code == 403
    connect(visitor)
    record = preview(visitor)
    stranger = TestClient(visitor.http.app)
    with stranger:
        headers = bootstrap(stranger)
        assert stranger.get(f"/api/v1/zenodo/publications/{record['id']}").status_code == 404
        assert stranger.get("/api/v1/zenodo/publications").json() == {"publications": []}
        taken = stranger.post(
            f"/api/v1/zenodo/publications/{record['id']}/publish",
            headers=headers,
            json={"manifest_sha256": record["manifest_sha256"]},
        )
        assert taken.status_code == 404
        foreign = stranger.post(
            "/api/v1/zenodo/publications",
            headers=headers,
            json={
                "artifact_id": str(visitor.artifact.id),
                "destination": "sandbox",
                "metadata": METADATA,
            },
        )
        assert foreign.status_code == 409  # not connected, and the finding is not theirs


@pytest.mark.integration
def test_an_unknown_creation_is_only_ever_followed_by_a_search(visitor):
    connect(visitor)
    record = preview(visitor)
    publish(visitor, record)
    visitor.fake.lose_create_response = True
    assert run(visitor) == "checking"
    visitor.fake.lose_create_response = False
    assert run(visitor) == "published"
    creates = [call for call in visitor.fake.calls if call == "POST /api/deposit/depositions"]
    assert creates == ["POST /api/deposit/depositions"]
    assert "GET /api/deposit/depositions" in visitor.fake.calls
    assert visitor.http.get(f"/api/v1/zenodo/publications/{record['id']}").json()["doi"]


@pytest.mark.integration
def test_when_nothing_reached_zenodo_the_visitor_decides_to_try_again(visitor):
    connect(visitor)
    record = preview(visitor)
    publish(visitor, record)
    visitor.fake.lose_create_response = True
    assert run(visitor) == "checking"
    visitor.fake.deposits.clear()  # the lost request never landed
    visitor.fake.lose_create_response = False
    assert run(visitor) == "missing"
    assert run(visitor) == "idle"
    again = visitor.http.post(
        f"/api/v1/zenodo/publications/{record['id']}/retry", headers=visitor.headers
    )
    assert again.json()["status"] == "queued"
    assert run(visitor) == "published"


@pytest.mark.integration
def test_an_expired_token_is_refreshed_and_a_refused_refresh_asks_to_reconnect(visitor):
    connect(visitor)
    record = preview(visitor)
    publish(visitor, record)
    with Session(visitor.engine) as session, session.begin():
        session.execute(
            update(ZenodoConnection).values(access_expires_at=datetime(2000, 1, 1, tzinfo=UTC))
        )
    visitor.fake.reject_token = True
    assert run(visitor) == "reconnect"
    visitor.fake.reject_token = False
    retried = visitor.http.post(
        f"/api/v1/zenodo/publications/{record['id']}/retry", headers=visitor.headers
    )
    assert retried.json()["status"] == "queued"
    assert run(visitor) == "published"
    assert visitor.fake.tokens[-1]["grant_type"] == ["refresh_token"]


@pytest.mark.integration
def test_a_daily_limit_and_a_changed_preview_are_refused(visitor):
    connect(visitor)
    record = preview(visitor)
    changed = visitor.http.post(
        f"/api/v1/zenodo/publications/{record['id']}/publish",
        headers=visitor.headers,
        json={"manifest_sha256": "0" * 64},
    )
    assert changed.status_code == 409 and changed.json()["detail"] == "zenodo_publication_changed"
    limited = VisitorZenodoStore(visitor.engine, policy(daily=1))
    limited.confirm(
        visitor.owner_hash,
        uuid.UUID(record["id"]),
        record["manifest_sha256"],
        confirm_permanent=False,
    )
    second = limited.preview(
        visitor.owner_hash,
        visitor.artifact.id,
        PublicationMetadata.model_validate({**METADATA, "title": "A second synthetic title"}),
        "sandbox",
    )
    with pytest.raises(VisitorZenodoError, match="zenodo_daily_limit"):
        limited.confirm(
            visitor.owner_hash,
            uuid.UUID(second["id"]),
            second["manifest_sha256"],
            confirm_permanent=False,
        )


@pytest.mark.integration
def test_connections_and_states_disappear_with_their_browser_session(visitor):
    other = "d" * 64
    with Session(visitor.engine) as session, session.begin():
        session.add(BrowserSession(token_hash=other, expires_at=datetime(2999, 1, 1, tzinfo=UTC)))
    visitor.zstore.begin(other, "sandbox")
    visitor.zstore._save_tokens(
        other,
        "sandbox",
        parse_token_response(json.dumps(token_body()).encode(), datetime.now(UTC)),
    )
    with Session(visitor.engine) as session, session.begin():
        session.delete(session.get(BrowserSession, other))
    with Session(visitor.engine) as session:
        assert session.scalar(select(ZenodoConnection)) is None
        assert session.scalar(select(ZenodoOAuthState)) is None


def test_unavailable_server_says_so_without_a_session():
    app = FastAPI()

    class Engine:
        dialect = SimpleNamespace(name="postgresql")

    app.include_router(create_visitor_zenodo_router(Engine(), VisitorZenodoPolicy()))
    with TestClient(app) as client:
        assert client.get("/api/v1/zenodo/status").json() == {
            "available": False,
            "destinations": [],
            "daily_publications": 0,
        }


@pytest.mark.integration
def test_an_unexpected_error_never_stops_the_worker_or_repeats_a_creation(visitor):
    connect(visitor)
    record = preview(visitor)
    publish(visitor, record)
    real = visitor.worker.client_factory

    def broken(destination, token):
        raise RuntimeError("synthetic failure")

    visitor.worker.client_factory = broken
    assert run(visitor) == "failed"
    assert visitor.http.get(f"/api/v1/zenodo/publications/{record['id']}").json()["error"] == (
        "zenodo_unexpected_error"
    )
    visitor.worker.client_factory = real
    visitor.http.post(f"/api/v1/zenodo/publications/{record['id']}/retry", headers=visitor.headers)
    assert run(visitor) == "published"
