"""Explicit, owned reassessment admission over real saved evidence; no model calls."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text, update
from sqlalchemy.orm import Session
from test_assessments import assessment_result, revised_result
from test_evidence import document
from test_ingestion_api import create as create_watch
from test_ingestion_api import save_fixture_page
from test_knowledge import seed
from test_run_api import bootstrap, submit
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.assessment_api import create_assessment_router
from polymath.assessment_models import AssessmentRequest
from polymath.assessments import AssessmentStore
from polymath.evidence import EvidenceStore
from polymath.ingestion_api import create_ingestion_router
from polymath.jobs import JobRepository
from polymath.models import ResearchRun
from polymath.run_api import QUEUE_LOCK_KEY, create_run_router

database = database_fixture
pytestmark = pytest.mark.integration
PREFIX = "/api/v1/assessments"


@pytest.fixture
def assessments(database):
    with ExitStack() as clients:

        def factory(*, enabled=False, availability=None):
            app = FastAPI()
            app.include_router(create_run_router(database, availability=lambda: True))
            app.include_router(create_ingestion_router(database, availability=lambda: True))
            app.include_router(
                create_assessment_router(
                    database,
                    availability=availability if availability is not None else lambda: enabled,
                )
            )
            return clients.enter_context(TestClient(app))

        yield database, factory


def prepare(engine, client, *, suffix="owned"):
    source_run_id, source_result_id = seed(
        engine, client, title=f"Synthetic {suffix} prior finding", suffix=suffix
    )
    watch = create_watch(
        client,
        label=f"Synthetic {suffix} source watch",
        query=f"Synthetic {suffix} literature",
        source_result_id=str(source_result_id),
    )
    assert watch.status_code == 201, watch.text
    watch_id = watch.json()["id"]
    observed = document(
        f"fixture:assessment-new-{suffix}",
        title=f"Synthetic {suffix} newly observed source",
        text="Synthetic new evidence bounds the scope of the earlier proposed comparison.",
    )
    source = save_fixture_page(engine, client, watch_id, (observed,))[0]
    return {
        "source_result_id": str(source_result_id),
        "watch_id": watch_id,
        "new_source_version_ids": [str(source.version_id)],
        "expected_head_revision": 0,
    }, source_run_id


def enqueue(client, body, headers=None):
    return client.post(
        PREFIX + "/jobs", headers=bootstrap(client) if headers is None else headers, json=body
    )


def count_jobs(engine):
    with Session(engine) as session:
        return session.scalar(
            select(func.count())
            .select_from(ResearchRun)
            .where(ResearchRun.task_kind == "reassess_finding")
        )


def claim_assessment(engine, client, body):
    response = enqueue(client, body)
    assert response.status_code == 201, response.text
    lease = JobRepository(engine).claim_next(
        "synthetic-api-assessment", lease_seconds=300, task_kinds=("reassess_finding",)
    )
    assert str(lease.run_id) == response.json()["id"]
    return lease


def assert_private(response):
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_reassessment_defaults_disabled_and_collection_does_not_enqueue_one(assessments):
    engine, factory = assessments
    client = factory()
    capabilities = client.get(PREFIX + "/capabilities")
    assert capabilities.json() == {
        "execution_available": False,
        "reason": "research_unavailable",
        "automatic_reassessment": False,
    }
    assert_private(capabilities)
    assert client.get(PREFIX + "/jobs").status_code == 401
    body, _ = prepare(engine, client)
    assert count_jobs(engine) == 0
    assert client.get(f"{PREFIX}/heads/{body['source_result_id']}").status_code == 200
    assert client.get(f"{PREFIX}/heads/{body['source_result_id']}/history").json() == {
        "assessments": [],
        "next_cursor": None,
    }
    blocked = enqueue(client, body)
    assert blocked.status_code == 503 and blocked.json() == {"detail": "research_unavailable"}
    assert_private(blocked)
    assert count_jobs(engine) == 0


@pytest.mark.parametrize("policy", [lambda: 1, lambda: "true", lambda: None])
def test_only_explicit_true_admits_a_model_capable_job(assessments, policy):
    engine, factory = assessments
    client = factory(availability=policy)
    body, _ = prepare(engine, client)
    assert enqueue(client, body).status_code == 503
    assert count_jobs(engine) == 0


def test_explicit_submission_pins_real_evidence_and_uses_shared_status_events_and_cancel(
    assessments,
):
    engine, factory = assessments
    client = factory(enabled=True)
    body, source_run_id = prepare(engine, client)
    response = enqueue(client, body)
    assert response.status_code == 201, response.text
    assert_private(response)
    job = response.json()
    assert job["task_kind"] == "reassess_finding" and job["status"] == "queued"
    assert job["assessment_id"] is None and job["result_id"] is None and job["artifact_id"] is None
    for field in (
        "source_result_id",
        "watch_id",
        "new_source_version_ids",
        "expected_head_revision",
    ):
        assert job[field] == body[field]
    assert job["root_result_id"] == body["source_result_id"]
    assert len(job["source_sha256"]) == 64
    with Session(engine) as session:
        original = session.get(ResearchRun, source_run_id)
        queued = session.get(ResearchRun, uuid.UUID(job["id"]))
        assert (queued.goal, queued.mode) == (original.goal, original.mode)
        assert session.get(AssessmentRequest, queued.id) is not None
    assert client.get(f"{PREFIX}/jobs/{job['id']}").json() == job
    assert client.get(PREFIX + "/jobs", params={"watch_id": body["watch_id"]}).json()["jobs"] == [
        job
    ]
    assert client.get(PREFIX + "/jobs", params={"root_result_id": body["source_result_id"]}).json()[
        "jobs"
    ] == [job]
    assert all(run["id"] != job["id"] for run in client.get("/api/v1/runs").json()["runs"])
    cancelled = client.post(f"/api/v1/runs/{job['id']}/cancel", headers=bootstrap(client))
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    status = client.get(f"{PREFIX}/jobs/{job['id']}").json()
    assert status["status"] == "cancelled" and status["assessment_id"] is None
    replay = client.get(f"/api/v1/runs/{job['id']}/events")
    assert replay.status_code == 200 and "event: activity" in replay.text
    assert "Explicit reassessment queued" in replay.text
    assert count_jobs(engine) == 1


def test_legacy_finding_detail_and_head_are_exact_owned_versions(assessments):
    engine, factory = assessments
    client, other = factory(), factory()
    body, source_run_id = prepare(engine, client)
    source_id = body["source_result_id"]
    head = client.get(f"{PREFIX}/heads/{source_id}")
    assert head.status_code == 200
    assert_private(head)
    state = head.json()
    assert state["root_result_id"] == state["current_result_id"] == source_id
    assert state["requested_result_id"] == source_id and state["current_run_id"] == str(
        source_run_id
    )
    assert state["revision"] == 0 and state["state"] == "supported"
    assert state["can_generate"] and state["is_current"] and not state["is_historical"]
    detail = client.get(f"{PREFIX}/findings/{source_id}")
    assert detail.status_code == 200
    assert_private(detail)
    record = detail.json()
    assert record["id"] == source_id and record["run_id"] == str(source_run_id)
    assert record["task_kind"] == "research" and record["run"]["id"] == str(source_run_id)
    assert record["payload"] == client.get(f"/api/v1/runs/{source_run_id}/result").json()
    assert record["assessment"] == state
    bootstrap(other)
    for suffix in (f"/heads/{source_id}", f"/heads/{source_id}/history", f"/findings/{source_id}"):
        foreign = other.get(PREFIX + suffix)
        assert foreign.status_code == 404 and foreign.json() == {"detail": "assessment_not_found"}


@pytest.mark.parametrize(
    "changes",
    [
        {"source_result_id": "PRIVATE-ASSESSMENT-INPUT"},
        {"watch_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
        {"new_source_version_ids": []},
        {"new_source_version_ids": [str(uuid.uuid4())] * 2},
        {"new_source_version_ids": [str(uuid.uuid4()) for _ in range(6)]},
        {"new_source_version_ids": ["PRIVATE-ASSESSMENT-INPUT"]},
        {"new_source_version_ids": [True]},
        {"expected_head_revision": -1},
        {"expected_head_revision": True},
        {"expected_head_revision": "0"},
        {"expected_head_revision": 2**31},
        {"disposition": "unsupported"},
        {"applied": True},
        {"owner_session_hash": "PRIVATE-ASSESSMENT-INPUT"},
    ],
)
def test_invalid_or_forged_control_input_is_sanitized_before_admission(assessments, changes):
    engine, factory = assessments
    client = factory(enabled=True)
    body = {
        "source_result_id": str(uuid.uuid4()),
        "watch_id": str(uuid.uuid4()),
        "new_source_version_ids": [str(uuid.uuid4())],
        "expected_head_revision": 0,
        **changes,
    }
    response = client.post(
        PREFIX + "/jobs",
        headers={**bootstrap(client), "Content-Type": "application/json"},
        content=json.dumps(body).encode("ascii"),
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_assessment_request"}
    assert "PRIVATE-ASSESSMENT-INPUT" not in response.text
    assert_private(response)
    assert count_jobs(engine) == 0


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"X-CSRF-Token": "0" * 64},
        {"Origin": "https://another.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_submission_needs_browser_csrf_and_origin_without_operator_role(assessments, changes):
    engine, factory = assessments
    client = factory(enabled=True)
    body, _ = prepare(engine, client)
    good = bootstrap(client)
    supplied = {} if not changes else {**good, **changes}
    response = enqueue(client, body, supplied)
    assert response.status_code == 403
    assert_private(response)
    assert count_jobs(engine) == 0


def test_foreign_and_missing_inputs_are_generic_and_atomic(assessments):
    engine, factory = assessments
    own, other = factory(enabled=True), factory(enabled=True)
    own_body, _ = prepare(engine, own)
    foreign_body, _ = prepare(engine, other, suffix="foreign")
    attempts = [
        {**own_body, "source_result_id": foreign_body["source_result_id"]},
        {**own_body, "watch_id": foreign_body["watch_id"]},
        {**own_body, "new_source_version_ids": foreign_body["new_source_version_ids"]},
        {**own_body, "source_result_id": str(uuid.uuid4())},
        {**own_body, "new_source_version_ids": [str(uuid.uuid4())]},
    ]
    for body in attempts:
        response = enqueue(own, body)
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "assessment_not_found"}
        assert_private(response)
    assert count_jobs(engine) == 0
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AssessmentRequest)) == 0


def test_stale_head_and_shared_active_quota_cannot_create_another_job(assessments):
    engine, factory = assessments
    client = factory(enabled=True)
    body, _ = prepare(engine, client)
    stale = enqueue(client, {**body, "expected_head_revision": 1})
    assert stale.status_code == 409 and stale.json() == {"detail": "assessment_head_changed"}
    assert count_jobs(engine) == 0
    assert submit(client, bootstrap(client)).status_code == 201
    blocked = enqueue(client, body)
    assert blocked.status_code == 429 and blocked.json() == {"detail": "active_run_limit"}
    assert count_jobs(engine) == 0


def test_concurrent_submissions_share_quota_and_cannot_duplicate_work(assessments):
    engine, factory = assessments
    client = factory(enabled=True)
    body, _ = prepare(engine, client)
    headers, start = bootstrap(client), Barrier(2)

    def create():
        start.wait(timeout=5)
        return enqueue(client, body, headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: create(), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 429]
    assert count_jobs(engine) == 1


def test_owned_job_history_has_bounded_nonrepeating_cursors(assessments):
    engine, factory = assessments
    own, other = factory(enabled=True), factory(enabled=True)
    body, _ = prepare(engine, own)
    created = []
    for _ in range(3):
        response = enqueue(own, body)
        assert response.status_code == 201, response.text
        job_id = response.json()["id"]
        created.append(job_id)
        assert own.post(f"/api/v1/runs/{job_id}/cancel", headers=bootstrap(own)).status_code == 200
    first = own.get(PREFIX + "/jobs", params={"limit": 2}).json()
    second = own.get(PREFIX + "/jobs", params={"limit": 2, "before": first["next_cursor"]}).json()
    assert len(first["jobs"]) == 2 and len(second["jobs"]) == 1 and second["next_cursor"] is None
    assert {job["id"] for job in [*first["jobs"], *second["jobs"]]} == set(created)
    bootstrap(other)
    assert other.get(PREFIX + "/jobs").json() == {"jobs": [], "next_cursor": None}
    assert other.get(f"{PREFIX}/jobs/{created[0]}").status_code == 404
    assert other.get(PREFIX + "/jobs", params={"before": created[0]}).status_code == 404
    for parameters in ({"limit": 21}, {"limit": 0}, {"before": "PRIVATE-ASSESSMENT-INPUT"}):
        response = own.get(PREFIX + "/jobs", params=parameters)
        assert response.status_code == 422 and "PRIVATE-ASSESSMENT-INPUT" not in response.text


def test_expiry_and_policy_change_do_not_admit_a_job(assessments):
    engine, factory = assessments
    active = {"enabled": True}
    client = factory(availability=lambda: active["enabled"])
    body, _ = prepare(engine, client)
    headers = bootstrap(client)
    active["enabled"] = False
    assert enqueue(client, body, headers).status_code == 503
    active["enabled"] = True
    with Session(engine) as session, session.begin():
        session.execute(
            update(BrowserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert enqueue(client, body, headers).status_code == 401
    for suffix in (
        "/jobs",
        f"/heads/{body['source_result_id']}",
        f"/findings/{body['source_result_id']}",
    ):
        assert client.get(PREFIX + suffix).status_code == 401
    assert count_jobs(engine) == 0


@pytest.mark.parametrize("change", ["browser_expiry", "availability"])
def test_admission_rechecks_authority_after_waiting_for_the_shared_queue_lock(assessments, change):
    engine, factory = assessments
    active = {"enabled": True}
    client = factory(availability=lambda: active["enabled"])
    body, _ = prepare(engine, client)
    headers, waiting = bootstrap(client), Event()

    def observe_lock(connection, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock" in statement:
            waiting.set()

    event.listen(engine, "before_cursor_execute", observe_lock)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with engine.begin() as blocker:
                blocker.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK_KEY})
                waiting.clear()
                future = pool.submit(enqueue, client, body, headers)
                assert waiting.wait(timeout=10)
                if change == "browser_expiry":
                    with engine.begin() as connection:
                        connection.execute(
                            update(BrowserSession).values(expires_at=func.clock_timestamp())
                        )
                else:
                    active["enabled"] = False
            response = future.result(timeout=10)
    finally:
        event.remove(engine, "before_cursor_execute", observe_lock)
    assert response.status_code == (401 if change == "browser_expiry" else 503)
    assert_private(response)
    assert count_jobs(engine) == 0


def test_completed_reassessment_has_owned_exact_passages_and_unsupported_head(assessments):
    engine, factory = assessments
    own, other = factory(enabled=True), factory(enabled=True)
    body, _ = prepare(engine, own)
    lease = claim_assessment(engine, own, body)
    store = AssessmentStore(engine)
    saved = store.save(lease, assessment_result(engine, store, lease))
    response = own.get(f"{PREFIX}/{saved.id}")
    assert response.status_code == 200
    assert_private(response)
    assert response.json() == saved.to_response()
    assessment = response.json()
    assert assessment["applied"] and assessment["disposition"] == "unsupported"
    assert assessment["payload"]["verified_passages"]
    for passage in assessment["payload"]["verified_passages"]:
        source = EvidenceStore(engine).get_document(
            lease.run_id, uuid.UUID(passage["retrieval_id"])
        )
        assert source.text[passage["start"] : passage["end"]] == passage["quote"]
        assert source.content_sha256 == passage["source_sha256"]
    job = own.get(f"{PREFIX}/jobs/{lease.run_id}").json()
    assert job["status"] == "succeeded" and job["assessment_id"] == str(saved.id)
    head = own.get(f"{PREFIX}/heads/{body['source_result_id']}").json()
    assert head["state"] == "unsupported" and head["revision"] == 1
    assert not head["can_generate"] and head["is_historical"]
    # Unsupported remains inspectable as the exact saved historical version.
    finding = own.get(f"{PREFIX}/findings/{body['source_result_id']}")
    assert finding.status_code == 200 and finding.json()["assessment"] == head
    consumed = enqueue(own, {**body, "expected_head_revision": 1})
    assert consumed.status_code == 422
    assert consumed.json() == {"detail": "assessment_source_already_assessed"}
    bootstrap(other)
    for path in (f"/{saved.id}", f"/jobs/{lease.run_id}"):
        foreign = other.get(PREFIX + path)
        assert foreign.status_code == 404 and foreign.json() == {"detail": "assessment_not_found"}


def test_applied_revised_finding_reader_uses_exact_positive_version_and_old_head_warning(
    assessments,
):
    engine, factory = assessments
    own, other = factory(enabled=True), factory(enabled=True)
    body, _ = prepare(engine, own)
    lease = claim_assessment(engine, own, body)
    store = AssessmentStore(engine)
    saved = store.save(lease, revised_result(engine, store, lease))
    candidate_id = str(saved.candidate_result_id)
    detail = own.get(f"{PREFIX}/findings/{candidate_id}")
    assert detail.status_code == 200, detail.text
    record = detail.json()
    assert record["id"] == candidate_id and record["run_id"] == str(lease.run_id)
    assert record["task_kind"] == "reassess_finding" and record["run"]["result_id"] is None
    assert record["payload"]["status"] == "bridge" and record["payload"]["sources"]
    assert record["assessment"]["can_generate"] and record["assessment"]["is_current"]
    assert record["assessment"]["current_run_id"] == str(lease.run_id)
    old = own.get(f"{PREFIX}/findings/{body['source_result_id']}")
    assert old.status_code == 200
    assert old.json()["assessment"]["is_historical"]
    assert old.json()["assessment"]["current_result_id"] == candidate_id
    assert not old.json()["assessment"]["can_generate"]
    assert own.get(f"/api/v1/runs/{lease.run_id}/result").status_code == 404
    bootstrap(other)
    assert other.get(f"{PREFIX}/findings/{candidate_id}").status_code == 404


def test_cas_losing_positive_candidate_is_auditable_but_never_an_exposed_finding(assessments):
    from test_assessments import enqueue as enqueue_fixture

    from polymath.access_models import ResearchRunAccess

    engine, factory = assessments
    client = factory(enabled=True)
    body, _ = prepare(engine, client)
    winner = claim_assessment(engine, client, body)
    with Session(engine) as session:
        token = session.get(ResearchRunAccess, winner.run_id).session_hash
    # Deliberately bypass API concurrency admission to exercise a recovered old
    # branch racing a newer durable assessment at the immutable storage boundary.
    loser = enqueue_fixture(
        engine,
        token,
        uuid.UUID(body["source_result_id"]),
        uuid.UUID(body["watch_id"]),
        [uuid.UUID(value) for value in body["new_source_version_ids"]],
    )
    store = AssessmentStore(engine)
    winning_result = assessment_result(engine, store, winner)
    losing_result = revised_result(engine, store, loser)
    store.save(winner, winning_result)
    saved = store.save(loser, losing_result)
    assert not saved.applied and saved.candidate_result_id is not None
    audit = client.get(f"{PREFIX}/{saved.id}")
    assert audit.status_code == 200 and not audit.json()["applied"]
    response = client.get(f"{PREFIX}/findings/{saved.candidate_result_id}")
    assert response.status_code == 404 and response.json() == {"detail": "assessment_not_found"}


def test_failed_and_inconclusive_receipts_have_owned_bounded_history_without_head_change(
    assessments,
):
    from polymath.assessment_contracts import AssessmentResult
    from polymath.research.models import Usage

    engine, factory = assessments
    client = factory(enabled=True)
    body, _ = prepare(engine, client)
    store, created = AssessmentStore(engine), []
    for status in ("failed", "limit_reached", "completed"):
        lease = claim_assessment(engine, client, body)
        if status == "completed":
            result = assessment_result(engine, store, lease, disposition="inconclusive")
        else:
            result = AssessmentResult(
                status=status,
                limitations=("Synthetic bounded assessment did not establish a conclusion.",),
                reason="Synthetic source coverage was incomplete.",
                usage=Usage(),
            )
        saved = store.save(lease, result)
        created.append(str(saved.id))
        job = client.get(f"{PREFIX}/jobs/{lease.run_id}").json()
        assert job["assessment_id"] == str(saved.id)
        assert job["status"] == ("failed" if status == "failed" else "succeeded")
        assert client.get(f"{PREFIX}/{saved.id}").json() == saved.to_response()
    path = f"{PREFIX}/heads/{body['source_result_id']}/history"
    first = client.get(path, params={"limit": 2}).json()
    second = client.get(path, params={"limit": 2, "before": first["next_cursor"]}).json()
    assert len(first["assessments"]) == 2 and len(second["assessments"]) == 1
    assert second["next_cursor"] is None
    assert {row["id"] for row in [*first["assessments"], *second["assessments"]]} == set(created)
    assert all(not row["applied"] for row in first["assessments"])
    head = client.get(f"{PREFIX}/heads/{body['source_result_id']}").json()
    assert head["revision"] == 0 and head["can_generate"]
    for params in ({"limit": 21}, {"limit": 0}, {"before": "PRIVATE-ASSESSMENT-INPUT"}):
        invalid = client.get(path, params=params)
        assert invalid.status_code == 422 and "PRIVATE-ASSESSMENT-INPUT" not in invalid.text


def test_history_and_result_ids_never_reveal_foreign_or_missing_rows(assessments):
    engine, factory = assessments
    client = factory(enabled=True)
    body, _ = prepare(engine, client)
    for suffix in (
        f"/{uuid.uuid4()}",
        f"/findings/{uuid.uuid4()}",
        f"/heads/{uuid.uuid4()}/history",
    ):
        response = client.get(PREFIX + suffix)
        assert response.status_code == 404 and response.json() == {"detail": "assessment_not_found"}
    assert (
        client.get(
            f"{PREFIX}/heads/{body['source_result_id']}/history", params={"before": uuid.uuid4()}
        ).status_code
        == 404
    )
