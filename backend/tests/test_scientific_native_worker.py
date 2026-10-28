"""Actual API/native adapter/PG journeys; every provider uses mock HTTP only."""

import hashlib
import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_artifact_worker import ArtifactFixture
from test_assessment_controller import AssessmentFixtureModel
from test_evidence import document
from test_ingestion_api import save_fixture_page
from test_meta_readiness import readiness as readiness_fixture
from test_native_research_worker import (
    ScientificHttpFixture,
    actual_worker,
    native_reservations,
)
from test_native_research_worker import native as native_fixture
from test_public_delegations import public_active
from test_public_native_worker import expire_issuer, worker_for
from test_run_api import bootstrap
from test_worker import database as database_fixture

from polymath.assessments import AssessmentStore
from polymath.budget_models import BudgetAccount, BudgetReservation, BudgetRunLimit
from polymath.config import Settings
from polymath.evidence import EvidenceStore
from polymath.main import create_app
from polymath.meta_probe_contracts import ProviderAccountAttestation
from polymath.meta_research_contracts import NativeResearchReview, ScientificContextIntent
from polymath.meta_research_models import NativeResearchRunBinding
from polymath.models import ResearchRun
from polymath.public_research_contracts import PublicLimits
from polymath.research.meta import ModelReply
from polymath.scientific_context_models import ArtifactContextBinding, AskOverviewIntentRow

database = database_fixture
native = native_fixture
readiness = readiness_fixture
pytestmark = pytest.mark.integration

ASK_INTENT = {"protocol": "polymath-ask-claim-overview-v1"}
ARTIFACT_INTENT = {"protocol": "polymath-artifact-scientific-context-v1"}
ASSESSMENT_INTENT = {"protocol": "polymath-ask-reassessment-overview-v1"}


class ScientificActionFixture:
    """Adapt only test HTTP action fields to the newly reviewed strict schemas."""

    def __init__(self, delegate=None, *, long_bridge=False):
        self.delegate = delegate or ScientificHttpFixture()
        self.long_bridge = long_bridge

    async def complete(self, messages, *, max_output_tokens):
        reply = await self.delegate.complete(messages, max_output_tokens=max_output_tokens)
        action = json.loads(reply.content)
        finding = action.get("conclusion") or action.get("revised_finding")
        if finding is not None and finding.get("outcome") == "answer":
            finding.pop("summary", None)
            finding["overview_claim_ids"] = [claim["id"] for claim in finding["claims"][:2]][::-1]
        if self.long_bridge and finding is not None and finding.get("outcome") == "bridge":
            # This tests exact JSON context characters, never a token estimate.
            finding["explanation"] = "🧪" * 3000
            finding["strongest_objection"] = "🧪" * 2000
        return ModelReply(
            json.dumps(action, ensure_ascii=False), reply.input_tokens, reply.output_tokens
        )


def approve_scientific(value, *, public=False):
    """Reapprove the SAME saved proposal/key/model/account with all five variants."""
    record = value.public_activation if public else value.activation
    manifest = record.to_response()["manifest"]
    review = NativeResearchReview.model_validate_json(json.dumps(manifest["review"]))
    attestation = ProviderAccountAttestation.model_validate_json(
        json.dumps(manifest["provider_account_attestation"])
    )
    if public:
        from datetime import datetime

        preview = value.delegations.preview(
            value.request,
            manifest["readiness_id"],
            manifest["canary"]["receipt_id"],
            review,
            attestation,
            PublicLimits.model_validate_json(json.dumps(manifest["public_limits"])),
            datetime.fromisoformat(manifest["valid_until"]),
            scientific_context=ScientificContextIntent(),
        )
        value.public_preview = preview
        value.public_activation = value.delegations.activate(
            value.request, preview.manifest, preview.manifest_sha256
        )
        value.public_activation_sha256 = preview.manifest_sha256
    else:
        preview = value.store.preview(
            value.request,
            manifest["readiness_id"],
            manifest["canary"]["receipt_id"],
            review,
            attestation,
            scientific_context=ScientificContextIntent(),
        )
        value.activation = value.store.activate(
            value.request, preview.manifest, preview.manifest_sha256
        )
        value.activation_sha256 = preview.manifest_sha256
    return value


def app_for(value, *, public=False):
    return create_app(
        Settings(
            database_url=value.engine.url.render_as_string(hide_password=False),
            environment="test",
            model_api_key=None,
            operator_bootstrap_token=None,
            literature_ingestion_enabled=True,
            _env_file=None,
        ),
        research_policy=value.policy,
        readiness_policy=value.readiness_policy,
        operator_policy=value.operator_policy,
        **(
            {
                "public_policy": value.public_policy,
                "resource_controls": value.parent.resource_controls,
            }
            if public
            else {}
        ),
    )


@contextmanager
def scientific_client(value, *, public=False):
    with TestClient(app_for(value, public=public), client=("127.0.0.1", 50001)) as client:
        if not public:
            client.cookies.update(value.client.cookies)
        yield client


def create_run(client, value, *, mode="ask", intent=True, headers=None):
    response = client.post(
        "/api/v1/runs",
        headers=headers or value.headers,
        json={
            "mode": mode,
            "goal": "What do these synthetic native sources support?",
            "expected_activation_sha256": getattr(
                value, "public_activation_sha256", value.activation_sha256
            ),
            **({"ask_overview_intent": ASK_INTENT} if intent else {}),
        },
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def assert_native_binding(value, run_id, protocol):
    record = value.public_activation if hasattr(value, "public_activation") else value.activation
    manifest = record.to_response()["manifest"]
    with Session(value.engine) as session:
        binding = session.get(NativeResearchRunBinding, run_id)
        assert binding.profile["controller_protocol"] == protocol
        assert binding.manifest_sha256 == getattr(
            value, "public_activation_sha256", value.activation_sha256
        )
        approved = next(
            entry
            for entry in manifest["scientific_context_profiles"]["bindings"]
            if entry["profile"]["task_kind"] == binding.task_kind
            and entry["profile"]["controller_protocol"] == protocol
        )
        assert binding.profile == approved["profile"]
        assert binding.profile_sha256 == approved["profile_sha256"]
        cap = session.get(BudgetRunLimit, (binding.account_id, run_id))
        assert cap.cap_microdollars == value.proposal.account.run_cap_microdollars
        assert cap.max_calls == approved["profile"]["max_model_calls"]
    rows = native_reservations(value, run_id)
    assert rows and all(row.status == "settled" and row.actual_microdollars == 40 for row in rows)


def ledger_snapshot(value):
    with Session(value.engine) as session:
        account = session.get(BudgetAccount, value.policy.native_account_id)
        return (
            account.id,
            account.hard_cap_microdollars,
            account.daily_cap_microdollars,
            account.max_in_flight,
            account.execution_enabled,
            account.pricing_verified,
            session.scalar(select(func.count()).select_from(BudgetAccount)),
        )


async def seed_bound_ask(value, client, monkeypatch):
    run_id = create_run(client, value)
    worker, calls = actual_worker(value, monkeypatch, ScientificActionFixture())
    outcome = await worker.run_once("scientific-claim-bound-ask")
    assert outcome.status == "succeeded" and outcome.result_id
    response = client.get(f"/api/v1/runs/{run_id}/result")
    assert response.status_code == 200, response.text
    return SimpleNamespace(
        run_id=run_id, result_id=uuid.UUID(outcome.result_id), result=response.json(), calls=calls
    )


async def test_three_context_artifacts_use_actual_api_native_worker_and_exact_saved_exports(
    native, monkeypatch
):
    original_caps = ledger_snapshot(native)
    approve_scientific(native)
    with scientific_client(native) as client:
        source_run = create_run(client, native, mode="explore", intent=False)
        investigator, _ = actual_worker(native, monkeypatch, ScientificHttpFixture())
        source = await investigator.run_once("scientific-artifact-source")
        assert source.status == "succeeded" and source.result_id
        for kind in ("formalise", "write_summary", "write_paper"):
            response = client.post(
                "/api/v1/artifacts/jobs",
                headers=native.headers,
                json={
                    "kind": kind,
                    "source_result_id": source.result_id,
                    "expected_activation_sha256": native.activation_sha256,
                    "scientific_context_intent": ARTIFACT_INTENT,
                },
            )
            assert response.status_code == 201, response.text
            run_id = uuid.UUID(response.json()["id"])
            worker, calls = actual_worker(native, monkeypatch, ArtifactFixture())
            outcome = await worker.run_once("scientific-context-" + kind)
            assert outcome.status == "succeeded" and outcome.artifact_id
            detail = client.get(f"/api/v1/artifacts/{outcome.artifact_id}")
            assert detail.status_code == 200, detail.text
            artifact = detail.json()
            context = artifact["scientific_context"]
            assert context["context"]["source_reference"]["result_id"] == source.result_id
            exported = client.get(f"/api/v1/artifacts/{outcome.artifact_id}/export")
            assert exported.status_code == 200
            assert exported.content == artifact["markdown"].encode("utf-8")
            assert hashlib.sha256(exported.content).hexdigest() == artifact["markdown_sha256"]
            assert context["context_sha256"] in exported.text
            assert len(calls.requests) == (2 if kind == "formalise" else 1)
            assert_native_binding(native, run_id, "polymath-meta-artifact-scientific-context-v1")
            with Session(native.engine) as session:
                assert (
                    session.get(ArtifactContextBinding, run_id).context_sha256
                    == context["context_sha256"]
                )
        assert client.get(f"/api/v1/runs/{source_run}").json()["status"] == "succeeded"
    assert ledger_snapshot(native) == original_caps


async def test_bound_ask_and_revised_assessment_use_all_native_paths_and_preserve_unchanged(
    native, monkeypatch
):
    caps = ledger_snapshot(native)
    approve_scientific(native)
    with scientific_client(native) as client:
        initial = await seed_bound_ask(native, client, monkeypatch)
        result = initial.result
        claims = {claim["id"]: claim for claim in result["finding"]["claims"]}
        expected = "\n\n".join(
            claims[key]["text"] for key in result["overview_binding"]["claim_ids"]
        )
        assert result["finding"]["summary"] == expected
        assert_native_binding(native, initial.run_id, "polymath-meta-ask-claim-overview-v1")
        watch = client.post(
            "/api/v1/knowledge/watches",
            headers=native.headers,
            json={
                "label": "Synthetic bound answer evidence",
                "query": "Synthetic updated premise",
                "source_result_id": str(initial.result_id),
            },
        )
        assert watch.status_code == 201, watch.text
        watch_id = watch.json()["id"]
        versions = save_fixture_page(
            native.engine,
            client,
            watch_id,
            tuple(
                document(
                    f"fixture:bound-answer-new:{index}",
                    text="New saved synthetic evidence preserves a bounded claim.",
                )
                for index in range(2)
            ),
        )
        for revision, revised in enumerate((False, True)):
            version = versions[revision]
            response = client.post(
                "/api/v1/assessments/jobs",
                headers=native.headers,
                json={
                    "source_result_id": str(initial.result_id),
                    "watch_id": watch_id,
                    "new_source_version_ids": [str(version.version_id)],
                    "expected_head_revision": revision,
                    "expected_activation_sha256": native.activation_sha256,
                    "assessment_overview_intent": ASSESSMENT_INTENT,
                },
            )
            assert response.status_code == 201, response.text
            run_id = uuid.UUID(response.json()["id"])
            worker, calls = actual_worker(
                native,
                monkeypatch,
                ScientificActionFixture(AssessmentFixtureModel(revised=revised)),
            )
            outcome = await worker.run_once("scientific-assessment-overview")
            assert outcome.status == "succeeded" and outcome.assessment_id
            saved = AssessmentStore(native.engine).get(
                native.owner_hash, uuid.UUID(outcome.assessment_id)
            )
            assert saved.applied and saved.disposition == ("revised" if revised else "unchanged")
            assert saved.payload["basis"]["prior_overview_binding"] == result["overview_binding"]
            assert_native_binding(native, run_id, "polymath-meta-ask-reassessment-overview-v1")
            if not revised:
                assert saved.candidate_result_id is None
                current = EvidenceStore(native.engine).get_result(initial.run_id, initial.result_id)
                assert current.payload["overview_binding"] == result["overview_binding"]
            else:
                candidate = (
                    EvidenceStore(native.engine)
                    .get_result(run_id, saved.candidate_result_id)
                    .payload
                )
                selected = candidate["overview_binding"]["claim_ids"]
                texts = {claim["id"]: claim["text"] for claim in candidate["finding"]["claims"]}
                assert candidate["finding"]["summary"] == "\n\n".join(
                    texts[key] for key in selected
                )
                assert calls.requests
    assert ledger_snapshot(native) == caps


async def test_unapproved_protocol_rolls_back_admission_before_any_native_grant(native):
    with scientific_client(native) as client:
        with Session(native.engine) as session:
            before = session.scalar(select(func.count()).select_from(ResearchRun))
            reserved = session.scalar(select(func.count()).select_from(BudgetReservation))
        response = client.post(
            "/api/v1/runs",
            headers=native.headers,
            json={
                "mode": "ask",
                "goal": "Unapproved scientific schema?",
                "expected_activation_sha256": native.activation_sha256,
                "ask_overview_intent": ASK_INTENT,
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "meta_research_scientific_protocol_unavailable"
        with Session(native.engine) as session:
            assert session.scalar(select(func.count()).select_from(ResearchRun)) == before
            assert session.scalar(select(func.count()).select_from(BudgetReservation)) == reserved
            assert session.scalar(select(func.count()).select_from(AskOverviewIntentRow)) == 0


async def test_exact_large_context_admission_rolls_back_without_another_model_call(
    native, monkeypatch
):
    approve_scientific(native)
    with scientific_client(native) as client:
        create_run(client, native, mode="explore", intent=False)
        worker, calls = actual_worker(
            native, monkeypatch, ScientificActionFixture(long_bridge=True)
        )
        source = await worker.run_once("scientific-large-valid-source")
        assert source.status == "succeeded" and source.result_id
        with Session(native.engine) as session:
            before = session.scalar(select(func.count()).select_from(ResearchRun))
            reservations = session.scalar(select(func.count()).select_from(BudgetReservation))
        sent = len(calls.requests)
        response = client.post(
            "/api/v1/artifacts/jobs",
            headers=native.headers,
            json={
                "kind": "write_summary",
                "source_result_id": source.result_id,
                "expected_activation_sha256": native.activation_sha256,
                "scientific_context_intent": ARTIFACT_INTENT,
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "artifact_context_too_large"
        assert len(calls.requests) == sent
        with Session(native.engine) as session:
            assert session.scalar(select(func.count()).select_from(ResearchRun)) == before
            assert (
                session.scalar(select(func.count()).select_from(BudgetReservation)) == reservations
            )
            assert session.scalar(select(func.count()).select_from(ArtifactContextBinding)) == 0


async def test_public_bound_ask_continues_after_issuer_logout_with_original_wallet(
    readiness, monkeypatch
):
    public = public_active(readiness, short_issuer=False)
    original = ledger_snapshot(public)
    approve_scientific(public, public=True)
    with scientific_client(public, public=True) as visitor:
        headers = bootstrap(visitor)
        run_id = create_run(visitor, public, headers=headers)
        expire_issuer(public)
        worker, calls = worker_for(public, monkeypatch, ScientificActionFixture())
        outcome = await worker.run_once("scientific-public-durable-ask")
        assert outcome.status == "succeeded" and outcome.result_id
        result = visitor.get(f"/api/v1/runs/{run_id}/result")
        assert result.status_code == 200, result.text
        assert result.json()["overview_binding"]["claim_ids"]
        assert len(calls.model_requests) == 6
        assert_native_binding(public, run_id, "polymath-meta-ask-claim-overview-v1")
        assert "polymath_operator" not in dict(visitor.cookies)
    assert ledger_snapshot(public) == original
