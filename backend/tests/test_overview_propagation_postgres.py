"""Bound Ask metadata in real owned memory, knowledge and immutable public grants."""

import json
import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_memory import assign, current, owner
from test_overview_propagation import bound_answer
from test_run_api import bootstrap
from test_sharing_projection import answer_result
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.budget_models import BudgetReservation
from polymath.evidence import EvidenceStore
from polymath.jobs import JobRepository
from polymath.knowledge import create_knowledge_router
from polymath.memory import MemoryError
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.research.models import MemoryFinding
from polymath.run_api import create_run_router
from polymath.scientific_context_contracts import AskOverviewIntent
from polymath.scientific_context_store import bind_ask_overview_intent
from polymath.sharing import SharingError, SharingPolicy, SharingStore
from polymath.sharing_contracts import classified_answer_navigation_summary
from polymath.sharing_models import SharedFinding
from polymath.worker import _RecordedMemory, _RunContext

database = database_fixture
pytestmark = pytest.mark.integration


def save_answer(engine, token, *, bound=True):
    jobs, evidence = JobRepository(engine), EvidenceStore(engine)
    result = bound_answer() if bound else answer_result()
    run = jobs.create(mode="ask", goal=result.finding.question)
    assign(engine, run.id, token)
    if bound:
        with Session(engine) as session, session.begin():
            bind_ask_overview_intent(
                session,
                run=session.get(ResearchRun, run.id),
                owner_hash=token,
                intent=AskOverviewIntent(),
            )
    lease = jobs.claim_next("synthetic-overview-source", lease_seconds=300)
    assert lease.run_id == run.id
    for document in result.sources:
        evidence.save_source(lease, document)
    saved = evidence.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    return saved, result


@contextmanager
def owner_client(engine):
    app = FastAPI()
    app.include_router(create_run_router(engine, availability=lambda: True))
    app.include_router(create_knowledge_router(engine))
    with TestClient(app) as client:
        bootstrap(client)
        with Session(engine) as session:
            token = session.scalar(select(BrowserSession.token_hash))
        yield client, token


def test_full_owned_memory_preserves_binding_and_exact_sources_without_extra_authority(database):
    token = owner(database)
    saved, original = save_answer(database, token)
    memory, _ = current(database, token)
    search = memory.search("original Ask")
    assert len(search.findings) == 1
    neutral = classified_answer_navigation_summary(claim.label for claim in original.finding.claims)
    assert search.findings[0].summary == neutral
    found = memory.read_finding(str(saved.id))
    assert found.overview_binding == original.overview_binding
    assert found.finding == original.finding and found.reference.summary == neutral
    assert MemoryFinding.model_validate_json(found.model_dump_json()) == found
    assert len(found.sources) == 2
    document = memory.read_source(str(saved.id), found.sources[0].retrieval_id)
    assert document.content_sha256 == found.sources[0].content_sha256
    assert document.text in [source.text for source in original.sources]
    foreign, _ = current(database, owner(database, "foreign-overview-owner"))
    with pytest.raises(MemoryError, match="memory_finding_not_found"):
        foreign.read_finding(str(saved.id))
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 0
        assert session.scalar(select(func.count()).select_from(ResearchExternalRequest)) == 0


def test_owned_knowledge_and_public_library_use_neutral_navigation_not_clipped_claims(database):
    with owner_client(database) as (client, token):
        saved, result = save_answer(database, token)
        data = client.get("/api/v1/knowledge").json()
        item = next(item for item in data["items"] if item["result_id"] == str(saved.id))
        neutral = classified_answer_navigation_summary(
            claim.label for claim in result.finding.claims
        )
        assert item["summary"] == neutral
        assert all(claim.text not in item["summary"] for claim in result.finding.claims)
        store = SharingStore(database, SharingPolicy(enabled=True))
        preview = store.preview(token, saved.id)
        shared = store.share(token, saved.id, preview.projection_sha256, uuid.uuid4())
        detail = store.get_public(shared.public_id)
        assert detail["projection"]["overview_binding"]["claim_ids"] == ["claim-3", "claim-1"]
        listing = store.list_public()
        assert listing["items"][0]["summary"] == neutral
        public = json.dumps({"detail": detail, "list": listing, "map": store.public_map()})
        for private in (
            token,
            str(saved.id),
            str(saved.run_id),
            *result.overview_binding.claim_ids,
            *(source.source_id for source in result.sources),
        ):
            assert private not in public


def test_bound_and_legacy_public_snapshots_remain_distinct_immutable_history(database):
    token = owner(database)
    legacy, _ = save_answer(database, token, bound=False)
    store = SharingStore(database, SharingPolicy(enabled=True))
    old_preview = store.preview(token, legacy.id)
    old = store.share(token, legacy.id, old_preview.projection_sha256, uuid.uuid4())
    original_bytes = json.dumps(old.projection, sort_keys=True, separators=(",", ":"))
    bound, _ = save_answer(database, token)
    preview = store.preview(token, bound.id)
    new = store.share(token, bound.id, preview.projection_sha256, uuid.uuid4())
    assert "overview_binding" not in old.projection
    assert new.projection["overview_binding"]["claim_ids"] == ["claim-3", "claim-1"]
    assert (
        json.dumps(
            store.get_public(old.public_id)["projection"], sort_keys=True, separators=(",", ":")
        )
        == original_bytes
    )
    with Session(database) as session:
        row = session.scalar(select(SharedFinding).where(SharedFinding.public_id == old.public_id))
        assert row.projection_sha256 == old.projection_sha256
        assert json.dumps(row.projection, sort_keys=True, separators=(",", ":")) == original_bytes
    store.revoke(token, new.public_id)
    with pytest.raises(SharingError, match="sharing_not_found"):
        store.get_public(new.public_id)
    assert store.get_public(old.public_id)["projection_sha256"] == old.projection_sha256
    with Session(database) as session:
        row = session.scalar(select(SharedFinding).where(SharedFinding.public_id == new.public_id))
        assert row.projection == preview.projection
        assert row.projection_sha256 == preview.projection_sha256


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [False, True])
async def test_recorded_search_replays_exact_payload_without_rebuilding_observation(
    database, monkeypatch, bound
):
    token = owner(database)
    _, result = save_answer(database, token, bound=bound)
    memory, lease = current(database, token)
    context = _RunContext(database, lease, 300)
    first = await _RecordedMemory(context, memory).search("original Ask")
    cache_before = JobRepository(database).get(lease.run_id).checkpoint["tool_observations"]

    def forbidden(*args):
        pytest.fail("Cached search was rebuilt from current projection code")

    monkeypatch.setattr(memory, "search", forbidden)
    replayed = await _RecordedMemory(context, memory).search("original Ask")
    cache_after = JobRepository(database).get(lease.run_id).checkpoint["tool_observations"]
    assert replayed.model_dump_json() == first.model_dump_json()
    assert cache_after == cache_before
    if bound:
        assert first.findings[0].summary == classified_answer_navigation_summary(
            claim.label for claim in result.finding.claims
        )
    else:
        assert first.findings[0].summary == result.finding.summary[:1000]


def test_expired_owner_cannot_recover_private_overview_or_public_grant(database):
    token = owner(database)
    saved, _ = save_answer(database, token)
    store = SharingStore(database, SharingPolicy(enabled=True))
    preview = store.preview(token, saved.id)
    shared = store.share(token, saved.id, preview.projection_sha256, uuid.uuid4())
    memory, _ = current(database, token)
    with Session(database) as session, session.begin():
        session.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == token)
            .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )
    assert memory.search("original Ask").findings == ()
    with pytest.raises(MemoryError, match="memory_finding_not_found"):
        memory.read_finding(str(saved.id))
    with pytest.raises(SharingError, match="sharing_not_found"):
        store.get_public(shared.public_id)
