"""Owned HTTP reads of genuine committed stages; no native or provider calls."""

import hashlib
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_discovery_controller import DiscoveryFixtureModel
from test_discovery_worker import queued, worker
from test_research_controller import source
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.assessment_api import create_assessment_router
from polymath.budget_models import BudgetReservation
from polymath.discovery_api import create_discovery_router
from polymath.discovery_models import StageCommit
from polymath.discovery_store import DiscoveryStore
from polymath.evidence import EvidenceStore
from polymath.evidence_models import EvidenceResult, SourceRetrieval
from polymath.jobs import JobRepository
from polymath.knowledge import create_knowledge_router
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.research.meta import MetaModel
from polymath.run_api import COOKIE_NAME, create_run_router

database = database_fixture
pytestmark = pytest.mark.integration


def private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert "set-cookie" not in response.headers


def counts(engine):
    with Session(engine) as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                BrowserSession,
                ResearchRun,
                ResearchExternalRequest,
                BudgetReservation,
                StageCommit,
                EvidenceResult,
                SourceRetrieval,
            )
        )


@pytest.fixture
async def discovery_api(database, monkeypatch):
    token, run_id = queued(database)
    outcome = await worker(database, DiscoveryFixtureModel(stop_after_stage=1)).run_once(
        "discovery-http-fixture"
    )
    assert outcome.status == "succeeded"
    with ExitStack() as clients:

        def client(*, owned=True, session=True):
            app = FastAPI()
            app.include_router(create_run_router(database))
            app.include_router(create_discovery_router(database))
            app.include_router(create_assessment_router(database))
            value = clients.enter_context(TestClient(app))
            if session:
                response = value.get("/api/v1/session")
                assert response.status_code == 200
                if owned:
                    identity = hashlib.sha256(value.cookies[COOKIE_NAME].encode()).hexdigest()
                    with Session(database) as db, db.begin():
                        db.execute(
                            update(ResearchRunAccess)
                            .where(ResearchRunAccess.run_id == run_id)
                            .values(session_hash=identity)
                        )
            return value

        owned_client = client()

        def forbidden(*args, **kwargs):
            pytest.fail("Discovery inspection constructed a native model")

        monkeypatch.setattr(MetaModel, "__init__", forbidden)
        yield database, run_id, owned_client, client


def paths(run_id, stage_id):
    prefix = f"/api/v1/runs/{run_id}/discovery"
    return prefix, prefix + "/stages", prefix + f"/stages/{stage_id}"


async def test_owned_summary_list_and_exact_stage_are_read_only(discovery_api):
    engine, run_id, client, _ = discovery_api
    with Session(engine) as session:
        stage = session.scalar(select(StageCommit).where(StageCommit.run_id == run_id))
    before = counts(engine)
    summary, listing, detail = [client.get(path) for path in paths(run_id, stage.id)]
    for response in (summary, listing, detail):
        assert response.status_code == 200, response.text
        private(response)
    assert summary.json()["selected_final_stage"]["stage_id"] == str(stage.id)
    assert summary.json()["total_usage"]["model_calls"] == 8
    assert detail.json() == listing.json()["stages"][0]
    assert detail.json()["stage"]["result_id"] == str(stage.result_id)
    assert detail.json()["result"]["status"] == "bridge"
    assert detail.json()["status"] == "current_supported"
    assert len(detail.json()["primary_paper_groups"]) == 2
    head = client.get(f"/api/v1/assessments/heads/{stage.result_id}")
    assert head.status_code == 200, head.text
    assert head.headers["cache-control"] == "no-store"
    assert "set-cookie" not in head.headers
    assert head.json()["discovery_stage"] == {
        "stage_id": str(stage.id),
        "ordinal": 0,
        "depth": 1,
        "status": "current_supported",
    }
    assert counts(engine) == before


async def test_committed_stage_is_readable_after_whole_run_cancellation(database):
    _, run_id = queued(database)
    outcome = await worker(database, DiscoveryFixtureModel(), calls=7).run_once("partial-http")
    assert outcome.status == "interrupted"
    JobRepository(database).request_cancel(run_id)
    app = FastAPI()
    app.include_router(create_run_router(database))
    app.include_router(create_discovery_router(database))
    with TestClient(app) as client:
        client.get("/api/v1/session")
        identity = hashlib.sha256(client.cookies[COOKIE_NAME].encode()).hexdigest()
        with Session(database) as session, session.begin():
            session.execute(
                update(ResearchRunAccess)
                .where(ResearchRunAccess.run_id == run_id)
                .values(session_hash=identity)
            )
            stage = session.scalar(select(StageCommit).where(StageCommit.run_id == run_id))
            stage_id = stage.id
        summary, listing, detail = [client.get(path) for path in paths(run_id, stage_id)]
        assert all(response.status_code == 200 for response in (summary, listing, detail))
        assert summary.json()["run_status"] == "cancelled"
        assert summary.json()["selected_final_stage"] is None
        assert detail.json()["status"] == "current_supported"


async def test_foreign_missing_and_wrong_run_stage_are_generic_404(discovery_api):
    engine, run_id, owned, factory = discovery_api
    with Session(engine) as session:
        stage = session.scalar(select(StageCommit).where(StageCommit.run_id == run_id))
    foreign = factory(owned=False)
    before = counts(engine)
    for path in paths(run_id, stage.id):
        response = foreign.get(path)
        assert response.status_code == 404
        assert response.json() == {"detail": "discovery_not_found"}
        private(response)
    for path in paths(uuid.uuid4(), stage.id):
        response = owned.get(path)
        assert response.status_code == 404
        assert response.json() == {"detail": "discovery_not_found"}
    response = owned.get(paths(run_id, uuid.uuid4())[-1])
    assert response.status_code == 404
    assert counts(engine) == before


async def test_unauthenticated_read_does_not_create_browser_session(discovery_api):
    engine, run_id, _, factory = discovery_api
    client = factory(session=False)
    before = counts(engine)
    response = client.get(f"/api/v1/runs/{run_id}/discovery")
    assert response.status_code == 401
    private(response)
    assert not client.cookies and counts(engine) == before


async def test_expired_owner_cannot_read_historical_stages(discovery_api):
    engine, run_id, client, _ = discovery_api
    identity = hashlib.sha256(client.cookies[COOKIE_NAME].encode()).hexdigest()
    with Session(engine) as session, session.begin():
        session.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == identity)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        stage = session.scalar(select(StageCommit).where(StageCommit.run_id == run_id))
        stage_id = stage.id
    before = counts(engine)
    for path in paths(run_id, stage_id):
        response = client.get(path)
        assert response.status_code == 401
        private(response)
    assert counts(engine) == before


@pytest.mark.parametrize("bad", ["bad", "A" * 36, "00000000000000000000000000000000"])
async def test_malformed_identifiers_are_static_private_errors(discovery_api, bad):
    _, run_id, client, _ = discovery_api
    for path in (
        f"/api/v1/runs/{bad}/discovery",
        f"/api/v1/runs/{run_id}/discovery/stages/{bad}",
    ):
        response = client.get(path)
        assert response.status_code == 422
        assert response.json() == {"detail": "invalid_discovery_request"}
        private(response)


async def test_storage_error_never_echoes_private_goal_or_database_text(discovery_api, monkeypatch):
    engine, run_id, client, _ = discovery_api

    def broken(*args, **kwargs):
        raise RuntimeError("secret owner goal and postgres credential")

    before = counts(engine)
    monkeypatch.setattr(DiscoveryStore, "summary", broken)
    response = client.get(f"/api/v1/runs/{run_id}/discovery")
    assert response.status_code == 503
    assert response.json() == {"detail": "discovery_storage_unavailable"}
    private(response)
    assert counts(engine) == before


async def test_partial_and_needs_review_heads_return_exact_owned_stage_proof(database):
    _, run_id = queued(database)
    assert (
        await worker(database, DiscoveryFixtureModel(), calls=7).run_once("head-stage-prefix")
    ).status == "interrupted"
    app = FastAPI()
    app.include_router(create_run_router(database))
    app.include_router(create_assessment_router(database))
    app.include_router(create_knowledge_router(database))
    with TestClient(app) as client:
        client.get("/api/v1/session")
        identity = hashlib.sha256(client.cookies[COOKIE_NAME].encode()).hexdigest()
        with Session(database) as session, session.begin():
            session.execute(
                update(ResearchRunAccess)
                .where(ResearchRunAccess.run_id == run_id)
                .values(session_hash=identity)
            )
            stage = session.scalar(select(StageCommit).where(StageCommit.run_id == run_id))
            stage_id, result_id = str(stage.id), str(stage.result_id)
        head_path = f"/api/v1/assessments/heads/{result_id}"
        current = client.get(head_path)
        assert current.status_code == 200, current.text
        assert current.json()["can_generate"] is True
        assert current.json()["discovery_stage"] == {
            "stage_id": stage_id,
            "ordinal": 0,
            "depth": 1,
            "status": "current_supported",
        }
        snapshot = JobRepository(database).get(run_id)
        JobRepository(database).resume_interrupted(run_id, expected_token=snapshot.lease_token)
        lease = JobRepository(database).claim_next("later-head-identity", lease_seconds=300)
        connector = source("left", "Later metadata aliases the previously separate handles.")
        connector = connector.model_copy(update={"url": "https://example.org/fixtures/right"})
        EvidenceStore(database).save_source(lease, connector)
        before = counts(database)
        reviewed = client.get(head_path)
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.headers["cache-control"] == "no-store"
        assert "set-cookie" not in reviewed.headers
        assert reviewed.json()["state"] == "supported"
        assert reviewed.json()["is_current"] is True
        assert reviewed.json()["is_supported"] is False
        assert reviewed.json()["is_historical"] is False
        assert reviewed.json()["can_generate"] is False
        assert reviewed.json()["discovery_stage"] == {
            "stage_id": stage_id,
            "ordinal": 0,
            "depth": 1,
            "status": "needs_review",
        }
        version = client.get(f"/api/v1/assessments/findings/{result_id}")
        assert version.status_code == 200, version.text
        assert version.json()["discovery_stage"]["status"] == "needs_review"
        assert version.json()["assessment"] == reviewed.json()
        graph = client.get("/api/v1/knowledge/graph")
        assert graph.status_code == 200, graph.text
        finding = next(node for node in graph.json()["nodes"] if node["kind"] == "finding")
        assert finding["discovery_stage"]["status"] == "needs_review"
        quoted_sources = [
            node
            for node in graph.json()["nodes"]
            if node["kind"] == "source" and node.get("assessment_result_id") == result_id
        ]
        assert len(quoted_sources) == 2
        assert all(node["assessment_status"] == "saved_snapshot" for node in quoted_sources)
        library = client.get("/api/v1/knowledge", params={"limit": 50})
        assert library.status_code == 200, library.text
        assessed_sources = [
            item
            for item in library.json()["items"]
            if item["type"] == "source" and item.get("assessment_result_id") == result_id
        ]
        assert len(assessed_sources) == 2
        assert all(item["assessment_status"] == "saved_snapshot" for item in assessed_sources)
        assert counts(database) == before
