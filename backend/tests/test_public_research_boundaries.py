"""Independent public authority and true pre-D2 compatibility checks.

Historical baselines were generated from an isolated read-only git archive of
f220986, not regenerated from the implementation under test. Native transports
in the integration tests are synthetic; this module authorizes no live calls.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_meta_readiness import readiness as readiness_fixture
from test_meta_research import active_research, native_call, research_observation
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.budget import BudgetService
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.discovery_contracts import DiscoveryIntent
from polymath.meta_research import MetaResearchError, MetaResearchStore
from polymath.meta_research_contracts import (
    MetaResearchManifest,
    canonical_research_manifest_json,
    profile_for_task,
    research_manifest_sha256,
    task_profile_sha256,
)
from polymath.meta_research_models import (
    MetaResearchApproval,
    NativeResearchCallRequest,
    NativeResearchRunBinding,
)
from polymath.operator_models import OperatorSession
from polymath.public_research_models import PublicResearchRunAccess

database = database_fixture
readiness = readiness_fixture

BASELINE = json.loads(
    (Path(__file__).parent / "fixtures/pre_public_research_manifests.json").read_text()
)


def public_visitor(public, *, peer="198.51.100.10"):
    """Mint an actual owner cookie, then attach trusted server resource identity."""
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from test_meta_readiness import make_request
    from test_run_api import bootstrap

    from polymath.run_api import create_run_router

    app = FastAPI()
    app.include_router(create_run_router(public.engine))
    with TestClient(app) as client:
        headers = bootstrap(client)
        original = make_request(client, headers)
        scope = dict(original.scope)
        scope.update(path="/api/v1/runs", client=(peer, 12345))
        request = Request(scope)
    identity = public.parent.resource_controls.identify(peer, request.headers.get("cookie"))
    request.state.public_resource_identity = identity
    assert "polymath_operator=" not in request.headers.get("cookie", "")
    return SimpleNamespace(request=request, owner_hash=identity.owner_hash, headers=headers)


def enqueue_public(public, *, visitor=None, **inputs):
    """Use the real common admission/basis helper with a distinct requester."""
    from test_native_research_worker import enqueue_native

    visitor = visitor or public_visitor(public)
    current = SimpleNamespace(
        **(
            vars(public)
            | {
                "store": public.parent,
                "request": visitor.request,
                "activation_sha256": public.public_activation_sha256,
                "owner_hash": visitor.owner_hash,
            }
        )
    )
    return enqueue_native(current, **inputs), visitor


def public_call(public, *, visitor=None, mode="explore"):
    from polymath.jobs import JobRepository
    from polymath.meta_research_contracts import build_meta_research_request, research_step_sha256
    from polymath.meta_research_dispatch import MetaResearchDispatcher

    run_id, visitor = enqueue_public(public, visitor=visitor, mode=mode)
    lease = JobRepository(public.engine).claim_next("public-boundary-worker", native_only=True)
    assert lease is not None and lease.run_id == run_id
    value = public.parent.load_original_input(lease)
    request = build_meta_research_request(
        value.proposal,
        [{"role": "user", "content": "Synthetic public boundary check"}],
        max_output_tokens=value.profile.max_output_tokens,
    )
    step = research_step_sha256(
        request,
        activation_sha256=value.activation_sha256,
        profile_sha256=value.profile_sha256,
        request_id="model-0001",
    )
    dispatcher = MetaResearchDispatcher(
        public.engine,
        public.policy,
        public.readiness_policy,
        public.operator_policy,
        public_policy=public.public_policy,
    )
    return SimpleNamespace(
        run_id=run_id,
        lease=lease,
        value=value,
        request=request,
        step=step,
        visitor=visitor,
        dispatcher=dispatcher,
    )


@pytest.mark.parametrize("kind", ["private", "private_ladder"])
def test_pre_d2_private_canonical_manifest_and_every_profile_hash_are_exact(kind):
    saved = BASELINE[kind]
    manifest = MetaResearchManifest.model_validate_json(json.dumps(saved["manifest"]))
    assert BASELINE["source_revision"] == "f22098640e8af8b94d9cc731b3c5a3ca1402fa1d"
    assert canonical_research_manifest_json(manifest) == saved["canonical"]
    assert research_manifest_sha256(manifest) == saved["manifest_sha256"]
    for binding in saved["manifest"]["task_profiles"]:
        profile = profile_for_task(manifest, binding["profile"]["task_kind"])
        assert profile.model_dump(mode="json") == binding["profile"]
        assert task_profile_sha256(profile) == binding["profile_sha256"]
    if kind == "private_ladder":
        binding = saved["manifest"]["discovery_ladder_profile"]
        profile = profile_for_task(manifest, "research", discovery=DiscoveryIntent())
        assert profile.model_dump(mode="json") == binding["profile"]
        assert task_profile_sha256(profile) == binding["profile_sha256"]


@pytest.mark.parametrize(
    "change",
    [
        {"audience": "public_sessions"},
        {"protocol": "polymath-meta-public-research-v1"},
        {"authorization_mode": "durable_owner_delegation"},
        {"continues_after_operator_logout": True},
    ],
)
def test_historical_private_manifest_cannot_be_reinterpreted_as_public_authority(change):
    with pytest.raises(ValidationError):
        MetaResearchManifest.model_validate_json(
            json.dumps(BASELINE["private"]["manifest"] | change)
        )


@pytest.mark.parametrize("kind", ["private", "private_ladder"])
def test_private_snapshots_do_not_acquire_public_default_fields(kind):
    manifest = MetaResearchManifest.model_validate_json(json.dumps(BASELINE[kind]["manifest"]))
    payload = manifest.model_dump(mode="json")
    assert not {
        "authorization_mode",
        "continues_after_operator_logout",
        "public_limits",
        "sponsor_policy_sha256",
        "public_policy_sha256",
    }.intersection(payload)
    if kind == "private":
        assert "discovery_ladder_profile" not in payload


@pytest.mark.integration
@pytest.mark.parametrize("stop", ["operator_logout", "operator_expiry", "browser_expiry"])
def test_existing_private_activation_never_inherits_public_after_logout_authority(readiness, stop):
    active = active_research(readiness)
    _, lease, _, request, step = native_call(active)
    with Session(active.engine) as session:
        before = session.scalar(select(func.count()).select_from(BudgetReservation))
    if stop == "operator_logout":
        _, _, _, client, headers = readiness
        assert client.post("/api/v1/operator/logout", headers=headers).status_code == 200
    else:
        table = OperatorSession if stop == "operator_expiry" else BrowserSession
        with Session(active.engine) as session, session.begin():
            session.execute(
                update(table).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
    with pytest.raises(MetaResearchError, match="meta_research_authority_expired"):
        active.dispatcher.prepare(lease, "model-0001", request, step)
    with Session(active.engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == before
        assert session.scalar(select(func.count()).select_from(NativeResearchCallRequest)) == 0


@pytest.mark.integration
def test_pre_d2_private_known_billing_remains_free_after_all_live_authority_expires(readiness):
    active = active_research(readiness)
    run_id, lease, value, request, step = native_call(active)
    grant = active.dispatcher.prepare(lease, "model-0001", request, step)
    observed = research_observation(value, request)
    active.dispatcher.complete(grant, observed)
    BudgetService(active.engine).disable_execution(value.account_id)
    expired = datetime.now(UTC) - timedelta(seconds=1)
    with Session(active.engine) as session, session.begin():
        session.execute(update(OperatorSession).values(expires_at=expired, revoked_at=expired))
        session.execute(update(BrowserSession).values(expires_at=expired))
        reservations = session.scalar(select(func.count()).select_from(BudgetReservation))
    no_live_configuration = MetaResearchStore(active.engine)
    first = no_live_configuration.finalize_known_call(run_id, "model-0001").to_response()
    assert no_live_configuration.finalize_known_call(run_id, "model-0001").to_response() == first
    assert active.dispatcher.get_recorded_observation(lease, "model-0001", step) == observed
    assert first["usage"]["status"] == "known"
    assert first["scientific_completion_implied"] is False
    with Session(active.engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == reservations


@pytest.mark.integration
@pytest.mark.parametrize("issuer_stop", ["logout", "issuer_and_browser_expiry"])
def test_explicit_public_delegation_survives_issuer_stop_and_known_billing_is_still_free(
    readiness, issuer_stop
):
    from test_public_delegations import public_active

    public = public_active(readiness)
    call = public_call(public)
    if issuer_stop == "logout":
        assert (
            public.issuer_client.post(
                "/api/v1/operator/logout", headers=public.issuer_headers
            ).status_code
            == 200
        )
    else:
        expired = datetime.now(UTC) - timedelta(seconds=1)
        with Session(public.engine) as session, session.begin():
            session.execute(update(OperatorSession).values(expires_at=expired, revoked_at=expired))
            session.execute(
                update(BrowserSession)
                .where(BrowserSession.token_hash != call.visitor.owner_hash)
                .values(expires_at=expired)
            )
    grant = call.dispatcher.prepare(call.lease, "model-0001", call.request, call.step)
    assert grant is not None
    with Session(public.engine) as session:
        binding = session.get(NativeResearchRunBinding, call.run_id)
        access = session.get(PublicResearchRunAccess, call.run_id)
        approval = session.get(MetaResearchApproval, binding.approval_id)
        assert (
            binding.browser_session_hash == access.requester_session_hash == call.visitor.owner_hash
        )
        assert approval.browser_session_hash != call.visitor.owner_hash
    observed = research_observation(call.value, call.request)
    call.dispatcher.complete(grant, observed)
    BudgetService(public.engine).disable_execution(call.value.account_id)
    with Session(public.engine) as session, session.begin():
        session.execute(
            update(BrowserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        before = session.scalar(select(func.count()).select_from(BudgetReservation))
    free = MetaResearchStore(public.engine)
    receipt = free.finalize_known_call(call.run_id, "model-0001").to_response()
    assert receipt["usage"]["status"] == "known"
    assert call.dispatcher.get_recorded_observation(call.lease, "model-0001", call.step) == observed
    assert free.finalize_known_call(call.run_id, "model-0001").to_response() == receipt
    with Session(public.engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == before


@pytest.mark.integration
@pytest.mark.parametrize(
    "stop",
    [
        "role",
        "meta_key",
        "operator_policy",
        "public_flag",
        "epoch",
        "kill",
        "freeze",
        "requester_expiry",
        "deadline",
    ],
)
def test_public_fresh_grant_still_requires_live_visitor_policy_epoch_and_funds(
    readiness, monkeypatch, stop
):
    from test_public_delegations import public_active

    public = public_active(readiness)
    call = public_call(public)
    with Session(public.engine) as session:
        before = session.scalar(select(func.count()).select_from(BudgetReservation))
    if stop == "role":
        call.dispatcher.store.readiness_policy = replace(
            public.readiness_policy, allowed_actor_ids=()
        )
    elif stop == "meta_key":
        call.dispatcher.store.research_policy = replace(
            public.policy, api_key=SecretStr("synthetic-rotated-public-model-key")
        )
    elif stop == "operator_policy":
        call.dispatcher.store.operator_policy = replace(
            public.operator_policy,
            bootstrap_token=SecretStr(
                "Synthetic_changed_public_bootstrap_0123456789-ABCDEFGHIJKLM"
            ),
        )
    elif stop == "public_flag":
        call.dispatcher.store.public_policy = replace(public.public_policy, enabled=False)
    elif stop == "epoch":
        BudgetService(public.engine).disable_execution(call.value.account_id)
    elif stop == "kill":
        BudgetService(public.engine).set_kill_switch(call.value.account_id, active=True)
    elif stop == "freeze":
        with Session(public.engine) as session, session.begin():
            session.execute(
                update(BudgetAccount)
                .where(BudgetAccount.id == call.value.account_id)
                .values(frozen=True, frozen_reason="synthetic-review")
            )
    elif stop == "requester_expiry":
        with Session(public.engine) as session, session.begin():
            session.execute(
                update(BrowserSession)
                .where(BrowserSession.token_hash == call.visitor.owner_hash)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
    else:
        import polymath.public_research as public_service

        monkeypatch.setattr(
            public_service, "_clock", lambda _: public.public_deadline + timedelta(seconds=1)
        )
    with pytest.raises(MetaResearchError):
        call.dispatcher.prepare(call.lease, "model-0001", call.request, call.step)
    with Session(public.engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == before
        assert session.scalar(select(func.count()).select_from(NativeResearchCallRequest)) == 0


@pytest.mark.integration
def test_public_access_has_no_key_issuer_ledger_or_administrator_authority(readiness):
    from test_public_delegations import public_active

    public = public_active(readiness)
    visitor = public_visitor(public)
    projection = public.parent.public_service.access(visitor.request)
    assert projection["audience"] == "public_sessions" and projection["available"] is True
    forbidden = {
        "account_id",
        "actor_id",
        "operator_session_hash",
        "sponsor_browser_session_hash",
        "credential_binding_sha256",
        "pricing",
        "api_key",
        "provider_account_attestation",
    }

    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert not forbidden.intersection(keys(projection))
    with pytest.raises(Exception) as rejected:
        public.delegations.list_activations(visitor.request)
    assert getattr(rejected.value, "status_code", None) == 401
    with Session(public.engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 1


@pytest.mark.integration
@pytest.mark.parametrize("pending_state", ["live", "expired_lease", "cancelled", "missing_marker"])
def test_public_queue_accepts_only_known_live_pending_work_and_never_releases_its_hold(
    readiness, pending_state
):
    from test_public_delegations import public_active

    from polymath.jobs import JobRepository
    from polymath.models import ResearchRun

    public = public_active(readiness)
    call = public_call(public)
    grant = call.dispatcher.prepare(call.lease, "model-0001", call.request, call.step)
    assert grant is not None
    before = BudgetService(public.engine).usage(call.value.account_id)
    if pending_state == "cancelled":
        JobRepository(public.engine).request_cancel(call.run_id)
    elif pending_state in {"expired_lease", "missing_marker"}:
        change = (
            {"lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
            if pending_state == "expired_lease"
            else {"external_request_in_flight": False}
        )
        with Session(public.engine) as session, session.begin():
            session.execute(
                update(ResearchRun).where(ResearchRun.id == call.run_id).values(**change)
            )
    second = public_visitor(public, peer="198.51.100.11")
    if pending_state == "live":
        run_id, _ = enqueue_public(public, visitor=second)
        assert JobRepository(public.engine).get(run_id).status == "queued"
    else:
        with pytest.raises(MetaResearchError, match="meta_research_prior_unresolved"):
            enqueue_public(public, visitor=second)
    after = BudgetService(public.engine).usage(call.value.account_id)
    assert after.committed_microdollars == before.committed_microdollars
    assert after.in_flight == before.in_flight == 1
    assert after.reservations == before.reservations == 2
    with Session(public.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicResearchRunAccess)) == (
            2 if pending_state == "live" else 1
        )


@pytest.mark.integration
def test_private_audience_replacement_fences_public_work_without_another_wallet(readiness):
    from test_meta_probes import attestation_data
    from test_meta_research import review_data
    from test_public_delegations import public_active

    from polymath.jobs import JobRepository
    from polymath.public_research_quota import public_monetary_usage

    public = public_active(readiness, short_issuer=False)
    call = public_call(public)
    grant = call.dispatcher.prepare(call.lease, "model-0001", call.request, call.step)
    call.dispatcher.complete(grant, research_observation(call.value, call.request))
    JobRepository(public.engine).interrupt(call.lease)
    preview = public.parent.preview(
        public.request,
        public.saved.id,
        public.canary.id,
        review_data(),
        attestation_data(public.proposal.model_id),
    )
    private = public.parent.activate(public.request, preview.manifest, preview.manifest_sha256)
    assert private.to_response()["audience"] == "operator_browser"
    second = public_visitor(public, peer="198.51.100.12")
    with pytest.raises(MetaResearchError):
        enqueue_public(public, visitor=second)
    access = public.parent.public_service.access(second.request)
    assert access["audience"] is None and not access["available"]
    assert access["expected_activation_sha256"] is None
    with Session(public.engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetAccount)) == 1
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 2
        usage = public_monetary_usage(
            session, account_id=call.value.account_id, requester_hash=call.visitor.owner_hash
        )
        assert usage.public_total_microdollars == usage.session_total_microdollars == 203
    assert BudgetService(public.engine).usage(call.value.account_id).committed_microdollars == 406


@pytest.mark.integration
def test_restored_interrupted_external_marker_without_journal_closes_public_access(readiness):
    from test_public_delegations import public_active

    from polymath.jobs import JobRepository
    from polymath.models import ResearchExternalRequest, ResearchRun

    public = public_active(readiness)
    call = public_call(public)
    JobRepository(public.engine).interrupt(call.lease)
    with Session(public.engine) as session, session.begin():
        assert session.scalar(select(func.count()).select_from(ResearchExternalRequest)) == 1
        assert session.get(ResearchExternalRequest, (call.run_id, "model-0001")) is None
        # Simulate an incomplete restored control record, not a new model send.
        session.execute(
            update(ResearchRun)
            .where(ResearchRun.id == call.run_id)
            .values(
                external_request_in_flight=True,
                external_request_id="model-0001",
            )
        )
        before = session.scalar(select(func.count()).select_from(BudgetReservation))
    access = public.parent.public_service.access(call.visitor.request)
    assert not access["available"] and access["reason"] == "public_research_prior_unresolved"
    second = public_visitor(public, peer="198.51.100.13")
    with pytest.raises(MetaResearchError, match="meta_research_prior_unresolved"):
        enqueue_public(public, visitor=second)
    with Session(public.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicResearchRunAccess)) == 1
        assert session.scalar(select(func.count()).select_from(NativeResearchCallRequest)) == 0
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == before == 1
    usage = BudgetService(public.engine).usage(call.value.account_id)
    assert usage.in_flight == 0 and usage.committed_microdollars == 203
