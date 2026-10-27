"""Session-owned saved-evidence projections; all sources/findings are synthetic."""

import os
import uuid
from contextlib import ExitStack
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from test_evidence import document, result_for
from test_run_api import bootstrap

from polymath.evidence import EvidenceStore
from polymath.jobs import JobRepository
from polymath.knowledge import create_knowledge_router
from polymath.research.models import (
    AnswerClaim,
    AnswerFinding,
    ClaimQuote,
    FieldSelection,
    ResearchResult,
    Usage,
    VerifiedQuote,
)
from polymath.run_api import create_run_router

pytestmark = pytest.mark.integration


@pytest.fixture
def knowledge(monkeypatch):
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL for real saved-knowledge checks")
    url = make_url(raw)
    schema = "polymath_knowledge_" + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    engine = create_engine(scoped, hide_parameters=True)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with ExitStack() as clients:
        try:
            command.upgrade(config, "head")

            def client():
                app = FastAPI()
                app.include_router(create_run_router(engine, availability=lambda: True))
                app.include_router(create_knowledge_router(engine))
                return clients.enter_context(TestClient(app))

            yield engine, client
        finally:
            clients.close()
            engine.dispose()
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def seed(
    engine, client, *, title="Synthetic graph analogy", label="Fixture source", suffix="shared"
):
    headers = bootstrap(client)
    response = client.post(
        "/api/v1/runs",
        headers=headers,
        json={"mode": "explore", "goal": "Synthetic saved knowledge"},
    )
    assert response.status_code == 201
    run_id = uuid.UUID(response.json()["id"])
    jobs, store = JobRepository(engine), EvidenceStore(engine)
    lease = jobs.claim_next("knowledge-fixture")
    assert lease.run_id == run_id
    left = document(f"fixture:left:{suffix}", title=f"{label} left")
    right = document(
        f"fixture:right:{suffix}",
        title=f"{label} right",
        text="🌱 Synthetic pathways carry fixture signals under noise.",
    )
    extra = document(f"fixture:unused:{suffix}", title=f"{label} unused")
    for source in (left, right, extra):
        store.save_source(lease, source)
    result = result_for(left, right)
    result = result.model_copy(
        update={"finding": result.finding.model_copy(update={"title": title})}
    )
    saved = store.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    return run_id, saved.id


def test_authentication_and_empty_graph_are_explicit(knowledge):
    _, factory = knowledge
    client = factory()
    assert client.get("/api/v1/knowledge").status_code == 401
    assert client.get("/api/v1/knowledge/graph").status_code == 401
    bootstrap(client)
    response = client.get("/api/v1/knowledge/graph")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["nodes"] == []
    assert response.json()["edges"] == []
    assert response.json()["coverage"]["scope"] == "current_browser_session"


def test_search_owned_sources_and_findings_without_source_body(knowledge):
    engine, factory = knowledge
    client = factory()
    run_id, result_id = seed(engine, client)
    response = client.get("/api/v1/knowledge")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 4
    assert {item["type"] for item in payload["items"]} == {"source", "finding"}
    assert all(item["run_id"] == str(run_id) for item in payload["items"])
    assert "source_text" not in response.text
    assert "Synthetic redundancy protects" not in response.text
    found = client.get(f"/api/v1/knowledge/findings/{result_id}")
    assert found.json()["title"] == "Synthetic graph analogy"
    assert found.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/knowledge?q=FIXTURE%20SOURCE").json()["items"]
    limited = client.get("/api/v1/knowledge?limit=1").json()
    assert len(limited["items"]) == 1 and limited["truncated"]


def test_foreign_findings_and_shared_source_versions_do_not_leak(knowledge):
    engine, factory = knowledge
    own, foreign = factory(), factory()
    own_run, own_result = seed(engine, own, title="Owner finding", label="Owner metadata")
    foreign_run, foreign_result = seed(
        engine, foreign, title="Private foreign finding", label="Foreign metadata"
    )
    own_graph = own.get("/api/v1/knowledge/graph").json()
    text = str(own_graph)
    assert str(own_result) in text
    assert str(foreign_result) not in text and str(foreign_run) not in text
    assert "Foreign metadata" not in text
    assert all(node["run_id"] in (None, str(own_run)) for node in own_graph["nodes"])
    assert own.get(f"/api/v1/knowledge/findings/{foreign_result}").status_code == 404
    assert own.get(f"/api/v1/knowledge/findings/{uuid.uuid4()}").json() == (
        own.get(f"/api/v1/knowledge/findings/{foreign_result}").json()
    )
    assert own.get("/api/v1/knowledge?q=Foreign").json()["items"] == []


def test_graph_relations_are_saved_mapping_and_quote_provenance_only(knowledge):
    engine, factory = knowledge
    client = factory()
    _, result_id = seed(engine, client)
    graph = client.get("/api/v1/knowledge/graph").json()
    nodes = {node["id"]: node for node in graph["nodes"]}
    finding_id = f"finding:{result_id}"
    assert len([node for node in nodes.values() if node["kind"] == "field"]) == 2
    supports = [edge for edge in graph["edges"] if edge["kind"] == "supported_by"]
    assert len(supports) == 2
    assert all(edge["source"] == finding_id for edge in supports)
    assert all("unused" not in nodes[edge["target"]]["label"] for edge in supports)
    assert len([node for node in nodes.values() if node["kind"] == "source"]) == 3
    assert all(edge["source"] in nodes and edge["target"] in nodes for edge in graph["edges"])
    filtered = client.get("/api/v1/knowledge/graph?q=Fixture%20source%20left").json()
    assert any(node["id"] == finding_id for node in filtered["nodes"])


def test_answer_graph_uses_claim_quotes_without_inventing_fields(knowledge):
    engine, factory = knowledge
    client = factory()
    headers = bootstrap(client)
    created = client.post(
        "/api/v1/runs", headers=headers, json={"mode": "ask", "goal": "Synthetic graph question?"}
    )
    run_id = uuid.UUID(created.json()["id"])
    jobs, store = JobRepository(engine), EvidenceStore(engine)
    lease = jobs.claim_next("knowledge-fixture")
    assert lease.run_id == run_id
    source = document("fixture:answer", title="Synthetic answer evidence")
    store.save_source(lease, source)
    quote = source.text[2:]
    finding = AnswerFinding(
        outcome="answer",
        question="Synthetic graph question?",
        summary="Synthetic answer only.",
        claims=[
            AnswerClaim(
                id="claim1",
                text="Synthetic claim.",
                label="known",
                evidence=[
                    ClaimQuote(source_id=source.source_id, quote=quote, supports="Synthetic claim")
                ],
            )
        ],
        limitations=["Synthetic fixture is not a real scientific answer."],
    )
    result = ResearchResult(
        mode="ask",
        status="answer",
        finding=finding,
        sources=(source,),
        usage=Usage(),
        quotes=(
            VerifiedQuote(
                source_id=source.source_id,
                side="answer",
                claim_id="claim1",
                quote=quote,
                supports="Synthetic claim",
                start=2,
                end=len(source.text),
                source_sha256=source.content_sha256,
                content_kind="abstract",
            ),
        ),
    )
    saved = store.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    graph = client.get("/api/v1/knowledge/graph").json()
    assert {node["kind"] for node in graph["nodes"]} == {"finding", "source"}
    assert len(graph["edges"]) == 1 and graph["edges"][0]["kind"] == "supported_by"
    assert client.get("/api/v1/knowledge?q=graph%20question").json()["items"][0]["title"] == (
        "Synthetic graph question?"
    )


def test_surprise_graph_records_selected_fields_without_asserting_new_links(knowledge):
    engine, factory = knowledge
    client = factory()
    headers = bootstrap(client)
    created = client.post("/api/v1/runs", headers=headers, json={"mode": "surprise", "goal": ""})
    run_id = uuid.UUID(created.json()["id"])
    jobs, store = JobRepository(engine), EvidenceStore(engine)
    lease = jobs.claim_next("knowledge-fixture")
    assert lease.run_id == run_id
    left, right = document(), document("fixture:right", text="🌱 Synthetic pathways carry signals.")
    store.save_source(lease, left)
    store.save_source(lease, right)
    result = result_for(left, right).model_copy(
        update={
            "mode": "surprise",
            "field_selections": (
                FieldSelection(
                    fields=("Fixture domain A", "Fixture domain B", "Fixture domain C"),
                    rationale="Synthetic autonomous choice, not a real discovery.",
                ),
            ),
        }
    )
    saved = store.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    graph = client.get("/api/v1/knowledge/graph").json()
    assert len([node for node in graph["nodes"] if node["kind"] == "field"]) == 3
    assert len([edge for edge in graph["edges"] if edge["kind"] == "selected_field"]) == 3
    assert len([edge for edge in graph["edges"] if edge["kind"] == "maps_to"]) == 2
    assert client.get("/api/v1/knowledge?q=Fixture%20domain%20C").json()["items"][0][
        "result_id"
    ] == str(saved.id)


@pytest.mark.parametrize("term", ["%", "_", "' OR 1=1 --"])
def test_search_treats_wildcards_and_sql_as_literal_text(knowledge, term):
    engine, factory = knowledge
    client = factory()
    seed(engine, client)
    assert client.get("/api/v1/knowledge", params={"q": term}).json()["items"] == []
    assert client.get("/api/v1/knowledge/graph", params={"q": term}).json()["nodes"] == []


@pytest.mark.parametrize("query", ["x" * 201, "bad\x00query", "bad\nquery"])
def test_invalid_queries_are_sanitized(knowledge, query):
    _, factory = knowledge
    client = factory()
    bootstrap(client)
    response = client.get("/api/v1/knowledge", params={"q": query})
    assert response.status_code == 422
    assert query not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_graph_caps_and_field_deduplication_are_explicit(knowledge):
    engine, factory = knowledge
    client = factory()
    # Real source/finding records, not a synthesized graph response. Shared
    # sources and fields deduplicate while each stored finding remains distinct.
    for number in range(61):
        seed(engine, client, title=f"Synthetic bounded finding {number}")
    graph = client.get("/api/v1/knowledge/graph").json()
    assert graph["truncated"]
    assert graph["coverage"]["findings_considered"] == 60
    assert len(graph["nodes"]) <= 200 and len(graph["edges"]) <= 500
    assert len([node for node in graph["nodes"] if node["kind"] == "field"]) == 2
    assert len([node for node in graph["nodes"] if node["kind"] == "source"]) == 3
    graph_again = client.get("/api/v1/knowledge/graph").json()
    assert graph_again == graph
