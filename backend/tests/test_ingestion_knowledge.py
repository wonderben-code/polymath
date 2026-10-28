"""Collected versions enter the map with collection links, never fake findings."""

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session
from test_evidence import document
from test_knowledge import knowledge as knowledge_fixture
from test_knowledge import seed
from test_run_api import bootstrap

from polymath.access_models import ResearchRunAccess
from polymath.evidence import EvidenceStore
from polymath.ingestion import IngestionRepository
from polymath.jobs import JobRepository
from polymath.run_api import COOKIE_NAME

knowledge = knowledge_fixture
pytestmark = pytest.mark.integration


def owner(client):
    bootstrap(client)
    return hashlib.sha256(client.cookies.get(COOKIE_NAME).encode("ascii")).hexdigest()


def test_only_committed_ingestion_sources_enter_owned_search_and_map(knowledge):
    engine, factory = knowledge
    client, foreign = factory(), factory()
    owner_hash = owner(client)
    owner(foreign)
    repository, jobs, evidence = (
        IngestionRepository(engine),
        JobRepository(engine),
        EvidenceStore(engine),
    )
    watch = repository.create(owner_hash, label="Synthetic source watch", query="Synthetic topic")
    tick = repository.run_now(owner_hash, watch.id)
    lease = jobs.claim_next("synthetic-reader", task_kinds=("ingest_literature",))
    assert lease.run_id == tick.run_id
    doc = document("fixture:collected", title="Synthetic unassessed source")
    artifact = evidence.save_source(lease, doc)
    # Saving a read is not committing a provider page or adding it to a watch.
    assert client.get("/api/v1/knowledge").json()["items"] == []
    assert client.get("/api/v1/knowledge/graph").json()["nodes"] == []
    basis = repository.snapshot(lease)
    repository.commit_page(
        lease, "openalex", basis.cursors["openalex"], None, (artifact,), exhausted=True
    )
    response = client.get("/api/v1/knowledge")
    item = response.json()["items"][0]
    assert item["type"] == "source"
    assert item["task_kind"] == "ingest_literature"
    assert item["watch_id"] == str(watch.id)
    assert item["source_version_id"] == str(artifact.version_id)
    assert "unassessed" in item["summary"]
    assert doc.text not in response.text
    graph = client.get("/api/v1/knowledge/graph").json()
    assert len(graph["nodes"]) == 1
    assert graph["nodes"][0]["watch_id"] == str(watch.id)
    assert graph["nodes"][0]["source_version_id"] == str(artifact.version_id)
    assert graph["edges"] == []
    assert foreign.get("/api/v1/knowledge").json()["items"] == []
    assert foreign.get("/api/v1/knowledge/graph").json()["nodes"] == []


def test_document_source_import_cannot_replace_research_navigation(knowledge):
    engine, factory = knowledge
    client = factory()
    research_id, _ = seed(engine, client)
    owner_hash = owner(client)
    jobs, evidence = JobRepository(engine), EvidenceStore(engine)
    original = evidence.list_sources(research_id)[0]
    doc = evidence.get_document(research_id, original.retrieval_id)
    artifact_run = jobs.create(
        mode="explore", goal="Synthetic source-copy regression", task_kind="write_summary"
    )
    with Session(engine) as session, session.begin():
        session.add(ResearchRunAccess(run_id=artifact_run.id, session_hash=owner_hash))
    lease = jobs.claim_next("synthetic-document", task_kinds=("write_summary",))
    newer_metadata = doc.model_copy(update={"retrieved_at": doc.retrieved_at + timedelta(hours=1)})
    assert evidence.save_source(lease, newer_metadata).version_id == original.version_id
    item = next(
        item
        for item in client.get("/api/v1/knowledge").json()["items"]
        if item.get("source_version_id") == str(original.version_id)
    )
    assert item["run_id"] == str(research_id)
    assert item["task_kind"] == "research"
    assert item["watch_id"] is None
