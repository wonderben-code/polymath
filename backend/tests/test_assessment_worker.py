"""Actual budgeted worker/assessment journeys with synthetic model intelligence."""

import asyncio
import json
import uuid
from contextlib import suppress
from threading import Event

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_assessment_controller import AssessmentFixtureModel
from test_assessments import enqueue, prepared
from test_evidence import document
from test_worker import database as database_fixture
from test_worker import setup_worker

from polymath.assessment_controller import AssessmentLimits
from polymath.assessment_models import FindingAssessment
from polymath.assessments import AssessmentStore
from polymath.budget import BudgetService
from polymath.evidence import EvidenceStore
from polymath.jobs import JobRepository
from polymath.research.meta import ModelError, ModelReply
from polymath.worker import ExecutionStopped, ResearchWorker, _RecordedAssessmentTools

database = database_fixture
pytestmark = pytest.mark.integration


def replacement(worker, model):
    return ResearchWorker(
        worker.engine,
        model_factory=lambda _: model,
        tools_factory=worker.tools_factory,
        policy=worker.policy,
        limits=worker.limits,
        artifact_limits=worker.artifact_limits,
        assessment_limits=worker.assessment_limits,
        execution_enabled=True,
    )


def queued(engine, *, contradicted=False):
    source = document(
        "fixture:new-worker-version",
        text=(
            "🌱 New synthetic evidence contradicts the earlier mapping."
            if contradicted
            else "🌱 New synthetic evidence leaves this fixture mapping unchanged."
        ),
    )
    token, prior, original, watch, versions, _ = prepared(engine, new_documents=(source,))
    lease = enqueue(engine, token, prior.id, watch.id, [versions[0].version_id])
    jobs = JobRepository(engine)
    interrupted = jobs.interrupt(lease)
    jobs.resume_interrupted(lease.run_id, expected_token=interrupted.lease_token)
    return token, prior, original, watch, versions, lease.run_id


@pytest.mark.parametrize("contradicted", [False, True])
@pytest.mark.asyncio
async def test_actual_new_source_observation_changes_model_judgement(database, contradicted):
    token, prior, _, _, _, run_id = queued(database, contradicted=contradicted)
    model = AssessmentFixtureModel()
    worker = setup_worker(database, model)
    outcome = await worker.run_once("synthetic-reassessment")
    assert outcome.status == "succeeded" and outcome.assessment_id
    saved = AssessmentStore(database).get(token, uuid.UUID(outcome.assessment_id))
    assert saved.applied and saved.disposition == ("unsupported" if contradicted else "unchanged")
    state = AssessmentStore(database).head(token, prior.id)
    assert state["state"] == ("unsupported" if contradicted else "supported")
    assert state["current_result_id"] == str(prior.id)
    assert len(saved.payload["verified_passages"]) == 2
    assert len(EvidenceStore(database).list_sources(run_id)) == 2
    assert BudgetService(database).usage("fixture").reservations == len(model.seen) == 3
    assert JobRepository(database).get(run_id).outcome["assessment_id"] == outcome.assessment_id


@pytest.mark.asyncio
async def test_revised_result_uses_actual_search_receipts_and_atomic_current_head(database):
    token, prior, _, _, _, run_id = queued(database)
    model = AssessmentFixtureModel(revised=True)
    outcome = await setup_worker(database, model).run_once("synthetic-revision")
    assert outcome.status == "succeeded" and outcome.assessment_id and outcome.result_id
    saved = AssessmentStore(database).get(token, uuid.UUID(outcome.assessment_id))
    assert saved.applied and str(saved.candidate_result_id) == outcome.result_id
    state = AssessmentStore(database).head(token, prior.id)
    assert state["current_result_id"] == outcome.result_id and state["revision"] == 1
    run = JobRepository(database).get(run_id)
    assert set(run.checkpoint["assessment_search_purposes"]) == {"counterevidence", "prior_work"}
    result = EvidenceStore(database).get_result(run_id, uuid.UUID(outcome.result_id))
    assert result.payload["finding"]["title"] == "Synthetic revised connection"
    assert BudgetService(database).usage("fixture").reservations == len(model.seen) == 6


@pytest.mark.asyncio
async def test_disabled_assessment_worker_never_claims_or_constructs_a_model(database):
    *_, run_id = queued(database)
    worker = setup_worker(database, AssessmentFixtureModel(), enabled=False)
    worker.model_factory = lambda _: pytest.fail("Disabled worker constructed a model")
    assert (await worker.run_once("disabled")).status == "disabled"
    assert JobRepository(database).get(run_id).status == "queued"
    assert BudgetService(database).usage("fixture").reservations == 0


class FreshReadModel(AssessmentFixtureModel):
    async def complete(self, messages, *, max_output_tokens):
        observations = [
            value["observation"]
            for message in messages
            if message["role"] == "user"
            if "observation" in (value := json.loads(message["content"]))
        ]
        discovery = next(
            (
                item
                for item in observations
                if item.get("kind") == "search_results" and item.get("purpose") == "discovery"
            ),
            None,
        )
        fresh = any(
            item.get("kind") == "assessment_source_text" and item.get("role") == "discovered"
            for item in observations
        )
        if discovery is None:
            action = {
                "kind": "search_literature",
                "query": "Synthetic fresh evidence",
                "purpose": "discovery",
            }
        elif not fresh:
            action = {
                "kind": "read_literature_source",
                "source_id": discovery["sources"][0]["source_id"],
            }
        else:
            return await super().complete(messages, max_output_tokens=max_output_tokens)
        return ModelReply(json.dumps(action), 10, 15)


@pytest.mark.parametrize("lose_response", [False, True])
@pytest.mark.asyncio
async def test_fresh_literature_read_uses_durable_assigned_handles_and_replay(
    database, monkeypatch, lose_response
):
    token, _, *_, run_id = queued(database)
    model = FreshReadModel()
    worker = setup_worker(database, model)
    original, first = EvidenceStore.save_source, True

    def save_source(store, lease, source):
        nonlocal first
        result = original(store, lease, source)
        if lose_response and first and source.source_id == "left":
            first = False
            raise RuntimeError("synthetic fresh read response loss after commit")
        return result

    monkeypatch.setattr(EvidenceStore, "save_source", save_source)
    outcome = await worker.run_once("fresh-assessor")
    if lose_response:
        assert outcome.status == "interrupted"
        snapshot = JobRepository(database).get(run_id)
        JobRepository(database).resume_interrupted(run_id, expected_token=snapshot.lease_token)
        outcome = await replacement(worker, model).run_once("fresh-replacement")
    assert outcome.status == "succeeded" and outcome.assessment_id
    saved = AssessmentStore(database).get(token, uuid.UUID(outcome.assessment_id))
    assert len(saved.payload["observed_sources"]) == 3
    assert any(item["source_id"] == "left" for item in saved.payload["observed_sources"])
    assert JobRepository(database).get(run_id).checkpoint["assessment_search_purposes"] == [
        "discovery"
    ]
    assert BudgetService(database).usage("fixture").reservations == 5


@pytest.mark.parametrize("failure", ["response_lost", "timeout"])
@pytest.mark.asyncio
async def test_committed_source_response_loss_interrupts_then_replays_without_charge(
    database, monkeypatch, failure
):
    *_, run_id = queued(database)
    model = AssessmentFixtureModel()
    worker = setup_worker(database, model)
    worker.assessment_limits = AssessmentLimits(tool_timeout_seconds=20)
    original, release, read_finished = AssessmentStore.read_source, Event(), Event()
    loop, committed = asyncio.get_running_loop(), asyncio.Event()
    first = True

    def interrupted_read(store, lease, retrieval_id):
        nonlocal first
        result = original(store, lease, retrieval_id)
        if first:
            first = False
            if failure == "timeout":
                loop.call_soon_threadsafe(committed.set)
                try:
                    release.wait(timeout=30)
                finally:
                    read_finished.set()
            else:
                raise RuntimeError("synthetic read response lost after commit")
        return result

    monkeypatch.setattr(AssessmentStore, "read_source", interrupted_read)
    if failure == "timeout":
        read_tool = _RecordedAssessmentTools.read_source_by_retrieval
        armed = True

        async def timeout_after_commit(tools, retrieval_id):
            nonlocal armed
            if not armed:
                return await read_tool(tools, retrieval_id)
            armed = False
            reading = asyncio.create_task(read_tool(tools, retrieval_id))
            try:
                # Keep normal DB/ownership work and replay on their real tool
                # deadline. Inject cancellation only after the target commit.
                await committed.wait()
                return await asyncio.wait_for(reading, timeout=0.01)
            finally:
                if not reading.done():
                    reading.cancel()
                    with suppress(ExecutionStopped, asyncio.CancelledError):
                        await reading

        monkeypatch.setattr(
            _RecordedAssessmentTools, "read_source_by_retrieval", timeout_after_commit
        )
    try:
        assert (await worker.run_once("first-assessor")).status == "interrupted"
    finally:
        release.set()
        if failure == "timeout" and committed.is_set():
            assert await asyncio.to_thread(read_finished.wait, timeout=5)
    assert BudgetService(database).usage("fixture").reservations == 1
    assert len(EvidenceStore(database).list_sources(run_id)) == 1
    snapshot = JobRepository(database).get(run_id)
    assert not any(
        value.get("error") for value in snapshot.checkpoint.get("tool_observations", {}).values()
    )
    JobRepository(database).resume_interrupted(run_id, expected_token=snapshot.lease_token)
    # Keep the same execution policy/fingerprint on resume.
    resumed = replacement(worker, model)
    assert (await resumed.run_once("replacement-assessor")).status == "succeeded"
    assert BudgetService(database).usage("fixture").reservations == len(model.seen) == 3


@pytest.mark.asyncio
async def test_unknown_model_outcome_retains_hold_and_does_not_apply_assessment(database):
    token, prior, *_, run_id = queued(database)

    class Ambiguous:
        async def complete(self, *_args, **_kwargs):
            raise ModelError("model_timeout_outcome_unknown")

    outcome = await setup_worker(database, Ambiguous()).run_once("unknown-assessor")
    assert outcome.status == "interrupted"
    assert JobRepository(database).get(run_id).external_request_in_flight
    assert BudgetService(database).usage("fixture").in_flight == 1
    assert AssessmentStore(database).head(token, prior.id)["revision"] == 0
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(FindingAssessment)) == 0


@pytest.mark.asyncio
async def test_final_commit_response_loss_preserves_one_applied_assessment(database, monkeypatch):
    token, prior, *_, run_id = queued(database)
    model, original = AssessmentFixtureModel(), AssessmentStore.save

    def lost_response(store, lease, result):
        original(store, lease, result)
        raise RuntimeError("synthetic final response lost after atomic commit")

    monkeypatch.setattr(AssessmentStore, "save", lost_response)
    worker = setup_worker(database, model)
    outcome = await worker.run_once("finishing-assessor")
    assert outcome.status == "succeeded"
    assert JobRepository(database).get(run_id).outcome["assessment_id"]
    assert AssessmentStore(database).head(token, prior.id)["revision"] == 1
    assert len(AssessmentStore(database).history(token, prior.id)) == 1
    assert (await replacement(worker, model).run_once("replacement")).status == "idle"
    assert BudgetService(database).usage("fixture").reservations == len(model.seen) == 3


@pytest.mark.asyncio
async def test_cooperative_shutdown_during_threaded_read_is_graceful(database, monkeypatch):
    *_, run_id = queued(database)
    original, started, release = AssessmentStore.read_source, Event(), Event()

    def held_read(store, lease, retrieval_id):
        result = original(store, lease, retrieval_id)
        started.set()
        release.wait(timeout=5)
        return result

    monkeypatch.setattr(AssessmentStore, "read_source", held_read)
    worker = setup_worker(database, AssessmentFixtureModel())
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_until_stopped("stopping-assessor", stop))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        stop.set()
        await asyncio.wait_for(task, timeout=5)
    finally:
        release.set()
    assert JobRepository(database).get(run_id).status == "interrupted"
    assert BudgetService(database).usage("fixture").reservations == 1
