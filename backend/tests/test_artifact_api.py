"""Real owned artifact API/export checks using synthetic saved research only."""

import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from test_answer_persistence import QUESTION, answer_result
from test_knowledge import seed
from test_run_api import bootstrap, submit

from polymath.access_models import BrowserSession
from polymath.artifact_api import create_artifact_router
from polymath.artifact_contracts import (
    ArtifactResult,
    CitedText,
    Definition,
    DocumentDraft,
    DocumentSection,
    FormalisationDraft,
)
from polymath.artifact_models import ArtifactRequest, GeneratedArtifact
from polymath.artifacts import ArtifactStore
from polymath.evidence import EvidenceStore
from polymath.jobs import JobRepository
from polymath.models import ResearchRun
from polymath.research.models import Usage
from polymath.run_api import create_run_router

pytestmark = pytest.mark.integration


@pytest.fixture
def artifacts_api(monkeypatch):
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL for real artifact API checks")
    url = make_url(raw)
    schema = "polymath_artifact_api_" + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    engine = create_engine(scoped, hide_parameters=True, pool_size=10, max_overflow=10)
    with ExitStack() as clients:
        try:
            command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")

            def factory(*, enabled=False, policy=None):
                app = FastAPI()
                app.include_router(create_run_router(engine, lambda: True))
                app.include_router(create_artifact_router(engine, policy or (lambda: enabled)))
                return clients.enter_context(TestClient(app))

            yield engine, factory
        finally:
            clients.close()
            engine.dispose()
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def artifact_request(client, source_id, *, kind="write_summary", headers=None, **changes):
    return client.post(
        "/api/v1/artifacts/jobs",
        headers=bootstrap(client) if headers is None else headers,
        json={"kind": kind, "source_result_id": str(source_id), **changes},
    )


def draft_result(kind="write_summary", *, text="Synthetic source-backed prose."):
    paragraph = CitedText(text=text, label="postulated", citation_ordinals=(0, 1))
    if kind == "formalise":
        draft = FormalisationDraft(
            title="Synthetic open conjecture",
            definitions=(Definition(symbol="x", meaning=paragraph),),
            assumptions=(paragraph,),
            conjecture=paragraph,
            proof_attempt=(paragraph,),
            limitations=["Synthetic sources do not establish a mathematical proof."],
        )
    else:
        draft = DocumentDraft(
            kind="summary" if kind == "write_summary" else "paper",
            title="Synthetic written finding",
            abstract=(paragraph,),
            sections=tuple(
                DocumentSection(heading=heading, paragraphs=(paragraph,))
                for heading in ("Background", "Findings", "Evidence", "Limitations")
            ),
            limitations=["A synthetic fixture does not establish scientific support."],
        )
    return ArtifactResult(kind=kind, status="ready", draft=draft, usage=Usage())


def complete_artifact(engine, client, source_id, *, kind="write_summary", **changes):
    created = artifact_request(client, source_id, kind=kind, **changes)
    assert created.status_code == 201, created.text
    run_id = uuid.UUID(created.json()["id"])
    jobs, store = JobRepository(engine), ArtifactStore(engine)
    lease = jobs.claim_next("synthetic-artifact-api")
    assert lease.run_id == run_id
    store.load_basis(lease)
    saved = store.save(lease, draft_result(kind))
    jobs.finish(lease, status="succeeded", outcome={"artifact_id": str(saved.id)})
    return run_id, saved


def test_default_disabled_never_enqueues_even_with_a_real_saved_basis(artifacts_api):
    engine, factory = artifacts_api
    client = factory()
    _, source_id = seed(engine, client)
    response = artifact_request(client, source_id)
    assert response.status_code == 503
    assert response.json() == {"detail": "research_unavailable"}
    assert response.headers["cache-control"] == "no-store"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 1
        assert session.scalar(select(func.count()).select_from(ArtifactRequest)) == 0


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-CSRF-Token": "incorrect"},
        {"Origin": "https://attacker.invalid"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_artifact_writes_preserve_csrf_and_origin_boundary(artifacts_api, headers):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    supplied = (
        headers if "X-CSRF-Token" in headers or not headers else {**bootstrap(client), **headers}
    )
    response = artifact_request(client, source_id, headers=supplied)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ArtifactRequest)) == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "private-unsupported-kind"},
        {"source_result_id": "private-malformed-selection"},
        {"source_result_id": str(uuid.uuid4()).upper()},
        {"source_result_id": True},
        {"formalisation_id": "private-malformed-formalisation"},
        {"kind": "formalise", "formalisation_id": str(uuid.uuid4())},
        {"unexpected": "private-extra-data"},
    ],
)
def test_invalid_request_is_private_and_cannot_create_orphans(artifacts_api, changes):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    response = artifact_request(client, source_id, **changes)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_research_request"}
    assert "private-" not in response.text
    assert response.headers["cache-control"] == "no-store"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 1
        assert session.scalar(select(func.count()).select_from(ArtifactRequest)) == 0


def test_missing_and_foreign_source_versions_are_indistinguishable(artifacts_api):
    engine, factory = artifacts_api
    own, other = factory(enabled=True), factory(enabled=True)
    bootstrap(own)
    _, foreign_id = seed(engine, other, title="Private foreign finding")
    foreign = artifact_request(own, foreign_id)
    missing = artifact_request(own, uuid.uuid4())
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": "artifact_source_not_found"}
    assert "Private foreign" not in foreign.text
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ArtifactRequest)) == 0


def test_shared_queue_quota_includes_research_and_artifact_jobs(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    headers = bootstrap(client)
    barrier = Barrier(2)

    def research():
        barrier.wait(timeout=5)
        return submit(client, headers)

    def artifact():
        barrier.wait(timeout=5)
        return artifact_request(client, source_id, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(research), pool.submit(artifact)
        responses = (first.result(timeout=15), second.result(timeout=15))
    assert sorted(response.status_code for response in responses) == [201, 429]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 2


def test_global_queue_full_blocks_artifact_enqueue(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    with Session(engine) as session, session.begin():
        session.add_all(
            ResearchRun(mode="explore", goal=f"Synthetic queue fixture {index}")
            for index in range(100)
        )
    response = artifact_request(client, source_id)
    assert response.status_code == 429
    assert response.json()["detail"] == "research_queue_full"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ArtifactRequest)) == 0


def test_exact_artifact_export_has_immutable_content_and_owned_history(artifacts_api):
    engine, factory = artifacts_api
    client, other = factory(enabled=True), factory(enabled=True)
    _, source_id = seed(engine, client)
    run_id, saved = complete_artifact(engine, client, source_id)
    record = client.get(f"/api/v1/artifacts/{saved.id}")
    assert record.status_code == 200
    payload = record.json()
    assert payload["id"] == str(saved.id)
    assert payload["run_id"] == str(run_id)
    assert payload["source_result_id"] == str(source_id)
    assert payload["kind"] == "write_summary"
    export = client.get(f"/api/v1/artifacts/{saved.id}/export")
    assert export.status_code == 200
    assert export.content == payload["markdown"].encode("utf-8") == saved.markdown.encode("utf-8")
    assert hashlib.sha256(export.content).hexdigest() == payload["markdown_sha256"]
    assert export.headers["content-type"].startswith("text/markdown")
    assert export.headers["x-content-type-options"] == "nosniff"
    assert export.headers["cache-control"] == record.headers["cache-control"] == "no-store"
    assert export.headers["content-disposition"] == (
        f'attachment; filename="artifact-{saved.id}.md"'
    )
    own_jobs = client.get("/api/v1/artifacts/jobs").json()
    assert [job["id"] for job in own_jobs["jobs"]] == [str(run_id)]
    assert own_jobs["jobs"][0]["artifact_id"] == str(saved.id)
    assert own_jobs["jobs"][0]["result_id"] is None
    assert client.get(f"/api/v1/runs/{run_id}/result").status_code == 404
    assert client.get(f"/api/v1/artifacts/jobs/{run_id}").json()["task_kind"] == "write_summary"
    bootstrap(other)
    assert other.get("/api/v1/artifacts/jobs").json()["jobs"] == []
    assert other.get(f"/api/v1/artifacts/jobs/{run_id}").status_code == 404
    for suffix in ("", "/export"):
        foreign = other.get(f"/api/v1/artifacts/{saved.id}{suffix}")
        missing = other.get(f"/api/v1/artifacts/{uuid.uuid4()}{suffix}")
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json() == {"detail": "artifact_not_found"}
        assert foreign.headers["cache-control"] == "no-store"


def test_writing_can_pin_an_exact_same_source_formalisation(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    _, formalisation = complete_artifact(engine, client, source_id, kind="formalise")
    run_id, paper = complete_artifact(
        engine, client, source_id, kind="write_paper", formalisation_id=str(formalisation.id)
    )
    record = client.get(f"/api/v1/artifacts/{paper.id}").json()
    assert record["formalisation_id"] == str(formalisation.id)
    assert record["payload"]["draft"]["kind"] == "paper"
    with Session(engine) as session:
        bound = session.get(ArtifactRequest, run_id)
        assert bound.formalisation_id == formalisation.id


def test_wrong_source_or_foreign_formalisation_cannot_be_reused(artifacts_api):
    engine, factory = artifacts_api
    client, other = factory(enabled=True), factory(enabled=True)
    _, source_id = seed(engine, client)
    _, formalisation = complete_artifact(engine, client, source_id, kind="formalise")
    _, second_source = seed(engine, client, suffix="second")
    wrong = artifact_request(client, second_source, formalisation_id=str(formalisation.id))
    assert wrong.status_code == 404
    _, foreign_source = seed(engine, other, suffix="foreign")
    foreign = artifact_request(other, foreign_source, formalisation_id=str(formalisation.id))
    assert foreign.status_code == 404


def test_export_treats_model_text_as_plain_text(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    created = artifact_request(client, source_id)
    run_id = uuid.UUID(created.json()["id"])
    lease = JobRepository(engine).claim_next("synthetic-export-escaping")
    assert lease.run_id == run_id
    store = ArtifactStore(engine)
    store.load_basis(lease)
    saved = store.save(
        lease,
        draft_result(
            text='![tracking](https://tracker.invalid/a)<script>alert("fixture")</script>'
        ),
    )
    response = client.get(f"/api/v1/artifacts/{saved.id}/export")
    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "![tracking](" not in response.text
    assert hashlib.sha256(response.content).hexdigest() == saved.markdown_sha256


def test_artifact_job_can_be_cancelled_through_common_owned_route(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    headers = bootstrap(client)
    created = artifact_request(client, source_id, headers=headers)
    run_id = created.json()["id"]
    cancelled = client.post(f"/api/v1/runs/{run_id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.get(f"/api/v1/artifacts/jobs/{run_id}").json()["status"] == "cancelled"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(GeneratedArtifact)) == 0


def test_artifact_history_is_filtered_and_paginated_over_owned_jobs(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    first_run, first_source = seed(engine, client, suffix="first")
    first_id, _ = complete_artifact(engine, client, first_source)
    _, second_source = seed(engine, client, suffix="second")
    second_id, _ = complete_artifact(engine, client, second_source)
    filtered = client.get("/api/v1/artifacts/jobs", params={"source_result_id": str(first_source)})
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()["jobs"]] == [str(first_id)]
    assert (
        client.get("/api/v1/artifacts/jobs", params={"source_result_id": str(uuid.uuid4())}).json()[
            "jobs"
        ]
        == []
    )
    page = client.get("/api/v1/artifacts/jobs?limit=1").json()
    assert [item["id"] for item in page["jobs"]] == [str(second_id)]
    assert page["next_cursor"] == str(second_id)
    following = client.get(
        "/api/v1/artifacts/jobs", params={"limit": 1, "before": page["next_cursor"]}
    ).json()
    assert [item["id"] for item in following["jobs"]] == [str(first_id)]
    assert following["next_cursor"] is None
    assert client.get(f"/api/v1/artifacts/jobs?before={first_run}").status_code == 404
    malformed = client.get("/api/v1/artifacts/jobs?source_result_id=private-malformed-filter")
    assert malformed.status_code == 422 and "private-malformed" not in malformed.text


def test_expired_session_cannot_read_jobs_or_exports(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    run_id, saved = complete_artifact(engine, client, source_id)
    with Session(engine) as session, session.begin():
        owner = session.scalar(select(BrowserSession))
        owner.expires_at = session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
    for path in (
        "/api/v1/artifacts/jobs",
        f"/api/v1/artifacts/jobs/{run_id}",
        f"/api/v1/artifacts/{saved.id}",
        f"/api/v1/artifacts/{saved.id}/export",
    ):
        response = client.get(path)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"


def test_availability_is_rechecked_after_queue_lock(artifacts_api):
    engine, factory = artifacts_api
    calls = []

    def policy():
        calls.append(True)
        return len(calls) == 1

    client = factory(policy=policy)
    _, source_id = seed(engine, client)
    response = artifact_request(client, source_id)
    assert response.status_code == 503
    assert len(calls) == 2
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ArtifactRequest)) == 0
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 1


def test_answer_can_be_written_but_cannot_be_formalised_as_a_bridge(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    created = submit(client, bootstrap(client), mode="ask", goal=QUESTION)
    assert created.status_code == 201
    jobs, store = JobRepository(engine), EvidenceStore(engine)
    lease = jobs.claim_next("synthetic-answer-basis")
    result = answer_result()
    for source in result.sources:
        store.save_source(lease, source)
    source = store.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(source.id)})
    assert artifact_request(client, source.id, kind="formalise").status_code == 404
    created_document = artifact_request(client, source.id, kind="write_summary")
    assert created_document.status_code == 201
    assert created_document.json()["mode"] == "ask"
    assert created_document.json()["task_kind"] == "write_summary"
