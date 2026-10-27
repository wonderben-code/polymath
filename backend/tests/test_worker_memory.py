"""Injected memory journeys use synthetic saved sources, never a live model."""

import asyncio
import json
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from test_memory import assign, complete, owner
from test_worker import database as database_fixture
from test_worker import setup_worker

from polymath.access_models import BrowserSession
from polymath.budget import BudgetService
from polymath.evidence import EvidenceStore
from polymath.jobs import JobRepository
from polymath.memory import ResearchMemory
from polymath.memory_models import ResearchMemoryRead
from polymath.research.controller import ResearchLimits
from polymath.research.meta import ModelReply
from polymath.research.models import SearchResults

database = database_fixture
pytestmark = pytest.mark.integration


class MemoryFixture:
    def __init__(self):
        self.seen = []

    async def complete(self, messages, *, max_output_tokens):
        observations = [
            json.loads(item["content"])["observation"]
            for item in messages
            if item["role"] == "user" and "observation" in json.loads(item["content"])
        ]
        latest = observations[-1] if observations else {}
        self.seen.append(latest)
        finding = next(
            (item for item in observations if item.get("kind") == "memory_finding"), None
        )
        if not observations:
            action = {"kind": "search_memory", "query": "Synthetic memory bridge"}
        elif latest.get("kind") == "memory_search_results":
            action = {
                "kind": "read_memory_finding",
                "result_id": latest["findings"][0]["result_id"],
            }
        elif latest.get("kind") in ("memory_finding", "source_text"):
            read = {item.get("memory_retrieval_id") for item in observations}
            remaining = [item for item in finding["sources"] if item["retrieval_id"] not in read]
            action = (
                {
                    "kind": "read_memory_source",
                    "result_id": finding["reference"]["result_id"],
                    "retrieval_id": remaining[0]["retrieval_id"],
                }
                if remaining
                else {
                    "kind": "search_literature",
                    "query": "Synthetic memory objection",
                    "purpose": "counterevidence",
                }
            )
        elif latest.get("purpose") == "counterevidence":
            action = {
                "kind": "search_literature",
                "query": "Synthetic memory prior work",
                "purpose": "prior_work",
            }
        else:
            action = {"kind": "conclude", "conclusion": finding["finding"]}
        return ModelReply(json.dumps(action), 10, 15)


class ChallengeTools:
    def __init__(self, pause=False):
        self.pause = pause
        self.started = asyncio.Event()

    async def search(self, query, purpose):
        assert purpose in ("counterevidence", "prior_work")
        if self.pause and purpose == "counterevidence":
            self.started.set()
            await asyncio.Event().wait()
        return SearchResults(sources=())

    async def read(self, source_id):
        raise AssertionError("Original memory sources must not be fetched from a provider")


def prepare(engine):
    token = owner(engine)
    parent, _ = complete(engine, token)
    run = JobRepository(engine).create(mode="explore", goal="Synthetic research-memory exercise")
    assign(engine, run.id, token)
    return token, parent, run


@pytest.mark.asyncio
async def test_memory_worker_reuses_exact_sources_and_persists_access_provenance(database):
    _, parent, run = prepare(database)
    model = MemoryFixture()
    worker = setup_worker(database, model, tools_factory=lambda _: ChallengeTools())
    outcome = await worker.run_once("memory-fixture-worker")
    assert outcome.status == "succeeded"
    saved = EvidenceStore(database).get_result(run.id, uuid.UUID(outcome.result_id))
    assert saved.payload["status"] == "bridge"
    assert saved.payload["memory_reads"][0]["result_id"] == str(parent.id)
    original = EvidenceStore(database).list_sources(parent.run_id)
    current = EvidenceStore(database).list_sources(run.id)
    assert {item.retrieval_id for item in original} == {item.retrieval_id for item in current}
    assert len(saved.payload["quotes"]) == 2
    events = JobRepository(database).events_after(run.id)
    assert [item.kind for item in events].count("memory_source_read") == 2
    assert BudgetService(database).usage("fixture").reservations == len(model.seen) == 7


@pytest.mark.asyncio
async def test_memory_restart_replays_search_and_originals_without_new_model_charges(
    database, monkeypatch
):
    _, parent, run = prepare(database)
    model, tools = MemoryFixture(), ChallengeTools(pause=True)
    worker = setup_worker(database, model, tools_factory=lambda _: tools)
    task = asyncio.create_task(worker.run_once("memory-before-restart"))
    await asyncio.wait_for(tools.started.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    snapshot = JobRepository(database).get(run.id)
    assert snapshot.status == "interrupted"
    assert BudgetService(database).usage("fixture").reservations == 5
    # Replay must use the frozen search observation, even if the current query
    # would now fail. Finding/source bodies come from their exact immutable IDs.
    monkeypatch.setattr(
        ResearchMemory, "search", lambda *args: pytest.fail("Memory search was repeated on replay")
    )
    JobRepository(database).resume_interrupted(run.id, expected_token=snapshot.lease_token)
    resumed = MemoryFixture()
    worker.model_factory = lambda _: resumed
    worker.tools_factory = lambda _: ChallengeTools()
    outcome = await worker.run_once("memory-after-restart")
    assert outcome.status == "succeeded"
    assert len(resumed.seen) == 2
    assert BudgetService(database).usage("fixture").reservations == 7
    with Session(database) as session:
        reads = session.scalars(
            select(ResearchMemoryRead).where(ResearchMemoryRead.run_id == run.id)
        ).all()
        assert [item.parent_result_id for item in reads] == [parent.id]


@pytest.mark.asyncio
async def test_expired_session_cannot_revive_cached_memory_or_make_another_dispatch(database):
    token, _, run = prepare(database)
    tools = ChallengeTools(pause=True)
    worker = setup_worker(database, MemoryFixture(), tools_factory=lambda _: tools)
    task = asyncio.create_task(worker.run_once("memory-before-expiry"))
    await asyncio.wait_for(tools.started.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    snapshot = JobRepository(database).get(run.id)
    with Session(database) as session, session.begin():
        session.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == token)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    JobRepository(database).resume_interrupted(run.id, expected_token=snapshot.lease_token)
    resumed = MemoryFixture()
    worker.model_factory = lambda _: resumed
    worker.tools_factory = lambda _: ChallengeTools()
    assert (await worker.run_once("memory-expired-restart")).status == "interrupted"
    assert resumed.seen == []
    assert BudgetService(database).usage("fixture").reservations == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tool_timeout", "unknown_response"])
async def test_committed_memory_read_response_loss_resumes_without_duplicate_model_charge(
    database, monkeypatch, failure
):
    _, parent, run = prepare(database)
    committed, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = ResearchMemory.read_finding

    def commit_then_lose_response(memory, result_id):
        try:
            found = original(memory, result_id)
            committed.set()
            if failure == "unknown_response":
                raise OSError("synthetic lost database response after commit")
            # Bound the thread even if an assertion fails; cancellation cannot
            # stop a database thread that has already committed its receipt.
            if not release.wait(timeout=5):
                raise TimeoutError("synthetic read response was not released")
            return found
        finally:
            finished.set()

    monkeypatch.setattr(ResearchMemory, "read_finding", commit_then_lose_response)
    model = MemoryFixture()
    worker = setup_worker(database, model, tools_factory=lambda _: ChallengeTools())
    worker.limits = ResearchLimits(tool_timeout_seconds=0.5)
    task = asyncio.create_task(worker.run_once("memory-lost-read-response"))
    try:
        assert await asyncio.to_thread(committed.wait, 5)
        outcome = await asyncio.wait_for(task, timeout=5)
        assert outcome.status == "interrupted"
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    snapshot = JobRepository(database).get(run.id)
    assert snapshot.status == "interrupted"
    assert not snapshot.external_request_in_flight
    assert "memory-0002" not in snapshot.checkpoint["tool_observations"]
    with Session(database) as session:
        reads = session.scalars(
            select(ResearchMemoryRead).where(ResearchMemoryRead.run_id == run.id)
        ).all()
        assert [item.parent_result_id for item in reads] == [parent.id]
    usage = BudgetService(database).usage("fixture")
    assert usage.reservations == len(model.seen) == 2
    assert usage.committed_microdollars == 50
    assert usage.in_flight == 0

    monkeypatch.setattr(ResearchMemory, "read_finding", original)
    JobRepository(database).resume_interrupted(run.id, expected_token=snapshot.lease_token)
    resumed = MemoryFixture()
    worker.model_factory = lambda _: resumed
    outcome = await worker.run_once("memory-idempotent-read-resume")
    assert outcome.status == "succeeded"
    assert len(resumed.seen) == 5
    assert BudgetService(database).usage("fixture").reservations == 7
    saved = EvidenceStore(database).get_result(run.id, uuid.UUID(outcome.result_id))
    assert saved.payload["memory_reads"][0]["result_id"] == str(parent.id)
