"""Owned publication queue/control APIs using local fixtures, never Zenodo IO."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_artifact_api import artifacts_api as artifact_api_fixture
from test_publication_api import (
    approve,
    document,
    login,
    operator_policy,
    prepare,
    publisher_policy,
)
from test_run_api import bootstrap

from polymath.operator_auth import create_operator_router
from polymath.operator_models import OperatorSession
from polymath.publication import PublicationStore
from polymath.publication_api import create_publication_router
from polymath.publication_job_api import create_publication_job_router
from polymath.publication_job_models import PublicationJob, PublicationOperation
from polymath.publication_jobs import PublicationJobError, PublicationJobRepository
from polymath.publication_models import PublicationApproval
from polymath.run_api import COOKIE_NAME, _hash_token

artifacts_api = artifact_api_fixture
pytestmark = pytest.mark.integration


def repository(engine):
    return PublicationJobRepository(
        engine, PublicationStore(engine, publisher_policy(), operator_policy=operator_policy())
    )


@pytest.fixture
def publication_job_api(artifacts_api):
    engine, artifact_factory = artifacts_api

    def factory(*, enabled=False, availability=None, publication_policy=None, auth_policy=None):
        policy = publication_policy or publisher_policy()
        op_policy = auth_policy or operator_policy()
        store = PublicationStore(engine, policy, operator_policy=op_policy)
        jobs = PublicationJobRepository(engine, store)
        client = artifact_factory(enabled=True)
        client.app.include_router(create_operator_router(engine, op_policy))
        client.app.include_router(
            create_publication_router(
                engine,
                policy=policy,
                operator_policy=op_policy,
                status_resolver=jobs.status_for_manifest,
            )
        )
        client.app.include_router(
            create_publication_job_router(
                engine,
                policy=policy,
                operator_policy=op_policy,
                availability=availability or (lambda: enabled),
            )
        )
        return client

    return engine, factory


def approved_manifest(engine, client):
    artifact = document(engine, client)
    preview = prepare(client, artifact.id)
    assert preview.status_code == 201, preview.text
    headers = login(client)
    response = approve(client, preview.json(), headers)
    assert response.status_code == 200, response.text
    return response.json(), headers


def enqueue(client, manifest, headers, **changes):
    return client.post(
        "/api/v1/publication-jobs",
        headers=headers,
        json={
            "manifest_id": manifest["id"],
            "approval_id": manifest["approval"]["id"],
            "expected_manifest_sha256": manifest["manifest_sha256"],
            **changes,
        },
    )


def queued(engine, client):
    manifest, headers = approved_manifest(engine, client)
    response = enqueue(client, manifest, headers)
    assert response.status_code == 201, response.text
    return response.json(), manifest, headers


def test_default_execution_gate_stays_disabled_after_approval(publication_job_api):
    engine, factory = publication_job_api
    client = factory()
    capability = client.get("/api/v1/publication-jobs/capabilities")
    assert capability.status_code == 200
    assert capability.json() == {
        "execution_available": False,
        "reason": "publication_execution_disabled",
    }
    manifest, headers = approved_manifest(engine, client)
    response = enqueue(client, manifest, headers)
    assert response.status_code == 503
    assert response.json() == {"detail": "publication_execution_disabled"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationJob)) == 0


def test_capability_failure_is_closed_and_never_echoes_configuration(publication_job_api):
    _, factory = publication_job_api

    def unavailable():
        raise RuntimeError("private-fixture-credential")

    client = factory(availability=unavailable)
    response = client.get("/api/v1/publication-jobs/capabilities")
    assert response.json() == {
        "execution_available": False,
        "reason": "publication_execution_disabled",
    }
    assert "private" not in response.text


def test_enqueue_is_explicit_owned_idempotent_and_private(publication_job_api):
    engine, factory = publication_job_api
    client = factory(enabled=True)
    job, manifest, headers = queued(engine, client)
    assert job["status"] == job["stage"] == "queued"
    assert job["work_kind"] == "publish"
    assert job["manifest_id"] == manifest["id"]
    assert job["approval_id"] == manifest["approval"]["id"]
    assert job["destination"] == "sandbox" and job["sandbox"]
    assert not job["verified"]
    assert job["doi"] is None and job["record_url"] is None
    assert job["can_cancel"] and not job["can_resume"]
    forbidden = {
        "owner_session_hash",
        "lease_token",
        "lease_owner",
        "outcome",
        "bucket",
        "remote_deposit_id",
    }
    assert not forbidden.intersection(job)
    repeated = enqueue(client, manifest, headers)
    assert repeated.status_code == 201
    assert repeated.json()["id"] == job["id"]
    assert client.get(f"/api/v1/publication-jobs/{job['id']}").json() == job
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationJob)) == 1
        assert session.scalar(select(func.count()).select_from(PublicationOperation)) == 0


def test_concurrent_enqueue_never_creates_a_second_publication_job(publication_job_api):
    engine, factory = publication_job_api
    client = factory(enabled=True)
    manifest, headers = approved_manifest(engine, client)
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=5)
        return enqueue(client, manifest, headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(submit), pool.submit(submit)
        responses = first.result(timeout=15), second.result(timeout=15)
    assert all(response.status_code == 201 for response in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationJob)) == 1


@pytest.mark.parametrize("changed", ["browser_csrf", "operator_csrf", "origin"])
def test_publish_requires_both_csrf_layers_and_origin(publication_job_api, changed):
    engine, factory = publication_job_api
    client = factory(enabled=True)
    manifest, headers = approved_manifest(engine, client)
    if changed == "browser_csrf":
        headers["X-CSRF-Token"] = "incorrect"
    elif changed == "operator_csrf":
        headers["X-Operator-CSRF-Token"] = "incorrect"
    else:
        headers["Origin"] = "https://attacker.invalid"
    response = enqueue(client, manifest, headers)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationJob)) == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"manifest_id": "private-invalid-manifest"},
        {"manifest_id": str(uuid.uuid4()).upper()},
        {"approval_id": "private-invalid-receipt"},
        {"expected_manifest_sha256": "private-invalid-hash"},
        {"extra": "private-extra-field"},
    ],
)
def test_invalid_job_requests_are_redacted(publication_job_api, changes):
    engine, factory = publication_job_api
    client = factory(enabled=True)
    manifest, headers = approved_manifest(engine, client)
    response = enqueue(client, manifest, headers, **changes)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_publication_request"}
    assert "private-" not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationJob)) == 0


def test_manifest_or_policy_changes_cannot_enqueue_an_old_approval(publication_job_api):
    engine, factory = publication_job_api
    client = factory(enabled=True)
    manifest, headers = approved_manifest(engine, client)
    wrong = enqueue(client, manifest, headers, expected_manifest_sha256="0" * 64)
    assert wrong.status_code == 409
    changed = factory(
        enabled=True, publication_policy=publisher_policy(identifier="changed-publisher")
    )
    changed.cookies.update(client.cookies)
    rejected = enqueue(changed, manifest, headers)
    assert rejected.status_code == 409
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationJob)) == 0


def test_foreign_jobs_manifests_and_events_remain_private(publication_job_api):
    engine, factory = publication_job_api
    own, foreign = factory(enabled=True), factory(enabled=True)
    job, manifest, _ = queued(engine, own)
    foreign_headers = login(foreign)
    for suffix in ("", "/events"):
        known = foreign.get(f"/api/v1/publication-jobs/{job['id']}{suffix}")
        missing = foreign.get(f"/api/v1/publication-jobs/{uuid.uuid4()}{suffix}")
        assert known.status_code == missing.status_code == 404
        assert known.json() == missing.json()
        assert known.headers["cache-control"] == "no-store"
    assert foreign.get("/api/v1/publication-jobs").json()["jobs"] == []
    assert enqueue(foreign, manifest, foreign_headers).status_code == 404
    assert (
        foreign.post(
            f"/api/v1/publication-jobs/{job['id']}/cancel", headers=foreign_headers
        ).status_code
        == 404
    )


def test_cancel_works_when_execution_gate_is_off_and_preserves_replay(publication_job_api):
    engine, factory = publication_job_api
    client = factory(enabled=True)
    job, _, headers = queued(engine, client)
    initial = client.get(f"/api/v1/publication-jobs/{job['id']}/events").json()
    assert initial["events"] and not initial["terminal"]
    cursor = initial["next_cursor"]
    disabled = factory(enabled=False)
    disabled.cookies.update(client.cookies)
    assert disabled.get(f"/api/v1/publication-jobs/{job['id']}").json()["can_cancel"]
    cancelled = disabled.post(f"/api/v1/publication-jobs/{job['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    after = client.get(
        f"/api/v1/publication-jobs/{job['id']}/events", params={"after": cursor}
    ).json()
    assert after["terminal"] and after["events"]
    assert all(event["sequence"] > cursor for event in after["events"])
    again = client.get(
        f"/api/v1/publication-jobs/{job['id']}/events", params={"after": after["next_cursor"]}
    ).json()
    assert again["events"] == []
    assert client.get(f"/api/v1/publication-jobs/{job['id']}/events?after=-1").status_code == 422


def test_unknown_external_operation_blocks_resume_and_reconcile_only_queues_reads(
    publication_job_api,
):
    engine, factory = publication_job_api
    client = factory(enabled=True)
    job, manifest, headers = queued(engine, client)
    jobs = repository(engine)
    lease = jobs.claim_next("synthetic-lost-publication-worker")
    grant = jobs.prepare_operation(lease, "create", "a" * 64)
    assert grant is not None
    with Session(engine) as session, session.begin():
        row = session.get(PublicationJob, uuid.UUID(job["id"]))
        row.status = "interrupted"
        row.lease_owner = None
        row.lease_expires_at = None
        row.reason = "publication_operation_unresolved"
    state = client.get(f"/api/v1/publication-jobs/{job['id']}").json()
    assert state["external_outcome_unknown"]
    assert not state["can_resume"] and state["can_reconcile"]
    assert (
        client.get(f"/api/v1/publications/{manifest['id']}").json()["publication_status"]
        == "unknown"
    )
    resume = client.post(
        f"/api/v1/publication-jobs/{job['id']}/resume",
        headers=headers,
        json={
            "approval_id": manifest["approval"]["id"],
            "expected_manifest_sha256": manifest["manifest_sha256"],
        },
    )
    assert resume.status_code == 409
    before = client.get(f"/api/v1/publications/{manifest['id']}").json()["approval"]
    reconciled = client.post(f"/api/v1/publication-jobs/{job['id']}/reconcile", headers=headers)
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["id"] == job["id"]
    assert reconciled.json()["work_kind"] == "reconcile"
    assert reconciled.json()["status"] == "queued"
    assert client.get(f"/api/v1/publications/{manifest['id']}").json()["approval"] == before
    readonly = jobs.claim_next("synthetic-readonly-publication-worker")
    with pytest.raises(PublicationJobError):
        jobs.prepare_operation(readonly, "create", "b" * 64)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationJob)) == 1
        assert session.scalar(select(func.count()).select_from(PublicationOperation)) == 1
        assert session.scalar(select(func.count()).select_from(PublicationApproval)) == 1


def test_safe_resume_retains_job_identity_and_requires_exact_current_receipt(publication_job_api):
    engine, factory = publication_job_api
    client = factory(enabled=True)
    job, manifest, headers = queued(engine, client)
    with Session(engine) as session, session.begin():
        row = session.get(PublicationJob, uuid.UUID(job["id"]))
        row.status = "interrupted"
        row.reason = "synthetic_worker_shutdown"
    assert client.get(f"/api/v1/publication-jobs/{job['id']}").json()["can_resume"]
    payload = {"approval_id": manifest["approval"]["id"], "expected_manifest_sha256": "0" * 64}
    assert (
        client.post(
            f"/api/v1/publication-jobs/{job['id']}/resume", headers=headers, json=payload
        ).status_code
        == 409
    )
    payload["expected_manifest_sha256"] = manifest["manifest_sha256"]
    resumed = client.post(
        f"/api/v1/publication-jobs/{job['id']}/resume", headers=headers, json=payload
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["id"] == job["id"] and resumed.json()["status"] == "queued"


def test_operator_expiry_hides_controls_but_keeps_owned_status_readable(publication_job_api):
    engine, factory = publication_job_api
    client = factory(enabled=True)
    job, manifest, headers = queued(engine, client)
    with Session(engine) as session, session.begin():
        operator = session.scalar(select(OperatorSession))
        operator.expires_at = session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
    view = client.get(f"/api/v1/publication-jobs/{job['id']}")
    assert view.status_code == 200
    assert not any(view.json()[key] for key in ("can_cancel", "can_resume", "can_reconcile"))
    assert (
        client.post(f"/api/v1/publication-jobs/{job['id']}/cancel", headers=headers).status_code
        == 401
    )
    assert enqueue(client, manifest, headers).status_code == 401


def test_manifest_filter_and_cursor_only_expose_owned_jobs(publication_job_api):
    engine, factory = publication_job_api
    client = factory(enabled=True)
    job, manifest, _ = queued(engine, client)
    page = client.get("/api/v1/publication-jobs", params={"manifest_id": manifest["id"]}).json()
    assert [item["id"] for item in page["jobs"]] == [job["id"]]
    assert page["next_cursor"] is None
    assert (
        client.get("/api/v1/publication-jobs", params={"manifest_id": str(uuid.uuid4())}).json()[
            "jobs"
        ]
        == []
    )
    foreign = factory(enabled=True)
    bootstrap(foreign)
    assert foreign.get(f"/api/v1/publication-jobs?before={job['id']}").status_code == 404


def test_public_status_never_exposes_unverified_doi_or_private_operation_data(publication_job_api):
    engine, factory = publication_job_api
    client = factory(enabled=True)
    job, manifest, _ = queued(engine, client)
    with Session(engine) as session, session.begin():
        row = session.get(PublicationJob, uuid.UUID(job["id"]))
        row.status = "interrupted"
        row.stage = "verify"
        row.remote_deposit_id = "7"
        row.publication_reported = True
        row.reason = "private backend exception body"
        row.outcome = {
            "verified": True,
            "manifest_sha256": "0" * 64,
            "markdown_sha256": manifest["manifest"]["markdown_sha256"],
            "destination": "sandbox",
            "remote_deposit_id": "7",
            "record_id": "77",
            "doi": "10.5072/zenodo.77",
            "record_url": "https://sandbox.zenodo.org/records/77",
            "bucket": "private-bucket-fixture",
            "access_token": "private-credential-fixture",
        }
    unverified = client.get(f"/api/v1/publication-jobs/{job['id']}")
    assert unverified.status_code == 200
    view = unverified.json()
    assert not view["verified"] and view["doi"] is None and view["record_url"] is None
    assert view["publication_reported"] and view["external_outcome_unknown"]
    assert not view["can_resume"] and view["can_reconcile"]
    assert "private" not in unverified.text
    assert "bucket" not in unverified.text and "outcome" not in view
    with Session(engine) as session, session.begin():
        session.get(PublicationJob, uuid.UUID(job["id"])).status = "published"
    # A raw status and a claimed verification boolean cannot substitute for
    # the exact manifest/file bindings, including on the legacy manifest API.
    claimed = client.get(f"/api/v1/publication-jobs/{job['id']}")
    assert claimed.status_code == 503
    assert claimed.json() == {"detail": "publication_job_storage_unavailable"}
    assert "10.5072" not in claimed.text and "private" not in claimed.text
    assert (
        client.get(f"/api/v1/publications/{manifest['id']}").json()["publication_status"]
        == "unknown"
    )


@pytest.mark.parametrize("path", ["record", "records"])
def test_verified_record_id_can_differ_from_deposit_id(publication_job_api, path):
    engine, factory = publication_job_api
    client = factory(enabled=True)
    job, manifest, _ = queued(engine, client)
    with Session(engine) as session, session.begin():
        row = session.get(PublicationJob, uuid.UUID(job["id"]))
        row.status = "published"
        row.stage = "complete"
        row.remote_deposit_id = "7"
        row.publication_reported = True
        row.outcome = {
            "verified": True,
            "manifest_sha256": manifest["manifest_sha256"],
            "markdown_sha256": manifest["manifest"]["markdown_sha256"],
            "destination": "sandbox",
            "remote_deposit_id": "7",
            "record_id": "77",
            "doi": "10.5072/zenodo.77",
            "record_url": f"https://sandbox.zenodo.org/{path}/77",
        }
    state = client.get(f"/api/v1/publication-jobs/{job['id']}").json()
    assert state["verified"]
    assert state["doi"] == "10.5072/zenodo.77"
    assert state["record_url"] == f"https://sandbox.zenodo.org/{path}/77"
    assert (
        client.get(f"/api/v1/publications/{manifest['id']}").json()["publication_status"]
        == "published"
    )


def test_browser_expiry_prevents_status_and_control_access(publication_job_api):
    from polymath.access_models import BrowserSession

    engine, factory = publication_job_api
    client = factory(enabled=True)
    job, _, headers = queued(engine, client)
    with Session(engine) as session, session.begin():
        browser = session.get(BrowserSession, _hash_token(client.cookies.get(COOKIE_NAME)))
        browser.expires_at = session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
    assert client.get(f"/api/v1/publication-jobs/{job['id']}").status_code == 401
    assert client.get("/api/v1/publication-jobs").status_code == 401
    assert (
        client.post(f"/api/v1/publication-jobs/{job['id']}/cancel", headers=headers).status_code
        == 401
    )
