"""One ordinary worker/ledger through model-ranked Explore collections and replay."""

import asyncio
from threading import Event

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_discovery_controller import DiscoveryFixtureTools
from test_explore_collection_controller import (
    GOAL,
    RecordingModel,
    insufficient,
    prefix,
    ranking_action,
    second_branch,
)
from test_memory import assign, owner
from test_sharing_projection import answer_result, bridge_result
from test_worker import database as database_fixture
from test_worker import setup_worker

from polymath.budget import BudgetService
from polymath.discovery_models import StageCommit
from polymath.evidence import EvidenceStore
from polymath.evidence_models import EvidenceResult
from polymath.explore_collection_admission import bind_explore_collection_intent
from polymath.explore_collection_contracts import ExploreCollectionIntent
from polymath.explore_collection_models import (
    ExploreCollectionFinish,
    ExploreRankingRecord,
)
from polymath.explore_collection_store import ExploreCollectionStore
from polymath.jobs import JobRepository
from polymath.memory_models import ResearchMemoryRead
from polymath.models import ResearchRun
from polymath.research.controller import ResearchLimits
from polymath.research.meta import ModelError

pytestmark = pytest.mark.integration
database = database_fixture


def queued(engine, *, token=None, maximum=3):
    token = token or owner(engine)
    run = JobRepository(engine).create(mode="explore", goal=GOAL)
    assign(engine, run.id, token)
    with Session(engine) as session, session.begin():
        bind_explore_collection_intent(
            session, session.get(ResearchRun, run.id), ExploreCollectionIntent(max_bridges=maximum)
        )
    return token, run.id


def worker(engine, model, *, calls=20, actions=16):
    value = setup_worker(engine, model, tools_factory=lambda _: DiscoveryFixtureTools())
    value.limits = ResearchLimits(
        max_model_calls=calls, max_actions=actions, max_output_tokens=4096
    )
    return value


def rows(engine, kind, run_id):
    with Session(engine) as session:
        statement = select(kind).where(kind.run_id == run_id)
        if kind is StageCommit:
            statement = statement.order_by(kind.ordinal)
        elif kind is ExploreRankingRecord:
            statement = statement.order_by(kind.action_ordinal)
        return session.scalars(statement).all()


def counts(engine, run_id):
    return {
        kind.__name__: len(rows(engine, kind, run_id))
        for kind in (StageCommit, ExploreRankingRecord, ExploreCollectionFinish, EvidenceResult)
    }


async def test_model_order_differs_from_confidence_and_all_bridges_share_whole_run_usage(database):
    _, run_id = queued(database)
    model = RecordingModel([*prefix(notes=True), *second_branch(), ranking_action])
    outcome = await worker(database, model).run_once("model-ranked-explore")
    assert outcome.status == "succeeded", (
        model.calls,
        JobRepository(database).get(run_id).interruption_reason,
        [(event.kind, event.summary) for event in JobRepository(database).events_after(run_id)],
    )
    stages = rows(database, StageCommit, run_id)
    assert [item.depth for item in stages] == [1, 1]
    assert [item.model_request_id for item in stages] == ["model-0009", "model-0014"]
    ranking = rows(database, ExploreRankingRecord, run_id)[0]
    assert ranking.receipt["ranked"][0]["reference"]["result_id"] == str(stages[1].result_id)
    assert ranking.receipt["ranked"][1]["reference"]["result_id"] == str(stages[0].result_id)
    assert (
        stages[0].receipt["assessment"]["confidence_score"]
        > (stages[1].receipt["assessment"]["confidence_score"])
    )
    assert outcome.result_id == str(stages[1].result_id)
    assert counts(database, run_id) == {
        "StageCommit": 2,
        "ExploreRankingRecord": 1,
        "ExploreCollectionFinish": 1,
        "EvidenceResult": 2,
    }
    usage = BudgetService(database).usage("fixture")
    assert model.calls == usage.reservations == 15
    assert usage.committed_microdollars == 375 and usage.in_flight == 0
    snapshot = JobRepository(database).get(run_id)
    assert snapshot.outcome["result_id"] == outcome.result_id
    saved = EvidenceStore(database).get_result(run_id, stages[1].result_id)
    assert saved.payload["usage"]["model_calls"] == 14
    assert any(
        event.kind == "research_note" and event.summary.startswith("discarded:")
        for event in JobRepository(database).events_after(run_id)
    )


async def test_selected_earlier_bridge_does_not_hide_later_work_or_create_a_duplicate_result(
    database,
):
    _, run_id = queued(database)
    model = RecordingModel(
        [*prefix(), *second_branch(), lambda messages: ranking_action(messages, order=(0, 1))]
    )
    outcome = await worker(database, model).run_once("prefer-earlier-bridge")
    stages = rows(database, StageCommit, run_id)
    assert outcome.status == "succeeded" and outcome.result_id == str(stages[0].result_id)
    assert len(rows(database, EvidenceResult, run_id)) == 2
    original = EvidenceStore(database).get_result(run_id, stages[0].result_id)
    assert original.payload["usage"]["model_calls"] == 7
    assert model.calls == BudgetService(database).usage("fixture").reservations == 13
    summary = ExploreCollectionStore(database).summary(owner(database), run_id)
    assert summary["total_usage"]["model_calls"] == 13
    assert summary["accounting"]["counted_spend_microdollars"] == "325"


@pytest.mark.parametrize("operation", ["commit", "rank", "finish"])
async def test_lost_atomic_acknowledgement_replays_original_prefix_without_duplicate_charge(
    database, monkeypatch, operation
):
    _, run_id = queued(database)
    original, first = getattr(ExploreCollectionStore, operation), True

    def persist_then_lose_reply(store, *args, **kwargs):
        nonlocal first
        value = original(store, *args, **kwargs)
        if first:
            first = False
            raise RuntimeError("synthetic committed acknowledgement lost")
        return value

    monkeypatch.setattr(ExploreCollectionStore, operation, persist_then_lose_reply)
    actions = [*prefix()]
    if operation == "rank":
        actions.append(lambda messages: ranking_action(messages, finish=False))
    actions.append(ranking_action)
    initial = RecordingModel(actions)
    instance = worker(database, initial)
    assert (await instance.run_once("lost-collection-ack")).status == "interrupted"
    committed = rows(database, StageCommit, run_id)
    assert len(committed) == 1
    saved_id = committed[0].result_id
    charged_before = {"commit": 7, "rank": 8, "finish": 8}[operation]
    assert initial.calls == BudgetService(database).usage("fixture").reservations == charged_before
    snapshot = JobRepository(database).get(run_id)
    JobRepository(database).resume_interrupted(run_id, expected_token=snapshot.lease_token)
    replacement = RecordingModel([] if operation == "finish" else [ranking_action])
    instance.model_factory = lambda _: replacement
    result = await instance.run_once("explicit-collection-replay")
    assert result.status == "succeeded" and result.result_id == str(saved_id)
    assert replacement.calls == (0 if operation == "finish" else 1)
    assert (
        len(rows(database, StageCommit, run_id)) == len(rows(database, EvidenceResult, run_id)) == 1
    )
    assert len(rows(database, ExploreCollectionFinish, run_id)) == 1
    assert (
        BudgetService(database).usage("fixture").reservations == charged_before + replacement.calls
    )


async def test_call_limit_preserves_unranked_bridge_without_application_final_selection(database):
    _, run_id = queued(database)
    model = RecordingModel([*prefix(), ranking_action])
    result = await worker(database, model, calls=7).run_once("bounded-collection")
    assert result.status == "interrupted" and result.result_id is None
    assert len(rows(database, StageCommit, run_id)) == 1
    assert rows(database, ExploreRankingRecord, run_id) == []
    assert rows(database, ExploreCollectionFinish, run_id) == []
    assert model.calls == BudgetService(database).usage("fixture").reservations == 7
    assert not (JobRepository(database).get(run_id).outcome or {}).get("result_id")


async def test_empty_and_all_excluded_rankings_never_invent_a_top_bridge(database):
    _, run_id = queued(database)
    model = RecordingModel([lambda messages: ranking_action(messages, order=())])
    instance = worker(database, model)
    result = await instance.run_once("honest-empty-collection")
    assert result.status == "succeeded" and result.result_id is None
    assert rows(database, StageCommit, run_id) == []
    assert rows(database, ExploreCollectionFinish, run_id)[0].selected_result_id is None
    _, next_id = queued(database)
    model = RecordingModel(
        [*prefix(), lambda messages: ranking_action(messages, order=(), excluded=(0,))]
    )
    instance.model_factory = lambda _: model
    result = await instance.run_once("model-excludes-saved-bridge")
    assert result.status == "succeeded" and result.result_id is None
    assert len(rows(database, StageCommit, next_id)) == 1
    assert rows(database, ExploreCollectionFinish, next_id)[0].selected_result_id is None


async def test_insufficient_next_branch_retains_history_without_a_final_ranking(database):
    _, run_id = queued(database)
    model = RecordingModel([*prefix(), insufficient()])
    result = await worker(database, model).run_once("honest-partial-collection")
    assert result.result_id is None
    assert len(rows(database, StageCommit, run_id)) == 1
    assert rows(database, ExploreCollectionFinish, run_id) == []
    assert model.calls == BudgetService(database).usage("fixture").reservations == 8


async def test_cancellation_after_interim_rank_preserves_it_and_stops_before_another_call(
    database, monkeypatch
):
    _, run_id = queued(database)
    original = ExploreCollectionStore.rank
    loop, committed = asyncio.get_running_loop(), asyncio.Event()
    release, returned = Event(), Event()

    def rank_before_cancel(store, *args, **kwargs):
        value = original(store, *args, **kwargs)
        loop.call_soon_threadsafe(committed.set)
        try:
            assert release.wait(timeout=30)
            return value
        finally:
            returned.set()

    monkeypatch.setattr(ExploreCollectionStore, "rank", rank_before_cancel)
    model = RecordingModel(
        [*prefix(), lambda messages: ranking_action(messages, finish=False), ranking_action]
    )
    task = asyncio.create_task(worker(database, model).run_once("cancel-ranked-collection"))
    try:
        await asyncio.wait_for(committed.wait(), timeout=10)
        JobRepository(database).request_cancel(run_id)
        release.set()
        result = await asyncio.wait_for(task, timeout=10)
        assert result.status == "cancelled" and result.result_id is None
        assert model.calls == BudgetService(database).usage("fixture").reservations == 8
        assert len(rows(database, StageCommit, run_id)) == 1
        assert len(rows(database, ExploreRankingRecord, run_id)) == 1
        assert rows(database, ExploreCollectionFinish, run_id) == []
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(returned.wait, 10)


async def test_unknown_model_request_keeps_partial_bridge_and_never_automatically_resends(database):
    _, run_id = queued(database)
    model = RecordingModel([*prefix(), ModelError("model_request_failed")])
    instance = worker(database, model)
    result = await instance.run_once("unknown-collection-call")
    assert result.status == "interrupted" and result.result_id is None
    assert len(rows(database, StageCommit, run_id)) == 1
    assert rows(database, ExploreCollectionFinish, run_id) == []
    assert model.calls == 8
    assert BudgetService(database).usage("fixture").reservations == 8
    assert JobRepository(database).get(run_id).external_request_in_flight
    assert (await instance.run_once("never-auto-resume")).status == "idle"


@pytest.mark.parametrize("kind", ["bridge", "answer"])
async def test_ordinary_memory_leads_keep_actual_receipts_without_becoming_stage_parents(
    database, kind
):
    token = owner(database)
    prior = bridge_result() if kind == "bridge" else answer_result()
    jobs = JobRepository(database)
    old = jobs.create(
        mode=prior.mode,
        goal=prior.finding.question if kind == "answer" else "Synthetic saved memory lead",
    )
    assign(database, old.id, token)
    lease = jobs.claim_next("save-original-memory-lead")
    evidence = EvidenceStore(database)
    for document in prior.sources:
        evidence.save_source(lease, document)
    saved = evidence.save_result(lease, prior)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    with Session(database) as session:
        prior_hash = session.get(EvidenceResult, saved.id).content_sha256
    source = evidence.list_sources(old.id)[0]
    _, run_id = queued(database, token=token)
    actions = [
        prefix()[0],
        {
            "kind": "search_memory",
            "query": prior.finding.question if kind == "answer" else prior.finding.title,
        },
        {"kind": "read_memory_finding", "result_id": str(saved.id)},
        {
            "kind": "read_memory_source",
            "result_id": str(saved.id),
            "retrieval_id": str(source.retrieval_id),
        },
        *prefix()[1:],
        ranking_action,
    ]
    model = RecordingModel(actions)
    result = await worker(database, model).run_once("ordinary-memory-collection")
    assert result.status == "succeeded"
    stages = rows(database, StageCommit, run_id)
    assert len(stages) == 1 and stages[0].receipt["parent_refs"] == []
    current = evidence.get_result(run_id, stages[0].result_id)
    assert current.payload["memory_reads"] == [
        {"result_id": str(saved.id), "run_id": str(old.id), "content_sha256": prior_hash}
    ]
    with Session(database) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResearchMemoryRead)
                .where(ResearchMemoryRead.run_id == run_id)
            )
            == 1
        )
    assert model.calls == BudgetService(database).usage("fixture").reservations == 11
