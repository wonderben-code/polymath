"""Real PostgreSQL public/owner HTTP boundaries; all research is synthetic."""

import hashlib
import json
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_finding_eligibility_integration import candidate, head
from test_knowledge import seed
from test_run_api import bootstrap
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.assessment_models import FindingHead
from polymath.evidence import EvidenceStore
from polymath.jobs import JobRepository
from polymath.models import ResearchRun
from polymath.run_api import COOKIE_NAME, create_run_router
from polymath.sharing import SharingPolicy, SharingStore
from polymath.sharing_api import create_sharing_router
from polymath.sharing_models import SharedFinding, SharedFindingRevocation

database = database_fixture
pytestmark = pytest.mark.integration
PREFIX = "/api/v1/shares"
PUBLIC = "/api/v1/public"


@pytest.fixture
def sharing_api(database):
    with ExitStack() as clients:

        def factory(*, enabled=False, availability=None):
            app = FastAPI()
            app.include_router(create_run_router(database, availability=lambda: True))
            callback = (
                availability if availability is not None else (lambda: True) if enabled else None
            )
            app.include_router(create_sharing_router(database, availability=callback))
            return clients.enter_context(TestClient(app))

        yield database, factory


def assert_headers(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert "set-cookie" not in response.headers


def owner_hash(client):
    bootstrap(client)
    return hashlib.sha256(client.cookies.get(COOKIE_NAME).encode("ascii")).hexdigest()


def share_body(record):
    return {
        "source_result_id": record["source_result_id"],
        "expected_projection_sha256": record["projection_sha256"],
        "idempotency_key": record["idempotency_key"],
    }


def grant(engine, client, *, source_result_id=None, title="Synthetic explicitly shared finding"):
    if source_result_id is None:
        _, source_result_id = seed(engine, client, title=title)
    preview = client.get(PREFIX + "/preview", params={"source_result_id": str(source_result_id)})
    assert preview.status_code == 200, preview.text
    assert_headers(preview)
    response = client.post(
        PREFIX,
        headers=bootstrap(client),
        json={
            "source_result_id": str(source_result_id),
            "expected_projection_sha256": preview.json()["projection_sha256"],
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    assert_headers(response)
    return response.json()


def rows(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def test_default_public_gate_and_capabilities_never_bootstrap_a_session(sharing_api):
    engine, factory = sharing_api
    client = factory()
    response = client.get(PREFIX + "/capabilities")
    assert response.json() == {"sharing_available": False, "reason": "sharing_unavailable"}
    assert_headers(response)
    for path in (
        PUBLIC + "/library",
        PUBLIC + "/map",
        PUBLIC + "/findings/" + secrets.token_urlsafe(32),
    ):
        response = client.get(path)
        assert response.status_code == 404 and response.json() == {"detail": "sharing_not_found"}
        assert_headers(response)
    assert client.get(PUBLIC + "/library", params={"q": "private" * 100}).status_code == 404
    assert not client.cookies
    assert (
        rows(engine, BrowserSession)
        == rows(engine, ResearchRun)
        == rows(engine, ResearchRunAccess)
        == 0
    )


def test_enabled_empty_public_reading_is_get_only_and_session_free(sharing_api):
    engine, factory = sharing_api
    client = factory(enabled=True)
    assert client.get(PREFIX + "/capabilities").json() == {
        "sharing_available": True,
        "reason": None,
    }
    library = client.get(PUBLIC + "/library")
    assert library.json() == {"items": [], "next_cursor": None}
    graph = client.get(PUBLIC + "/map")
    assert graph.json()["nodes"] == graph.json()["edges"] == []
    assert graph.json()["scope"] == "explicitly_shared_current_findings"
    for response in (library, graph):
        assert_headers(response)
    assert rows(engine, BrowserSession) == rows(engine, ResearchRunAccess) == 0


def test_private_authority_and_disabled_owner_controls_remain_explicit(sharing_api):
    engine, factory = sharing_api
    client = factory()
    for path in (PREFIX, PREFIX + "/preview?source_result_id=" + str(uuid.uuid4())):
        response = client.get(path)
        assert response.status_code == 401
        assert_headers(response)
    _, source_id = seed(engine, client)
    preview = client.get(PREFIX + "/preview", params={"source_result_id": str(source_id)})
    assert preview.status_code == 503 and preview.json() == {"detail": "sharing_unavailable"}
    blocked = client.post(
        PREFIX,
        headers=bootstrap(client),
        json={
            "source_result_id": str(source_id),
            "expected_projection_sha256": "a" * 64,
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert blocked.status_code == 503
    assert client.get(PREFIX).json() == {"shares": [], "next_cursor": None}
    assert rows(engine, SharedFinding) == 0


def test_exact_preview_hash_share_and_cookie_free_public_detail(sharing_api):
    engine, factory = sharing_api
    owner, visitor = factory(enabled=True), factory(enabled=True)
    _, source_id = seed(engine, owner)
    before_runs = rows(engine, ResearchRun)
    preview = owner.get(PREFIX + "/preview", params={"source_result_id": str(source_id)}).json()
    assert rows(engine, FindingHead) == rows(engine, SharedFinding) == 0
    canonical = json.dumps(
        preview["projection"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert hashlib.sha256(canonical.encode()).hexdigest() == preview["projection_sha256"]
    record = grant(engine, owner, source_result_id=source_id)
    assert record["projection"] == preview["projection"]
    assert record["projection_sha256"] == preview["projection_sha256"]
    assert record["expires_at"] == preview["expires_at"]
    assert record["public_path"] == "/shared/" + record["public_id"]
    public = visitor.get(PUBLIC + "/findings/" + record["public_id"])
    assert public.status_code == 200
    assert set(public.json()) == {
        "public_id",
        "projection_sha256",
        "projection",
        "created_at",
        "expires_at",
        "status",
    }
    assert public.json()["projection"] == record["projection"]
    assert public.json()["status"] == "current"
    assert str(source_id) not in public.text and record["idempotency_key"] not in public.text
    assert_headers(public)
    assert rows(engine, BrowserSession) == 1 and not visitor.cookies
    assert rows(engine, ResearchRun) == before_runs


@pytest.mark.parametrize("action", ["share", "revoke"])
@pytest.mark.parametrize("failure", ["missing_csrf", "wrong_csrf", "foreign_origin", "cross_site"])
def test_owner_mutations_require_browser_csrf_and_same_origin(sharing_api, action, failure):
    engine, factory = sharing_api
    client = factory(enabled=True)
    record = grant(engine, client)
    headers = bootstrap(client)
    if failure == "missing_csrf":
        headers = {}
    elif failure == "wrong_csrf":
        headers["X-CSRF-Token"] = "f" * 64
    elif failure == "foreign_origin":
        headers["Origin"] = "https://untrusted.example"
    else:
        headers["Sec-Fetch-Site"] = "cross-site"
    response = client.post(
        PREFIX if action == "share" else f"{PREFIX}/{record['public_id']}/revoke",
        headers=headers,
        json=share_body(record) if action == "share" else None,
    )
    assert response.status_code == 403
    assert_headers(response)
    assert rows(engine, SharedFinding) == 1 and rows(engine, SharedFindingRevocation) == 0


def test_foreign_preview_revoke_and_owner_list_do_not_reveal_private_grants(sharing_api):
    engine, factory = sharing_api
    client, foreign = factory(enabled=True), factory(enabled=True)
    record = grant(engine, client)
    headers = bootstrap(foreign)
    foreign_preview = foreign.get(
        PREFIX + "/preview", params={"source_result_id": record["source_result_id"]}
    )
    missing_preview = foreign.get(
        PREFIX + "/preview", params={"source_result_id": str(uuid.uuid4())}
    )
    assert foreign_preview.status_code == missing_preview.status_code == 404
    assert foreign_preview.json() == missing_preview.json() == {"detail": "sharing_not_found"}
    revoke = foreign.post(f"{PREFIX}/{record['public_id']}/revoke", headers=headers)
    assert revoke.status_code == 404
    assert foreign.get(PREFIX).json() == {"shares": [], "next_cursor": None}
    assert rows(engine, SharedFindingRevocation) == 0


@pytest.mark.parametrize(
    "change", ["source_uuid", "key_uuid", "key_bool", "hash_upper", "hash_long", "extra", "missing"]
)
def test_share_request_validation_is_bounded_canonical_and_never_echoes_input(sharing_api, change):
    engine, factory = sharing_api
    client = factory(enabled=True)
    record = grant(engine, client)
    body = share_body(record)
    secret = "PRIVATE-INPUT-MUST-NOT-BE-ECHOED"
    if change == "source_uuid":
        body["source_result_id"] = body["source_result_id"].replace("-", "")
    elif change == "key_uuid":
        body["idempotency_key"] = secret.ljust(36, "x")
    elif change == "key_bool":
        body["idempotency_key"] = True
    elif change == "hash_upper":
        body["expected_projection_sha256"] = "F" * 64
    elif change == "hash_long":
        body["expected_projection_sha256"] = "a" * 10000
    elif change == "extra":
        body["private_context"] = secret
    else:
        del body["idempotency_key"]
    response = client.post(PREFIX, headers=bootstrap(client), json=body)
    assert response.status_code == 422 and response.json() == {"detail": "sharing_invalid_request"}
    assert secret not in response.text
    assert_headers(response)
    assert rows(engine, SharedFinding) == 1


def test_exact_preview_hash_is_required_and_idempotency_never_changes_a_grant(sharing_api):
    engine, factory = sharing_api
    client = factory(enabled=True)
    record = grant(engine, client)
    body = share_body(record)
    retry = client.post(PREFIX, headers=bootstrap(client), json=body)
    assert retry.status_code == 201 and retry.json() == record
    changed = {**body, "expected_projection_sha256": "0" * 64}
    conflict = client.post(PREFIX, headers=bootstrap(client), json=changed)
    assert conflict.status_code == 409 and conflict.json() == {
        "detail": "sharing_idempotency_conflict"
    }
    bad_preview = client.post(
        PREFIX, headers=bootstrap(client), json={**changed, "idempotency_key": str(uuid.uuid4())}
    )
    assert bad_preview.status_code == 409 and bad_preview.json() == {
        "detail": "sharing_projection_changed"
    }
    assert rows(engine, SharedFinding) == 1


def test_revocation_is_permanent_idempotent_and_every_public_surface_removes_it(sharing_api):
    engine, factory = sharing_api
    client, visitor = factory(enabled=True), factory(enabled=True)
    record = grant(engine, client)
    for _ in range(2):
        response = client.post(f"{PREFIX}/{record['public_id']}/revoke", headers=bootstrap(client))
        assert response.status_code == 200 and response.json()["status"] == "revoked"
        assert_headers(response)
    assert rows(engine, SharedFindingRevocation) == 1
    missing = visitor.get(PUBLIC + "/findings/" + secrets.token_urlsafe(32))
    revoked = visitor.get(PUBLIC + "/findings/" + record["public_id"])
    assert missing.status_code == revoked.status_code == 404 and missing.json() == revoked.json()
    assert visitor.get(PUBLIC + "/library").json()["items"] == []
    assert visitor.get(PUBLIC + "/map").json()["nodes"] == []
    retry = client.post(PREFIX, headers=bootstrap(client), json=share_body(record))
    assert retry.status_code == 201 and retry.json()["status"] == "revoked"
    fresh = grant(engine, client, source_result_id=record["source_result_id"])
    assert fresh["public_id"] != record["public_id"]
    assert visitor.get(PUBLIC + "/findings/" + record["public_id"]).status_code == 404
    assert visitor.get(PUBLIC + "/findings/" + fresh["public_id"]).status_code == 200


def test_expired_owner_hides_public_data_and_private_ownership_is_not_renewed(sharing_api):
    engine, factory = sharing_api
    client, visitor = factory(enabled=True), factory(enabled=True)
    record = grant(engine, client)
    token_hash = owner_hash(client)
    with engine.begin() as connection:
        connection.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == token_hash)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    for path in (PUBLIC + "/findings/" + record["public_id"],):
        response = visitor.get(path)
        assert response.status_code == 404 and response.json() == {"detail": "sharing_not_found"}
        assert_headers(response)
    assert visitor.get(PUBLIC + "/library").json()["items"] == []
    assert visitor.get(PUBLIC + "/map").json()["nodes"] == []
    assert client.get(PREFIX).status_code == 401
    assert rows(engine, BrowserSession) == 1


@pytest.mark.parametrize("state", ["superseded", "unsupported"])
def test_current_status_is_separate_from_immutable_public_content(sharing_api, state):
    engine, factory = sharing_api
    client, visitor = factory(enabled=True), factory(enabled=True)
    record = grant(engine, client)
    root_id = uuid.UUID(record["source_result_id"])
    if state == "superseded":
        newer, _ = candidate(
            engine, owner_hash(client), root_id, title="PRIVATE unshared successor"
        )
        head(engine, root_id, newer.id)
    else:
        head(engine, root_id, state="unsupported")
    response = visitor.get(PUBLIC + "/findings/" + record["public_id"])
    assert response.status_code == 200 and response.json()["status"] == state
    assert response.json()["projection"] == record["projection"]
    assert response.json()["projection_sha256"] == record["projection_sha256"]
    assert "PRIVATE unshared successor" not in response.text
    assert visitor.get(PUBLIC + "/library").json()["items"] == []
    assert visitor.get(PUBLIC + "/map").json()["nodes"] == []
    retry = client.post(PREFIX, headers=bootstrap(client), json=share_body(record))
    assert retry.status_code == 201 and retry.json()["public_id"] == record["public_id"]
    fresh = client.post(
        PREFIX,
        headers=bootstrap(client),
        json={**share_body(record), "idempotency_key": str(uuid.uuid4())},
    )
    assert fresh.status_code == 409


def test_unapplied_candidate_can_never_be_shared(sharing_api):
    engine, factory = sharing_api
    client = factory(enabled=True)
    _, root_id = seed(engine, client)
    loser, _ = candidate(engine, owner_hash(client), root_id, title="Synthetic unapplied candidate")
    response = client.get(PREFIX + "/preview", params={"source_result_id": str(loser.id)})
    assert response.status_code == 409 and response.json() == {
        "detail": "sharing_source_not_current"
    }
    assert rows(engine, SharedFinding) == 0


def test_owner_and_public_pagination_use_only_public_handles_and_literal_queries(sharing_api):
    engine, factory = sharing_api
    client, visitor = factory(enabled=True), factory(enabled=True)
    _, source_id = seed(engine, client, title="Synthetic 50%_bridge")
    records = [grant(engine, client, source_result_id=source_id) for _ in range(3)]
    own, public, before_owned, before_public = [], [], None, None
    for _ in range(3):
        owned = client.get(
            PREFIX, params={"limit": 1, **({"before": before_owned} if before_owned else {})}
        ).json()
        listed = visitor.get(
            PUBLIC + "/library",
            params={"limit": 1, **({"before": before_public} if before_public else {})},
        ).json()
        own.extend(row["public_id"] for row in owned["shares"])
        public.extend(row["public_id"] for row in listed["items"])
        before_owned, before_public = owned["next_cursor"], listed["next_cursor"]
    assert set(own) == set(public) == {row["public_id"] for row in records}
    assert len(own) == len(set(own)) == 3 and before_owned is before_public is None
    assert visitor.get(PUBLIC + "/library", params={"q": "50%_"}).json()["items"]
    assert visitor.get(PUBLIC + "/library", params={"q": "50%_missing"}).json()["items"] == []
    assert str(source_id) not in json.dumps(visitor.get(PUBLIC + "/library").json())


@pytest.mark.parametrize(
    "query", [{"q": "x" * 201}, {"q": "\x00"}, {"limit": 0}, {"limit": 21}, {"before": "invalid"}]
)
def test_public_query_validation_is_static_and_bounded(sharing_api, query):
    _, factory = sharing_api
    client = factory(enabled=True)
    response = client.get(PUBLIC + "/library", params=query)
    assert response.status_code in (404, 422)
    assert response.json()["detail"] in ("sharing_invalid_request", "sharing_not_found")
    assert_headers(response)


@pytest.mark.parametrize("value", [1, "true", None])
def test_availability_requires_literal_true_and_public_reads_do_not_create_sessions(
    sharing_api, value
):
    engine, factory = sharing_api
    client = factory(availability=lambda: value)
    assert client.get(PREFIX + "/capabilities").json()["sharing_available"] is False
    assert client.get(PUBLIC + "/library").status_code == 404
    assert rows(engine, BrowserSession) == 0


def test_gate_removal_keeps_owner_controls_and_known_successful_receipt(sharing_api, monkeypatch):
    engine, factory = sharing_api
    active = True
    client = factory(availability=lambda: active)
    _, source_id = seed(engine, client)
    preview = client.get(PREFIX + "/preview", params={"source_result_id": str(source_id)}).json()
    original = SharingStore.share

    def disable_after_commit(store, *args, **kwargs):
        nonlocal active
        record = original(store, *args, **kwargs)
        active = False
        return record

    monkeypatch.setattr(SharingStore, "share", disable_after_commit)
    response = client.post(
        PREFIX,
        headers=bootstrap(client),
        json={
            "source_result_id": str(source_id),
            "expected_projection_sha256": preview["projection_sha256"],
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201
    record = response.json()
    assert client.get(PUBLIC + "/findings/" + record["public_id"]).status_code == 404
    assert client.get(PREFIX).json()["shares"][0]["public_id"] == record["public_id"]
    assert (
        client.post(f"{PREFIX}/{record['public_id']}/revoke", headers=bootstrap(client)).json()[
            "status"
        ]
        == "revoked"
    )


def test_disable_while_head_waiting_rolls_back_new_grant(sharing_api):
    engine, factory = sharing_api
    active = True
    client = factory(availability=lambda: active)
    _, source_id = seed(engine, client)
    preview = client.get(PREFIX + "/preview", params={"source_result_id": str(source_id)}).json()
    with Session(engine) as session, session.begin():
        session.add(
            FindingHead(
                root_result_id=source_id, current_result_id=source_id, state="supported", revision=0
            )
        )
    waiting = Event()

    def blocked(connection, cursor, statement, parameters, context, executemany):
        if "finding_heads" in statement and "FOR UPDATE" in statement:
            waiting.set()

    headers = bootstrap(client)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            select(FindingHead).where(FindingHead.root_result_id == source_id).with_for_update()
        )
        event.listen(engine, "before_cursor_execute", blocked)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    client.post,
                    PREFIX,
                    headers=headers,
                    json={
                        "source_result_id": str(source_id),
                        "expected_projection_sha256": preview["projection_sha256"],
                        "idempotency_key": str(uuid.uuid4()),
                    },
                )
                try:
                    assert waiting.wait(timeout=10)
                    active = False
                finally:
                    transaction.rollback()
                response = future.result(timeout=20)
        finally:
            event.remove(engine, "before_cursor_execute", blocked)
    assert response.status_code == 503 and response.json() == {"detail": "sharing_unavailable"}
    assert rows(engine, SharedFinding) == 0
    assert client.get(PREFIX).json()["shares"] == []


def test_api_quota_errors_remain_owner_scoped_and_revocation_frees_capacity(
    sharing_api, monkeypatch
):
    engine, factory = sharing_api
    monkeypatch.setattr(
        "polymath.sharing_api.SharingPolicy",
        lambda *, enabled: SharingPolicy(enabled=enabled, max_active_per_owner=1),
    )
    client = factory(enabled=True)
    record = grant(engine, client)
    response = client.post(
        PREFIX,
        headers=bootstrap(client),
        json={**share_body(record), "idempotency_key": str(uuid.uuid4())},
    )
    assert response.status_code == 429 and response.json() == {"detail": "sharing_active_limit"}
    client.post(f"{PREFIX}/{record['public_id']}/revoke", headers=bootstrap(client))
    assert grant(engine, client, source_result_id=record["source_result_id"])["status"] == "current"


def test_public_reads_never_use_private_source_routes_or_job_creation(sharing_api, monkeypatch):
    engine, factory = sharing_api
    client, visitor = factory(enabled=True), factory(enabled=True)
    record = grant(engine, client)

    def forbidden(*args, **kwargs):
        raise AssertionError("Public read attempted private execution")

    monkeypatch.setattr(EvidenceStore, "get_result", forbidden)
    monkeypatch.setattr(EvidenceStore, "get_document", forbidden)
    monkeypatch.setattr(JobRepository, "create", forbidden)
    monkeypatch.setattr(SharingStore, "preview", forbidden)
    for path in (PUBLIC + "/findings/" + record["public_id"], PUBLIC + "/library", PUBLIC + "/map"):
        response = visitor.get(path)
        assert response.status_code == 200
        assert_headers(response)
    visitor.cookies.set(COOKIE_NAME, record["public_id"])
    with Session(engine) as session:
        run_id = session.scalar(select(ResearchRun.id))
    assert visitor.get(f"/api/v1/runs/{run_id}").status_code == 401
    assert rows(engine, BrowserSession) == 1 and rows(engine, ResearchRunAccess) == 1


def test_storage_errors_are_sanitized_with_all_privacy_headers(sharing_api, monkeypatch):
    _, factory = sharing_api
    client = factory(enabled=True)

    def broken(*args, **kwargs):
        raise SQLAlchemyError("PRIVATE DATABASE INPUT MUST NOT BE RETURNED")

    monkeypatch.setattr(SharingStore, "get_public", broken)
    response = client.get(PUBLIC + "/findings/" + secrets.token_urlsafe(32))
    assert response.status_code == 503 and response.json() == {
        "detail": "sharing_storage_unavailable"
    }
    assert "PRIVATE" not in response.text
    assert_headers(response)
