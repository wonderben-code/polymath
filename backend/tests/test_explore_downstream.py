"""Committed Explore bridges feed existing owned workflows, never automatic sharing.

All stages, source text and models are synthetic. PostgreSQL and the actual
controllers/workers/projections are real; there is no external provider call.
"""

import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_artifact_api import artifact_request
from test_artifact_worker import ArtifactFixture
from test_assessments import enqueue, revised_result
from test_explore_collection_controller import RecordingModel, prefix, ranking_action, second_branch
from test_explore_worker import queued, rows, worker
from test_memory import current, owner
from test_run_api import bootstrap
from test_scientific_context_builder import watch_new
from test_worker import database as database_fixture
from test_worker_lineage import LineageFixture
from test_worker_memory import ChallengeTools

from polymath.artifact_api import create_artifact_router
from polymath.artifacts import ArtifactStore
from polymath.assessments import AssessmentStore
from polymath.budget import BudgetService
from polymath.budget_models import BudgetAccount
from polymath.discovery_models import StageCommit
from polymath.evidence import EvidenceStore
from polymath.evidence_models import EvidenceQuote
from polymath.explore_collection_models import ExploreRankingRecord
from polymath.jobs import JobRepository
from polymath.knowledge import create_knowledge_router
from polymath.lineage_models import ResearchRunParent
from polymath.research.meta import ModelReply
from polymath.run_api import COOKIE_NAME, create_run_router
from polymath.scientific_context_builder import build_artifact_scientific_context
from polymath.sharing import SharingPolicy, SharingStore
from polymath.sharing_api import create_sharing_router

database = database_fixture
pytestmark = pytest.mark.integration
CONTEXT_INTENT = {"protocol": "polymath-artifact-scientific-context-v1"}


@pytest.fixture
async def collection(database):
    # An explicit test-owned browser token supplies a real authenticated owner;
    # no owner/header injection or private-route authorization bypass is used.
    raw = "e" * 43
    token = owner(database, raw)
    _, run_id = queued(database, token=token)
    model = RecordingModel([*prefix(), *second_branch(), ranking_action])
    instance = worker(database, model)
    outcome = await instance.run_once("explore-downstream-seed")
    assert outcome.status == "succeeded"
    stages = rows(database, StageCommit, run_id)
    assert len(stages) == 2 and outcome.result_id == str(stages[1].result_id)
    app = FastAPI()
    app.include_router(create_run_router(database, availability=lambda: True))
    app.include_router(create_knowledge_router(database))
    app.include_router(create_artifact_router(database, availability=lambda: True))
    app.include_router(create_sharing_router(database, availability=lambda: True))
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, raw, domain="testserver.local", path="/api/v1")
        assert client.get("/api/v1/session").status_code == 200
        yield SimpleNamespace(
            engine=database,
            token=token,
            run_id=run_id,
            stages=stages,
            client=client,
            app=app,
            model=model,
            worker=instance,
        )


async def test_owned_map_and_memory_include_selected_and_unselected_committed_bridges(collection):
    value = collection
    ids = {str(stage.result_id) for stage in value.stages}
    listing = value.client.get("/api/v1/knowledge")
    assert listing.status_code == 200
    assert ids.issubset(
        {item["result_id"] for item in listing.json()["items"] if item["type"] == "finding"}
    )
    graph = value.client.get("/api/v1/knowledge/graph").json()
    assert {f"finding:{identity}" for identity in ids}.issubset(
        {node["id"] for node in graph["nodes"]}
    )
    for stage in value.stages:
        detail = value.client.get(f"/api/v1/knowledge/findings/{stage.result_id}")
        assert detail.status_code == 200
        assert detail.json()["discovery_stage"]["status"] == "current_supported"
    memory, lease = current(value.engine, value.token)
    discovered = memory.search("Synthetic Explore")
    assert {reference.result_id for reference in discovered.findings} == ids
    for stage in value.stages:
        saved = memory.read_finding(str(stage.result_id))
        assert saved.reference.content_sha256 == stage.content_sha256
        assert len(saved.sources) == 2
        original = memory.read_source(str(stage.result_id), saved.sources[0].retrieval_id)
        assert original.content_sha256 == saved.sources[0].content_sha256
    JobRepository(value.engine).finish(lease, status="failed", outcome={})
    with TestClient(value.app) as foreign:
        bootstrap(foreign)
        assert foreign.get("/api/v1/knowledge/graph").json()["nodes"] == []
        for stage in value.stages:
            assert foreign.get(f"/api/v1/knowledge/findings/{stage.result_id}").status_code == 404


class DistinctOriginals(LineageFixture):
    """The fixture reads a different original paper from the second saved bridge."""

    async def complete(self, messages, *, max_output_tokens):
        reply = await super().complete(messages, max_output_tokens=max_output_tokens)
        action = json.loads(reply.content)
        initial = json.loads(messages[1]["content"])
        if (
            action["kind"] == "read_memory_source"
            and action["result_id"] == (initial["parent_result_ids"][1])
        ):
            finding = next(
                observation
                for message in messages
                if message["role"] == "user"
                if (observation := json.loads(message["content"]).get("observation", {})).get(
                    "kind"
                )
                == "memory_finding"
                and observation["reference"]["result_id"] == action["result_id"]
            )
            action["retrieval_id"] = finding["sources"][-1]["retrieval_id"]
        return ModelReply(json.dumps(action), reply.input_tokens, reply.output_tokens)


async def test_both_saved_stages_can_seed_one_explicit_f3_investigation(collection):
    value = collection
    parents = [str(stage.result_id) for stage in value.stages]
    created = value.client.post(
        "/api/v1/runs",
        headers=bootstrap(value.client),
        json={
            "mode": "explore",
            "goal": "Compare the original papers behind these saved synthetic bridges.",
            "parent_result_ids": parents,
        },
    )
    assert created.status_code == 201, created.text
    run_id = uuid.UUID(created.json()["id"])
    model = DistinctOriginals()
    instance = value.worker
    instance.model_factory = lambda _: model
    instance.tools_factory = lambda _: ChallengeTools()
    outcome = await instance.run_once("explore-stages-as-f3-premises")
    assert outcome.status == "succeeded" and outcome.result_id
    result = EvidenceStore(value.engine).get_result(run_id, uuid.UUID(outcome.result_id))
    assert {ref["result_id"] for ref in result.payload["memory_reads"]} == set(parents)
    assert {quote["source_id"] for quote in result.payload["quotes"]} == {"left", "third"}
    with Session(value.engine) as session:
        links = session.scalars(
            select(ResearchRunParent).where(ResearchRunParent.run_id == run_id)
        ).all()
        assert {str(link.parent_result_id) for link in links} == set(parents)
        for quote in session.scalars(select(EvidenceQuote).where(EvidenceQuote.run_id == run_id)):
            assert (
                session.scalar(
                    select(EvidenceQuote.result_id).where(
                        EvidenceQuote.result_id.in_([stage.result_id for stage in value.stages]),
                        EvidenceQuote.version_id == quote.version_id,
                        EvidenceQuote.retrieval_id == quote.retrieval_id,
                    )
                )
                is not None
            )
        context = build_artifact_scientific_context(
            session, owner_hash=value.token, source_result_id=outcome.result_id
        )
    assert len(context.ancestry_nodes) == 3 and len(context.ancestry_edges) == 2
    assert {edge.parent.result_id for edge in context.ancestry_edges} == set(parents)


@pytest.mark.parametrize("index", (0, 1), ids=("unselected-stage", "model-selected-stage"))
async def test_each_committed_stage_drives_formalise_summary_and_paper_with_exact_context(
    collection, index
):
    value, prior = collection, None
    source = value.stages[index]
    for kind in ("formalise", "write_summary", "write_paper"):
        created = artifact_request(
            value.client,
            source.result_id,
            kind=kind,
            scientific_context_intent=CONTEXT_INTENT,
            **({"formalisation_id": str(prior.id)} if prior is not None else {}),
        )
        assert created.status_code == 201, created.text
        run_id = uuid.UUID(created.json()["id"])
        model = ArtifactFixture()
        value.worker.model_factory = lambda _, current=model: current
        outcome = await value.worker.run_once("explore-stage-artifact")
        assert outcome.status == "succeeded" and outcome.artifact_id
        record = value.client.get(f"/api/v1/artifacts/{outcome.artifact_id}")
        assert record.status_code == 200, record.text
        body = record.json()
        context = body["scientific_context"]["context"]
        assert context["stage_origin"]["receipt"] == source.receipt
        assert context["stage_origin"]["relationship"] == "exact_stage_result"
        assert context["source_reference"]["result_id"] == str(source.result_id)
        assert (
            context["stage_origin"]["receipt"]["assessment"]["confidence_score"]
            == (source.assessment["confidence_score"])
        )
        assert context["ancestry_edges"] == [] and len(context["ancestry_nodes"]) == 1
        exported = value.client.get(f"/api/v1/artifacts/{outcome.artifact_id}/export")
        assert exported.content == body["markdown"].encode("utf-8")
        assert hashlib.sha256(exported.content).hexdigest() == body["markdown_sha256"]
        assert body["scientific_context"]["context_sha256"] in body["markdown"]
        assert "uncalibrated" in body["markdown"]
        if kind == "formalise":
            prior = ArtifactStore(value.engine).get(run_id, uuid.UUID(outcome.artifact_id))
            assert body["payload"]["checks"][0]["proof_status"] == "not_a_general_proof"
        else:
            assert context["prior_formalisation"]["artifact_id"] == str(prior.id)
            assert (
                context["prior_formalisation"]["context_sha256"]
                == (prior.basis["scientific_context"]["context_sha256"])
            )
    usage = BudgetService(value.engine).usage("fixture")
    assert usage.reservations == value.model.calls + 4 == 17
    assert usage.committed_microdollars == 425 and usage.in_flight == 0
    with Session(value.engine) as session:
        account = session.get(BudgetAccount, "fixture")
        assert (
            account.hard_cap_microdollars,
            account.daily_cap_microdollars,
            account.max_in_flight,
        ) == (10_000, 10_000, 1)


async def test_explicit_sharing_of_either_stage_omits_private_ranking_and_identifiers(collection):
    value = collection
    sharing = SharingStore(value.engine, SharingPolicy(enabled=True))
    assert sharing.list_public()["items"] == []
    grants = []
    for source in value.stages:
        preview = value.client.get(
            "/api/v1/shares/preview", params={"source_result_id": str(source.result_id)}
        )
        assert preview.status_code == 200, preview.text
        assert len(sharing.list_public()["items"]) == len(grants)
        created = value.client.post(
            "/api/v1/shares",
            headers=bootstrap(value.client),
            json={
                "source_result_id": str(source.result_id),
                "expected_projection_sha256": preview.json()["projection_sha256"],
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        assert created.status_code == 201, created.text
        grants.append(created.json())
    assert len(sharing.list_public()["items"]) == 2
    private = [str(value.run_id), value.token]
    private.extend(str(stage.id) for stage in value.stages)
    private.extend(str(stage.result_id) for stage in value.stages)
    private.extend(str(row.id) for row in rows(value.engine, ExploreRankingRecord, value.run_id))
    with TestClient(value.app) as anonymous:
        for grant in grants:
            detail = anonymous.get(f"/api/v1/public/findings/{grant['public_id']}")
            assert detail.status_code == 200 and detail.json()["status"] == "current"
            encoded = detail.text
            assert all(identity not in encoded for identity in private)
            assert all(
                field not in encoded
                for field in (
                    "ranking_id",
                    "ranked",
                    "excluded",
                    "confidence_score",
                    "scientific_context",
                )
            )
            assert "set-cookie" not in detail.headers
        assert not anonymous.cookies


async def test_revised_explore_stage_keeps_original_score_as_historical_context_only(collection):
    value, source = collection, collection.stages[0]
    sharing = SharingStore(value.engine, SharingPolicy(enabled=True))
    preview = sharing.preview(value.token, source.result_id)
    grant = sharing.share(value.token, source.result_id, preview.projection_sha256, uuid.uuid4())
    watch, version = watch_new(value.engine, value.token, source.result_id)
    lease = enqueue(value.engine, value.token, source.result_id, watch.id, [version.version_id])
    assessments = AssessmentStore(value.engine)
    applied = assessments.save(lease, revised_result(value.engine, assessments, lease))
    assert applied.applied and applied.candidate_result_id
    with Session(value.engine) as session:
        context = build_artifact_scientific_context(
            session, owner_hash=value.token, source_result_id=str(applied.candidate_result_id)
        )
    assert context.stage_origin.relationship == "reassessed_descendant"
    assert context.stage_origin.receipt.stage_id == str(source.id)
    assert context.stage_origin.receipt_sha256 == source.receipt_sha256
    assert (
        context.stage_origin.receipt.assessment.confidence_score
        == source.assessment["confidence_score"]
    )
    assert context.ancestry_nodes[0].stage_origin is None
    assert context.ancestry_nodes[1].stage_origin.relationship == "exact_stage_result"
    assert context.ancestry_edges[0].assessment_id == str(applied.id)
    # Assessment changes private scientific state, never the approved projection
    # or the public visitor's authority to inspect the private successor.
    public = sharing.get_public(grant.public_id)
    assert public["status"] == "superseded" and public["projection"] == preview.projection
    assert str(applied.candidate_result_id) not in json.dumps(public)
    assert len(sharing.list_owned(value.token)) == 1
    assert sharing.list_public()["items"] == []
