"""Full PostgreSQL worker journeys with explicitly synthetic models and sources."""

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_research_controller import AdaptiveFixture, Tools

from polymath.budget import BudgetService
from polymath.budget_models import BudgetReservation
from polymath.evidence import EvidenceStore
from polymath.jobs import JobRepository
from polymath.research.meta import ModelError, ModelReply
from polymath.worker import ResearchWorker, WorkerPolicy

pytestmark = pytest.mark.integration


@pytest.fixture
def database(monkeypatch):
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL for real PostgreSQL worker checks")
    url = make_url(raw)
    schema = "polymath_worker_" + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    engine = create_engine(scoped, hide_parameters=True)
    try:
        command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def setup_worker(engine, model, *, enabled=True, tools_factory=None):
    budget = BudgetService(engine)
    budget.create_account(
        "fixture", hard_cap_microdollars=10_000, daily_cap_microdollars=10_000, max_in_flight=1
    )
    budget.authorize_execution("fixture", pricing_reference="synthetic-test-pricing-only")
    policy = WorkerPolicy(
        account_id="fixture",
        model_identity="fixture-adaptive-v1",
        reservation_microdollars=100,
        input_microdollars_per_million=1_000_000,
        output_microdollars_per_million=1_000_000,
        run_cap_microdollars=5_000,
        lease_seconds=3,
        heartbeat_seconds=0.05,
    )
    return ResearchWorker(
        engine,
        model_factory=lambda _: model,
        tools_factory=tools_factory or (lambda _: Tools()),
        policy=policy,
        execution_enabled=enabled,
    )


@pytest.mark.asyncio
async def test_disabled_worker_never_claims_or_invokes_model(database):
    run = JobRepository(database).create(mode="explore", goal="Synthetic test")
    worker = setup_worker(database, AdaptiveFixture(), enabled=False)
    assert (await worker.run_once("disabled-worker")).status == "disabled"
    assert JobRepository(database).get(run.id).status == "queued"
    assert BudgetService(database).usage("fixture").reservations == 0


@pytest.mark.asyncio
async def test_complete_worker_persists_evidence_events_result_and_cost(database):
    jobs = JobRepository(database)
    run = jobs.create(mode="explore", goal="Synthetic redundancy protocol exercise")
    model = AdaptiveFixture()
    worker = setup_worker(database, model)
    outcome = await worker.run_once("fixture-worker")
    assert outcome.status == "succeeded"
    saved = jobs.get(run.id)
    assert saved.status == "succeeded"
    result = EvidenceStore(database).get_result(run.id, uuid.UUID(outcome.result_id))
    assert result.payload["status"] == "bridge"
    assert len(result.payload["quotes"]) == 2
    assert len(EvidenceStore(database).list_sources(run.id)) == 2
    assert all(source["content_kind"] == "abstract" for source in result.payload["sources"])
    events = jobs.events_after(run.id)
    assert any(event.kind == "source_read" for event in events)
    assert events[-1].kind == "succeeded"
    usage = BudgetService(database).usage("fixture")
    assert usage.reservations == len(model.seen)
    assert usage.committed_microdollars == 25 * len(model.seen)
    assert usage.in_flight == 0
    assert (await worker.run_once("another-worker")).status == "idle"


@pytest.mark.asyncio
async def test_ambiguous_model_failure_interrupts_and_retains_cost(database):
    class AmbiguousModel:
        calls = 0

        async def complete(self, messages, *, max_output_tokens):
            self.calls += 1
            raise ModelError("model_timeout_outcome_unknown")

    jobs = JobRepository(database)
    run = jobs.create(mode="explore", goal="Synthetic timeout exercise")
    model = AmbiguousModel()
    outcome = await setup_worker(database, model).run_once("fixture-worker")
    assert outcome.status == "interrupted"
    assert model.calls == 1
    assert jobs.get(run.id).external_request_in_flight
    usage = BudgetService(database).usage("fixture")
    assert usage.committed_microdollars == 100
    assert usage.in_flight == 1
    assert jobs.claim_next("unsafe-retry") is None


@pytest.mark.asyncio
async def test_cancellation_during_model_wait_keeps_unknown_hold(database):
    called = asyncio.Event()

    class WaitingModel:
        async def complete(self, messages, *, max_output_tokens):
            called.set()
            await asyncio.Event().wait()

    jobs = JobRepository(database)
    run = jobs.create(mode="explore", goal="Synthetic cancellation exercise")
    task = asyncio.create_task(setup_worker(database, WaitingModel()).run_once("fixture-worker"))
    await asyncio.wait_for(called.wait(), timeout=5)
    await asyncio.to_thread(jobs.request_cancel, run.id)
    outcome = await asyncio.wait_for(task, timeout=5)
    assert outcome.status == "cancelled"
    assert jobs.get(run.id).external_request_in_flight
    assert BudgetService(database).usage("fixture").committed_microdollars == 100
    assert BudgetService(database).usage("fixture").in_flight == 1


@pytest.mark.asyncio
async def test_restart_replays_observed_model_response_without_duplicate_charge(database):
    class StatefulTools(Tools):
        def __init__(self):
            super().__init__()
            self.discovered = set()

        async def search(self, query, purpose):
            matches = await super().search(query, purpose)
            self.discovered.update(item.source_id for item in matches)
            return matches

        async def read(self, source_id):
            if source_id not in self.discovered:
                raise ValueError("Source was never discovered by this new provider instance")
            return await super().read(source_id)

    class PausingTools(StatefulTools):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()

        async def read(self, source_id):
            if source_id == "right":
                self.started.set()
                await asyncio.Event().wait()
            return await super().read(source_id)

    jobs = JobRepository(database)
    run = jobs.create(mode="explore", goal="Synthetic restart exercise")
    first_model, tools = AdaptiveFixture(), PausingTools()
    worker = setup_worker(database, first_model, tools_factory=lambda _: tools)
    task = asyncio.create_task(worker.run_once("fixture-worker-one"))
    await asyncio.wait_for(tools.started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    interrupted = jobs.get(run.id)
    assert interrupted.status == "interrupted"
    assert not interrupted.external_request_in_flight
    with Session(database) as session:
        before = list(session.scalars(select(BudgetReservation)))
        assert len(before) == 3  # Search + two read actions; the first source is durable.
    jobs.resume_interrupted(run.id, expected_token=interrupted.lease_token)
    second_model = AdaptiveFixture()
    worker.model_factory = lambda _: second_model
    worker.tools_factory = lambda _: StatefulTools()
    outcome = await worker.run_once("fixture-worker-two")
    assert outcome.status == "succeeded"
    with Session(database) as session:
        rows = list(session.scalars(select(BudgetReservation)))
        assert len(rows) == 6
    assert len(second_model.seen) == 3
    assert BudgetService(database).usage("fixture").committed_microdollars == 150


@pytest.mark.asyncio
async def test_unsupported_mode_fails_explicitly_without_model_calls(database):
    run = JobRepository(database).create(mode="ask", goal="Synthetic unsupported-mode exercise")
    outcome = await setup_worker(database, AdaptiveFixture()).run_once("fixture-worker")
    assert outcome.status == "failed"
    assert JobRepository(database).get(run.id).outcome["error_code"] == "unsupported_research_mode"
    assert BudgetService(database).usage("fixture").reservations == 0


@pytest.mark.asyncio
async def test_worker_cannot_bypass_disabled_budget_account(database):
    jobs = JobRepository(database)
    run = jobs.create(mode="explore", goal="Synthetic disabled-account exercise")
    model = AdaptiveFixture()
    worker = setup_worker(database, model)
    BudgetService(database).disable_execution("fixture")
    outcome = await worker.run_once("fixture-worker")
    assert outcome.status == "interrupted"
    assert model.seen == []
    assert not jobs.get(run.id).external_request_in_flight
    assert BudgetService(database).usage("fixture").reservations == 0


@pytest.mark.asyncio
async def test_worker_loop_shutdown_retains_pending_operation_without_replay(database):
    called = asyncio.Event()

    class WaitingModel:
        async def complete(self, messages, *, max_output_tokens):
            called.set()
            await asyncio.Event().wait()

    jobs = JobRepository(database)
    run = jobs.create(mode="explore", goal="Synthetic worker shutdown exercise")
    worker = setup_worker(database, WaitingModel())
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_until_stopped("fixture-worker", stop, poll_seconds=0.01))
    await asyncio.wait_for(called.wait(), timeout=5)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert jobs.get(run.id).status == "interrupted"
    assert jobs.get(run.id).external_request_in_flight
    assert BudgetService(database).usage("fixture").in_flight == 1


@pytest.mark.asyncio
async def test_unusable_response_content_still_accounts_known_usage(database):
    class InvalidContent:
        async def complete(self, messages, *, max_output_tokens):
            return ModelReply("invalid\x00content", 10, 15)

    jobs = JobRepository(database)
    run = jobs.create(mode="explore", goal="Synthetic invalid-response accounting")
    outcome = await setup_worker(database, InvalidContent()).run_once("fixture-worker")
    assert outcome.status == "failed"
    assert not jobs.get(run.id).external_request_in_flight
    usage = BudgetService(database).usage("fixture")
    assert usage.committed_microdollars == 25
    assert usage.in_flight == 0
