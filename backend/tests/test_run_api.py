"""Real PostgreSQL HTTP/session tests; no paid or live model requests occur."""

import hashlib
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from threading import Barrier, Event

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.evidence import EvidenceStore
from polymath.jobs import JobRepository
from polymath.models import ResearchRun
from polymath.research.models import InsufficientFinding, ResearchResult, Usage
from polymath.run_api import COOKIE_NAME, create_run_router

pytestmark = pytest.mark.integration


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch):
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL for real session/API tests")
    url = make_url(database_url)
    schema = "polymath_api_" + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    engine = create_engine(scoped, hide_parameters=True, pool_size=10, max_overflow=10)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with ExitStack() as clients:

        def make_client(*, enabled=False, base_url="http://testserver"):
            application = FastAPI()
            application.include_router(
                create_run_router(
                    engine, (lambda: enabled), poll_seconds=0.001, heartbeat_seconds=0.01
                )
            )
            return clients.enter_context(TestClient(application, base_url=base_url))

        try:
            command.upgrade(config, "head")
            yield engine, make_client
        finally:
            clients.close()
            engine.dispose()
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def bootstrap(client: TestClient) -> dict[str, str]:
    response = client.get("/api/v1/session")
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def submit(client: TestClient, headers: dict[str, str], **changes):
    return client.post(
        "/api/v1/runs",
        headers=headers,
        json={
            "mode": "explore",
            "goal": "Synthetic fixture concept",
            **changes,
        },
    )


def test_cookie_is_hashed_httponly_scoped_and_reused(api):
    engine, factory = api
    client = factory()
    response = client.get("/api/v1/session")
    cookie = client.cookies.get(COOKIE_NAME)
    assert cookie not in response.text
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert "Path=/api/v1" in response.headers["set-cookie"]
    assert "Secure" not in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"
    with Session(engine) as session:
        stored = session.scalar(select(BrowserSession))
        assert stored.token_hash == hashlib.sha256(cookie.encode()).hexdigest()
        assert stored.token_hash != cookie
    assert client.get("/api/v1/session").json() == response.json()
    secure = factory(base_url="https://testserver")
    assert "Secure" in secure.get("/api/v1/session").headers["set-cookie"]


def test_default_unavailable_never_creates_a_job(api):
    engine, factory = api
    client = factory()
    headers = bootstrap(client)
    response = submit(client, headers)
    assert response.status_code == 503
    assert response.json() == {"detail": "research_unavailable"}
    assert response.headers["cache-control"] == "no-store"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0


def test_global_queue_bound_stops_enqueue_and_leaves_no_owned_orphan(api):
    engine, factory = api
    client = factory(enabled=True)
    headers = bootstrap(client)
    with Session(engine) as session, session.begin():
        session.add_all(
            ResearchRun(mode="explore", goal=f"Fixture {index}") for index in range(100)
        )
    response = submit(client, headers)
    assert response.status_code == 429
    assert response.json()["detail"] == "research_queue_full"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 100
        assert session.scalar(select(func.count()).select_from(ResearchRunAccess)) == 0


@pytest.mark.parametrize(
    "change",
    [
        {"X-CSRF-Token": "incorrect"},
        {"Origin": "https://attacker.example"},
        {"Origin": "http://[invalid"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_write_rejects_forgery_before_creating_a_run(api, change):
    engine, factory = api
    client = factory(enabled=True)
    headers = {**bootstrap(client), **change}
    assert submit(client, headers).status_code == 403
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0


def test_error_responses_are_private_and_validation_never_echoes_input(api):
    _, factory = api
    client = factory(enabled=True)
    unauthenticated = client.get("/api/v1/runs")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["cache-control"] == "no-store"
    headers = bootstrap(client)
    invalid = submit(client, headers, goal="private-fixture" + "x" * 4001)
    assert invalid.status_code == 422
    assert invalid.headers["cache-control"] == "no-store"
    assert "private-fixture" not in invalid.text
    forbidden = submit(client, {"X-CSRF-Token": "invalid"})
    assert forbidden.status_code == 403
    assert forbidden.headers["cache-control"] == "no-store"
    missing = client.get(f"/api/v1/runs/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"


def test_session_expiring_while_waiting_for_quota_lock_cannot_enqueue(api, monkeypatch):
    import polymath.run_api as routes

    engine, factory = api
    client = factory(enabled=True)
    headers = bootstrap(client)
    authenticated = Event()
    original = routes._authenticated

    def watched(session, request):
        owner = original(session, request)
        authenticated.set()
        return owner

    monkeypatch.setattr(routes, "_authenticated", watched)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with engine.begin() as blocker:
            blocker.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": routes.QUEUE_LOCK_KEY}
            )
            future = executor.submit(submit, client, headers)
            assert authenticated.wait(timeout=10)
            blocker.execute(
                text(
                    "UPDATE browser_sessions "
                    "SET expires_at = clock_timestamp() - interval '1 second'"
                )
            )
        response = future.result(timeout=10)
    assert response.status_code == 401
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0


def test_ownership_hides_foreign_runs_and_never_exposes_internal_state(api):
    engine, factory = api
    first, other = factory(enabled=True), factory(enabled=True)
    headers, foreign_headers = bootstrap(first), bootstrap(other)
    response = submit(first, headers)
    assert response.status_code == 201
    identity = response.json()["id"]
    assert first.get(f"/api/v1/runs/{identity}").json()["goal"] == "Synthetic fixture concept"
    for suffix in ("", "/events", "/result"):
        assert other.get(f"/api/v1/runs/{identity}{suffix}").status_code == 404
    assert other.post(f"/api/v1/runs/{identity}/cancel", headers=foreign_headers).status_code == 404
    assert other.get("/api/v1/runs").json() == {"runs": [], "next_cursor": None}
    with Session(engine) as session, session.begin():
        run = session.get(ResearchRun, uuid.UUID(identity))
        run.checkpoint = {"internal": "private-prompt-fixture"}
        run.outcome = {"secret": "must-not-leak", "error_code": "fixture_failure"}
    result = first.get(f"/api/v1/runs/{identity}")
    assert "private-prompt-fixture" not in result.text
    assert "must-not-leak" not in result.text
    assert result.json()["error_code"] == "fixture_failure"
    assert result.headers["cache-control"] == "no-store"


def test_expired_cookie_cannot_read_or_write_and_bootstrap_rotates_it(api):
    engine, factory = api
    client = factory(enabled=True)
    headers = bootstrap(client)
    identity = submit(client, headers).json()["id"]
    old_cookie = client.cookies.get(COOKIE_NAME)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE browser_sessions SET expires_at = clock_timestamp() - interval '1 second'")
        )
    assert client.get(f"/api/v1/runs/{identity}").status_code == 401
    assert submit(client, headers).status_code == 401
    assert client.get("/api/v1/session").status_code == 200
    assert client.cookies.get(COOKIE_NAME) != old_cookie
    assert client.get(f"/api/v1/runs/{identity}").status_code == 404


def test_creation_and_ownership_roll_back_together(api, monkeypatch):
    import polymath.run_api as routes

    engine, factory = api
    client = factory(enabled=True)
    headers = bootstrap(client)

    def fail(*args, **kwargs):
        raise SQLAlchemyError("do-not-expose-database-secret")

    monkeypatch.setattr(routes, "_event", fail)
    response = submit(client, headers)
    assert response.status_code == 503
    assert "do-not-expose" not in response.text
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0
        assert session.scalar(select(func.count()).select_from(ResearchRunAccess)) == 0


def test_concurrent_same_session_requests_have_one_winner(api):
    engine, factory = api
    first = factory(enabled=True)
    headers = bootstrap(first)
    token = first.cookies.get(COOKIE_NAME)
    clients = [factory(enabled=True) for _ in range(8)]
    for client in clients:
        client.cookies.set(COOKIE_NAME, token, path="/api/v1")
    barrier = Barrier(len(clients))

    def attempt(client):
        barrier.wait(timeout=10)
        return submit(client, headers).status_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(attempt, clients))
    assert statuses.count(201) == 1
    assert statuses.count(429) == 7
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 1
        assert session.scalar(select(func.count()).select_from(ResearchRunAccess)) == 1


def test_cancel_and_keyset_history_pagination(api):
    _, factory = api
    client = factory(enabled=True)
    headers = bootstrap(client)
    identities = []
    for _ in range(3):
        response = submit(client, headers)
        assert response.status_code == 201
        identity = response.json()["id"]
        identities.append(identity)
        assert (
            client.post(f"/api/v1/runs/{identity}/cancel", headers=headers).json()["status"]
            == "cancelled"
        )
    page = client.get("/api/v1/runs?limit=2").json()
    assert [item["id"] for item in page["runs"]] == list(reversed(identities[-2:]))
    assert page["next_cursor"] == identities[1]
    second = client.get("/api/v1/runs", params={"before": page["next_cursor"], "limit": 2}).json()
    assert [item["id"] for item in second["runs"]] == identities[:1]
    assert second["next_cursor"] is None


@pytest.mark.parametrize("cursor", ["-1", "oops", str(2**63), "1.5"])
def test_invalid_event_cursor_rejected(api, cursor):
    _, factory = api
    client = factory(enabled=True)
    headers = bootstrap(client)
    identity = submit(client, headers).json()["id"]
    assert (
        client.get(f"/api/v1/runs/{identity}/events", headers={"Last-Event-ID": cursor}).status_code
        == 400
    )


def test_sse_replays_persisted_events_and_drains_more_than_one_batch(api):
    engine, factory = api
    client = factory(enabled=True)
    headers = bootstrap(client)
    identity = uuid.UUID(submit(client, headers).json()["id"])
    jobs = JobRepository(engine)
    lease = jobs.claim_next("fixture-worker")
    for index in range(105):
        jobs.append_event(
            lease, event_key=f"fixture:{index}", kind="fixture", summary=f"Fixture {index}"
        )
    jobs.finish(lease, status="failed", outcome={"error_code": "fixture_complete"})
    response = client.get(f"/api/v1/runs/{identity}/events", headers={"Last-Event-ID": "3"})
    assert response.headers["content-type"].startswith("text/event-stream")
    records = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: {") and '"sequence"' in line
    ]
    assert len(records) == 105
    assert [record["sequence"] for record in records] == list(range(4, 109))
    assert response.text.endswith("event: end\ndata: {}\n\n")
    assert "id: 1\n" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_result_returns_only_owned_durable_evidence_payload(api):
    engine, factory = api
    client = factory(enabled=True)
    headers = bootstrap(client)
    identity = uuid.UUID(submit(client, headers).json()["id"])
    assert client.get(f"/api/v1/runs/{identity}/result").status_code == 404
    jobs = JobRepository(engine)
    lease = jobs.claim_next("fixture-worker")
    result = ResearchResult(
        status="insufficient_evidence",
        finding=InsufficientFinding(
            outcome="insufficient_evidence", reason="Synthetic fixture only"
        ),
        usage=Usage(),
    )
    stored = EvidenceStore(engine).save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(stored.id)})
    response = client.get(f"/api/v1/runs/{identity}/result")
    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_evidence"
    assert response.json()["finding"]["reason"] == "Synthetic fixture only"
    assert response.headers["cache-control"] == "no-store"


def test_open_event_stream_stops_when_session_expires(api):
    engine, factory = api
    client = factory(enabled=True)
    headers = bootstrap(client)
    identity = submit(client, headers).json()["id"]

    def expire():
        time.sleep(0.05)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE browser_sessions "
                    "SET expires_at = clock_timestamp() - interval '1 second'"
                )
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        expired = executor.submit(expire)
        response = client.get(f"/api/v1/runs/{identity}/events")
        expired.result(timeout=10)
    assert response.status_code == 200
    assert "event: session_expired" in response.text


def test_invalid_goal_and_missing_session_cannot_create(api):
    _, factory = api
    client = factory(enabled=True)
    assert client.get("/api/v1/runs").status_code == 401
    assert submit(client, {}).status_code == 403
    headers = bootstrap(client)
    assert submit(client, headers, goal=" ").status_code == 422
    assert submit(client, headers, mode="unknown").status_code == 422
    assert submit(client, headers, goal="x" * 4001).status_code == 422
    assert submit(client, headers, mode="surprise", goal="").status_code == 201
