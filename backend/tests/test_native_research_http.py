"""Actual native admission through every HTTP task surface; all HTTP providers mocked."""

import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_artifact_worker import ArtifactFixture
from test_assessment_controller import AssessmentFixtureModel
from test_evidence import document
from test_native_research_worker import ScientificHttpFixture, actual_worker
from test_native_research_worker import native as native_fixture
from test_worker import database as database_fixture

from polymath.budget_models import BudgetReservation, BudgetRunLimit
from polymath.config import Settings
from polymath.evidence import EvidenceStore
from polymath.ingestion import IngestionRepository
from polymath.jobs import JobRepository
from polymath.main import create_app
from polymath.meta_research_models import NativeResearchRunBinding
from polymath.models import ResearchRun

pytestmark = pytest.mark.integration
database = database_fixture
native = native_fixture


@contextmanager
def application_client(native):
    application = create_app(
        Settings(
            model_api_key=native.policy.api_key,
            meta_model=native.policy.model_id,
            _env_file=None,
        ),
        research_policy=native.policy,
        readiness_policy=native.readiness_policy,
        operator_policy=native.operator_policy,
    )
    with TestClient(application) as client:
        client.cookies.update(native.client.cookies)
        yield client


def body(native, **changes):
    return {
        "mode": "explore",
        "goal": "Synthetic native research question?",
        "expected_activation_sha256": native.activation_sha256,
        **changes,
    }


def ledger_counts(native):
    with Session(native.engine) as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (ResearchRun, NativeResearchRunBinding, BudgetRunLimit, BudgetReservation)
        )


async def seed_finding(client, native, monkeypatch):
    response = client.post("/api/v1/runs", headers=native.headers, json=body(native))
    assert response.status_code == 201, response.text
    worker, _ = actual_worker(native, monkeypatch, ScientificHttpFixture())
    result = await worker.run_once("native-http-basis")
    assert result.status == "succeeded" and result.result_id
    return response.json(), result


@pytest.mark.parametrize("mode", ["explore", "ask", "surprise"])
async def test_http_modes_bind_native_policy_and_stream_actual_adapter_results(
    native, monkeypatch, mode
):
    with application_client(native) as client:
        caps = client.get("/api/v1/capabilities").json()
        assert caps["research_available"] and caps["native_research_required"]
        current = client.get("/api/v1/budget/research/current")
        assert current.status_code == 200
        assert current.json()["activation"]["manifest_sha256"] == native.activation_sha256
        response = client.post(
            "/api/v1/runs",
            headers=native.headers,
            json=body(native, mode=mode, goal="" if mode == "surprise" else "Synthetic question?"),
        )
        assert response.status_code == 201, response.text
        created = response.json()
        assert created["native_research"]["activation_sha256"] == native.activation_sha256
        assert created["native_research"]["expected_lease_token"] == "0"
        worker, calls = actual_worker(native, monkeypatch, ScientificHttpFixture())
        outcome = await worker.run_once("native-http-mode")
        assert outcome.status == "succeeded" and outcome.result_id
        result = client.get(f"/api/v1/runs/{created['id']}/result")
        assert result.status_code == 200
        assert result.json()["status"] == ("answer" if mode == "ask" else "bridge")
        detail = client.get(f"/api/v1/runs/{created['id']}").json()
        assert detail["status"] == "succeeded" and not detail["native_research"]["can_resume"]
        events = client.get(f"/api/v1/runs/{created['id']}/events")
        assert events.status_code == 200 and "source_read" in events.text
        assert len(calls.requests) >= 6 and calls.source_requests
        assert native.policy.api_key.get_secret_value() not in result.text + events.text


@pytest.mark.parametrize("kind", ["formalise", "write_summary", "write_paper"])
async def test_http_artifacts_bind_original_native_profile_and_export_exact_content(
    native, monkeypatch, kind
):
    with application_client(native) as client:
        _, source = await seed_finding(client, native, monkeypatch)
        response = client.post(
            "/api/v1/artifacts/jobs",
            headers=native.headers,
            json={
                "kind": kind,
                "source_result_id": source.result_id,
                "expected_activation_sha256": native.activation_sha256,
            },
        )
        assert response.status_code == 201, response.text
        created = response.json()
        assert created["native_research"]["activation_sha256"] == native.activation_sha256
        worker, calls = actual_worker(native, monkeypatch, ArtifactFixture())
        outcome = await worker.run_once("native-http-document")
        assert outcome.status == "succeeded" and outcome.artifact_id
        detail = client.get(f"/api/v1/artifacts/jobs/{created['id']}")
        assert detail.status_code == 200 and detail.json()["status"] == "succeeded"
        assert len(calls.requests) == (2 if kind == "formalise" else 1)
        exported = client.get(f"/api/v1/artifacts/{outcome.artifact_id}/export")
        assert exported.status_code == 200 and exported.content


async def test_http_reassessment_binds_owned_watch_and_applies_existing_head_rules(
    native, monkeypatch
):
    with application_client(native) as client:
        _, result = await seed_finding(client, native, monkeypatch)
        ingestion = IngestionRepository(native.engine)
        watch = ingestion.create(
            native.owner_hash,
            label="Synthetic HTTP watch",
            query="Synthetic fresh evidence",
            source_result_id=uuid.UUID(result.result_id),
        )
        ingestion.run_now(native.owner_hash, watch.id)
        jobs = JobRepository(native.engine)
        lease = jobs.claim_next("native-http-free-collection", task_kinds=("ingest_literature",))
        source = EvidenceStore(native.engine).save_source(
            lease,
            document(
                "fixture:native-http-new", text="Synthetic new evidence retains this analogy."
            ),
        )
        ingestion.commit_page(
            lease,
            "openalex",
            ingestion.snapshot(lease).cursors["openalex"],
            None,
            (source,),
            exhausted=True,
        )
        jobs.finish(lease, status="succeeded", outcome={"model_calls": 0})
        response = client.post(
            "/api/v1/assessments/jobs",
            headers=native.headers,
            json={
                "source_result_id": result.result_id,
                "watch_id": str(watch.id),
                "new_source_version_ids": [str(source.version_id)],
                "expected_head_revision": 0,
                "expected_activation_sha256": native.activation_sha256,
            },
        )
        assert response.status_code == 201, response.text
        created = response.json()
        assert created["native_research"]["activation_sha256"] == native.activation_sha256
        worker, calls = actual_worker(native, monkeypatch, AssessmentFixtureModel())
        outcome = await worker.run_once("native-http-reassessment")
        assert outcome.status == "succeeded" and outcome.assessment_id
        detail = client.get(f"/api/v1/assessments/jobs/{created['id']}")
        assert detail.status_code == 200 and detail.json()["assessment_id"] == outcome.assessment_id
        assert len(calls.requests) == 3


@pytest.mark.parametrize("path", ["runs", "artifacts/jobs", "assessments/jobs"])
async def test_every_scientific_enqueue_requires_operator_csrf_before_any_new_job(native, path):
    fake_id = str(uuid.uuid4())
    payload = {
        "runs": body(native),
        "artifacts/jobs": {"kind": "write_summary", "source_result_id": fake_id},
        "assessments/jobs": {
            "source_result_id": fake_id,
            "watch_id": fake_id,
            "new_source_version_ids": [fake_id],
            "expected_head_revision": 0,
        },
    }[path]
    payload["expected_activation_sha256"] = native.activation_sha256
    headers = {
        key: value
        for key, value in native.headers.items()
        if key.lower() != "x-operator-csrf-token"
    }
    before = ledger_counts(native)
    with application_client(native) as client:
        response = client.post("/api/v1/" + path, headers=headers, json=payload)
        assert response.status_code == 403, response.text
    assert ledger_counts(native) == before


async def test_native_resume_uses_original_approval_token_and_same_job(native, monkeypatch):
    with application_client(native) as client:
        response = client.post("/api/v1/runs", headers=native.headers, json=body(native))
        assert response.status_code == 201
        run_id = response.json()["id"]
        jobs = JobRepository(native.engine)
        lease = jobs.claim_next(
            "interrupted-native-http", native_only=True, task_kinds=("research",)
        )
        jobs.interrupt(lease)
        detail = client.get(f"/api/v1/runs/{run_id}").json()
        controls = detail["native_research"]
        assert controls["can_resume"]
        before = ledger_counts(native)
        stale = client.post(
            f"/api/v1/runs/{run_id}/resume",
            headers=native.headers,
            json={
                "expected_lease_token": "0",
                "expected_activation_sha256": native.activation_sha256,
            },
        )
        assert stale.status_code == 409 and ledger_counts(native) == before
        resumed = client.post(
            f"/api/v1/runs/{run_id}/resume",
            headers=native.headers,
            json={
                "expected_lease_token": controls["expected_lease_token"],
                "expected_activation_sha256": native.activation_sha256,
            },
        )
        assert resumed.status_code == 200 and resumed.json()["id"] == run_id
        assert ledger_counts(native) == before
        worker, _ = actual_worker(native, monkeypatch, ScientificHttpFixture())
        assert (await worker.run_once("explicit-native-http-resume")).status == "succeeded"


async def test_observed_response_with_unknown_billing_keeps_http_warning_and_no_resume(
    native, monkeypatch
):
    with application_client(native) as client:
        response = client.post("/api/v1/runs", headers=native.headers, json=body(native))
        assert response.status_code == 201
        run_id = response.json()["id"]

        def missing_usage(value):
            value.pop("usage")

        worker, calls = actual_worker(
            native, monkeypatch, ScientificHttpFixture(), change_response=missing_usage
        )
        assert (await worker.run_once("native-http-unknown-billing")).status == "interrupted"
        assert len(calls.requests) == 1
        detail = client.get(f"/api/v1/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["external_outcome_unknown"] is True
        assert detail.json()["native_research"]["can_resume"] is False
        with Session(native.engine) as session:
            # Transport returned and was journalled, yet the full charge remains uncertain.
            assert session.get(ResearchRun, uuid.UUID(run_id)).external_request_in_flight is False
            reservation = session.scalar(
                select(BudgetReservation).where(BudgetReservation.run_id == uuid.UUID(run_id))
            )
            assert reservation.status == "unknown" and reservation.actual_microdollars is None
