"""Actual owner-only SDK/HTTP/PG/native-adapter journeys with synthetic Meta HTTP."""

import json
import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_api_client_http import client_module as client_module_fixture
from test_api_client_http import sdk, serve_app
from test_api_client_public_http import actual_worker, native_bindings
from test_api_client_public_http import public_http as public_http_fixture
from test_artifact_worker import ArtifactFixture
from test_ask_reassessment_overview import BoundAdaptiveModel
from test_evidence import document
from test_meta_probes import attestation_data
from test_meta_readiness import make_request
from test_meta_readiness import readiness as readiness_fixture
from test_meta_research import review_data
from test_native_research_worker import ScientificHttpFixture
from test_operator_auth import login
from test_public_delegations import public_preview
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.assessments import AssessmentStore
from polymath.evidence import EvidenceStore
from polymath.ingestion import IngestionRepository
from polymath.jobs import JobRepository
from polymath.meta_research_contracts import SCIENTIFIC_CAPABILITIES, ScientificContextIntent
from polymath.meta_research_models import NativeResearchRunBinding
from polymath.research.meta import ModelReply
from polymath.sharing import SharingPolicy, SharingStore
from polymath.sharing_api import create_sharing_router

database = database_fixture
readiness = readiness_fixture
client_module = client_module_fixture
public_http = public_http_fixture
pytestmark = pytest.mark.integration


@pytest.fixture
def public(readiness):
    value = public_preview(readiness, short_issuer=False)
    preview = value.delegations.preview(
        value.request,
        value.saved.id,
        value.canary.id,
        review_data(),
        attestation_data(value.proposal.model_id),
        value.public_preview.manifest.public_limits,
        value.public_deadline,
        scientific_context=ScientificContextIntent(),
    )
    value.public_activation = value.delegations.activate(
        value.request, preview.manifest, preview.manifest_sha256
    )
    value.public_activation_sha256 = preview.manifest_sha256
    return value


class BoundNativeFixture(ScientificHttpFixture):
    async def complete(self, messages, *, max_output_tokens):
        response = await super().complete(messages, max_output_tokens=max_output_tokens)
        action = json.loads(response.content)
        finding = action.get("conclusion")
        if isinstance(finding, dict) and finding.get("outcome") == "answer":
            finding.pop("summary")
            finding["overview_claim_ids"] = [claim["id"] for claim in reversed(finding["claims"])]
        return ModelReply(json.dumps(action), response.input_tokens, response.output_tokens)


def profile(public, run_id):
    with Session(public.engine) as session:
        return session.get(NativeResearchRunBinding, uuid.UUID(run_id)).profile


async def ask(client, public):
    created = client.create_run("ask", "What does synthetic native evidence support?")
    native_bindings(public, created["id"])
    assert (
        profile(public, created["id"])["controller_protocol"]
        == "polymath-meta-ask-claim-overview-v1"
    )
    worker, calls = actual_worker(public, BoundNativeFixture())
    outcome = await worker.run_once("scientific-sdk-ask")
    assert outcome.status == "succeeded" and outcome.result_id
    result = client.get_result(created["id"])
    assert result["overview_binding"]["protocol"] == "polymath-ask-claim-overview-v1"
    claims = {claim["id"]: claim for claim in result["finding"]["claims"]}
    assert result["finding"]["summary"] == "\n\n".join(
        claims[identifier]["text"] for identifier in result["overview_binding"]["claim_ids"]
    )
    assert calls.requests and calls.source_requests
    return created, outcome, result


async def test_sdk_bound_ask_all_context_artifacts_exact_exports_and_stopped_history(
    public, public_http, client_module, tmp_path
):
    with sdk(client_module, public_http, tmp_path / "scientific-owner.cookies") as client:
        with pytest.raises(client_module.ClientError, match="research_session_required"):
            client.research_protocols()
        created, source, result = await ask(client, public)
        capabilities = client.research_protocols()
        assert capabilities == {
            "protocol": "polymath-research-protocols-v1",
            "activation_sha256": public.public_activation_sha256,
            "capabilities": list(SCIENTIFIC_CAPABILITIES),
        }
        assert (
            public.issuer_client.post(
                "/api/v1/operator/logout", headers=public.issuer_headers
            ).status_code
            == 200
        )
        generated = client.create_artifact("write_summary", source.result_id)
        worker, _ = actual_worker(public, ArtifactFixture())
        summary = await worker.run_once("scientific-sdk-ask-summary")
        assert summary.status == "succeeded"
        ask_document = client.get_artifact(summary.artifact_id)
        assert ask_document["run_id"] == generated["id"]
        assert (
            ask_document["scientific_context"]["context"]["ancestry_nodes"][0]["overview_binding"]
            == result["overview_binding"]
        )
        bridge = client.create_run("explore", "Synthetic bridge for finite formalisation.")
        worker, _ = actual_worker(public, ScientificHttpFixture())
        source = await worker.run_once("scientific-sdk-bridge")
        assert source.status == "succeeded" and source.result_id
        assert bridge["id"] == source.run_id
        original_context, formalisation = None, None
        for kind in ("formalise", "write_summary", "write_paper"):
            generation = client.create_artifact(
                kind, source.result_id, formalisation if kind == "write_paper" else None
            )
            assert (
                profile(public, generation["id"])["controller_protocol"]
                == "polymath-meta-artifact-scientific-context-v1"
            )
            worker, calls = actual_worker(public, ArtifactFixture())
            outcome = await worker.run_once("scientific-sdk-" + kind)
            assert outcome.status == "succeeded" and outcome.artifact_id
            artifact = client.get_artifact(outcome.artifact_id)
            binding = artifact["scientific_context"]
            assert binding["context"]["source_reference"]["result_id"] == source.result_id
            assert binding["context"]["ancestry_nodes"][0]["overview_binding"] is None
            history = client.get_artifact_job(generation["id"])
            assert history["scientific_context_intent"] == {
                "protocol": "polymath-artifact-scientific-context-v1"
            }
            assert history["scientific_context_sha256"] == binding["context_sha256"]
            target = tmp_path / (kind + ".md")
            client.export_artifact(outcome.artifact_id, target)
            assert target.read_bytes() == artifact["markdown"].encode("utf-8")
            assert calls.requests
            if kind == "formalise":
                formalisation, original_context = outcome.artifact_id, binding["context_sha256"]
            elif kind == "write_paper":
                prior = binding["context"]["prior_formalisation"]
                assert prior["artifact_id"] == formalisation
                assert prior["context_sha256"] == original_context
        assert len(client.list_artifact_jobs(source_result_id=source.result_id)["jobs"]) == 3
        headers, _ = login(public.issuer_client)
        public.delegations.revoke(
            make_request(public.issuer_client, headers), public.public_activation.id
        )
        assert client.research_protocols() == {
            "protocol": "polymath-research-protocols-v1",
            "activation_sha256": None,
            "capabilities": [],
        }
        assert client.get_result(created["id"]) == result
        assert (
            client.get_artifact(formalisation)["scientific_context"]["context_sha256"]
            == original_context
        )
    assert not any(
        item["operator_header"] or item["operator_cookie"] for item in public_http.requests
    )


async def test_sdk_bound_ask_watch_reassessment_preserves_binding_and_exact_source(
    public, public_http, client_module, tmp_path
):
    with sdk(client_module, public_http, tmp_path / "scientific-reassess.cookies") as client:
        created, source, original = await ask(client, public)
        owner = native_bindings(public, created["id"])
        ingestion = IngestionRepository(public.engine)
        watch = ingestion.create(
            owner,
            label="Synthetic scientific overview watch",
            query="Synthetic new evidence",
            source_result_id=uuid.UUID(source.result_id),
        )
        tick = ingestion.run_now(owner, watch.id)
        jobs = JobRepository(public.engine)
        lease = jobs.claim_next("free-scientific-sdk-watch", task_kinds=("ingest_literature",))
        assert lease.run_id == tick.run_id
        saved = EvidenceStore(public.engine).save_source(
            lease,
            document(
                "fixture:scientific-fresh",
                text="A new synthetic study preserves the bounded analogy.",
            ),
        )
        ingestion.commit_page(
            lease,
            "openalex",
            ingestion.snapshot(lease).cursors["openalex"],
            None,
            (saved,),
            exhausted=True,
        )
        jobs.finish(lease, status="succeeded", outcome={"model_calls": 0})
        requested = client.create_assessment(
            source.result_id, str(watch.id), [str(saved.version_id)], 0
        )
        assert (
            profile(public, requested["id"])["controller_protocol"]
            == "polymath-meta-ask-reassessment-overview-v1"
        )
        worker, calls = actual_worker(public, BoundAdaptiveModel())
        outcome = await worker.run_once("scientific-sdk-reassessment")
        assert outcome.status == "succeeded" and outcome.assessment_id
        assessment = AssessmentStore(public.engine).get(owner, uuid.UUID(outcome.assessment_id))
        assert assessment.applied and assessment.disposition == "unchanged"
        assert calls.requests and client.get_result(created["id"]) == original
        assert client.get_run(requested["id"])["assessment_overview_intent"] == {
            "protocol": "polymath-ask-reassessment-overview-v1"
        }
        source_reads = [
            item
            for item in public_http.requests
            if item["path"] == "/api/v1/assessments/findings/" + source.result_id
        ]
        assert len(source_reads) == 1 and source_reads[0]["method"] == "GET"
    assert not any(
        item["operator_header"] or item["operator_cookie"] for item in public_http.requests
    )


async def test_sdk_anonymous_saved_bound_ask_read_keeps_only_local_claim_ids(
    public, public_http, client_module, tmp_path
):
    with sdk(client_module, public_http, tmp_path / "scientific-sharer.cookies") as client:
        created, source, _ = await ask(client, public)
        owner = native_bindings(public, created["id"])
        sharing = SharingStore(public.engine, policy=SharingPolicy(enabled=True))
        preview = sharing.preview(owner, uuid.UUID(source.result_id))
        shared = sharing.share(
            owner, uuid.UUID(source.result_id), preview.projection_sha256, uuid.uuid4()
        )
    app = FastAPI()
    app.include_router(create_sharing_router(public.engine, availability=lambda: True))
    with Session(public.engine) as session:
        sessions = session.scalar(select(func.count()).select_from(BrowserSession))
    with serve_app(app) as anonymous_server:
        with client_module.PolymathClient(anonymous_server.base_url) as anonymous:
            saved = anonymous.public_finding(shared.public_id)
            projection = saved["projection"]
            assert projection["overview_binding"]["protocol"] == "polymath-ask-claim-overview-v1"
            assert all(
                item.startswith("claim-") for item in projection["overview_binding"]["claim_ids"]
            )
            assert "scientific_context" not in json.dumps(saved)
            assert source.result_id not in json.dumps(saved)
        assert len(anonymous_server.requests) == 1
        assert anonymous_server.requests[0]["method"] == "GET"
        assert not anonymous_server.requests[0]["cookie"]
        assert not anonymous_server.requests[0]["operator_cookie"]
    with Session(public.engine) as session:
        assert session.scalar(select(func.count()).select_from(BrowserSession)) == sessions
