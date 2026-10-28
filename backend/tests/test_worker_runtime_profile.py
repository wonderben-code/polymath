"""The shared worker resolves original policy before spending or constructing a model."""

import asyncio
from dataclasses import replace
from threading import Event

import pytest
from sqlalchemy.orm import Session
from test_research_controller import AdaptiveFixture
from test_worker import database as database_fixture
from test_worker import setup_worker

from polymath.artifact_controller import ArtifactLimits
from polymath.budget import BudgetService
from polymath.budget_models import BudgetRunLimit
from polymath.dispatch import DispatchCoordinator
from polymath.jobs import JobRepository
from polymath.research.controller import ResearchLimits
from polymath.research.meta import ModelReply
from polymath.worker import ExecutionStopped, WorkerRuntimeProfile, _RecordedModel

pytestmark = pytest.mark.integration
database = database_fixture


async def test_profile_control_stop_interrupts_before_model_or_reservation(database):
    jobs = JobRepository(database)
    run = jobs.create(mode="explore", goal="Synthetic original policy boundary")
    model = AdaptiveFixture()
    worker = setup_worker(database, model)

    def resolver(*_args):
        raise ExecutionStopped("original_policy_invalid")

    worker.runtime_profile_resolver = resolver
    outcome = await worker.run_once("profile-control-stop")
    assert outcome.status == "interrupted"
    assert jobs.get(run.id).lease_owner is None
    assert model.seen == [] and BudgetService(database).usage("fixture").reservations == 0


async def test_cancel_while_profile_lookup_waits_fences_claim_without_paid_work(database):
    jobs = JobRepository(database)
    run = jobs.create(mode="explore", goal="Synthetic profile cancellation boundary")
    model = AdaptiveFixture()
    worker = setup_worker(database, model)
    entered, release = Event(), Event()

    def resolver(*_args):
        entered.set()
        assert release.wait(5)
        return WorkerRuntimeProfile(worker.policy, worker.limits, worker.artifact_limits)

    worker.runtime_profile_resolver = resolver
    task = asyncio.create_task(worker.run_once("profile-cancelled"))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert jobs.get(run.id).status == "interrupted"
        assert jobs.get(run.id).lease_owner is None
        assert model.seen == [] and BudgetService(database).usage("fixture").reservations == 0
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_original_profile_drives_limits_budget_and_single_recorded_model_wrapper(database):
    jobs = JobRepository(database)
    run = jobs.create(mode="explore", goal="Synthetic original policy selection")
    worker = setup_worker(database, None)
    requested = []

    class Model:
        async def complete(self, messages, *, max_output_tokens):
            requested.append(max_output_tokens)
            return ModelReply(
                '{"kind":"conclude","conclusion":{"outcome":"insufficient_evidence",'
                '"reason":"Synthetic isolated profile test has no evidence."}}',
                10,
                15,
            )

    policy = replace(worker.policy, reservation_microdollars=50, run_cap_microdollars=2000)
    profile = WorkerRuntimeProfile(
        policy,
        ResearchLimits(max_model_calls=1, max_output_tokens=4096),
        ArtifactLimits(max_model_calls=1),
        protocol_binding={"fixture_profile": "original-v1"},
    )
    worker.runtime_profile_resolver = lambda *_: profile

    def forbidden(*_args):
        pytest.fail("The legacy eager model factory must not run with a recorded factory")

    worker.model_factory = forbidden
    worker.recorded_model_factory = lambda context, snapshot, original: _RecordedModel(
        context, DispatchCoordinator(database), Model(), original.policy
    )
    outcome = await worker.run_once("original-profile")
    assert outcome.status == "succeeded" and requested == [4096]
    usage = BudgetService(database).usage("fixture")
    assert usage.reservations == 1 and usage.committed_microdollars == 25
    with Session(database) as session:
        limit = session.get(BudgetRunLimit, ("fixture", run.id))
        assert limit.cap_microdollars == 2000 and limit.max_calls == 1
