"""Owned watch APIs use a real database, without provider or model requests."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_evidence import document as source_document
from test_knowledge import seed
from test_run_api import bootstrap, submit
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.evidence import EvidenceStore
from polymath.ingestion import IngestionError, IngestionRepository
from polymath.ingestion_api import create_ingestion_router
from polymath.jobs import JobRepository
from polymath.models import ResearchRun
from polymath.run_api import COOKIE_NAME, create_run_router

database = database_fixture
pytestmark = pytest.mark.integration
PREFIX = "/api/v1/knowledge/watches"


@pytest.fixture
def watches(database):
    with ExitStack() as clients:

        def factory(*, enabled=False, availability=None):
            app = FastAPI()
            app.include_router(create_run_router(database, availability=lambda: True))
            app.include_router(
                create_ingestion_router(
                    database,
                    availability=availability if availability is not None else lambda: enabled,
                )
            )
            return clients.enter_context(TestClient(app))

        yield database, factory


def create(client, headers=None, **changes):
    return client.post(
        PREFIX,
        headers=bootstrap(client) if headers is None else headers,
        json={"label": "Synthetic literature watch", "query": "Synthetic bounded query", **changes},
    )


def assert_private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def save_fixture_page(engine, client, watch_id, documents):
    response = client.post(f"{PREFIX}/{watch_id}/check", headers=bootstrap(client))
    assert response.status_code == 201, response.text
    tick_id = uuid.UUID(response.json()["run_id"])
    jobs = JobRepository(engine)
    lease = jobs.claim_next("watch-source-api-fixture", task_kinds=("ingest_literature",))
    assert lease.run_id == tick_id
    repository, evidence = IngestionRepository(engine), EvidenceStore(engine)
    current = repository.snapshot(lease)
    artifacts = tuple(evidence.save_source(lease, document) for document in documents)
    repository.commit_page(
        lease, "openalex", current.cursors["openalex"], None, artifacts, exhausted=True
    )
    jobs.finish(lease, status="succeeded", outcome={"fixture_page_only": True})
    return artifacts


def test_default_collection_gate_and_real_browser_authentication(watches):
    engine, factory = watches
    client = factory()
    capabilities = client.get(PREFIX + "/capabilities")
    assert_private(capabilities)
    assert capabilities.json() == {
        "execution_available": False,
        "reason": "ingestion_execution_disabled",
        "model_calls": False,
        "min_interval_seconds": 21_600,
        "max_interval_seconds": 604_800,
        "watch_limit": 10,
        "source_limit_per_watch": 2000,
        "coverage": "bounded_provider_windows",
    }
    assert client.get(PREFIX).status_code == 401
    assert client.post(PREFIX, json={"label": "Watch", "query": "Synthetic"}).status_code == 403
    blocked = create(client)
    assert blocked.status_code == 503
    assert blocked.json() == {"detail": "ingestion_execution_disabled"}
    assert_private(blocked)
    assert client.get(PREFIX).json() == {"watches": [], "next_cursor": None}
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0


@pytest.mark.parametrize("policy", [lambda: 1, lambda: "true", lambda: None])
def test_only_explicit_true_enables_collection(watches, policy):
    _, factory = watches
    client = factory(availability=policy)
    assert not client.get(PREFIX + "/capabilities").json()["execution_available"]
    assert create(client).status_code == 503


def test_failed_availability_is_sanitized_and_disabled(watches):
    _, factory = watches

    def unavailable():
        raise RuntimeError("private-provider-configuration")

    client = factory(availability=unavailable)
    response = create(client)
    assert response.status_code == 503
    assert "private-provider" not in response.text


def test_watch_is_owned_expiring_and_readable_without_operator(watches):
    engine, factory = watches
    client = factory(enabled=True)
    created = create(client, label="  Synthetic owned watch  ", query="  Synthetic research  ")
    assert created.status_code == 201, created.text
    assert_private(created)
    watch = created.json()
    assert watch["label"] == "Synthetic owned watch"
    assert watch["query"] == "Synthetic research"
    assert watch["interval_seconds"] == 86_400 and watch["enabled"] is True
    assert watch["source_result_id"] is None
    assert watch["coverage"] == "current_browser_session"
    with Session(engine) as session:
        owner = session.scalar(select(BrowserSession))
        assert datetime.fromisoformat(watch["expires_at"]) <= owner.expires_at
    assert client.cookies.get(COOKIE_NAME) not in created.text
    assert "token_hash" not in created.text
    assert client.get(f"{PREFIX}/{watch['id']}").json() == watch
    assert client.get(PREFIX).json()["watches"] == [watch]
    for suffix, key in (("sources", "sources"), ("ticks", "ticks")):
        response = client.get(f"{PREFIX}/{watch['id']}/{suffix}")
        assert response.status_code == 200
        assert_private(response)
        assert response.json() == {key: [], "next_cursor": None}


def test_owned_positive_finding_can_be_pinned_but_foreign_or_missing_cannot(watches):
    engine, factory = watches
    own, other = factory(enabled=True), factory(enabled=True)
    _, own_result = seed(engine, own, title="Synthetic own finding", suffix="watch-own")
    _, foreign_result = seed(engine, other, title="Private foreign finding", suffix="watch-foreign")
    created = create(own, source_result_id=str(own_result))
    assert created.status_code == 201
    assert created.json()["source_result_id"] == str(own_result)
    foreign = create(own, label="Other source", source_result_id=str(foreign_result))
    missing = create(own, label="Missing source", source_result_id=str(uuid.uuid4()))
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": "watch_not_found"}
    assert "Private foreign" not in foreign.text
    assert len(own.get(PREFIX).json()["watches"]) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"label": ""},
        {"label": " " * 4},
        {"label": "x" * 121},
        {"label": "PRIVATE-WATCH-CONTENT\ncontrol"},
        {"query": ""},
        {"query": " " * 4},
        {"query": "x" * 501},
        {"query": "PRIVATE-WATCH-CONTENT\x00control"},
        {"query": "PRIVATE-WATCH-CONTENT\ud800"},
        {"interval_seconds": True},
        {"interval_seconds": "86400"},
        {"interval_seconds": 21_599},
        {"interval_seconds": 604_801},
        {"source_result_id": "PRIVATE-WATCH-CONTENT"},
        {"source_result_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
        {"owner_session_hash": "PRIVATE-WATCH-CONTENT"},
    ],
)
def test_invalid_watch_input_never_echoes_private_body_or_creates_watch(watches, changes):
    _, factory = watches
    client = factory(enabled=True)
    response = client.post(
        PREFIX,
        headers={**bootstrap(client), "Content-Type": "application/json"},
        content=json.dumps({"label": "Synthetic", "query": "Synthetic query", **changes}).encode(
            "ascii"
        ),
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_watch_request"}
    assert_private(response)
    assert "PRIVATE-WATCH-CONTENT" not in response.text
    assert client.get(PREFIX).json()["watches"] == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-CSRF-Token": "0" * 64},
        {"Origin": "https://another.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_every_watch_mutation_checks_csrf_and_origin(watches, headers):
    _, factory = watches
    client = factory(enabled=True)
    good = bootstrap(client)
    response = create(client, good)
    assert response.status_code == 201
    watch_id = response.json()["id"]
    supplied = {} if not headers else {**good, **headers}
    for suffix in ("pause", "resume", "check"):
        blocked = client.post(f"{PREFIX}/{watch_id}/{suffix}", headers=supplied)
        assert blocked.status_code == 403
        assert_private(blocked)
    assert client.get(f"{PREFIX}/{watch_id}").json()["enabled"] is True


def test_foreign_watch_and_cursors_are_indistinguishable_from_missing(watches):
    _, factory = watches
    own, other = factory(enabled=True), factory(enabled=True)
    own_id = create(own).json()["id"]
    foreign_id = create(other).json()["id"]
    for watch_id in (foreign_id, str(uuid.uuid4())):
        for suffix in ("", "/sources", "/ticks"):
            response = own.get(f"{PREFIX}/{watch_id}{suffix}")
            assert response.status_code == 404
            assert response.json() == {"detail": "watch_not_found"}
        for suffix in ("pause", "resume", "check"):
            response = own.post(f"{PREFIX}/{watch_id}/{suffix}", headers=bootstrap(own))
            assert response.status_code == 404
            assert response.json() == {"detail": "watch_not_found"}
    cursor = own.get(PREFIX, params={"before": foreign_id})
    assert cursor.status_code == 404
    assert own.get(f"{PREFIX}/{own_id}/sources", params={"before": uuid.uuid4()}).status_code == 404
    assert own.get(f"{PREFIX}/{own_id}/ticks", params={"before": uuid.uuid4()}).status_code == 404
    assert own.get(f"{PREFIX}/{own_id}/sources/{uuid.uuid4()}").status_code == 404


def test_pause_and_read_work_while_collection_is_off_but_resume_and_checks_do_not(watches):
    _, factory = watches
    enabled = {"value": True}
    client = factory(availability=lambda: enabled["value"])
    watch_id = create(client).json()["id"]
    headers = bootstrap(client)
    enabled["value"] = False
    paused = client.post(f"{PREFIX}/{watch_id}/pause", headers=headers)
    assert paused.status_code == 200 and not paused.json()["enabled"]
    for suffix in ("resume", "check"):
        response = client.post(f"{PREFIX}/{watch_id}/{suffix}", headers=headers)
        assert response.status_code == 503
        assert response.json() == {"detail": "ingestion_execution_disabled"}
    assert client.get(f"{PREFIX}/{watch_id}").status_code == 200
    assert client.get(f"{PREFIX}/{watch_id}/ticks").status_code == 200
    enabled["value"] = True
    resumed = client.post(f"{PREFIX}/{watch_id}/resume", headers=headers)
    assert resumed.status_code == 200 and resumed.json()["enabled"]


def test_history_is_bounded_and_cursor_pagination_does_not_repeat_rows(watches):
    _, factory = watches
    client = factory(enabled=True)
    ids = {
        create(client, label=f"Synthetic watch {index}", query=f"Synthetic {index}").json()["id"]
        for index in range(3)
    }
    first = client.get(PREFIX, params={"limit": 2}).json()
    assert len(first["watches"]) == 2 and first["next_cursor"] == first["watches"][-1]["id"]
    second = client.get(PREFIX, params={"limit": 2, "before": first["next_cursor"]}).json()
    assert len(second["watches"]) == 1 and second["next_cursor"] is None
    assert {watch["id"] for watch in [*first["watches"], *second["watches"]]} == ids
    for parameters in ({"limit": 21}, {"limit": 0}, {"before": "PRIVATE-WATCH-CONTENT"}):
        response = client.get(PREFIX, params=parameters)
        assert response.status_code == 422
        assert "PRIVATE-WATCH-CONTENT" not in response.text


def test_expired_browser_loses_watch_access_and_cannot_queue_checks(watches):
    engine, factory = watches
    client = factory(enabled=True)
    watch_id = create(client).json()["id"]
    headers = bootstrap(client)
    with Session(engine) as session, session.begin():
        session.execute(
            update(BrowserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    for suffix in ("", f"/{watch_id}", f"/{watch_id}/sources", f"/{watch_id}/ticks"):
        assert client.get(PREFIX + suffix).status_code == 401
    assert client.post(f"{PREFIX}/{watch_id}/check", headers=headers).status_code == 401


def test_manual_check_queues_only_free_task_kind_and_is_throttled(watches):
    engine, factory = watches
    client = factory(enabled=True)
    watch_id = create(client).json()["id"]
    headers = bootstrap(client)
    response = client.post(f"{PREFIX}/{watch_id}/check", headers=headers)
    assert response.status_code == 201, response.text
    tick = response.json()
    assert tick["watch_id"] == watch_id and tick["status"] == "queued"
    with Session(engine) as session:
        run = session.get(ResearchRun, uuid.UUID(tick["run_id"]))
        assert run.task_kind == "ingest_literature"
        assert session.get(ResearchRunAccess, run.id) is not None
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 1
    repeated = client.post(f"{PREFIX}/{watch_id}/check", headers=headers)
    assert repeated.status_code == 429
    assert repeated.json()["detail"] in ("watch_check_throttled", "ingestion_active_run_limit")
    assert client.get(f"{PREFIX}/{watch_id}/ticks").json()["ticks"][0]["run_id"] == tick["run_id"]


def test_manual_checks_respect_shared_research_queue_limit(watches):
    engine, factory = watches
    client = factory(enabled=True)
    watch_id = create(client).json()["id"]
    headers = bootstrap(client)
    assert submit(client, headers).status_code == 201
    response = client.post(f"{PREFIX}/{watch_id}/check", headers=headers)
    assert response.status_code == 429
    assert response.json() == {"detail": "ingestion_active_run_limit"}
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 1


def test_live_watch_quota_returns_explicit_limit_without_more_rows(watches):
    _, factory = watches
    client = factory(enabled=True)
    for index in range(10):
        assert create(client, label=f"Synthetic watch {index}").status_code == 201
    response = create(client, label="Synthetic watch over the limit")
    assert response.status_code == 429
    assert response.json() == {"detail": "ingestion_watch_limit"}
    assert len(client.get(PREFIX).json()["watches"]) == 10


def test_pausing_cancels_active_tick_and_paused_watch_cannot_queue_another(watches):
    engine, factory = watches
    client = factory(enabled=True)
    watch_id = create(client).json()["id"]
    headers = bootstrap(client)
    queued = client.post(f"{PREFIX}/{watch_id}/check", headers=headers)
    assert queued.status_code == 201
    run_id = uuid.UUID(queued.json()["run_id"])
    paused = client.post(f"{PREFIX}/{watch_id}/pause", headers=headers)
    assert paused.status_code == 200 and paused.json()["enabled"] is False
    assert paused.json()["active_run_id"] is None
    blocked = client.post(f"{PREFIX}/{watch_id}/check", headers=headers)
    assert blocked.status_code == 409 and blocked.json() == {"detail": "watch_paused"}
    assert JobRepository(engine).get(run_id).status == "cancelled"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 1


def test_storage_capacity_is_explicit_and_not_empty_literature(watches, monkeypatch):
    _, factory = watches
    client = factory(enabled=True)
    watch_id = create(client).json()["id"]

    def full(*args, **kwargs):
        raise IngestionError("ingestion_watch_source_limit")

    monkeypatch.setattr(IngestionRepository, "run_now", full)
    response = client.post(f"{PREFIX}/{watch_id}/check", headers=bootstrap(client))
    assert response.status_code == 429
    assert response.json() == {"detail": "ingestion_watch_source_limit"}
    assert_private(response)
    assert client.get(f"{PREFIX}/{watch_id}/ticks").json()["ticks"] == []


def test_saved_source_lists_are_bounded_and_body_requires_exact_owned_watch_link(watches):
    engine, factory = watches
    client, foreign = factory(enabled=True), factory(enabled=True)
    watch_id = create(client).json()["id"]
    documents = tuple(
        source_document(f"fixture:watch-source-{index}", title=f"Synthetic watched paper {index}")
        for index in range(3)
    )
    artifacts = save_fixture_page(engine, client, watch_id, documents)
    first = client.get(f"{PREFIX}/{watch_id}/sources", params={"limit": 2})
    assert first.status_code == 200
    assert_private(first)
    page = first.json()
    assert len(page["sources"]) == 2 and page["next_cursor"]
    assert all("document" not in item and "text" not in item for item in page["sources"])
    assert all(item["assessment_status"] == "unassessed" for item in page["sources"])
    second = client.get(
        f"{PREFIX}/{watch_id}/sources", params={"limit": 2, "before": page["next_cursor"]}
    ).json()
    assert len(second["sources"]) == 1 and second["next_cursor"] is None
    assert {item["id"] for item in [*page["sources"], *second["sources"]]} == {
        str(artifact.version_id) for artifact in artifacts
    }
    original = documents[0]
    document = client.get(f"{PREFIX}/{watch_id}/sources/{artifacts[0].version_id}")
    assert document.status_code == 200
    assert_private(document)
    body = document.json()
    assert body["document"] == original.model_dump(mode="json")
    assert body["content_sha256"] == original.content_sha256
    assert body["version_id"] == body["id"] == str(artifacts[0].version_id)
    assert body["retrieval_id"] == str(artifacts[0].retrieval_id)
    assert len(body["metadata_sha256"]) == 64
    assert body["assessment_status"] == "unassessed"
    empty_watch = create(client, label="Another owned query", query="Other synthetic query").json()[
        "id"
    ]
    assert (
        client.get(f"{PREFIX}/{empty_watch}/sources/{artifacts[0].version_id}").status_code == 404
    )
    bootstrap(foreign)
    assert foreign.get(f"{PREFIX}/{watch_id}/sources/{artifacts[0].version_id}").status_code == 404
    assert client.get(f"{PREFIX}/{watch_id}").json()["source_count"] == 3


def test_shared_source_bytes_never_leak_another_watch_retrieval_metadata(watches):
    engine, factory = watches
    own, other = factory(enabled=True), factory(enabled=True)
    own_watch = create(own).json()["id"]
    other_watch = create(other).json()["id"]
    own_document = source_document("fixture:shared-watch-paper", title="Owned synthetic title")
    other_document = own_document.model_copy(update={"title": "Private foreign retrieval title"})
    own_artifact = save_fixture_page(engine, own, own_watch, (own_document,))[0]
    other_artifact = save_fixture_page(engine, other, other_watch, (other_document,))[0]
    assert own_artifact.version_id == other_artifact.version_id
    assert own_artifact.retrieval_id != other_artifact.retrieval_id
    detail = own.get(f"{PREFIX}/{own_watch}/sources/{own_artifact.version_id}")
    assert detail.json()["document"]["title"] == "Owned synthetic title"
    assert detail.json()["retrieval_id"] == str(own_artifact.retrieval_id)
    assert "Private foreign" not in detail.text
    assert own.get(f"{PREFIX}/{other_watch}/sources/{own_artifact.version_id}").status_code == 404


def test_concurrent_manual_checks_cannot_duplicate_work(watches):
    engine, factory = watches
    client = factory(enabled=True)
    watch_id = create(client).json()["id"]
    headers = bootstrap(client)
    start = Barrier(2)

    def check():
        start.wait(timeout=5)
        return client.post(f"{PREFIX}/{watch_id}/check", headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: check(), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 429]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 1


def test_browser_expiry_after_http_authentication_still_blocks_repository_write(
    watches, monkeypatch
):
    engine, factory = watches
    client = factory(enabled=True)
    watch_id = create(client).json()["id"]
    headers = bootstrap(client)
    entered, release = Event(), Event()
    original = IngestionRepository.run_now

    def wait_before_store(repository, *args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(repository, *args, **kwargs)

    monkeypatch.setattr(IngestionRepository, "run_now", wait_before_store)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, f"{PREFIX}/{watch_id}/check", headers=headers)
        try:
            assert entered.wait(timeout=5)
            with Session(engine) as session, session.begin():
                session.execute(
                    update(BrowserSession).values(
                        expires_at=datetime.now(UTC) - timedelta(seconds=1)
                    )
                )
        finally:
            release.set()
        response = future.result(timeout=5)
    assert response.status_code in (401, 404, 409)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0
