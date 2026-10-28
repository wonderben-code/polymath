"""Actual queued artifact journeys with synthetic models and evidence only."""

import asyncio
import hashlib
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_knowledge import seed
from test_run_api import bootstrap
from test_worker import database as database_fixture
from test_worker import setup_worker

from polymath.artifact_api import create_artifact_router
from polymath.artifact_models import GeneratedArtifact
from polymath.artifacts import ArtifactStore
from polymath.budget import BudgetService
from polymath.evidence import EvidenceError, EvidenceStore
from polymath.jobs import JobRepository
from polymath.research.meta import ModelReply
from polymath.run_api import create_run_router
from polymath.worker import ExecutionStopped, _RecordedChecks

database = database_fixture
pytestmark = pytest.mark.integration


def finite_conjecture():
    return {
        "variables": [{"name": "x", "lower": -2, "upper": 2}],
        "assumptions": [],
        "conclusion": {
            "left": {"op": "constant", "value": 0},
            "operator": "le",
            "right": {"op": "power", "base": {"op": "variable", "name": "x"}, "exponent": 2},
        },
    }


class ArtifactFixture:
    """Responds to task, evidence ordinals, and actual checker observations."""

    def __init__(self):
        self.seen = []

    async def complete(self, messages, *, max_output_tokens):
        initial = json.loads(messages[1]["content"])
        kind, basis = initial["kind"], initial["research_basis"]
        latest = json.loads(messages[-1]["content"]).get("observation")
        self.seen.append((kind, latest))
        ordinals = [item["ordinal"] for item in basis["passages"]][:2]

        def cited(text):
            return {"text": text, "label": "postulated", "citation_ordinals": ordinals}

        if kind == "formalise" and latest is None:
            action = {"kind": "check_conjecture", "conjecture": finite_conjecture()}
        elif kind == "formalise":
            assert latest["kind"] == "finite_check_result"
            assert latest["status"] == "no_counterexample_in_domain"
            action = {
                "kind": "conclude",
                "draft": {
                    "kind": "formalisation",
                    "title": "Synthetic mathematical conjecture",
                    "definitions": [{"symbol": "x", "meaning": cited("A fixture integer.")}],
                    "assumptions": [cited("The toy encoding uses integers from minus two to two.")],
                    "conjecture": cited("In this toy domain, the square of x is nonnegative."),
                    "proof_attempt": [cited("A synthetic proof attempt remains unverified.")],
                    "limitations": [
                        "A finite check is not a general proof or scientific evidence."
                    ],
                    "finite_conjecture": finite_conjecture(),
                },
            }
        else:
            if basis.get("formalisation_id"):
                assert basis["formalisation"]["kind"] == "formalisation"
            action = {
                "kind": "conclude",
                "draft": {
                    "kind": "summary" if kind == "write_summary" else "paper",
                    "title": "Synthetic source-backed document",
                    "abstract": [
                        cited("This synthetic document tests immutable evidence binding.")
                    ],
                    "sections": [
                        {"heading": heading, "paragraphs": [cited(text)]}
                        for heading, text in (
                            ("Background", "The saved research supplies the original passages."),
                            ("Findings", "A proposed connection retains its uncertainty."),
                            (
                                "Evidence",
                                "Each paragraph links to the exact supplied source quotes.",
                            ),
                            ("Limitations", "The earlier conjecture remains open and unverified."),
                        )
                    ],
                    "limitations": ["Synthetic fixtures establish no scientific discovery."],
                },
            }
        return ModelReply(json.dumps(action), 10, 15)


def client_for(engine):
    app = FastAPI()
    app.include_router(create_run_router(engine, availability=lambda: True))
    app.include_router(create_artifact_router(engine, availability=lambda: True))
    return TestClient(app)


def queue(client, source_result_id, kind, formalisation_id=None):
    body = {"kind": kind, "source_result_id": str(source_result_id)}
    if formalisation_id is not None:
        body["formalisation_id"] = str(formalisation_id)
    response = client.post("/api/v1/artifacts/jobs", headers=bootstrap(client), json=body)
    assert response.status_code == 201, response.text
    assert response.json()["task_kind"] == kind
    return uuid.UUID(response.json()["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["formalise", "write_summary", "write_paper"])
async def test_queued_artifact_has_exact_source_citations_and_export(database, kind):
    with client_for(database) as client:
        source_run_id, source_result_id = seed(database, client)
        run_id = queue(client, source_result_id, kind)
        model = ArtifactFixture()
        worker = setup_worker(database, model)
        outcome = await worker.run_once("artifact-fixture-worker")
        assert outcome.status == "succeeded"
        assert outcome.run_id == str(run_id)
        assert outcome.result_id is None
        assert outcome.artifact_id is not None
        detail = client.get(f"/api/v1/artifacts/{outcome.artifact_id}")
        assert detail.status_code == 200
        record = detail.json()
        assert record["status"] == "ready"
        assert record["source_result_id"] == str(source_result_id)
        assert record["source_run_id"] == str(source_run_id)
        assert len(record["passages"]) == 2
        exported = client.get(f"/api/v1/artifacts/{outcome.artifact_id}/export")
        assert exported.status_code == 200
        assert exported.content == record["markdown"].encode("utf-8")
        assert hashlib.sha256(exported.content).hexdigest() == record["markdown_sha256"]
        assert len(EvidenceStore(database).list_sources(run_id)) == 2
        # A generated document is not a new scientific research finding.
        assert EvidenceStore(database).list_results(run_id) == ()
        assert client.get(f"/api/v1/runs/{run_id}/result").status_code == 404
        assert [item["id"] for item in client.get("/api/v1/runs").json()["runs"]] == [
            str(source_run_id)
        ]
        status = client.get(f"/api/v1/artifacts/jobs/{run_id}").json()
        assert status["artifact_id"] == outcome.artifact_id
        assert status["result_id"] is None
        if kind == "formalise":
            checked = record["payload"]["checks"][0]
            assert checked["assignments_tested"] == 5
            assert checked["proof_status"] == "not_a_general_proof"
        usage = BudgetService(database).usage("fixture")
        assert usage.reservations == len(model.seen) == (2 if kind == "formalise" else 1)
        assert usage.in_flight == 0


@pytest.mark.asyncio
async def test_formalise_then_write_pins_exact_prior_conjecture(database):
    with client_for(database) as client:
        _, source_result_id = seed(database, client)
        queue(client, source_result_id, "formalise")
        model = ArtifactFixture()
        worker = setup_worker(database, model)
        first = await worker.run_once("formalise-fixture")
        assert first.status == "succeeded"
        paper_run = queue(client, source_result_id, "write_paper", first.artifact_id)
        paper = await worker.run_once("write-fixture")
        assert paper.status == "succeeded"
        detail = client.get(f"/api/v1/artifacts/{paper.artifact_id}").json()
        assert detail["run_id"] == str(paper_run)
        assert detail["formalisation_id"] == first.artifact_id
        assert "open and unverified" in detail["markdown"]
        assert BudgetService(database).usage("fixture").reservations == 3


@pytest.mark.asyncio
async def test_artifact_restart_replays_model_and_recorded_finite_check(database, monkeypatch):
    checked = asyncio.Event()
    original = _RecordedChecks.check

    async def pause_after_check(recorder, conjecture):
        result = await original(recorder, conjecture)
        checked.set()
        await asyncio.Event().wait()
        return result

    with client_for(database) as client:
        _, source_result_id = seed(database, client)
        run_id = queue(client, source_result_id, "formalise")
        model = ArtifactFixture()
        worker = setup_worker(database, model)
        monkeypatch.setattr(_RecordedChecks, "check", pause_after_check)
        task = asyncio.create_task(worker.run_once("artifact-before-restart"))
        await asyncio.wait_for(checked.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        snapshot = JobRepository(database).get(run_id)
        assert snapshot.status == "interrupted"
        assert not snapshot.external_request_in_flight
        assert BudgetService(database).usage("fixture").reservations == 1
        assert "math-0001" in snapshot.checkpoint["tool_observations"]
        monkeypatch.setattr(_RecordedChecks, "check", original)
        JobRepository(database).resume_interrupted(run_id, expected_token=snapshot.lease_token)
        resumed = ArtifactFixture()
        worker.model_factory = lambda _: resumed
        outcome = await worker.run_once("artifact-after-restart")
        assert outcome.status == "succeeded"
        assert len(resumed.seen) == 1
        assert BudgetService(database).usage("fixture").reservations == 2
        assert (
            client.get(f"/api/v1/artifacts/{outcome.artifact_id}").json()["payload"]["checks"][0][
                "assignments_tested"
            ]
            == 5
        )


@pytest.mark.asyncio
async def test_saved_artifact_response_loss_resumes_without_duplicate_version(
    database, monkeypatch
):
    original = ArtifactStore.save

    def commit_then_interrupt(store, lease, result):
        original(store, lease, result)
        raise ExecutionStopped("synthetic_artifact_commit_response_lost")

    with client_for(database) as client:
        _, source_result_id = seed(database, client)
        run_id = queue(client, source_result_id, "write_summary")
        worker = setup_worker(database, ArtifactFixture())
        monkeypatch.setattr(ArtifactStore, "save", commit_then_interrupt)
        assert (
            await worker.run_once("artifact-before-commit-response-loss")
        ).status == "interrupted"
        snapshot = JobRepository(database).get(run_id)
        assert BudgetService(database).usage("fixture").reservations == 1
        with Session(database) as session:
            saved = session.scalar(
                select(GeneratedArtifact).where(GeneratedArtifact.run_id == run_id)
            )
            saved_id = saved.id
        monkeypatch.setattr(ArtifactStore, "save", original)
        JobRepository(database).resume_interrupted(run_id, expected_token=snapshot.lease_token)
        resumed = ArtifactFixture()
        worker.model_factory = lambda _: resumed
        outcome = await worker.run_once("artifact-after-commit-response-loss")
        assert outcome.status == "succeeded"
        assert outcome.artifact_id == str(saved_id)
        assert resumed.seen == []
        assert BudgetService(database).usage("fixture").reservations == 1
        with Session(database) as session:
            assert session.scalar(select(func.count()).select_from(GeneratedArtifact)) == 1


@pytest.mark.asyncio
async def test_cancelled_artifact_keeps_ambiguous_dispatch_hold(database):
    started = asyncio.Event()

    class WaitingModel:
        async def complete(self, messages, *, max_output_tokens):
            started.set()
            await asyncio.Event().wait()

    with client_for(database) as client:
        _, source_result_id = seed(database, client)
        run_id = queue(client, source_result_id, "write_paper")
        worker = setup_worker(database, WaitingModel())
        task = asyncio.create_task(worker.run_once("artifact-cancellation"))
        await asyncio.wait_for(started.wait(), timeout=10)
        await asyncio.to_thread(JobRepository(database).request_cancel, run_id)
        outcome = await asyncio.wait_for(task, timeout=10)
        assert outcome.status == "cancelled"
        assert BudgetService(database).usage("fixture").in_flight == 1
        assert BudgetService(database).usage("fixture").committed_microdollars == 100
        with Session(database) as session:
            assert session.scalar(select(func.count()).select_from(GeneratedArtifact)) == 0


@pytest.mark.asyncio
async def test_disabled_artifact_worker_never_claims_or_composes_a_model(database):
    with client_for(database) as client:
        _, source_result_id = seed(database, client)
        run_id = queue(client, source_result_id, "write_summary")
        worker = setup_worker(database, ArtifactFixture(), enabled=False)
        assert (await worker.run_once("disabled-artifact-worker")).status == "disabled"
        assert JobRepository(database).get(run_id).status == "queued"
        assert BudgetService(database).usage("fixture").reservations == 0


def test_document_task_cannot_save_a_scientific_result(database):
    with client_for(database) as client:
        source_run_id, source_result_id = seed(database, client)
        run_id = queue(client, source_result_id, "write_summary")
        lease = JobRepository(database).claim_next("wrong-task-result-fixture")
        assert lease.run_id == run_id
        from polymath.research.models import ResearchResult

        payload = EvidenceStore(database).get_result(source_run_id, source_result_id).payload
        with pytest.raises(EvidenceError, match="research_result_wrong_task_kind"):
            EvidenceStore(database).save_result(
                lease, ResearchResult.model_validate_json(json.dumps(payload))
            )
