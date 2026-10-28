"""Original scientific-profile billing/replay through actual adapter and PostgreSQL.

Every HTTP request uses MockTransport. These tests authorize only synthetic local
fixtures, never a paid model/provider call.
"""

import asyncio
import json
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from test_evidence import document
from test_ingestion_api import save_fixture_page
from test_meta_probe_worker import ACTION
from test_meta_readiness import readiness as readiness_fixture
from test_native_research_worker import ScientificHttpFixture, actual_worker
from test_native_research_worker import native as native_fixture
from test_public_delegations import public_active
from test_run_api import bootstrap
from test_scientific_native_worker import (
    ARTIFACT_INTENT,
    ASSESSMENT_INTENT,
    approve_scientific,
    create_run,
    scientific_client,
    seed_bound_ask,
)
from test_worker import database as database_fixture

import polymath.artifact_controller as artifact_controller
import polymath.assessment_overview as assessment_overview
import polymath.meta_research_contracts as native_contracts
import polymath.native_research_worker as runtime
import polymath.public_research_contracts as public_contracts
import polymath.research.ask_overview as ask_overview
import polymath.research.meta as meta_transport
import polymath.scientific_context_builder as context_builder
from polymath.access_models import BrowserSession
from polymath.artifacts import ArtifactStore
from polymath.assessment_overview import ask_reassessment_overview_initial_messages
from polymath.assessments import AssessmentStore
from polymath.budget import BudgetService
from polymath.budget_models import BudgetReservation
from polymath.meta_readiness_contracts import quote_proposal
from polymath.meta_research import MetaResearchStore
from polymath.meta_research_contracts import build_meta_research_request, research_step_sha256
from polymath.meta_research_dispatch import MetaResearchDispatcher
from polymath.meta_research_models import (
    MetaResearchApproval,
    NativeResearchCallReceipt,
    NativeResearchCallRequest,
    NativeResearchRunBinding,
)
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.native_research_worker import NativeRecordedModel, original_runtime_profile
from polymath.operator_models import OperatorSession
from polymath.research.ask_overview import ask_overview_initial_messages
from polymath.research.meta import MetaModel
from polymath.worker import ExecutionStopped, _RunContext

database = database_fixture
native = native_fixture
readiness = readiness_fixture
pytestmark = pytest.mark.integration


async def target(native, monkeypatch, kind):
    """Use actual API admission and immutable sidecars before any target grant."""
    approve_scientific(native)
    with scientific_client(native) as client:
        if kind == "research":
            return create_run(client, native)
        if kind == "formalise":
            # Formalise's existing source contract requires a saved bridge.
            create_run(client, native, mode="explore", intent=False)
            worker, _ = actual_worker(native, monkeypatch, ScientificHttpFixture())
            source = await worker.run_once("scientific-recovery-formalise-basis")
            assert source.status == "succeeded" and source.result_id
            source_result_id = source.result_id
        else:
            initial = await seed_bound_ask(native, client, monkeypatch)
            source_result_id = str(initial.result_id)
        if kind in ("formalise", "write_summary", "write_paper"):
            response = client.post(
                "/api/v1/artifacts/jobs",
                headers=native.headers,
                json={
                    "kind": kind,
                    "source_result_id": source_result_id,
                    "expected_activation_sha256": native.activation_sha256,
                    "scientific_context_intent": ARTIFACT_INTENT,
                },
            )
        else:
            watch = client.post(
                "/api/v1/knowledge/watches",
                headers=native.headers,
                json={
                    "label": "Synthetic original-profile recovery watch",
                    "query": "Synthetic source-bound recovery evidence",
                    "source_result_id": str(initial.result_id),
                },
            )
            assert watch.status_code == 201, watch.text
            version = save_fixture_page(
                native.engine,
                client,
                watch.json()["id"],
                (
                    document(
                        "fixture:scientific-recovery-new",
                        text="New synthetic evidence bounds the prior inference.",
                    ),
                ),
            )[0]
            response = client.post(
                "/api/v1/assessments/jobs",
                headers=native.headers,
                json={
                    "source_result_id": str(initial.result_id),
                    "watch_id": watch.json()["id"],
                    "new_source_version_ids": [str(version.version_id)],
                    "expected_head_revision": 0,
                    "expected_activation_sha256": native.activation_sha256,
                    "assessment_overview_intent": ASSESSMENT_INTENT,
                },
            )
        assert response.status_code == 201, response.text
        return uuid.UUID(response.json()["id"])


def claim_and_messages(value, run_id, kind):
    from polymath.jobs import JobRepository

    lease = JobRepository(value.engine).claim_next(
        "scientific-original-profile-recovery",
        lease_seconds=300,
        task_kinds=(kind,),
        native_only=True,
        native_account_id=value.policy.native_account_id,
    )
    assert lease.run_id == run_id
    original = value.store.load_original_input(lease)
    profile = original_runtime_profile(original)
    with Session(value.engine) as session:
        binding = session.get(NativeResearchRunBinding, run_id)
        assert binding.profile["controller_protocol"] in (
            "polymath-meta-artifact-scientific-context-v1",
            "polymath-meta-ask-claim-overview-v1",
            "polymath-meta-ask-reassessment-overview-v1",
        )
        assert binding.input_sha256 == original.input_sha256
    if kind == "research":
        with Session(value.engine) as session:
            goal = session.get(ResearchRun, run_id).goal
        messages = ask_overview_initial_messages(goal, memory_available=True)
    elif kind == "reassess_finding":
        messages = ask_reassessment_overview_initial_messages(
            AssessmentStore(value.engine).load_basis(lease)
        )
    else:
        messages = artifact_controller.artifact_initial_messages(
            kind, ArtifactStore(value.engine).load_basis(lease)
        )
    return lease, original, profile, messages


async def prepare_and_observe(value, lease, original, messages, *, failure=None):
    dispatcher = MetaResearchDispatcher(
        value.engine,
        value.policy,
        value.readiness_policy,
        value.operator_policy,
        public_policy=getattr(value, "public_policy", None),
    )
    request = build_meta_research_request(
        original.proposal, messages, max_output_tokens=original.profile.max_output_tokens
    )
    request_hash = research_step_sha256(
        request,
        activation_sha256=original.activation_sha256,
        profile_sha256=original.profile_sha256,
        request_id="model-0001",
    )
    grant = await asyncio.to_thread(dispatcher.prepare, lease, "model-0001", request, request_hash)
    assert grant is not None
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.meta.ai/v1/chat/completions"
        assert json.loads(request.content)["messages"] == messages
        body = {
            "model": original.proposal.model_id,
            "choices": [{"finish_reason": "stop", "message": {"content": ACTION}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 15},
        }
        if failure == "unreported_usage":
            body.pop("usage")
        return httpx.Response(200, json=body)

    model = MetaModel(
        api_key=grant.api_key,
        model=grant.model_id,
        request_timeout_seconds=grant.request_timeout_seconds,
        live_enabled=True,
        transport=httpx.MockTransport(handler),
    )
    observation = await model.observe_research(request)
    assert len(calls) == 1
    with Session(value.engine) as session:
        reservation = session.get(BudgetReservation, grant.reservation_id)
        journal = session.get(ResearchExternalRequest, (lease.run_id, "model-0001"))
        assert reservation.status == "in_flight" and reservation.actual_microdollars is None
        assert journal.status == "prepared" and journal.observation is None
    return dispatcher, grant, observation, calls


def expire_requester(value, run_id):
    with Session(value.engine) as session, session.begin():
        binding = session.get(NativeResearchRunBinding, run_id)
        session.get(BrowserSession, binding.browser_session_hash).expires_at = session.scalar(
            select(func.clock_timestamp())
        ) - timedelta(seconds=1)
        approval = session.get(MetaResearchApproval, binding.approval_id)
        issuer = session.get(OperatorSession, approval.operator_session_hash)
        issuer.revoked_at = session.scalar(select(func.clock_timestamp()))
        issuer.expires_at = issuer.revoked_at - timedelta(seconds=1)


def historical_traps(monkeypatch):
    """Current prompts/profile derivation, controllers and clients must be unused."""

    def forbidden(*args, **kwargs):
        pytest.fail("Historical recovery consulted current science or attempted external work")

    for name in (
        "derive_task_profiles",
        "derive_scientific_context_profiles",
        "scientific_controller_sha256",
        "minimum_scientific_controller_context_characters",
        "validate_current_scientific_context_profiles",
    ):
        if hasattr(native_contracts, name):
            monkeypatch.setattr(native_contracts, name, forbidden)
    if hasattr(public_contracts, "derive_scientific_context_profiles"):
        monkeypatch.setattr(public_contracts, "derive_scientific_context_profiles", forbidden)
    monkeypatch.setattr(ask_overview, "ask_overview_initial_messages", forbidden)
    monkeypatch.setattr(ask_overview, "ask_overview_protocol_payload", forbidden)
    monkeypatch.setattr(
        assessment_overview, "ask_reassessment_overview_initial_messages", forbidden
    )
    monkeypatch.setattr(
        assessment_overview, "ask_reassessment_overview_protocol_payload", forbidden
    )
    monkeypatch.setattr(artifact_controller, "artifact_initial_messages", forbidden)
    monkeypatch.setattr(context_builder, "build_artifact_scientific_context", forbidden)
    monkeypatch.setattr(context_builder, "validated_owned_result_packet", forbidden)
    monkeypatch.setattr(runtime, "MetaModel", forbidden)
    monkeypatch.setattr(meta_transport, "MetaModel", forbidden)
    monkeypatch.setattr(runtime, "configured_native_research_policies", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("META_MODEL", raising=False)
    monkeypatch.setenv("PAID_META_RESEARCH_ENABLED", "invalid-live-settings-ignored-by-finalizer")
    monkeypatch.setenv("PUBLIC_META_RESEARCH_ENABLED", "invalid-live-settings-ignored-by-finalizer")
    return forbidden


def target_rows(value, run_id):
    with Session(value.engine) as session:
        reservation = session.scalar(
            select(BudgetReservation).where(BudgetReservation.run_id == run_id)
        )
        journal = session.get(ResearchExternalRequest, (run_id, "model-0001"))
        return (
            reservation.status,
            reservation.estimated_microdollars,
            reservation.actual_microdollars,
            journal.status,
            journal.observation,
            session.scalar(
                select(func.count())
                .select_from(NativeResearchCallRequest)
                .where(NativeResearchCallRequest.run_id == run_id)
            ),
            session.scalar(
                select(func.count())
                .select_from(NativeResearchCallReceipt)
                .where(NativeResearchCallReceipt.run_id == run_id)
            ),
        )


@pytest.mark.parametrize(
    "kind", ["research", "formalise", "write_summary", "write_paper", "reassess_finding"]
)
async def test_all_new_scientific_profiles_settle_and_replay_original_facts_after_closure(
    native, monkeypatch, capsys, kind
):
    run_id = await target(native, monkeypatch, kind)
    lease, original, profile, messages = claim_and_messages(native, run_id, kind)
    before = BudgetService(native.engine).usage(original.account_id)
    _, grant, observation, calls = await prepare_and_observe(native, lease, original, messages)
    await asyncio.to_thread(native.store.revoke, native.request, native.activation.id)
    expire_requester(native, run_id)
    forbidden = historical_traps(monkeypatch)
    sql = []

    def record_sql(connection, cursor, statement, parameters, context, executemany):
        sql.append(statement.lower())

    event.listen(Engine, "before_cursor_execute", record_sql)
    try:
        no_key_store = MetaResearchStore(native.engine)
        no_key_dispatcher = MetaResearchDispatcher(native.engine)
        # The send was authorized before closure; its known response is accounted
        # after revocation/expiry with no current key/profile/prompt dependency.
        await asyncio.to_thread(no_key_dispatcher.complete, grant, observation)
        after = BudgetService(native.engine).usage(original.account_id)
        assert after.committed_microdollars == before.committed_microdollars + 40
        assert after.in_flight == 0 and after.reservations == before.reservations + 1
        restored_input = no_key_store.load_original_input(lease)
        restored_profile = original_runtime_profile(restored_input)
        assert restored_input.profile_sha256 == original.profile_sha256
        assert restored_input.input_sha256 == original.input_sha256
        assert restored_profile == profile
        replay = NativeRecordedModel(
            _RunContext(native.engine, lease, 300),
            no_key_store,
            no_key_dispatcher,
            restored_profile,
            forbidden,
        )
        response = await replay.complete(
            messages, max_output_tokens=original.profile.max_output_tokens
        )
        assert response.content == ACTION
        assert (response.input_tokens, response.output_tokens) == (10, 15)
        receipt = no_key_store.finalize_known_call(run_id, "model-0001").to_response()
        assert receipt["usage"]["actual_microdollars"] == 40
        assert receipt["scientific_completion_implied"] is False
        settings = runtime._FinalizeDatabaseSettings(
            database_url=native.engine.url.render_as_string(hide_password=False), _env_file=None
        )
        assert (
            await runtime.run(
                settings, once=False, worker_id="free-scientific-finalize", finalize_only=run_id
            )
            == 0
        )
        assert "billing finalized" in capsys.readouterr().out
        assert no_key_store.finalize_known_call(run_id, "model-0001").to_response() == receipt
        with pytest.raises(ExecutionStopped):
            await replay.complete(messages, max_output_tokens=original.profile.max_output_tokens)
        rows = target_rows(native, run_id)
        assert rows[0] == "settled" and rows[2] == 40 and rows[3] == "observed"
        assert rows[5:] == (1, 1)
        assert BudgetService(native.engine).usage(original.account_id) == after
        assert len(calls) == 1
        assert not any("finding_heads" in statement for statement in sql)
    finally:
        event.remove(Engine, "before_cursor_execute", record_sql)


async def test_durable_public_scientific_request_has_the_same_keyless_late_accounting(
    readiness, monkeypatch
):
    public = public_active(readiness, short_issuer=False)
    approve_scientific(public, public=True)
    with scientific_client(public, public=True) as visitor:
        headers = bootstrap(visitor)
        run_id = create_run(visitor, public, headers=headers)
    lease, original, profile, messages = claim_and_messages(public, run_id, "research")
    before = BudgetService(public.engine).usage(original.account_id)
    _, grant, observation, calls = await prepare_and_observe(public, lease, original, messages)
    public.delegations.revoke(public.request, public.public_activation.id)
    expire_requester(public, run_id)
    forbidden = historical_traps(monkeypatch)
    no_key_store = MetaResearchStore(public.engine)
    no_key_dispatcher = MetaResearchDispatcher(public.engine)
    no_key_dispatcher.complete(grant, observation)
    restored = no_key_store.load_original_input(lease)
    assert original_runtime_profile(restored) == profile
    response = await NativeRecordedModel(
        _RunContext(public.engine, lease, 300), no_key_store, no_key_dispatcher, profile, forbidden
    ).complete(messages, max_output_tokens=original.profile.max_output_tokens)
    assert response.content == ACTION
    receipt = no_key_store.finalize_known_call(run_id, "model-0001").to_response()
    assert receipt["scientific_completion_implied"] is False
    assert receipt["usage"]["actual_microdollars"] == 40
    assert (
        BudgetService(public.engine).usage(original.account_id).committed_microdollars
        == before.committed_microdollars + 40
    )
    assert target_rows(public, run_id)[5:] == (1, 1)
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["unreported_usage", "lost_before_journal"])
async def test_unknown_new_protocol_retains_original_hold_and_never_resends(
    native, monkeypatch, capsys, failure
):
    run_id = await target(native, monkeypatch, "research")
    lease, original, profile, messages = claim_and_messages(native, run_id, "research")
    before = BudgetService(native.engine).usage(original.account_id)
    dispatcher, grant, observation, calls = await prepare_and_observe(
        native, lease, original, messages, failure=failure
    )
    if failure == "lost_before_journal":
        # The actual response never reached durable application knowledge, as
        # after process loss. Explicitly preserve uncertainty rather than infer it.
        dispatcher.mark_unknown(grant, "worker_lost")
    else:
        dispatcher.complete(grant, observation)
    forbidden = historical_traps(monkeypatch)
    no_key_store = MetaResearchStore(native.engine)
    no_key_dispatcher = MetaResearchDispatcher(native.engine)
    replay = NativeRecordedModel(
        _RunContext(native.engine, lease, 300), no_key_store, no_key_dispatcher, profile, forbidden
    )
    with pytest.raises(ExecutionStopped):
        await replay.complete(messages, max_output_tokens=original.profile.max_output_tokens)
    with pytest.raises(ExecutionStopped):
        await replay.complete(messages, max_output_tokens=original.profile.max_output_tokens)
    settings = runtime._FinalizeDatabaseSettings(
        database_url=native.engine.url.render_as_string(hide_password=False), _env_file=None
    )
    assert (
        await runtime.run(
            settings, once=False, worker_id="free-unknown-finalize", finalize_only=run_id
        )
        == 2
    )
    assert "no request sent" in capsys.readouterr().out
    rows = target_rows(native, run_id)
    estimate = quote_proposal(original.proposal).per_call_reservation_microdollars
    assert rows[:3] == ("unknown", estimate, None)
    assert rows[3] == ("prepared" if failure == "lost_before_journal" else "observed")
    assert rows[5] == 1
    after = BudgetService(native.engine).usage(original.account_id)
    assert after.committed_microdollars == before.committed_microdollars + estimate
    assert after.in_flight == 1 and after.reservations == before.reservations + 1
    assert len(calls) == 1
