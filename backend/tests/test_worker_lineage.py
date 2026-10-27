"""API-selected higher-level investigations use synthetic models and saved sources."""

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_knowledge import knowledge as knowledge_fixture
from test_knowledge import seed
from test_run_api import bootstrap
from test_worker import setup_worker
from test_worker_memory import ChallengeTools

from polymath.budget import BudgetService
from polymath.evidence import EvidenceStore
from polymath.evidence_models import EvidenceQuote
from polymath.jobs import JobRepository
from polymath.lineage import get_parent_refs
from polymath.lineage_models import ResearchRunParent
from polymath.memory_models import ResearchMemoryRead
from polymath.research.meta import ModelReply

knowledge = knowledge_fixture
pytestmark = pytest.mark.integration
DERIVED_TITLE = "Synthetic higher-level connection"


class LineageFixture:
    """An adaptive fixture, never a production research model or real discovery."""

    def __init__(self):
        self.seen = []
        self.parents_seen = []

    async def complete(self, messages, *, max_output_tokens):
        user_messages = [json.loads(item["content"]) for item in messages if item["role"] == "user"]
        parents = user_messages[0]["parent_result_ids"]
        self.parents_seen.append(tuple(parents))
        observations = [item["observation"] for item in user_messages if "observation" in item]
        latest = observations[-1] if observations else {}
        self.seen.append(latest)
        findings = {
            item["reference"]["result_id"]: item
            for item in observations
            if item.get("kind") == "memory_finding"
        }
        sources = {
            item["memory_result_id"]: item
            for item in observations
            if item.get("kind") == "source_text" and "memory_result_id" in item
        }
        unread_findings = [parent for parent in parents if parent not in findings]
        unread_sources = [parent for parent in parents if parent not in sources]
        purposes = {
            item["purpose"] for item in observations if item.get("kind") == "search_results"
        }
        if unread_findings:
            action = {"kind": "read_memory_finding", "result_id": unread_findings[0]}
        elif unread_sources:
            parent = unread_sources[0]
            action = {
                "kind": "read_memory_source",
                "result_id": parent,
                "retrieval_id": findings[parent]["sources"][0]["retrieval_id"],
            }
        elif "counterevidence" not in purposes:
            action = {
                "kind": "search_literature",
                "query": "Synthetic higher-level objection search",
                "purpose": "counterevidence",
            }
        elif "prior_work" not in purposes:
            action = {
                "kind": "search_literature",
                "query": "Synthetic higher-level prior-work search",
                "purpose": "prior_work",
            }
        else:
            finding = {
                **findings[parents[0]]["finding"],
                "title": DERIVED_TITLE,
                "field_left": "Synthetic shared structure",
                "field_right": "Synthetic cross-bridge mechanism",
                "explanation": (
                    "Synthetic comparison across saved bridge premises. "
                    "This tests traceability, not scientific validity."
                ),
                "strongest_objection": "These are synthetic premises rather than scientific work.",
                "objection_verdict": "No real discovery or independent corroboration is claimed.",
                "novelty_limitations": "Synthetic tests cannot assess scientific novelty.",
                "evidence": [
                    {
                        "source_id": sources[parent]["source_id"],
                        "side": "left" if ordinal % 2 == 0 else "right",
                        "quote": sources[parent]["text"][2:],
                        "supports": "A directly read original premise of this synthetic comparison",
                    }
                    for ordinal, parent in enumerate(parents)
                ],
            }
            action = {"kind": "conclude", "conclusion": finding}
        return ModelReply(json.dumps(action), 10, 15)


def prepare_lineage(engine, client, *, count=2):
    parents = tuple(
        seed(
            engine,
            client,
            title=f"Synthetic foundation bridge {index}",
            suffix=f"lineage-{index}",
        )[1]
        for index in range(count)
    )
    created = client.post(
        "/api/v1/runs",
        headers=bootstrap(client),
        json={
            "mode": "explore",
            "goal": "Compare the original evidence behind these synthetic bridges.",
            "parent_result_ids": [str(parent) for parent in parents],
        },
    )
    assert created.status_code == 201
    return uuid.UUID(created.json()["id"]), parents


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [2, 4])
async def test_api_selected_parents_reach_worker_and_direct_original_evidence(knowledge, count):
    engine, factory = knowledge
    client = factory()
    run_id, parents = prepare_lineage(engine, client, count=count)
    model = LineageFixture()
    worker = setup_worker(engine, model, tools_factory=lambda _: ChallengeTools())
    outcome = await worker.run_once("lineage-fixture-worker")
    assert outcome.status == "succeeded"
    saved = EvidenceStore(engine).get_result(run_id, uuid.UUID(outcome.result_id))
    assert saved.payload["status"] == "bridge"
    assert saved.payload["finding"]["title"] == DERIVED_TITLE
    assert len(saved.payload["quotes"]) == count
    assert len(saved.payload["sources"]) == count
    assert {item["result_id"] for item in saved.payload["memory_reads"]} == {
        str(parent) for parent in parents
    }
    with Session(engine) as session:
        selected = session.scalars(
            select(ResearchRunParent).where(ResearchRunParent.run_id == run_id)
        ).all()
        assert {row.parent_result_id for row in selected} == set(parents)
        for quote in session.scalars(select(EvidenceQuote).where(EvidenceQuote.run_id == run_id)):
            # The child has its own exact original-source quote associations;
            # quoting a parent's generated finding text is never substituted.
            parent_quotes = session.scalars(
                select(EvidenceQuote).where(
                    EvidenceQuote.result_id.in_(parents),
                    EvidenceQuote.retrieval_id == quote.retrieval_id,
                )
            ).all()
            assert parent_quotes
            assert all(item.version_id == quote.version_id for item in parent_quotes)
    assert len(model.seen) == 2 * count + 3
    assert all(ids == tuple(str(parent) for parent in parents) for ids in model.parents_seen)
    usage = BudgetService(engine).usage("fixture")
    assert usage.reservations == len(model.seen)
    assert usage.committed_microdollars == 25 * len(model.seen)
    assert usage.in_flight == 0


@pytest.mark.asyncio
async def test_parent_restart_preserves_exact_basis_and_avoids_duplicate_charges(knowledge):
    engine, factory = knowledge
    client = factory()
    run_id, parents = prepare_lineage(engine, client)
    tools = ChallengeTools(pause=True)
    model = LineageFixture()
    worker = setup_worker(engine, model, tools_factory=lambda _: tools)
    task = asyncio.create_task(worker.run_once("lineage-before-restart"))
    await asyncio.wait_for(tools.started.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    jobs = JobRepository(engine)
    snapshot = jobs.get(run_id)
    assert snapshot.status == "interrupted"
    assert not snapshot.external_request_in_flight
    assert BudgetService(engine).usage("fixture").reservations == 5
    with Session(engine) as session:
        selected = session.scalars(
            select(ResearchRunParent)
            .where(ResearchRunParent.run_id == run_id)
            .order_by(ResearchRunParent.ordinal)
        ).all()
        original_refs = tuple(
            (str(row.parent_result_id), str(row.parent_run_id), row.content_sha256)
            for row in selected
        )
    jobs.resume_interrupted(run_id, expected_token=snapshot.lease_token)
    resumed = LineageFixture()
    worker.model_factory = lambda _: resumed
    worker.tools_factory = lambda _: ChallengeTools()
    outcome = await worker.run_once("lineage-after-restart")
    assert outcome.status == "succeeded"
    assert len(resumed.seen) == 2
    assert BudgetService(engine).usage("fixture").reservations == 7
    saved = EvidenceStore(engine).get_result(run_id, uuid.UUID(outcome.result_id))
    assert {
        (item["result_id"], item["run_id"], item["content_sha256"])
        for item in saved.payload["memory_reads"]
    } == set(original_refs)
    with Session(engine) as session:
        reads = session.scalars(
            select(ResearchMemoryRead).where(ResearchMemoryRead.run_id == run_id)
        ).all()
        assert {row.parent_result_id for row in reads} == set(parents)
        selected = session.scalars(
            select(ResearchRunParent)
            .where(ResearchRunParent.run_id == run_id)
            .order_by(ResearchRunParent.ordinal)
        ).all()
        assert (
            tuple(
                (str(row.parent_result_id), str(row.parent_run_id), row.content_sha256)
                for row in selected
            )
            == original_refs
        )


@pytest.mark.asyncio
async def test_changed_parent_hash_cannot_replay_a_checkpoint_or_dispatch(knowledge, monkeypatch):
    import polymath.worker as workers

    engine, factory = knowledge
    run_id, _ = prepare_lineage(engine, factory())
    tools = ChallengeTools(pause=True)
    worker = setup_worker(engine, LineageFixture(), tools_factory=lambda _: tools)
    task = asyncio.create_task(worker.run_once("lineage-original-policy"))
    await asyncio.wait_for(tools.started.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    jobs = JobRepository(engine)
    snapshot = jobs.get(run_id)
    saved_fingerprint = snapshot.checkpoint["worker_protocol"]

    def changed_refs(engine, lease):
        refs = get_parent_refs(engine, lease)
        return (refs[0].model_copy(update={"content_sha256": "0" * 64}), *refs[1:])

    # The database rows themselves are immutable. Simulate a different worker
    # composition returning a changed version binding: request replay must halt
    # before making any new model request, even though the selected IDs match.
    monkeypatch.setattr(workers, "get_parent_refs", changed_refs)
    jobs.resume_interrupted(run_id, expected_token=snapshot.lease_token)
    resumed = LineageFixture()
    worker.model_factory = lambda _: resumed
    worker.tools_factory = lambda _: ChallengeTools()
    outcome = await worker.run_once("lineage-changed-policy")
    assert outcome.status == "interrupted"
    assert resumed.seen == []
    assert BudgetService(engine).usage("fixture").reservations == 5
    assert jobs.get(run_id).checkpoint["worker_protocol"] == saved_fingerprint
