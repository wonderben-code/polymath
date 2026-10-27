"""Graph lineage means investigated-from, never generated scientific support."""

import uuid

import pytest
from test_knowledge import knowledge as knowledge_fixture
from test_knowledge import seed
from test_run_api import bootstrap
from test_worker import setup_worker
from test_worker_lineage import DERIVED_TITLE, LineageFixture, prepare_lineage
from test_worker_memory import ChallengeTools

from polymath.evidence import EvidenceStore

knowledge = knowledge_fixture
pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_derived_graph_retains_pinned_parents_and_original_quote_support(knowledge):
    engine, factory = knowledge
    client = factory()
    run_id, parents = prepare_lineage(engine, client)
    worker = setup_worker(engine, LineageFixture(), tools_factory=lambda _: ChallengeTools())
    outcome = await worker.run_once("graph-lineage-fixture")
    assert outcome.status == "succeeded"
    saved = EvidenceStore(engine).get_result(run_id, uuid.UUID(outcome.result_id))
    response = client.get("/api/v1/knowledge/graph", params={"q": DERIVED_TITLE})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    graph = response.json()
    nodes = {item["id"]: item for item in graph["nodes"]}
    child_id = f"finding:{outcome.result_id}"
    parent_ids = {f"finding:{parent}" for parent in parents}
    assert {child_id, *parent_ids}.issubset(nodes)
    assert all(nodes[parent]["finding_outcome"] == "bridge" for parent in parent_ids)
    ancestry = [item for item in graph["edges"] if item["kind"] == "investigated_from"]
    assert len(ancestry) == 2
    assert {(edge["source"], edge["target"]) for edge in ancestry} == {
        (child_id, parent_id) for parent_id in parent_ids
    }
    support = [item for item in graph["edges"] if item["kind"] == "supported_by"]
    assert len(support) == 2
    assert all(edge["source"] == child_id for edge in support)
    assert {nodes[edge["target"]]["source_id"] for edge in support} == {
        item["source_id"] for item in saved.payload["quotes"]
    }
    assert all(nodes[edge["target"]]["kind"] == "source" for edge in support)
    assert all(edge["source"] in nodes and edge["target"] in nodes for edge in graph["edges"])
    assert not any(
        edge["source"] in parent_ids and edge["target"] == child_id for edge in graph["edges"]
    )
    # Filtering by the new title still retains selected older premises; they
    # need not independently match the query or become support edges.
    assert all(DERIVED_TITLE not in nodes[parent]["label"] for parent in parent_ids)


@pytest.mark.asyncio
async def test_foreign_browser_cannot_see_parent_or_child_lineage(knowledge):
    engine, factory = knowledge
    own, foreign = factory(), factory()
    run_id, parents = prepare_lineage(engine, own)
    worker = setup_worker(engine, LineageFixture(), tools_factory=lambda _: ChallengeTools())
    outcome = await worker.run_once("private-lineage-fixture")
    assert outcome.status == "succeeded"
    bootstrap(foreign)
    foreign_graph = foreign.get("/api/v1/knowledge/graph").json()
    assert foreign_graph["nodes"] == [] and foreign_graph["edges"] == []
    seed(engine, foreign, title="Foreign synthetic bridge", suffix="foreign-lineage")
    foreign_graph = foreign.get("/api/v1/knowledge/graph").json()
    serialized = str(foreign_graph)
    assert all(str(parent) not in serialized for parent in parents)
    assert str(run_id) not in serialized and outcome.result_id not in serialized
    assert not any(edge["kind"] == "investigated_from" for edge in foreign_graph["edges"])
    assert foreign.get(f"/api/v1/knowledge/findings/{outcome.result_id}").status_code == 404
