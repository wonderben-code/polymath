"""One-call native worker journeys through real MetaModel with mock HTTP only."""

import asyncio
import json
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_meta_readiness import make_request
from test_meta_readiness_contracts import proposal_data
from test_operator_auth import POLICY, login
from test_worker import database as database_fixture

import polymath.meta_probe_worker as runtime
from polymath.budget import BudgetService
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.evidence_models import EvidenceResult, RunEvidenceSource
from polymath.jobs import JobRepository, StateConflict
from polymath.memory_models import ResearchMemoryRead
from polymath.meta_probe_contracts import (
    MetaProbePolicy,
    ProviderAccountAttestation,
)
from polymath.meta_probe_models import MetaProbeReceipt
from polymath.meta_probe_worker import MetaProbeWorker
from polymath.meta_probes import MetaProbeStore
from polymath.meta_readiness import MetaReadinessStore
from polymath.meta_readiness_contracts import (
    MetaReadinessProposal,
    ReadinessEvidence,
    ReadinessPolicy,
    proposal_sha256,
)
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.operator_auth import create_operator_router
from polymath.research.meta import MetaModel
from polymath.run_api import create_run_router

database = database_fixture
pytestmark = pytest.mark.integration
READINESS = ReadinessPolicy(preparation_enabled=True, allowed_actor_ids=(POLICY.actor_id,))
SYNTHETIC_KEY = "synthetic-never-live-meta-probe-key"
ACTION = json.dumps(
    {
        "kind": "conclude",
        "conclusion": {"outcome": "insufficient_evidence", "reason": "Protocol check only."},
    }
)


@contextmanager
def prepared_probe(engine, *, change_proposal=None):
    """A genuine browser/operator consent transaction using synthetic attestation."""
    app = FastAPI()
    app.include_router(create_run_router(engine))
    app.include_router(create_operator_router(engine, POLICY))
    with TestClient(app) as client:
        headers, _ = login(client)
        request = make_request(client, headers)
        data = proposal_data(now=datetime.now(UTC))
        if change_proposal is not None:
            change_proposal(data)
        proposal = MetaReadinessProposal.model_validate(data)
        readiness = MetaReadinessStore(engine, READINESS, POLICY)
        saved = readiness.save(request, proposal, proposal_sha256(proposal))
        policy = MetaProbePolicy(
            paid_probe_enabled=True,
            native_account_id=proposal.account.account_id,
            model_id=proposal.model_id,
            api_key=SecretStr(SYNTHETIC_KEY),
        )
        store = MetaProbeStore(engine, policy, READINESS, POLICY)
        now = datetime.now(UTC)
        attestation = ProviderAccountAttestation(
            provider_billing_account_id="synthetic-provider-billing-scope",
            model_id=proposal.model_id,
            observed_at=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=30),
            available_credit_microdollars=150000000,
            remaining_provider_allowance_microdollars=150000000,
            provider_hard_cap_microdollars=150000000,
            evidence=(
                ReadinessEvidence(
                    url="https://example.org/synthetic-account-only", content_sha256="d" * 64
                ),
            ),
        )
        preview = store.preview(request, saved.id, attestation)
        job = store.arm_and_enqueue(request, preview.manifest, preview.manifest_sha256)
        yield SimpleNamespace(
            engine=engine,
            policy=policy,
            readiness_policy=READINESS,
            operator_policy=POLICY,
            proposal=proposal,
            request=request,
            client=client,
            headers=headers,
            store=store,
            manifest=preview.manifest,
            job=job,
            run_id=job.id,
        )


@pytest.fixture
def probe(database):
    with prepared_probe(database) as value:
        yield value


def response_body(probe, **changes):
    return {
        "model": probe.proposal.model_id,
        "choices": [{"finish_reason": "stop", "message": {"content": ACTION}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 15},
        **changes,
    }


def mock_native(monkeypatch, *, body=None, handler=None):
    """Keep the production lazy factory and real adapter; inject transport only."""
    state = SimpleNamespace(constructed=[], requests=[], observations=[])

    async def transport(request):
        state.requests.append(request)
        assert str(request.url) == "https://api.meta.ai/v1/chat/completions"
        if handler is not None:
            return await handler(request)
        return httpx.Response(200, json=body)

    def constructor(**kwargs):
        state.constructed.append(kwargs.copy())
        return MetaModel(**kwargs, transport=httpx.MockTransport(transport))

    monkeypatch.setattr(runtime, "MetaModel", constructor)
    return state


def worker(probe, **changes):
    return MetaProbeWorker(
        probe.engine,
        probe_policy=probe.policy,
        readiness_policy=probe.readiness_policy,
        operator_policy=probe.operator_policy,
        **changes,
    )


def receipt(probe, outcome):
    assert outcome.status == "finalized" and outcome.receipt_id
    return probe.store.get_receipt(probe.request, outcome.receipt_id).to_response()


def reservation(probe):
    with Session(probe.engine) as session:
        row = session.scalar(
            select(BudgetReservation).where(BudgetReservation.run_id == probe.run_id)
        )
        return SimpleNamespace(
            id=row.id,
            status=row.status,
            estimate=row.estimated_microdollars,
            actual=row.actual_microdollars,
        )


@pytest.mark.asyncio
async def test_default_factory_sends_exact_approved_payload_once_and_saves_limited_receipt(
    probe, monkeypatch
):
    native = mock_native(monkeypatch, body=response_body(probe))
    instance = worker(probe)
    outcome = await instance.run_once("synthetic-native-worker")
    saved = receipt(probe, outcome)
    assert outcome.native_status == saved["native_status"] == "passed"
    assert saved["research_execution_available"] is False
    assert saved["assessment"]["actual_microdollars"] == 40
    assert saved["assessment"]["action"]["outcome"] == "insufficient_evidence"
    assert len(native.constructed) == len(native.requests) == 1
    constructor = native.constructed[0]
    assert constructor["api_key"] == probe.policy.api_key
    assert constructor["model"] == probe.proposal.model_id
    assert constructor["request_timeout_seconds"] == probe.manifest.request_timeout_seconds
    assert json.loads(native.requests[0].content) == probe.manifest.request.payload.model_dump(
        mode="json"
    )
    assert json.loads(native.requests[0].content)["max_tokens"] == 4096
    assert reservation(probe).status == "settled" and reservation(probe).actual == 40
    assert (await instance.run_once("idle-after-one-call")).status == "idle"
    with Session(probe.engine) as session:
        assert session.get(BudgetAccount, probe.policy.native_account_id).execution_enabled is False
        assert session.get(BudgetAccount, probe.policy.native_account_id).pricing_verified is False
        assert session.scalar(select(func.count()).select_from(ResearchExternalRequest)) == 1
        assert session.scalar(select(func.count()).select_from(MetaProbeReceipt)) == 1
        assert session.scalar(select(func.count()).select_from(EvidenceResult)) == 0
        assert session.scalar(select(func.count()).select_from(RunEvidenceSource)) == 0
        assert session.scalar(select(func.count()).select_from(ResearchMemoryRead)) == 0
        assert session.get(ResearchRun, probe.run_id).task_kind == "verify_meta"
    assert SYNTHETIC_KEY not in json.dumps(saved) and ACTION not in json.dumps(saved)


@pytest.mark.asyncio
async def test_disabled_worker_leaves_queued_job_and_never_constructs_a_model(probe, monkeypatch):
    monkeypatch.setattr(runtime, "MetaModel", lambda **_: pytest.fail("constructed disabled model"))
    instance = MetaProbeWorker(
        probe.engine,
        probe_policy=replace(probe.policy, paid_probe_enabled=False),
        readiness_policy=READINESS,
        operator_policy=POLICY,
    )
    assert (await instance.run_once("disabled-native-worker")).status == "disabled"
    assert JobRepository(probe.engine).get(probe.run_id).status == "queued"
    assert BudgetService(probe.engine).usage(probe.policy.native_account_id).reservations == 0


@pytest.mark.parametrize("failure", ["finish", "invalid_json", "wrong_action"])
@pytest.mark.asyncio
async def test_bad_protocol_is_billed_from_known_usage_without_a_repair_call(
    probe, monkeypatch, failure
):
    body = response_body(probe)
    if failure == "finish":
        body["choices"][0]["finish_reason"] = "length"
    else:
        body["choices"][0]["message"]["content"] = (
            "not JSON"
            if failure == "invalid_json"
            else '{"kind":"read_literature_source","source_id":"x"}'
        )
    native = mock_native(monkeypatch, body=body)
    outcome = await worker(probe).run_once("invalid-protocol")
    saved = receipt(probe, outcome)
    assert saved["native_status"] == "failed"
    assert saved["assessment"]["actual_microdollars"] == 40
    assert reservation(probe).actual == 40 and reservation(probe).status == "settled"
    assert JobRepository(probe.engine).get(probe.run_id).status == "failed"
    assert len(native.requests) == 1


@pytest.mark.parametrize(
    "failure", ["missing_model", "different_model", "missing_usage", "bool_usage"]
)
@pytest.mark.asyncio
async def test_unusable_billing_facts_retain_full_unknown_hold_and_never_resend(
    probe, monkeypatch, failure
):
    body = response_body(probe)
    if failure == "missing_model":
        body.pop("model")
    elif failure == "different_model":
        body["model"] = "different-provider-model"
    elif failure == "missing_usage":
        body.pop("usage")
    else:
        body["usage"]["prompt_tokens"] = True
    native = mock_native(monkeypatch, body=body)
    instance = worker(probe)
    saved = receipt(probe, await instance.run_once("unknown-native-worker"))
    assert saved["native_status"] == "unknown"
    assert saved["assessment"]["actual_microdollars"] is None
    assert reservation(probe).actual is None and reservation(probe).status == "unknown"
    assert reservation(probe).estimate == probe.manifest.reservation_microdollars
    assert (await instance.run_once("no-second-send")).status == "idle"
    assert len(native.requests) == 1


@pytest.mark.parametrize("input_tokens,expected_cost", [(10, 10010), (400000, 410000)])
@pytest.mark.asyncio
async def test_known_billable_overage_is_fully_counted_and_freezes_account(
    probe, monkeypatch, input_tokens, expected_cost
):
    native = mock_native(
        monkeypatch,
        body=response_body(probe, usage={"prompt_tokens": input_tokens, "completion_tokens": 5000}),
    )
    saved = receipt(probe, await worker(probe).run_once("known-overage"))
    assert saved["native_status"] == "failed"
    assert saved["assessment"]["reason_code"] == "billable_bound_exceeded"
    assert saved["assessment"]["actual_microdollars"] == reservation(probe).actual == expected_cost
    with Session(probe.engine) as session:
        assert session.get(BudgetAccount, probe.policy.native_account_id).frozen
    assert len(native.requests) == 1


@pytest.mark.asyncio
async def test_separate_counter_rounding_is_not_the_legacy_combined_cost_formula(
    database, monkeypatch
):
    def fractional(data):
        data["pricing"] |= {
            "input_microdollars_per_million": 1,
            "output_microdollars_per_million": 1,
        }

    with prepared_probe(database, change_proposal=fractional) as probe:
        native = mock_native(
            monkeypatch,
            body=response_body(probe, usage={"prompt_tokens": 1, "completion_tokens": 1}),
        )
        saved = receipt(probe, await worker(probe).run_once("separate-rounded-counters"))
        assert saved["assessment"]["actual_input_microdollars"] == 1
        assert saved["assessment"]["actual_output_microdollars"] == 1
        assert saved["assessment"]["actual_microdollars"] == reservation(probe).actual == 2
        assert len(native.requests) == 1


@pytest.mark.asyncio
async def test_transport_timeout_is_unknown_without_automatic_retry(probe, monkeypatch):
    async def timeout(request):
        raise httpx.ReadTimeout("synthetic private transport message", request=request)

    native = mock_native(monkeypatch, handler=timeout)
    instance = worker(probe)
    result = await instance.run_once("timed-out-native-request")
    if result.status == "finalized":
        assert receipt(probe, result)["native_status"] == "unknown"
    else:
        assert result.status == "interrupted"
    assert reservation(probe).status == "unknown" and reservation(probe).actual is None
    assert (await instance.run_once("no-timeout-retry")).status == "idle"
    assert len(native.requests) == 1
    snapshot = JobRepository(probe.engine).get(probe.run_id)
    with pytest.raises(StateConflict):
        JobRepository(probe.engine).resume_interrupted(
            probe.run_id, expected_token=snapshot.lease_token
        )


@pytest.mark.asyncio
async def test_response_lost_after_journal_commit_can_finalize_free_without_current_key(
    probe, monkeypatch
):
    native = mock_native(monkeypatch, body=response_body(probe))
    instance = worker(probe)
    complete = instance.dispatcher.complete

    def lose_response(grant, observation):
        complete(grant, observation)
        raise RuntimeError("synthetic database reply lost after committed journal")

    monkeypatch.setattr(instance.dispatcher, "complete", lose_response)
    outcome = await instance.run_once("response-lost-worker")
    assert outcome.status == "interrupted" and reservation(probe).actual == 40
    with Session(probe.engine) as session:
        assert session.scalar(select(func.count()).select_from(MetaProbeReceipt)) == 0
    BudgetService(probe.engine).set_kill_switch(probe.policy.native_account_id, active=True)
    monkeypatch.setattr(runtime, "MetaModel", lambda **_: pytest.fail("recovery constructed model"))
    free = MetaProbeWorker(probe.engine)
    recovered = await free.finalize_known(probe.run_id)
    assert recovered.native_status == "passed" and recovered.receipt_id
    assert (await free.finalize_known(probe.run_id)) == recovered
    assert len(native.requests) == 1
    assert BudgetService(probe.engine).usage(probe.policy.native_account_id).reservations == 1


@pytest.mark.asyncio
async def test_cancel_after_grant_preserves_late_actual_response_and_charge(probe, monkeypatch):
    sent, release = asyncio.Event(), asyncio.Event()

    async def delayed(request):
        sent.set()
        await release.wait()
        return httpx.Response(200, json=response_body(probe))

    native = mock_native(monkeypatch, handler=delayed)
    task = asyncio.create_task(worker(probe).run_once("cancelled-after-grant"))
    try:
        await asyncio.wait_for(sent.wait(), timeout=10)
        cancelled = await asyncio.to_thread(probe.store.cancel, probe.request, probe.run_id)
        assert cancelled.to_response()["status"] == "cancelled"
    finally:
        release.set()
    outcome = await task
    assert outcome.native_status == "passed"
    assert reservation(probe).actual == 40
    assert JobRepository(probe.engine).get(probe.run_id).status == "cancelled"
    assert len(native.requests) == 1


@pytest.mark.asyncio
async def test_stopped_epoch_prevents_new_send_and_model_construction(probe, monkeypatch):
    BudgetService(probe.engine).set_kill_switch(probe.policy.native_account_id, active=True)
    monkeypatch.setattr(runtime, "MetaModel", lambda **_: pytest.fail("stopped model constructed"))
    outcome = await worker(probe).run_once("stopped-native-worker")
    assert outcome.status == "interrupted"
    assert BudgetService(probe.engine).usage(probe.policy.native_account_id).reservations == 0


@pytest.mark.asyncio
async def test_rotated_current_key_cannot_send_old_approval(probe, monkeypatch):
    monkeypatch.setattr(runtime, "MetaModel", lambda **_: pytest.fail("rotated model constructed"))
    instance = MetaProbeWorker(
        probe.engine,
        probe_policy=replace(probe.policy, api_key=SecretStr("synthetic-rotated-no-native-key")),
        readiness_policy=READINESS,
        operator_policy=POLICY,
    )
    assert (await instance.run_once("rotated-credential")).status == "interrupted"
    assert BudgetService(probe.engine).usage(probe.policy.native_account_id).reservations == 0


@pytest.mark.asyncio
async def test_factory_failure_after_committed_grant_retains_unknown_hold(probe, monkeypatch):
    def unavailable_model(**kwargs):
        raise RuntimeError("synthetic private factory failure")

    monkeypatch.setattr(runtime, "MetaModel", unavailable_model)
    assert (await worker(probe).run_once("failed-lazy-factory")).status == "interrupted"
    assert reservation(probe).status == "unknown" and reservation(probe).actual is None
    assert reservation(probe).estimate == probe.manifest.reservation_microdollars
    with Session(probe.engine) as session:
        journal = session.get(ResearchExternalRequest, (probe.run_id, "model-0001"))
        assert journal.status == "prepared" and journal.observation is None
        assert session.scalar(select(func.count()).select_from(MetaProbeReceipt)) == 0


@pytest.mark.asyncio
async def test_cancelled_prepare_response_never_sends_or_releases_committed_hold(
    probe, monkeypatch
):
    instance = worker(probe)
    prepare = instance.dispatcher.prepare
    loop, committed = asyncio.get_running_loop(), asyncio.Event()
    release, finished = Event(), Event()

    def prepare_then_lose_response(lease):
        grant = prepare(lease)
        loop.call_soon_threadsafe(committed.set)
        try:
            assert release.wait(timeout=30)
            return grant
        finally:
            finished.set()

    monkeypatch.setattr(instance.dispatcher, "prepare", prepare_then_lose_response)
    monkeypatch.setattr(runtime, "MetaModel", lambda **_: pytest.fail("Cancelled prepare sent"))
    task = asyncio.create_task(instance.run_once("lost-prepare-response"))
    try:
        await asyncio.wait_for(committed.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, timeout=5)
    assert reservation(probe).actual is None
    assert reservation(probe).status == "in_flight"
    assert reservation(probe).estimate == probe.manifest.reservation_microdollars
    with Session(probe.engine) as session:
        journal = session.get(ResearchExternalRequest, (probe.run_id, "model-0001"))
        assert journal.status == "prepared" and journal.observation is None
    assert (await instance.run_once("no-prepared-retry")).status == "idle"


@pytest.mark.asyncio
async def test_actual_finalize_cli_ignores_removed_credentials_and_stale_execution_configuration(
    probe, monkeypatch, tmp_path, capsys
):
    native = mock_native(monkeypatch, body=response_body(probe))
    instance = worker(probe)
    complete = instance.dispatcher.complete

    def lose_journal_response(grant, observation):
        complete(grant, observation)
        raise RuntimeError("synthetic committed journal reply loss")

    monkeypatch.setattr(instance.dispatcher, "complete", lose_journal_response)
    assert (await instance.run_once("before-stale-config")).status == "interrupted"
    monkeypatch.setenv("PAID_META_PROBE_ENABLED", "true")
    monkeypatch.setenv("BUDGET_READINESS_ENABLED", "true")
    monkeypatch.setenv("MODEL_API_KEY", "")
    monkeypatch.setenv("META_MODEL", "invalid removed model / configuration")
    monkeypatch.setenv("OPERATOR_BOOTSTRAP_TOKEN", "invalid-removed-credential")
    monkeypatch.setenv("PUBLICATION_EXECUTION_ENABLED", "true")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["meta-probe-worker", "--finalize-only", str(probe.run_id)])
    monkeypatch.setattr(
        runtime, "MetaModel", lambda **_: pytest.fail("Free CLI built native model")
    )
    assert await asyncio.to_thread(runtime.main) == 0
    assert "finalized: passed" in capsys.readouterr().out
    assert len(native.requests) == 1 and reservation(probe).actual == 40
    assert JobRepository(probe.engine).get(probe.run_id).status == "succeeded"
