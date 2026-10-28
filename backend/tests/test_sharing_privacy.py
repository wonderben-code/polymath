"""Adversarial real-storage public projections; all text and calls are synthetic."""

import hashlib
import json
import uuid
from contextlib import ExitStack

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_evidence import document, result_for
from test_run_api import bootstrap
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.evidence import EvidenceStore
from polymath.full_text import FullTextStore
from polymath.full_text_contracts import ExtractedFullText
from polymath.jobs import JobRepository
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.research.models import ResearchEvent, SourceBibliography, SourceReference, Usage
from polymath.run_api import create_run_router
from polymath.sharing_api import HEADERS, create_sharing_router

database = database_fixture
pytestmark = pytest.mark.integration
PRIVATE = (
    "PRIVATE_RESEARCH_GOAL",
    "PRIVATE_RAW_PAPER_BODY",
    "PRIVATE_ABSTRACT_BODY",
    "PRIVATE_SOURCE_IDENTIFIER",
    "PRIVATE_URL_PASSWORD",
    "PRIVATE_URL_QUERY",
    "PRIVATE_URL_FRAGMENT",
    "PRIVATE_URL_PATH",
    "PRIVATE_UNUSED_SOURCE_ID",
    "PRIVATE_UNUSED_SOURCE_BODY",
    "PRIVATE_UNUSED_SOURCE_TITLE",
    "PRIVATE_ACTIVITY_EVENT",
    "PRIVATE_RESULT_EVENT",
    "PRIVATE_OUTCOME_FIELD",
)


@pytest.fixture
def public_api(database):
    with ExitStack() as clients:

        def factory():
            app = FastAPI()
            app.include_router(create_run_router(database, availability=lambda: True))
            app.include_router(create_sharing_router(database, availability=lambda: True))
            return clients.enter_context(TestClient(app))

        yield database, factory


def private_finding(engine, client):
    response = client.post(
        "/api/v1/runs",
        headers=bootstrap(client),
        json={"mode": "explore", "goal": PRIVATE[0]},
    )
    assert response.status_code == 201, response.text
    run_id = uuid.UUID(response.json()["id"])
    jobs, evidence = JobRepository(engine), EvidenceStore(engine)
    lease = jobs.claim_next(
        "synthetic-sharing-privacy", lease_seconds=300, task_kinds=("research",)
    )
    assert lease.run_id == run_id
    body = (
        "🌌 Published public-science evidence. "
        + "publicword " * 80
        + PRIVATE[1]
        + " continued scientific description " * 80
    )
    reference = SourceReference(
        source_id="arxiv:2610.12345",
        title="Public synthetic paper A",
        url="https://arxiv.org/abs/2610.12345",
        bibliography=SourceBibliography(arxiv_id="2610.12345", arxiv_version=2),
    )
    extracted = ExtractedFullText(
        arxiv_id="2610.12345",
        arxiv_version=2,
        url="https://arxiv.org/html/2610.12345v2",
        text=body,
        raw_html_sha256=hashlib.sha256(("<article>" + body).encode()).hexdigest(),
        retrieved_at=document().retrieved_at,
        headings=(),
        limitations=("Synthetic HTML only; the actual paper is not verified.",),
    )
    full_text = FullTextStore(engine)
    index = full_text.save_snapshot(lease, reference, extracted)
    left = full_text.read_window(lease, index.snapshot_id, 0, 2400)
    right = document(
        PRIVATE[3],
        title="Public synthetic paper B",
        url="https://user:PRIVATE_URL_PASSWORD@untrusted.example/PRIVATE_URL_PATH"
        "?token=PRIVATE_URL_QUERY#PRIVATE_URL_FRAGMENT",
        text="🌱 Published independent abstract sentence. "
        + "safe evidence " * 80
        + PRIVATE[2]
        + " continuation " * 30,
        bibliography=SourceBibliography(doi="10.1234/public-privacy-fixture"),
    )
    evidence.save_source(lease, right)
    evidence.save_source(
        lease,
        document(
            PRIVATE[8],
            title=PRIVATE[10],
            text=PRIVATE[9],
            url="https://untrusted.example/PRIVATE_UNUSED_URL",
        ),
    )
    jobs.append_event(
        lease, event_key="synthetic-private-event", kind="fixture", summary=PRIVATE[11]
    )
    result = result_for(left, right).model_copy(
        update={
            "events": (ResearchEvent(sequence=1, kind="fixture", summary=PRIVATE[12]),),
            "usage": Usage(
                model_calls=7, input_tokens_reported=56789, output_tokens_reported=12345
            ),
        }
    )
    saved = evidence.save_result(lease, result)
    jobs.finish(
        lease, status="succeeded", outcome={"result_id": str(saved.id), "private": PRIVATE[13]}
    )
    with Session(engine) as session:
        owner_hash = session.get(ResearchRunAccess, run_id).session_hash
    identifiers = {str(run_id), str(saved.id), index.snapshot_id, owner_hash}
    for source in evidence.list_sources(run_id):
        identifiers.update((str(source.version_id), str(source.retrieval_id)))
    return {
        "run_id": run_id,
        "result_id": saved.id,
        "owner_hash": owner_hash,
        "private_identifiers": identifiers,
        "documents": (left, right),
    }


def share(client, result_id, *, body=None):
    preview = client.get("/api/v1/shares/preview", params={"source_result_id": str(result_id)})
    assert preview.status_code == 200, preview.text
    request = body or {
        "source_result_id": str(result_id),
        "expected_projection_sha256": preview.json()["projection_sha256"],
        "idempotency_key": str(uuid.uuid4()),
    }
    response = client.post("/api/v1/shares", headers=bootstrap(client), json=request)
    assert response.status_code == 201, response.text
    return response.json(), request


def assert_public_only(payload, original):
    encoded = json.dumps(payload, ensure_ascii=False)
    assert not any(value in encoded for value in (*PRIVATE, *original["private_identifiers"]))

    def visit(value):
        if isinstance(value, dict):
            assert not set(value) & {
                "source_result_id",
                "run_id",
                "source_id",
                "retrieval_id",
                "version_id",
                "snapshot_id",
                "owner_session_hash",
                "goal",
                "events",
                "usage",
                "outcome",
                "parent_result_id",
                "memory_reads",
                "assessment_id",
                "revoked_at",
            }
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    # Model-authored finding text is intentionally approved; private snapshots
    # and their independently marked bodies cannot pass this projection boundary.
    visit(payload)


def row_counts(engine):
    with Session(engine) as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (BrowserSession, ResearchRun, ResearchExternalRequest)
        )


def assert_headers(response):
    assert all(response.headers[key] == value for key, value in HEADERS.items())
    assert "set-cookie" not in response.headers


def test_unauthenticated_detail_search_and_map_never_leak_private_storage_or_create_work(
    public_api,
):
    engine, factory = public_api
    owner, anonymous = factory(), factory()
    original = private_finding(engine, owner)
    record, _ = share(owner, original["result_id"])
    before = row_counts(engine)
    responses = [
        anonymous.get(f"/api/v1/public/findings/{record['public_id']}"),
        anonymous.get("/api/v1/public/library"),
        anonymous.get("/api/v1/public/map"),
    ]
    for response in responses:
        assert response.status_code == 200, response.text
        assert_headers(response)
        assert_public_only(response.json(), original)
    detail = responses[0].json()
    assert detail["projection"] == record["projection"]
    citations = detail["projection"]["citations"]
    assert len(citations) == 2
    assert all(10 <= len(citation["quote"]["excerpt"]) <= 280 for citation in citations)
    for citation, source in zip(citations, original["documents"], strict=True):
        quote = citation["quote"]
        assert source.text[quote["excerpt_start"] : quote["excerpt_end"]] == quote["excerpt"]
        assert quote["excerpted"] and quote["full_quote_chars"] > 280
    links = [link["url"] for paper in detail["projection"]["papers"] for link in paper["links"]]
    assert "https://doi.org/10.1234/public-privacy-fixture" in links
    assert all(
        url.startswith(("https://doi.org/", "https://arxiv.org/", "https://openalex.org/"))
        for url in links
    )
    for sentinel in (PRIVATE[0], PRIVATE[1], PRIVATE[2], PRIVATE[10], str(original["result_id"])):
        assert anonymous.get("/api/v1/public/library", params={"q": sentinel}).json()["items"] == []
        graph = anonymous.get("/api/v1/public/map", params={"q": sentinel}).json()
        assert graph["nodes"] == [] and graph["edges"] == []
    assert row_counts(engine) == before and not anonymous.cookies
    assert anonymous.get(f"/api/v1/runs/{original['run_id']}/result").status_code == 401


def test_public_read_handle_never_grants_revocation_and_retries_cannot_revive_it(public_api):
    engine, factory = public_api
    owner, other, anonymous = factory(), factory(), factory()
    original = private_finding(engine, owner)
    record, request = share(owner, original["result_id"])
    path = f"/api/v1/public/findings/{record['public_id']}"
    assert anonymous.get(path).status_code == 200
    foreign = other.post(f"/api/v1/shares/{record['public_id']}/revoke", headers=bootstrap(other))
    assert foreign.status_code == 404
    assert anonymous.get(path).status_code == 200
    assert (
        owner.post(
            f"/api/v1/shares/{record['public_id']}/revoke", headers=bootstrap(owner)
        ).status_code
        == 200
    )
    retry = owner.post("/api/v1/shares", headers=bootstrap(owner), json=request)
    assert retry.status_code == 201 and retry.json()["public_id"] == record["public_id"]
    assert retry.json()["status"] == "revoked"
    missing = anonymous.get("/api/v1/public/findings/" + "a" * 43)
    revoked = anonymous.get(path)
    assert (
        (revoked.status_code, revoked.json())
        == (missing.status_code, missing.json())
        == (404, {"detail": "sharing_not_found"})
    )
    assert_headers(revoked)
    assert anonymous.get("/api/v1/public/library").json()["items"] == []
    assert anonymous.get("/api/v1/public/map").json()["nodes"] == []
    replacement, _ = share(owner, original["result_id"])
    assert replacement["public_id"] != record["public_id"]
    assert anonymous.get(path).status_code == 404
    assert anonymous.get(f"/api/v1/public/findings/{replacement['public_id']}").status_code == 200


def test_browser_expiry_hides_all_public_surfaces_without_disclosing_owner_details(public_api):
    engine, factory = public_api
    owner, anonymous = factory(), factory()
    original = private_finding(engine, owner)
    record, _ = share(owner, original["result_id"])
    with engine.begin() as connection:
        connection.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == original["owner_hash"])
            .values(expires_at=func.clock_timestamp())
        )
    response = anonymous.get(f"/api/v1/public/findings/{record['public_id']}")
    assert response.status_code == 404 and response.json() == {"detail": "sharing_not_found"}
    assert_headers(response)
    assert anonymous.get("/api/v1/public/library").json()["items"] == []
    graph = anonymous.get("/api/v1/public/map").json()
    assert graph["nodes"] == [] and graph["edges"] == []
    assert not anonymous.cookies


@pytest.mark.parametrize("disposition", ["unsupported", "revised"])
def test_private_reassessment_changes_only_approved_public_badge_not_snapshot_or_successor(
    public_api, disposition
):
    from test_assessments import assessment_result, enqueue, revised_result

    from polymath.assessments import AssessmentStore
    from polymath.ingestion import IngestionRepository

    engine, factory = public_api
    owner, anonymous = factory(), factory()
    original = private_finding(engine, owner)
    record, _ = share(owner, original["result_id"])
    before = anonymous.get(f"/api/v1/public/findings/{record['public_id']}").json()
    repository, jobs, evidence = (
        IngestionRepository(engine),
        JobRepository(engine),
        EvidenceStore(engine),
    )
    watch = repository.create(
        original["owner_hash"],
        label="PRIVATE_REASSESSMENT_WATCH",
        query="PRIVATE_REASSESSMENT_QUERY",
        source_result_id=original["result_id"],
    )
    tick = repository.run_now(original["owner_hash"], watch.id)
    ingestion_lease = jobs.claim_next(
        "synthetic-private-ingestion", lease_seconds=300, task_kinds=("ingest_literature",)
    )
    assert ingestion_lease.run_id == tick.run_id
    new_source = evidence.save_source(
        ingestion_lease,
        document(
            "fixture:PRIVATE_REASSESSMENT_SOURCE",
            title="PRIVATE_REASSESSMENT_SOURCE_TITLE",
            text="🌱 PRIVATE_REASSESSMENT_SOURCE_BODY changes only a private interpretation.",
        ),
    )
    repository.commit_page(
        ingestion_lease,
        "openalex",
        repository.snapshot(ingestion_lease).cursors["openalex"],
        None,
        (new_source,),
        exhausted=True,
    )
    jobs.finish(ingestion_lease, status="succeeded", outcome={"model_calls": 0})
    lease = enqueue(
        engine, original["owner_hash"], original["result_id"], watch.id, [new_source.version_id]
    )
    store = AssessmentStore(engine)
    if disposition == "revised":
        result = revised_result(engine, store, lease)
        result = result.model_copy(
            update={
                "revised_result": result.revised_result.model_copy(
                    update={
                        "finding": result.revised_result.finding.model_copy(
                            update={"title": "PRIVATE_SUCCESSOR_FINDING"}
                        )
                    }
                )
            }
        )
    else:
        result = assessment_result(engine, store, lease)
    saved = store.save(lease, result)
    assert saved.applied
    original["private_identifiers"].update(
        (str(watch.id), str(tick.run_id), str(lease.run_id), str(saved.id))
    )
    if saved.candidate_result_id:
        original["private_identifiers"].add(str(saved.candidate_result_id))
    response = anonymous.get(f"/api/v1/public/findings/{record['public_id']}")
    assert response.status_code == 200, response.text
    public = response.json()
    assert public["status"] == ("superseded" if disposition == "revised" else "unsupported")
    assert public["projection"] == before["projection"]
    assert public["projection_sha256"] == before["projection_sha256"]
    assert public["created_at"] == before["created_at"]
    assert_public_only(public, original)
    assert "PRIVATE_REASSESSMENT" not in response.text and "PRIVATE_SUCCESSOR" not in response.text
    assert anonymous.get("/api/v1/public/library").json()["items"] == []
    graph = anonymous.get("/api/v1/public/map").json()
    assert graph["nodes"] == [] and graph["edges"] == []
