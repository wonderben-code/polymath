"""Free ingestion worker journeys with synthetic feeds and real PostgreSQL.

The injected feed never calls a network or model. Provider transport boundaries
are tested separately with httpx.MockTransport.
"""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_memory import owner
from test_research_controller import source
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.budget_models import BudgetReservation
from polymath.evidence import EvidenceStore
from polymath.ingestion import IngestionRepository
from polymath.ingestion_worker import IngestionWorker, IngestionWorkerPolicy
from polymath.jobs import JobRepository
from polymath.literature.clients import DiscoveredSource
from polymath.literature.errors import ProviderError
from polymath.literature.feed import FeedPage
from polymath.research.models import SourceReference

database = database_fixture
pytestmark = pytest.mark.integration


class SyntheticFeed:
    def __init__(self):
        self.calls = []
        self.failures = {}
        self.paused_provider = None
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.metadata_only = False
        self.wrong_window = False
        self.exhausted = True

    async def fetch_page(self, provider, query, *, since, until, cursor=None, limit=5):
        self.calls.append((provider, query, since, until, cursor, limit))
        if provider == self.paused_provider:
            self.started.set()
            await self.release.wait()
        if provider in self.failures:
            raise ProviderError(provider, self.failures[provider])
        document = source(
            f"synthetic-feed:{provider}",
            f"Synthetic {provider} evidence preserves an original abstract snapshot.",
        )
        reference = SourceReference(
            **{name: getattr(document, name) for name in SourceReference.model_fields}
        )
        if self.metadata_only and provider == "openalex":
            records = (DiscoveredSource(reference, None),)
        else:
            records = (DiscoveredSource(reference, document),)
        return FeedPage(
            provider=provider,
            records=records,
            next_cursor=None
            if self.exhausted
            else (
                "next-page-2"
                if cursor and provider == "openalex"
                else "next-page-1"
                if provider == "openalex"
                else (cursor or 0) + 1
            ),
            exhausted=self.exhausted,
            since=since - timedelta(seconds=1)
            if self.wrong_window and provider == "openalex"
            else since,
            until=until,
        )


@pytest.fixture
def collection(database):
    token_hash = owner(database, "synthetic-ingestion-browser")
    repository = IngestionRepository(database)
    watch = repository.create(
        token_hash,
        label="Synthetic watched topic",
        query="Synthetic literature",
    )
    tick = repository.run_now(token_hash, watch.id)
    feed = SyntheticFeed()
    worker = IngestionWorker(
        database,
        tools_factory=lambda _: feed,
        execution_enabled=True,
        policy=IngestionWorkerPolicy(lease_seconds=30, heartbeat_seconds=0.05),
    )
    return database, token_hash, repository, watch, tick, feed, worker


@pytest.mark.asyncio
async def test_disabled_worker_never_claims_reaps_or_builds_tools(collection, monkeypatch):
    engine, _, _, _, tick, feed, worker = collection
    worker.execution_enabled = False
    monkeypatch.setattr(
        worker.jobs, "interrupt_expired", lambda: pytest.fail("Disabled reaper ran")
    )
    worker.tools_factory = lambda _: pytest.fail("Disabled worker built provider tools")
    assert (await worker.run_once("disabled-ingestion")).status == "disabled"
    assert JobRepository(engine).get(tick.run_id).status == "queued"
    assert feed.calls == []


@pytest.mark.asyncio
async def test_complete_tick_stores_exact_sources_and_zero_model_reservations(collection):
    engine, token, repository, watch, tick, feed, worker = collection
    outcome = await worker.run_once("synthetic-ingestion")
    assert outcome.status == "succeeded"
    assert outcome.sources_added == 2
    saved = JobRepository(engine).get(tick.run_id)
    assert saved.outcome["ingestion_status"] == "complete"
    assert saved.outcome["model_calls"] == 0
    assert saved.outcome["sources_seen"] == 2
    assert [call[0] for call in feed.calls] == ["openalex", "arxiv"]
    assert all(call[-1] == 5 for call in feed.calls)
    stored = EvidenceStore(engine).list_sources(tick.run_id)
    assert len(stored) == 2
    for artifact in stored:
        original = source(
            artifact.source_id,
            f"Synthetic {artifact.source_id.rsplit(':', 1)[-1]} evidence "
            "preserves an original abstract snapshot.",
        )
        assert EvidenceStore(engine).get_document(tick.run_id, artifact.retrieval_id) == original
    latest = repository.get(token, watch.id)
    assert latest.source_count == 2
    assert all(cursor.revision == 1 for cursor in latest.cursors.values())
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 0
    assert (await worker.run_once("no-more-ingestion")).status == "idle"


@pytest.mark.asyncio
async def test_provider_failure_preserves_only_its_cursor_and_reports_partial_coverage(collection):
    engine, token, repository, watch, tick, feed, worker = collection
    feed.failures["openalex"] = "rate_limited"
    assert (await worker.run_once("partially-available-ingestion")).status == "succeeded"
    saved = JobRepository(engine).get(tick.run_id)
    assert saved.outcome["ingestion_status"] == "partial"
    latest = repository.get(token, watch.id)
    assert latest.cursors["openalex"].revision == 0
    assert latest.cursors["arxiv"].revision == 1
    assert latest.source_count == 1
    assert saved.outcome["provider_results"][0]["error_code"] == "rate_limited"


@pytest.mark.asyncio
async def test_all_provider_failures_are_explicit_not_empty_evidence(collection):
    engine, token, repository, watch, tick, feed, worker = collection
    feed.failures = {"openalex": "access_denied", "arxiv": "timeout"}
    assert (await worker.run_once("all-unavailable-ingestion")).status == "failed"
    saved = JobRepository(engine).get(tick.run_id)
    assert saved.outcome["error_code"] == "all_providers_unavailable"
    assert saved.outcome["sources_added"] == 0
    assert all(cursor.revision == 0 for cursor in repository.get(token, watch.id).cursors.values())
    assert EvidenceStore(engine).list_sources(tick.run_id) == ()


@pytest.mark.asyncio
async def test_resume_skips_the_provider_already_committed_in_the_same_tick(collection):
    engine, token, repository, watch, tick, feed, worker = collection
    feed.paused_provider = "arxiv"
    task = asyncio.create_task(worker.run_once("ingestion-before-restart"))
    await asyncio.wait_for(feed.started.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    interrupted = JobRepository(engine).get(tick.run_id)
    assert interrupted.status == "interrupted"
    assert repository.get(token, watch.id).cursors["openalex"].revision == 1
    JobRepository(engine).resume_interrupted(tick.run_id, expected_token=interrupted.lease_token)
    second = SyntheticFeed()
    worker.tools_factory = lambda _: second
    result = await worker.run_once("ingestion-after-restart")
    assert result.status == "succeeded"
    assert [call[0] for call in second.calls] == ["arxiv"]
    assert result.sources_added == 2  # Includes the page committed before restart.
    assert JobRepository(engine).get(tick.run_id).outcome["sources_seen"] == 2


@pytest.mark.asyncio
async def test_source_persisted_before_failed_commit_can_resume_without_cursor_loss(
    collection, monkeypatch
):
    engine, token, repository, watch, tick, feed, worker = collection
    original = worker.repository.commit_page
    monkeypatch.setattr(
        worker.repository,
        "commit_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Synthetic process loss")),
    )
    assert (await worker.run_once("ingestion-crash-before-cursor")).status == "interrupted"
    assert len(EvidenceStore(engine).list_sources(tick.run_id)) == 1
    assert repository.get(token, watch.id).cursors["openalex"].revision == 0
    interrupted = JobRepository(engine).get(tick.run_id)
    JobRepository(engine).resume_interrupted(tick.run_id, expected_token=interrupted.lease_token)
    monkeypatch.setattr(worker.repository, "commit_page", original)
    assert (await worker.run_once("ingestion-recover-before-cursor")).status == "succeeded"
    assert repository.get(token, watch.id).source_count == 2
    assert len(EvidenceStore(engine).list_sources(tick.run_id)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expire", "pause"])
async def test_ownership_or_watch_pause_stops_after_inflight_get_before_any_source_write(
    collection, change
):
    engine, token, repository, watch, tick, feed, worker = collection
    feed.paused_provider = "openalex"
    task = asyncio.create_task(worker.run_once("ingestion-scope-lost"))
    await asyncio.wait_for(feed.started.wait(), timeout=10)
    if change == "pause":
        repository.pause(token, watch.id)
    else:
        with Session(engine) as session, session.begin():
            session.execute(
                update(BrowserSession)
                .where(BrowserSession.token_hash == token)
                .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
            )
    feed.release.set()
    outcome = await asyncio.wait_for(task, timeout=10)
    assert outcome.status in ("interrupted", "cancelled")
    assert len(feed.calls) == 1
    assert EvidenceStore(engine).list_sources(tick.run_id) == ()


@pytest.mark.asyncio
async def test_metadata_only_discoveries_are_not_fabricated_readable_sources(collection):
    engine, token, repository, watch, tick, feed, worker = collection
    feed.metadata_only = True
    assert (await worker.run_once("ingestion-metadata-only")).status == "succeeded"
    saved = JobRepository(engine).get(tick.run_id)
    assert saved.outcome["provider_results"][0]["metadata_only"] == 1
    assert saved.outcome["provider_results"][0]["sources_seen"] == 0
    assert repository.get(token, watch.id).source_count == 1
    assert len(EvidenceStore(engine).list_sources(tick.run_id)) == 1


@pytest.mark.asyncio
async def test_wrong_window_never_advances_its_provider_cursor(collection):
    engine, token, repository, watch, tick, feed, worker = collection
    feed.wrong_window = True
    assert (await worker.run_once("ingestion-wrong-window")).status == "succeeded"
    latest = repository.get(token, watch.id)
    assert latest.cursors["openalex"].revision == 0
    assert latest.cursors["arxiv"].revision == 1
    assert JobRepository(engine).get(tick.run_id).outcome["ingestion_status"] == "partial"


@pytest.mark.asyncio
async def test_worker_claims_only_free_ingestion_not_research(database):
    jobs = JobRepository(database)
    research = jobs.create(mode="explore", goal="Synthetic unrelated research")
    token = owner(database)
    repo = IngestionRepository(database)
    watch = repo.create(token, label="Synthetic watch", query="Synthetic topic")
    tick = repo.run_now(token, watch.id)
    feed = SyntheticFeed()
    worker = IngestionWorker(database, tools_factory=lambda _: feed, execution_enabled=True)
    assert (await worker.run_once("ingestion-only-lane")).run_id == str(tick.run_id)
    assert jobs.get(research.id).status == "queued"


@pytest.mark.asyncio
async def test_pacing_wait_is_bounded_and_timeout_never_issues_get(collection, monkeypatch):
    engine, token, repository, watch, tick, feed, worker = collection
    worker.policy = replace(worker.policy, provider_wait_seconds=0.01)
    monkeypatch.setattr(worker.repository, "acquire_provider_slot", lambda *args: 1.0)
    assert (await worker.run_once("ingestion-provider-paced")).status == "failed"
    assert feed.calls == []
    assert all(cursor.revision == 0 for cursor in repository.get(token, watch.id).cursors.values())
    assert (
        JobRepository(engine).get(tick.run_id).outcome["error_code"] == "all_providers_unavailable"
    )


@pytest.mark.asyncio
async def test_pacing_wait_rechecks_watch_before_get(collection, monkeypatch):
    engine, token, repository, watch, tick, feed, worker = collection
    called = False

    def gate(*args):
        nonlocal called
        if not called:
            called = True
            repository.pause(token, watch.id)
        return 0.01

    monkeypatch.setattr(worker.repository, "acquire_provider_slot", gate)
    assert (await worker.run_once("ingestion-paused-during-pacing")).status == "cancelled"
    assert feed.calls == []
    assert EvidenceStore(engine).list_sources(tick.run_id) == ()


@pytest.mark.asyncio
async def test_source_cap_finishes_partial_without_fetching_the_next_provider(
    collection, monkeypatch
):
    import polymath.ingestion as ingestion

    engine, token, repository, watch, tick, feed, worker = collection
    monkeypatch.setattr(ingestion, "MAX_WATCH_SOURCE_VERSIONS", 1)
    result = await worker.run_once("ingestion-source-cap")
    assert result.status == "succeeded"
    assert result.ingestion_status == "partial"
    assert [call[0] for call in feed.calls] == ["openalex"]
    assert feed.calls[0][-1] == 1
    latest = repository.get(token, watch.id)
    assert latest.source_count == 1
    assert latest.cursors["openalex"].revision == 1
    assert latest.cursors["arxiv"].revision == 0
    assert JobRepository(engine).get(tick.run_id).outcome["error_code"] == "source_limit_reached"


@pytest.mark.asyncio
async def test_next_tick_retains_unfinished_windows_and_deduplicates_source_versions(
    collection, monkeypatch
):
    import polymath.ingestion as ingestion

    engine, token, repository, watch, _, feed, worker = collection
    feed.exhausted = False
    assert (await worker.run_once("ingestion-first-page")).status == "succeeded"
    prior = repository.get(token, watch.id)
    with Session(engine) as session:
        future = session.scalar(select(func.clock_timestamp())) + timedelta(minutes=2)
    monkeypatch.setattr(ingestion, "_now", lambda _: future)
    second_tick = repository.run_now(token, watch.id)
    feed.calls.clear()
    result = await worker.run_once("ingestion-second-page")
    assert result.status == "succeeded"
    assert result.sources_added == 0
    assert repository.get(token, watch.id).source_count == 2
    for provider, _, since, until, cursor, _ in feed.calls:
        assert (since, until) == (prior.cursors[provider].since, prior.cursors[provider].until)
        assert cursor == prior.cursors[provider].cursor
    assert JobRepository(engine).get(second_tick.run_id).outcome["sources_seen"] == 2


@pytest.mark.asyncio
async def test_shutdown_interrupts_free_get_without_advancing_cursor(collection):
    engine, token, repository, watch, tick, feed, worker = collection
    feed.paused_provider = "openalex"
    stop = asyncio.Event()
    task = asyncio.create_task(
        worker.run_until_stopped("ingestion-shutdown", stop, poll_seconds=0.01)
    )
    await asyncio.wait_for(feed.started.wait(), timeout=10)
    stop.set()
    await asyncio.wait_for(task, timeout=10)
    assert JobRepository(engine).get(tick.run_id).status == "interrupted"
    assert all(cursor.revision == 0 for cursor in repository.get(token, watch.id).cursors.values())
    assert EvidenceStore(engine).list_sources(tick.run_id) == ()
