"""Owner-only SDK through real HTTP/PG and the real Meta adapter with mock HTTP."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_api_client_http import client_module as client_module_fixture
from test_api_client_http import sdk, serve_app
from test_artifact_worker import ArtifactFixture
from test_assessment_controller import AssessmentFixtureModel
from test_evidence import document
from test_full_text import full_text_fixture_transport
from test_meta_probes import attestation_data
from test_meta_readiness import readiness as readiness_fixture
from test_meta_research import review_data
from test_native_research_worker import ScientificHttpFixture
from test_public_delegations import public_active
from test_public_research_contracts import limits_data
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.assessments import AssessmentStore
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.config import Settings
from polymath.evidence import EvidenceStore
from polymath.ingestion import IngestionRepository
from polymath.jobs import JobRepository
from polymath.literature import OpenLiteratureTools
from polymath.main import create_app
from polymath.meta_research_models import NativeResearchRunBinding
from polymath.native_research_worker import build_native_research_worker
from polymath.public_research_contracts import PublicLimits
from polymath.public_research_models import PublicResearchDelegation, PublicResearchRunAccess
from polymath.research.meta import MetaModel

database = database_fixture
readiness = readiness_fixture
client_module = client_module_fixture
pytestmark = pytest.mark.integration


@pytest.fixture
def public(readiness):
    return public_active(readiness, short_issuer=False)


@pytest.fixture
def public_http(public):
    app = create_app(
        Settings(
            database_url=public.engine.url.render_as_string(hide_password=False),
            environment="test",
            model_api_key=None,
            operator_bootstrap_token=None,
            _env_file=None,
        ),
        operator_policy=public.operator_policy,
        readiness_policy=public.readiness_policy,
        research_policy=public.policy,
        public_policy=public.public_policy,
        resource_controls=public.parent.resource_controls,
    )
    with serve_app(app) as server:
        yield server


def actual_worker(public, intelligence):
    state = SimpleNamespace(requests=[], source_requests=[])

    async def meta_http(request):
        assert str(request.url) == "https://api.meta.ai/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == public.proposal.model_id
        state.requests.append(body)
        reply = await intelligence.complete(body["messages"], max_output_tokens=body["max_tokens"])
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"finish_reason": "stop", "message": {"content": reply.content}}],
                "usage": {
                    "prompt_tokens": reply.input_tokens,
                    "completion_tokens": reply.output_tokens,
                },
            },
        )

    def model_factory(value, grant):
        return MetaModel(
            api_key=grant.api_key,
            model=grant.model_id,
            live_enabled=True,
            request_timeout_seconds=grant.request_timeout_seconds,
            transport=httpx.MockTransport(meta_http),
        )

    worker = build_native_research_worker(
        public.engine,
        research_policy=public.policy,
        readiness_policy=public.readiness_policy,
        operator_policy=public.operator_policy,
        public_policy=public.public_policy,
        model_factory=model_factory,
        tools_factory=lambda _: OpenLiteratureTools(
            transport=full_text_fixture_transport(state.source_requests)
        ),
    )
    return worker, state


def native_bindings(public, run_id):
    with Session(public.engine) as session:
        binding = session.get(NativeResearchRunBinding, uuid.UUID(run_id))
        access = session.get(PublicResearchRunAccess, uuid.UUID(run_id))
        owner = session.get(ResearchRunAccess, uuid.UUID(run_id))
        assert binding.manifest_sha256 == access.manifest_sha256 == public.public_activation_sha256
        assert binding.browser_session_hash == access.requester_session_hash == owner.session_hash
        delegation = session.get(PublicResearchDelegation, access.approval_id)
        assert access.requester_session_hash != delegation.sponsor_browser_session_hash
        assert session.scalar(select(func.count()).select_from(BudgetAccount)) == 1
        return owner.session_hash


@pytest.mark.parametrize("mode", ["explore", "ask", "surprise"])
async def test_public_sdk_modes_actual_meta_dispatch_stop_and_retained_reads(
    public, public_http, client_module, tmp_path, mode
):
    path = tmp_path / "public-owner.cookies"
    with sdk(client_module, public_http, path) as client:
        with pytest.raises(client_module.ClientError, match="research_session_required"):
            client.research_access()
        with Session(public.engine) as session:
            before_sessions = session.scalar(select(func.count()).select_from(BrowserSession))
        created = client.create_run(mode, "Synthetic public scientific question?")
        run_id = created["id"]
        native_bindings(public, run_id)
        worker, calls = actual_worker(public, ScientificHttpFixture())
        outcome = await worker.run_once("public-sdk-mode")
        assert outcome.status == "succeeded" and outcome.result_id
        result = client.get_result(run_id)
        assert result["mode"] == mode and result["status"] == (
            "answer" if mode == "ask" else "bridge"
        )
        assert (
            client.get_run(run_id)["native_research"]["activation_sha256"]
            == public.public_activation_sha256
        )
        events = list(client.events(run_id, max_events=100, max_seconds=5))
        assert events[-1]["event"] == "end"
        assert calls.requests and calls.source_requests
        with Session(public.engine) as session:
            assert (
                session.scalar(select(func.count()).select_from(BrowserSession))
                == before_sessions + 1
            )
            assert (
                session.scalar(select(func.count()).select_from(BudgetReservation))
                == len(calls.requests) + 1
            )
        public.delegations.revoke(public.request, public.public_activation.id)
        access = client.research_access()
        assert not access["available"] and access["reason"] == "public_research_stopped"
        before_attempt = len(
            [request for request in public_http.requests if request["method"] == "POST"]
        )
        with pytest.raises(client_module.ClientError, match="public_research_stopped"):
            client.create_run("ask", "A new closed-funding question?")
        assert (
            len([request for request in public_http.requests if request["method"] == "POST"])
            == before_attempt
        )
        assert client.get_result(run_id) == result
        assert client.cancel_run(run_id)["status"] == "succeeded"
    with sdk(client_module, public_http, path) as reloaded:
        assert reloaded.get_result(run_id) == result
    assert not any(
        request["operator_header"] or request["operator_cookie"] for request in public_http.requests
    )


async def test_public_sdk_formalise_write_and_exact_export_continue_after_issuer_logout(
    public, public_http, client_module, tmp_path
):
    with sdk(client_module, public_http, tmp_path / "public-writer.cookies") as client:
        created = client.create_run("explore", "Synthetic public document basis.")
        worker, _ = actual_worker(public, ScientificHttpFixture())
        source = await worker.run_once("public-sdk-document-basis")
        assert source.status == "succeeded"
        logout = public.issuer_client.post("/api/v1/operator/logout", headers=public.issuer_headers)
        assert logout.status_code == 200
        assert client.research_access()["available"]
        formalisation_id = None
        for kind in ("formalise", "write_summary", "write_paper"):
            generation = client.create_artifact(
                kind, source.result_id, formalisation_id if kind == "write_paper" else None
            )
            native_bindings(public, generation["id"])
            worker, calls = actual_worker(public, ArtifactFixture())
            outcome = await worker.run_once("public-sdk-" + kind)
            assert outcome.status == "succeeded" and outcome.artifact_id
            artifact = client.get_artifact(outcome.artifact_id)
            assert artifact["run_id"] == generation["id"] and calls.requests
            if kind == "formalise":
                formalisation_id = outcome.artifact_id
            else:
                output = tmp_path / (kind + ".md")
                client.export_artifact(outcome.artifact_id, output)
                assert output.read_bytes() == artifact["markdown"].encode("utf-8")
        assert client.get_run(created["id"])["status"] == "succeeded"
    assert not any(
        request["operator_header"] or request["operator_cookie"] for request in public_http.requests
    )


async def test_public_sdk_watch_backed_reassessment_uses_same_owned_native_path(
    public, public_http, client_module, tmp_path
):
    with sdk(client_module, public_http, tmp_path / "public-assessment.cookies") as client:
        created = client.create_run("explore", "Synthetic public reassessment basis.")
        worker, _ = actual_worker(public, ScientificHttpFixture())
        source = await worker.run_once("public-sdk-assessment-basis")
        assert source.status == "succeeded"
        owner = native_bindings(public, created["id"])
        ingestion = IngestionRepository(public.engine)
        watch = ingestion.create(
            owner,
            label="Synthetic public watch",
            query="Synthetic new evidence",
            source_result_id=uuid.UUID(source.result_id),
        )
        tick = ingestion.run_now(owner, watch.id)
        jobs = JobRepository(public.engine)
        lease = jobs.claim_next("free-public-sdk-watch", task_kinds=("ingest_literature",))
        assert lease.run_id == tick.run_id
        saved = EvidenceStore(public.engine).save_source(
            lease,
            document("fixture:native-fresh", text="New synthetic evidence preserves this analogy."),
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
        native_bindings(public, requested["id"])
        worker, calls = actual_worker(public, AssessmentFixtureModel())
        outcome = await worker.run_once("public-sdk-reassessment")
        assert outcome.status == "succeeded" and outcome.assessment_id
        assert client.get_run(requested["id"])["status"] == "succeeded"
        assessment = AssessmentStore(public.engine).get(owner, uuid.UUID(outcome.assessment_id))
        assert (
            assessment.applied
            and assessment.disposition == "unchanged"
            and len(calls.requests) == 3
        )


async def test_public_sdk_original_resume_works_when_no_new_session_jobs_remain(
    public, public_http, client_module, tmp_path
):
    preview = public.delegations.preview(
        public.request,
        public.saved.id,
        public.canary.id,
        review_data(),
        attestation_data(public.proposal.model_id),
        PublicLimits.model_validate(limits_data(max_jobs_per_session_day=1)),
        datetime.now(UTC) + timedelta(minutes=5),
    )
    public.public_activation = public.delegations.activate(
        public.request, preview.manifest, preview.manifest_sha256
    )
    public.public_activation_sha256 = preview.manifest_sha256
    with sdk(client_module, public_http, tmp_path / "public-resume.cookies") as client:
        created = client.create_run("ask", "Synthetic exact original public resume?")
        native_bindings(public, created["id"])
        jobs = JobRepository(public.engine)
        lease = jobs.claim_next("public-sdk-initial-interrupt", native_only=True)
        assert str(lease.run_id) == created["id"]
        jobs.interrupt(lease)
        original = client.get_run(created["id"])["native_research"]
        assert original["can_resume"]
        access = client.research_access()
        assert access["available"] and access["remaining_session_jobs_today"] == 0
        before = len([request for request in public_http.requests if request["method"] == "POST"])
        resumed = client.resume_run(
            created["id"], original["expected_lease_token"], original["activation_sha256"]
        )
        assert resumed["id"] == created["id"] and resumed["status"] == "queued"
        assert (
            len([request for request in public_http.requests if request["method"] == "POST"])
            == before + 1
        )
        worker, _ = actual_worker(public, ScientificHttpFixture())
        outcome = await worker.run_once("public-sdk-resumed")
        assert (
            outcome.status == "succeeded" and client.get_result(created["id"])["status"] == "answer"
        )
        assert client.research_access()["remaining_session_jobs_today"] == 0
        with Session(public.engine) as session:
            assert session.scalar(select(func.count()).select_from(PublicResearchRunAccess)) == 1
