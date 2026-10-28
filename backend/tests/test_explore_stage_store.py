"""Actual shared PostgreSQL stage commits; all intelligence and papers are synthetic."""

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
    record_bridge,
    second_branch,
    selection,
)
from test_memory import assign, complete, owner
from test_research_memory import required_searches
from test_worker import database as database_fixture
from test_worker import setup_worker

from polymath.budget import BudgetService
from polymath.discovery_models import StageCommit, StageParent
from polymath.discovery_store import DiscoveryStore, ExploreStageStore
from polymath.evidence import EvidenceError, EvidenceStore
from polymath.evidence_models import EvidenceResult
from polymath.explore_collection_admission import bind_explore_collection_intent
from polymath.explore_collection_contracts import ExploreCollectionIntent
from polymath.jobs import JobRepository
from polymath.memory_models import ResearchMemoryRead
from polymath.models import ResearchRun
from polymath.research import ResearchLimits
from polymath.research.models import (
    AnswerClaim,
    AnswerFinding,
    ClaimQuote,
    ResearchResult,
    SourceBibliography,
    Usage,
    VerifiedQuote,
)
from polymath.scientific_context_builder import build_artifact_scientific_context

database = database_fixture
pytestmark = pytest.mark.integration


def queued(engine, token=None):
    token = token or owner(engine)
    run = JobRepository(engine).create(mode="explore", goal=GOAL)
    assign(engine, run.id, token)
    with Session(engine) as session, session.begin():
        bind_explore_collection_intent(
            session, session.get(ResearchRun, run.id), ExploreCollectionIntent()
        )
    return token, run.id


def worker(engine, model):
    instance = setup_worker(engine, model, tools_factory=lambda _: DiscoveryFixtureTools())
    instance.limits = ResearchLimits(max_model_calls=20, max_actions=16)
    return instance


def stages(engine, run_id):
    with Session(engine) as session:
        return session.scalars(
            select(StageCommit).where(StageCommit.run_id == run_id).order_by(StageCommit.ordinal)
        ).all()


def count(engine, cls, run_id=None):
    query = select(func.count()).select_from(cls)
    if run_id is not None:
        query = query.where(cls.run_id == run_id)
    with Session(engine) as session:
        return session.scalar(query)


def complete_ask(engine, token, *, bibliography=None):
    from test_evidence import document

    jobs, evidence = JobRepository(engine), EvidenceStore(engine)
    question = "What does this synthetic memory lead establish?"
    run = jobs.create(mode="ask", goal=question)
    assign(engine, run.id, token)
    lease = jobs.claim_next("synthetic-ask-memory", lease_seconds=60)
    assert lease.run_id == run.id
    source = document("fixture:ask-lead", bibliography=bibliography)
    evidence.save_source(lease, source)
    quote = source.text[2:]
    saved = evidence.save_result(
        lease,
        ResearchResult(
            mode="ask",
            status="answer",
            finding=AnswerFinding(
                outcome="answer",
                question=question,
                summary="Synthetic memory answer; not scientific evidence.",
                claims=[
                    AnswerClaim(
                        id="fixture_claim",
                        text="The fixture reports redundancy.",
                        label="known",
                        evidence=[
                            ClaimQuote(
                                source_id=source.source_id,
                                quote=quote,
                                supports="Synthetic fixture only",
                            )
                        ],
                    )
                ],
                limitations=["No real scientific inference is established."],
            ),
            quotes=(
                VerifiedQuote(
                    source_id=source.source_id,
                    side="answer",
                    claim_id="fixture_claim",
                    quote=quote,
                    supports="Synthetic fixture only",
                    start=2,
                    end=len(source.text),
                    source_sha256=source.content_sha256,
                    content_kind="abstract",
                ),
            ),
            sources=(source,),
            usage=Usage(),
        ),
    )
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    return saved


async def test_bridge_and_ask_memory_leads_are_cumulative_without_becoming_stage_parents(database):
    token = owner(database)
    bridge, _ = complete(database, token, title="Synthetic memory bridge")
    answer = complete_ask(database, token)
    _, run_id = queued(database, token)
    first, second = prefix(), second_branch()
    model = RecordingModel(
        [
            first[0],
            {"kind": "search_memory", "query": "Synthetic"},
            {"kind": "read_memory_finding", "result_id": str(bridge.id)},
            *first[1:],
            second[0],
            {"kind": "read_memory_finding", "result_id": str(answer.id)},
            *second[1:],
            ranking_action,
        ]
    )
    outcome = await worker(database, model).run_once("explore-cumulative-memory")
    assert outcome.status == "succeeded"
    committed = stages(database, run_id)
    assert len(committed) == 2 and [row.depth for row in committed] == [1, 1]
    assert count(database, ResearchMemoryRead, run_id) == 2
    assert count(database, StageParent) == 0
    assert model.calls == BudgetService(database).usage("fixture").reservations == 16
    with Session(database) as session:
        results = [session.get(EvidenceResult, row.result_id) for row in committed]
        assert [len(row.payload["result"]["memory_reads"]) for row in results] == [1, 2]
        assert all(row.payload["result"]["field_selections"] == [] for row in results)
        assert {r["result_id"] for r in results[1].payload["result"]["memory_reads"]} == {
            str(bridge.id),
            str(answer.id),
        }
        context = build_artifact_scientific_context(
            session, owner_hash=token, source_result_id=str(committed[1].result_id)
        )
    assert context.stage_origin.receipt.stage_id == str(committed[1].id)
    assert context.stage_origin.receipt.depth == 1
    assert len(context.ancestry_nodes) == 1 and context.ancestry_edges == ()
    # Merely reading a conceptual lead never imports all of its source bodies.
    assert {source.source_id for source in EvidenceStore(database).list_sources(run_id)} == {
        "left",
        "right",
        "third",
    }


async def test_unused_extra_branch_field_cannot_save_a_literal_duplicate_bridge(database):
    _, run_id = queued(database)
    unused = selection()
    unused["fields"].append("An unused selected field")
    model = RecordingModel(
        [*prefix(), unused, *required_searches(), record_bridge(), ranking_action]
    )
    outcome = await worker(database, model).run_once("explore-literal-dedup")
    assert outcome.status == "succeeded"
    assert len(stages(database, run_id)) == count(database, EvidenceResult, run_id) == 1
    assert model.calls == BudgetService(database).usage("fixture").reservations == 12
    observations = [
        message["content"]
        for request in model.messages
        for message in request
        if message["role"] == "user"
    ]
    assert any("discovery_duplicate_scientific_body" in message for message in observations)


async def test_uncited_ask_lead_alias_connector_is_authoritative_at_fresh_commit(
    database, monkeypatch
):
    from polymath.research import controller

    token = owner(database)
    saved = complete_ask(
        database,
        token,
        bibliography=SourceBibliography(doi="10.1234/connector", arxiv_id="2401.00001"),
    )
    _, run_id = queued(database, token)

    class SeparateReads(DiscoveryFixtureTools):
        def __init__(self):
            super().__init__()
            self.documents["left"] = self.documents["left"].model_copy(
                update={"bibliography": SourceBibliography(doi="10.1234/connector")}
            )
            self.documents["right"] = self.documents["right"].model_copy(
                update={"bibliography": SourceBibliography(arxiv_id="2401.00001")}
            )

        async def search(self, query, purpose):
            matches = await super().search(query, purpose)
            return [
                item.model_copy(
                    update={"bibliography": self.documents[item.source_id].bibliography}
                )
                for item in matches
            ]

    actions = prefix()
    model = RecordingModel(
        [
            actions[0],
            {"kind": "search_memory", "query": "synthetic"},
            {"kind": "read_memory_finding", "result_id": str(saved.id)},
            *actions[1:],
            insufficient(),
        ]
    )
    original = controller._verify_evidence
    reached_store = []
    real_commit = ExploreStageStore.commit

    # Deliberately bypass only the caller's alias preflight. The store still
    # reconstructs the complete actual memory/source ledger independently.
    def incomplete_caller_check(finding, sources, purposes, *, alias_context=None):
        return original(finding, sources, purposes, alias_context=())

    def authoritative_commit(store, lease, packet):
        reached_store.append(packet.model_request_id)
        return real_commit(store, lease, packet)

    monkeypatch.setattr(controller, "_verify_evidence", incomplete_caller_check)
    monkeypatch.setattr(ExploreStageStore, "commit", authoritative_commit)
    instance = setup_worker(database, model, tools_factory=lambda _: SeparateReads())
    instance.limits = ResearchLimits(max_model_calls=20, max_actions=16)
    outcome = await instance.run_once("explore-owned-memory-alias")
    assert outcome.status == "succeeded" and reached_store == ["model-0009"]
    assert stages(database, run_id) == []
    assert count(database, ResearchMemoryRead, run_id) == 1
    assert model.calls == BudgetService(database).usage("fixture").reservations == 10
    assert any(
        "distinct_evidence_sources_required" in message["content"]
        for request in model.messages
        for message in request
        if message["role"] == "user"
    )


async def test_lost_shared_commit_reply_replays_receipt_before_any_new_work(database, monkeypatch):
    token, run_id = queued(database)
    original, lost = ExploreStageStore.commit, []

    def committed_before_response_loss(store, lease, packet):
        receipt = original(store, lease, packet)
        if not lost:
            lost.append(receipt)
            raise RuntimeError("synthetic committed Explore acknowledgement loss")
        return receipt

    monkeypatch.setattr(ExploreStageStore, "commit", committed_before_response_loss)
    first = RecordingModel([*prefix(), ranking_action])
    instance = worker(database, first)
    assert (await instance.run_once("explore-lost-commit")).status == "interrupted"
    assert len(stages(database, run_id)) == 1 and first.calls == 7
    snapshot = JobRepository(database).get(run_id)
    JobRepository(database).resume_interrupted(run_id, expected_token=snapshot.lease_token)
    resumed = RecordingModel([ranking_action])
    instance.model_factory = lambda _: resumed
    outcome = await instance.run_once("explore-explicit-replay")
    assert outcome.status == "succeeded" and resumed.calls == 1
    assert len(stages(database, run_id)) == count(database, EvidenceResult, run_id) == 1
    assert str(stages(database, run_id)[0].result_id) == lost[0].result_id
    assert BudgetService(database).usage("fixture").reservations == 8
    assert len(ExploreStageStore(database).list_stages(token, run_id)["stages"]) == 1


async def test_generic_positive_cannot_bypass_explore_atomic_stage_commit(database, monkeypatch):
    _, run_id = queued(database)
    original, guarded = ExploreStageStore.commit, []

    def attempt_generic_write(store, lease, packet):
        before = count(database, EvidenceResult, run_id)
        with pytest.raises(EvidenceError, match="discovery_stage_commit_required"):
            EvidenceStore(database).save_result(lease, packet.result)
        assert count(database, EvidenceResult, run_id) == before
        guarded.append(packet.model_request_id)
        return original(store, lease, packet)

    monkeypatch.setattr(ExploreStageStore, "commit", attempt_generic_write)
    model = RecordingModel([*prefix(), ranking_action])
    assert (await worker(database, model).run_once("explore-positive-guard")).status == "succeeded"
    assert guarded == ["model-0007"]
    assert len(stages(database, run_id)) == 1


async def test_each_store_loads_only_its_own_concrete_intent(database):
    _, run_id = queued(database)
    lease = JobRepository(database).claim_next("explore-loader", lease_seconds=60)
    assert lease.run_id == run_id
    assert ExploreStageStore(database).load_intent(lease) == ExploreCollectionIntent()
    assert DiscoveryStore(database).load_intent(lease) is None
    JobRepository(database).finish(lease, status="failed", outcome={})
    ordinary = JobRepository(database).create(mode="explore", goal=GOAL)
    lease = JobRepository(database).claim_next("ordinary-loader", lease_seconds=60)
    assert lease.run_id == ordinary.id
    assert ExploreStageStore(database).load_intent(lease) is None
    assert DiscoveryStore(database).load_intent(lease) is None
