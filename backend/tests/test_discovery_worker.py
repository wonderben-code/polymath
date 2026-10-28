"""One real run/ledger across committed Surprise stages and explicit replay."""

import asyncio
import json
import uuid
from threading import Event

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_discovery_controller import DiscoveryFixtureModel, DiscoveryFixtureTools
from test_memory import assign, owner
from test_worker import database as database_fixture
from test_worker import setup_worker

from polymath.budget import BudgetService
from polymath.discovery_admission import bind_discovery_intent
from polymath.discovery_contracts import DiscoveryIntent
from polymath.discovery_models import StageCommit
from polymath.discovery_store import DiscoveryStore
from polymath.evidence import EvidenceStore
from polymath.evidence_models import EvidenceResult
from polymath.jobs import JobRepository
from polymath.models import ResearchRun
from polymath.research.discovery import DiscoveryLimits
from polymath.research.meta import ModelReply

database = database_fixture
pytestmark = pytest.mark.integration


def queued(engine):
    token = owner(engine)
    run = JobRepository(engine).create(mode="surprise", goal="Synthetic automatic discovery")
    assign(engine, run.id, token)
    intent = DiscoveryIntent()
    with Session(engine) as session, session.begin():
        bind_discovery_intent(session, session.get(ResearchRun, run.id), intent)
    return token, run.id


def worker(engine, model, *, calls=20):
    value = setup_worker(engine, model, tools_factory=lambda _: DiscoveryFixtureTools())
    value.limits = DiscoveryLimits(max_model_calls=calls, max_actions=20, max_output_tokens=4096)
    return value


def commits(engine, run_id):
    with Session(engine) as session:
        return session.scalars(
            select(StageCommit).where(StageCommit.run_id == run_id).order_by(StageCommit.ordinal)
        ).all()


async def test_two_base_bridges_and_meta_bridge_share_one_global_twenty_call_run(database):
    token, run_id = queued(database)
    model = DiscoveryFixtureModel()
    outcome = await worker(database, model).run_once("one-discovery-run")
    assert outcome.status == "succeeded" and outcome.result_id
    saved = commits(database, run_id)
    assert len(saved) == 3 and [item.depth for item in saved] == [1, 1, 2]
    assert outcome.result_id == str(saved[-1].result_id)
    assert [item.model_request_id for item in saved] == ["model-0007", "model-0012", "model-0020"]
    assert model.calls == BudgetService(database).usage("fixture").reservations == 20
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(EvidenceResult)) == 3
    result = EvidenceStore(database).get_result(run_id, uuid.UUID(outcome.result_id))
    assert result.payload["usage"]["model_calls"] == 20
    snapshot = JobRepository(database).get(run_id)
    assert snapshot.status == "succeeded" and snapshot.outcome["result_id"] == outcome.result_id
    summary = DiscoveryStore(database).summary(token, run_id)
    assert summary["total_usage"]["model_calls"] == 20
    assert summary["accounting"]["counted_spend_microdollars"] == "500"


async def test_lost_atomic_stage_response_replays_receipt_without_duplicate_result_or_charge(
    database, monkeypatch
):
    token, run_id = queued(database)
    original, first = DiscoveryStore.commit, True

    def commit_then_lose_reply(store, lease, packet):
        nonlocal first
        receipt = original(store, lease, packet)
        if first:
            first = False
            raise RuntimeError("synthetic committed stage acknowledgement lost")
        return receipt

    monkeypatch.setattr(DiscoveryStore, "commit", commit_then_lose_reply)
    initial = DiscoveryFixtureModel(stop_after_stage=1)
    instance = worker(database, initial)
    assert (await instance.run_once("lost-stage-response")).status == "interrupted"
    rows = commits(database, run_id)
    assert len(rows) == 1
    original_result = rows[0].result_id
    assert initial.calls == BudgetService(database).usage("fixture").reservations == 7
    snapshot = JobRepository(database).get(run_id)
    JobRepository(database).resume_interrupted(run_id, expected_token=snapshot.lease_token)
    replacement = DiscoveryFixtureModel(stop_after_stage=1)
    instance.model_factory = lambda _: replacement
    outcome = await instance.run_once("explicit-stage-replay")
    assert outcome.status == "succeeded" and outcome.result_id == str(original_result)
    assert len(commits(database, run_id)) == 1
    assert replacement.calls == 1
    assert BudgetService(database).usage("fixture").reservations == 8
    assert DiscoveryStore(database).summary(token, run_id)["total_usage"]["model_calls"] == 8


async def test_first_stage_survives_limits_without_synthetic_final_selection(database):
    token, run_id = queued(database)
    outcome = await worker(database, DiscoveryFixtureModel(), calls=7).run_once("bounded-discovery")
    assert outcome.status == "interrupted" and outcome.result_id is None
    rows = commits(database, run_id)
    assert len(rows) == 1
    snapshot = JobRepository(database).get(run_id)
    assert not (snapshot.outcome or {}).get("result_id")
    assert BudgetService(database).usage("fixture").reservations == 7
    summary = DiscoveryStore(database).summary(token, run_id)
    assert summary["selected_final_stage"] is None and summary["run_status"] == "interrupted"


async def test_finishing_with_first_stage_does_not_copy_it_to_update_later_totals(database):
    class PreferFirst(DiscoveryFixtureModel):
        async def complete(self, messages, *, max_output_tokens):
            observations = [
                json.loads(item["content"]).get("observation")
                for item in messages
                if item["role"] == "user"
            ]
            stages = [
                item for item in observations if item and item.get("kind") == "stage_committed"
            ]
            if len(stages) == 2 and observations[-1]["kind"] == "stage_committed":
                self.calls += 1
                first = stages[0]
                return ModelReply(
                    json.dumps(
                        {
                            "kind": "finish_saved",
                            "stage": {
                                "result_id": first["result_id"],
                                "run_id": first["run_id"],
                                "content_sha256": first["result_sha256"],
                            },
                            "reason": "Prefer the first saved bridge after exploring another lead.",
                        }
                    ),
                    10,
                    15,
                )
            return await super().complete(messages, max_output_tokens=max_output_tokens)

    token, run_id = queued(database)
    model = PreferFirst()
    outcome = await worker(database, model).run_once("choose-earlier-stage")
    rows = commits(database, run_id)
    assert outcome.status == "succeeded" and len(rows) == 2
    assert outcome.result_id == str(rows[0].result_id)
    result = EvidenceStore(database).get_result(run_id, rows[0].result_id)
    assert result.payload["usage"]["model_calls"] == 7
    assert model.calls == BudgetService(database).usage("fixture").reservations == 13
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(EvidenceResult)) == 2
    summary = DiscoveryStore(database).summary(token, run_id)
    assert summary["selected_final_stage"]["result_id"] == str(rows[0].result_id)
    assert summary["total_usage"]["model_calls"] == 13
    assert summary["accounting"]["counted_spend_microdollars"] == "325"


async def test_insufficient_next_stage_retains_positive_history_without_auto_selecting_it(database):
    _, run_id = queued(database)
    outcome = await worker(database, DiscoveryFixtureModel(insufficient_after_stage=1)).run_once(
        "honest-higher-rejection"
    )
    assert outcome.result_id is None
    assert len(commits(database, run_id)) == 1
    assert not (JobRepository(database).get(run_id).outcome or {}).get("result_id")
    assert BudgetService(database).usage("fixture").reservations == 8


async def test_cancellation_after_atomic_commit_keeps_stage_without_another_model_call(
    database, monkeypatch
):
    token, run_id = queued(database)
    original = DiscoveryStore.commit
    loop, committed = asyncio.get_running_loop(), asyncio.Event()
    release, returned = Event(), Event()

    def committed_before_cancel(store, lease, packet):
        receipt = original(store, lease, packet)
        loop.call_soon_threadsafe(committed.set)
        try:
            assert release.wait(timeout=30)
            return receipt
        finally:
            returned.set()

    monkeypatch.setattr(DiscoveryStore, "commit", committed_before_cancel)
    model = DiscoveryFixtureModel()
    task = asyncio.create_task(worker(database, model).run_once("cancelled-discovery"))
    try:
        await asyncio.wait_for(committed.wait(), timeout=10)
        JobRepository(database).request_cancel(run_id)
    finally:
        release.set()
    outcome = await task
    assert await asyncio.to_thread(returned.wait, timeout=5)
    assert outcome.status == "cancelled" and outcome.result_id is None
    assert model.calls == BudgetService(database).usage("fixture").reservations == 7
    assert len(commits(database, run_id)) == 1
    summary = DiscoveryStore(database).summary(token, run_id)
    assert summary["run_status"] == "cancelled" and summary["selected_final_stage"] is None
    assert summary["total_usage"]["model_calls"] == 7
