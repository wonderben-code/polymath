"""Durable public grants through the actual Meta adapter and fixed-host clients."""

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_full_text import full_text_fixture_transport
from test_meta_probe_worker import ACTION
from test_meta_readiness import readiness as readiness_fixture
from test_native_research_worker import ScientificHttpFixture
from test_public_delegations import public_active
from test_public_research_boundaries import enqueue_public
from test_worker import database as database_fixture

import polymath.native_research_worker as runtime
from polymath.access_models import BrowserSession
from polymath.budget_models import BudgetReservation
from polymath.evidence import EvidenceStore
from polymath.ingestion_models import IngestionProviderGate
from polymath.jobs import JobRepository
from polymath.literature import OpenLiteratureTools
from polymath.meta_research_dispatch import MetaResearchDispatcher
from polymath.meta_research_models import MetaResearchApproval
from polymath.models import ResearchExternalRequest
from polymath.operator_models import OperatorSession
from polymath.public_research_quota import PublicQuotaError
from polymath.research.meta import MetaModel
from polymath.worker import ExecutionStopped

database = database_fixture
readiness = readiness_fixture
pytestmark = pytest.mark.integration


def worker_for(public, monkeypatch, intelligence=None, *, unknown=False):
    state = SimpleNamespace(model_requests=[], source_requests=[], factories=[])

    async def native_http(request):
        state.model_requests.append(request)
        body = json.loads(request.content)
        assert str(request.url) == "https://api.meta.ai/v1/chat/completions"
        assert body["model"] == public.proposal.model_id
        assert body["max_tokens"] == public.proposal.application_limits.max_output_tokens
        content = ACTION
        if intelligence is not None:
            reply = await intelligence.complete(
                body["messages"], max_output_tokens=body["max_tokens"]
            )
            content = reply.content
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {"prompt_tokens": True if unknown else 10, "completion_tokens": 15},
            },
        )

    def native_constructor(**kwargs):
        state.factories.append(kwargs)
        return MetaModel(**kwargs, transport=httpx.MockTransport(native_http))

    def source_constructor(**kwargs):
        assert kwargs["provider_slot"] is not None
        return OpenLiteratureTools(
            **kwargs, transport=full_text_fixture_transport(state.source_requests)
        )

    monkeypatch.setattr(runtime, "MetaModel", native_constructor)
    monkeypatch.setattr(runtime, "OpenLiteratureTools", source_constructor)
    return runtime.build_native_research_worker(
        public.engine,
        research_policy=public.policy,
        readiness_policy=public.readiness_policy,
        operator_policy=public.operator_policy,
        public_policy=public.public_policy,
    ), state


def expire_issuer(public):
    with Session(public.engine) as session, session.begin():
        approval = session.scalar(
            select(MetaResearchApproval).where(
                MetaResearchApproval.manifest_sha256 == public.public_activation_sha256
            )
        )
        now = session.scalar(select(func.clock_timestamp()))
        issuer = session.get(OperatorSession, approval.operator_session_hash)
        issuer.revoked_at = now
        issuer.expires_at = now - timedelta(seconds=1)
        browser = session.get(BrowserSession, approval.browser_session_hash)
        browser.expires_at = now - timedelta(seconds=1)


async def test_public_ask_uses_actual_adapter_and_paced_sources_after_issuer_logout(
    readiness, monkeypatch
):
    public = public_active(readiness, short_issuer=True)
    run_id, visitor = enqueue_public(public, mode="ask")
    expire_issuer(public)
    worker, state = worker_for(public, monkeypatch, ScientificHttpFixture())
    outcome = await worker.run_once("public-actual-ask")
    assert outcome.status == "succeeded" and outcome.result_id
    result = EvidenceStore(public.engine).get_result(run_id, uuid.UUID(outcome.result_id))
    assert result.payload["status"] == "answer" and result.payload["quotes"]
    assert "polymath_operator=" not in visitor.request.headers["cookie"]
    assert len(state.model_requests) == len(state.factories) == 6
    assert {request.url.host for request in state.source_requests} == {
        "api.openalex.org",
        "export.arxiv.org",
    }
    with Session(public.engine) as session:
        reservations = session.scalars(
            select(BudgetReservation).where(BudgetReservation.run_id == run_id)
        ).all()
        assert len(reservations) == 6
        assert all(
            row.status == "settled" and row.actual_microdollars == 40 for row in reservations
        )
        assert session.get(IngestionProviderGate, "openalex").sequence == 3
        assert session.get(IngestionProviderGate, "arxiv").sequence == 3


async def test_public_unknown_usage_holds_once_without_followup_source_or_send(
    readiness, monkeypatch
):
    public = public_active(readiness)
    run_id, _ = enqueue_public(public)
    expire_issuer(public)
    worker, state = worker_for(public, monkeypatch, unknown=True)
    assert (await worker.run_once("public-unknown")).status == "interrupted"
    assert len(state.model_requests) == 1 and not state.source_requests
    assert (await worker.run_once("public-no-auto-resend")).status == "idle"
    with Session(public.engine) as session:
        rows = session.scalars(
            select(BudgetReservation).where(BudgetReservation.run_id == run_id)
        ).all()
        assert len(rows) == 1 and rows[0].status == "unknown"
        assert rows[0].actual_microdollars is None
        assert session.get(ResearchExternalRequest, (run_id, "model-0001")).status == "observed"


@pytest.mark.parametrize("stop", ["expired", "cancelled"])
async def test_default_source_pacer_checks_requester_and_lease_before_provider_http(
    readiness, monkeypatch, stop
):
    public = public_active(readiness)
    run_id, visitor = enqueue_public(public)
    worker, state = worker_for(public, monkeypatch)
    lease = worker.jobs.claim_next(
        "public-source-fence", native_only=True, native_account_id=public.policy.native_account_id
    )
    assert lease.run_id == run_id
    tools = worker.tools_context_factory(SimpleNamespace(lease=lease), worker.jobs.get(run_id))
    if stop == "expired":
        with Session(public.engine) as session, session.begin():
            session.execute(
                update(BrowserSession)
                .where(BrowserSession.token_hash == visitor.owner_hash)
                .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
            )
    else:
        worker.jobs.request_cancel(run_id)
    with pytest.raises(ExecutionStopped):
        await tools.search("Synthetic public provider fence", "discovery")
    assert not state.source_requests and not state.model_requests
    with Session(public.engine) as session:
        assert session.get(IngestionProviderGate, "openalex").sequence == 0
        assert session.get(IngestionProviderGate, "arxiv").sequence == 0


@pytest.mark.parametrize(
    "code", ["public_session_total_budget_exceeded", "public_research_delegation_expired"]
)
async def test_public_fresh_quota_stop_keeps_static_reason_without_constructing_model(
    readiness, monkeypatch, code
):
    public = public_active(readiness)
    run_id, _ = enqueue_public(public)
    worker, state = worker_for(public, monkeypatch)

    def reject(*args, **kwargs):
        raise PublicQuotaError(code)

    monkeypatch.setattr(MetaResearchDispatcher, "prepare", reject)
    assert (await worker.run_once("public-bounded-stop")).status == "interrupted"
    assert JobRepository(public.engine).get(run_id).interruption_reason == code
    assert not state.model_requests and not state.factories and not state.source_requests
    with Session(public.engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(BudgetReservation)
                .where(BudgetReservation.run_id == run_id)
            )
            == 0
        )
