"""Native activation/admission use the same real ledger and synthetic canary facts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_meta_probes import arm, attestation_data, observation
from test_meta_readiness import READINESS
from test_meta_readiness import readiness as readiness_fixture
from test_operator_auth import POLICY
from test_worker import database as database_fixture

from polymath.access_models import ResearchRunAccess
from polymath.budget import BudgetError, BudgetService
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.dispatch import DispatchCoordinator
from polymath.jobs import JobRepository
from polymath.meta_research import (
    MetaResearchError,
    MetaResearchStore,
    native_run_controls,
    native_run_input_sha256,
    native_run_outcome_unknown,
)
from polymath.meta_research_contracts import (
    NativeResearchPolicy,
    build_meta_research_request,
    research_step_sha256,
)
from polymath.meta_research_dispatch import MetaResearchDispatcher
from polymath.meta_research_models import MetaResearchManifestRecord
from polymath.models import ResearchRun
from polymath.run_api import QUEUE_LOCK_KEY

pytestmark = pytest.mark.integration
database = database_fixture
readiness = readiness_fixture


def review_data(**changes):
    now = datetime.now(UTC)
    return {
        "model_wire_usage_confirmed": True,
        "pricing_bounds_confirmed": True,
        "funding_cap_confirmed": True,
        "observed_at": (now - timedelta(seconds=1)).isoformat(),
        "valid_until": (now + timedelta(minutes=20)).isoformat(),
        "evidence": [
            {"url": "https://example.org/synthetic-current-review", "content_sha256": "f" * 64}
        ],
        **changes,
    }


def active_research(readiness):
    engine, probe, dispatcher, request, proposal, saved, _, job, lease = arm(readiness)
    original = probe.load_input(lease)
    grant = dispatcher.prepare(lease)
    dispatcher.complete(grant, observation(original))
    canary = probe.finalize_known(job.id)
    policy = NativeResearchPolicy(
        paid_research_enabled=True,
        native_account_id=proposal.account.account_id,
        model_id=proposal.model_id,
        api_key=probe.probe_policy.api_key,
    )
    store = MetaResearchStore(engine, policy, READINESS, POLICY)
    preview = store.preview(
        request, saved.id, canary.id, review_data(), attestation_data(proposal.model_id)
    )
    activation = store.activate(request, preview.manifest, preview.manifest_sha256)
    return SimpleNamespace(
        engine=engine,
        store=store,
        dispatcher=MetaResearchDispatcher(engine, policy, READINESS, POLICY),
        request=request,
        policy=policy,
        readiness_policy=READINESS,
        operator_policy=POLICY,
        proposal=proposal,
        saved=saved,
        canary=canary,
        preview=preview,
        activation=activation,
        activation_sha256=preview.manifest_sha256,
    )


def enqueue_native(active, *, goal="Synthetic native scientific goal", mode="explore"):
    with Session(active.engine) as session, session.begin():
        admission = active.store.begin_admission(session, active.request, active.activation_sha256)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK_KEY})
        active.store.lock_account_in_session(session, admission)
        run = ResearchRun(mode=mode, task_kind="research", goal=goal)
        session.add(run)
        session.flush()
        session.add(ResearchRunAccess(run_id=run.id, session_hash=admission.browser_session_hash))
        session.flush()
        active.store.bind_run_in_session(
            session, admission, run, native_run_input_sha256(session, run)
        )
        return run.id


def native_call(active, mode="explore"):
    run_id = enqueue_native(active, mode=mode)
    lease = JobRepository(active.engine).claim_next("native-synthetic-worker", native_only=True)
    assert lease is not None and lease.run_id == run_id
    value = active.store.load_original_input(lease)
    request = build_meta_research_request(
        value.proposal,
        [{"role": "user", "content": "Synthetic native test"}],
        max_output_tokens=value.profile.max_output_tokens,
    )
    step = research_step_sha256(
        request,
        activation_sha256=value.activation_sha256,
        profile_sha256=value.profile_sha256,
        request_id="model-0001",
    )
    return run_id, lease, value, request, step


def research_observation(value, request, **changes):
    from polymath.meta_research_contracts import meta_research_request_sha256

    basis = SimpleNamespace(
        proposal=value.proposal,
        manifest=SimpleNamespace(request_sha256=meta_research_request_sha256(request)),
    )
    return observation(basis, **changes)


def test_activation_preserves_ledger_and_generic_flags_and_is_exact_idempotent(readiness):
    active = active_research(readiness)
    assert (
        active.store.activate(active.request, active.preview.manifest, active.activation_sha256).id
        == active.activation.id
    )
    current = active.store.current(active.request)
    assert current["activation"]["manifest_sha256"] == active.activation_sha256
    assert {x["task_kind"] for x in current["activation"]["profiles"]} == {
        "research",
        "formalise",
        "write_summary",
        "write_paper",
        "reassess_finding",
    }
    with Session(active.engine) as session:
        account = session.get(BudgetAccount, active.policy.native_account_id)
        assert account.native_scope == "research" and account.native_control_epoch == 2
        assert (
            not account.execution_enabled
            and not account.pricing_verified
            and not account.kill_switch
        )
        assert session.scalar(select(func.count()).select_from(BudgetAccount)) == 1
        assert session.scalar(select(BudgetReservation.actual_microdollars)) == 203


@pytest.mark.parametrize("mode", ["explore", "ask", "surprise"])
def test_owned_admission_single_send_exact_late_usage_and_free_recovery(readiness, mode):
    active = active_research(readiness)
    run_id, lease, value, request, step = native_call(active, mode)
    assert JobRepository(active.engine).claim_next("legacy") is None
    grant = active.dispatcher.prepare(lease, "model-0001", request, step)
    assert grant.api_key == active.policy.api_key and "api_key" not in repr(grant)
    assert active.dispatcher.prepare(lease, "model-0001", request, step) is None
    with pytest.raises(BudgetError):
        DispatchCoordinator(active.engine).prepare_model_call(
            lease,
            account_id=value.account_id,
            request_id="forged",
            request_hash="a" * 64,
            estimated_microdollars=1,
        )
    observed = research_observation(value, request)
    active.store.revoke(active.request, active.activation.id)
    active.dispatcher.complete(grant, observed)
    assert active.dispatcher.get_recorded_observation(lease, "model-0001", step) == observed
    receipt = (
        MetaResearchStore(active.engine).finalize_known_call(run_id, "model-0001").to_response()
    )
    assert receipt["usage"]["actual_microdollars"] == 203
    assert receipt["usage"]["input_tokens"] == "101"
    assert receipt["scientific_completion_implied"] is False
    assert "request" not in receipt and "content" not in receipt
    assert active.store.current(active.request)["activation"] is None


def test_revoke_retry_cannot_rearm_and_new_fresh_call_is_denied(readiness):
    active = active_research(readiness)
    _, lease, _, request, step = native_call(active)
    active.store.revoke(active.request, active.activation.id)
    with Session(active.engine) as session:
        epoch = session.get(BudgetAccount, active.policy.native_account_id).native_control_epoch
    old = active.store.activate(active.request, active.preview.manifest, active.activation_sha256)
    assert not old.to_response()["research_execution_available"]
    with Session(active.engine) as session:
        assert (
            session.get(BudgetAccount, active.policy.native_account_id).native_control_epoch
            == epoch
        )
    with pytest.raises(MetaResearchError):
        active.dispatcher.prepare(lease, "model-0001", request, step)


def test_unknown_hold_blocks_native_fresh_calls_and_new_activation(readiness):
    active = active_research(readiness)
    run_id, lease, _, request, step = native_call(active)
    grant = active.dispatcher.prepare(lease, "model-0001", request, step)
    active.dispatcher.mark_unknown(grant, "timeout")
    with pytest.raises(MetaResearchError, match="meta_research_outcome_unknown"):
        active.dispatcher.get_recorded_observation(lease, "model-0001", step)
    with pytest.raises(MetaResearchError, match="meta_research_prior_unresolved"):
        active.store.preview(
            active.request, active.saved.id, active.canary.id, review_data(), attestation_data()
        )
    with pytest.raises(MetaResearchError, match="meta_research_outcome_unknown"):
        MetaResearchStore(active.engine).finalize_known_call(run_id, "model-0001")


def test_immutable_intent_and_original_profile_binding_controls_resume(readiness):
    active = active_research(readiness)
    run_id, lease, value, _, _ = native_call(active)
    JobRepository(active.engine).interrupt(lease)
    with Session(active.engine) as session:
        run = session.get(ResearchRun, run_id)
        controls = native_run_controls(session, run)
        assert controls["can_resume"] and controls["expected_lease_token"] == str(run.lease_token)
        token = run.lease_token
    with Session(active.engine) as session, session.begin():
        active.store.resume_in_session(
            session, active.request, run_id, token, value.activation_sha256
        )
    lease = JobRepository(active.engine).claim_next("native-resume", native_only=True)
    assert active.store.load_original_input(lease).profile_sha256 == value.profile_sha256
    with Session(active.engine) as session, session.begin():
        session.execute(update(ResearchRun).where(ResearchRun.id == run_id).values(goal="edited"))
    with pytest.raises(MetaResearchError, match="meta_research_input_changed"):
        active.store.load_original_input(lease)


def test_foreign_browser_current_is_null_and_cannot_admit(readiness):
    from fastapi.testclient import TestClient
    from test_meta_readiness import make_request
    from test_operator_auth import login

    active = active_research(readiness)
    _, _, _, client, _ = readiness
    with TestClient(client.app) as foreign:
        headers, _ = login(foreign)
        request = make_request(foreign, headers)
        current = active.store.current(request)
        assert current == {"activation": None, "reason": "meta_research_activation_required"}
        with Session(active.engine) as session, session.begin():
            with pytest.raises(MetaResearchError, match="meta_research_activation_required"):
                active.store.begin_admission(session, request, active.activation_sha256)


def test_old_profile_and_known_usage_survive_current_prompt_protocol_change(readiness, monkeypatch):
    import polymath.meta_research_contracts as contracts

    active = active_research(readiness)
    run_id, lease, value, request, step = native_call(active)
    grant = active.dispatcher.prepare(lease, "model-0001", request, step)
    observed = research_observation(value, request)
    active.dispatcher.complete(grant, observed)
    original = contracts.controller_protocol_sha256
    monkeypatch.setattr(contracts, "controller_protocol_sha256", lambda task: "f" * 64)
    assert active.store.load_original_input(lease).profile == value.profile
    assert (
        MetaResearchStore(active.engine)
        .finalize_known_call(run_id, "model-0001")
        .to_response()["usage"]["actual_microdollars"]
        == 203
    )
    assert active.dispatcher.get_recorded_observation(lease, "model-0001", step) == observed
    with pytest.raises(MetaResearchError):
        active.dispatcher.prepare(
            lease,
            "model-0002",
            request,
            research_step_sha256(
                request,
                activation_sha256=value.activation_sha256,
                profile_sha256=value.profile_sha256,
                request_id="model-0002",
            ),
        )
    monkeypatch.setattr(contracts, "controller_protocol_sha256", original)


def test_cancelled_prepared_inflight_request_blocks_another_native_run(readiness):
    active = active_research(readiness)
    run_id, lease, _, request, step = native_call(active)
    active.dispatcher.prepare(lease, "model-0001", request, step)
    JobRepository(active.engine).request_cancel(run_id)
    JobRepository(active.engine).finalize_cancellation(run_id)
    with pytest.raises(MetaResearchError, match="meta_research_prior_unresolved"):
        enqueue_native(active, goal="Must not bypass pending cost")
    with Session(active.engine) as session:
        rows = session.scalars(
            select(BudgetReservation).where(BudgetReservation.run_id == run_id)
        ).all()
        assert len(rows) == 1 and rows[0].status == "in_flight"


def test_fresh_price_review_reuses_canary_without_repricing_old_known_calls(readiness):
    from polymath.meta_readiness import MetaReadinessStore
    from polymath.meta_readiness_contracts import MetaReadinessProposal, proposal_sha256

    active = active_research(readiness)
    run_id, lease, value, request, step = native_call(active)
    grant = active.dispatcher.prepare(lease, "model-0001", request, step)
    observed = research_observation(value, request)
    active.dispatcher.complete(grant, observed)
    data = active.proposal.model_dump(mode="json")
    data["pricing"] |= {
        "input_microdollars_per_million": 2_000_000,
        "output_microdollars_per_million": 3_000_000,
    }
    proposal = MetaReadinessProposal.model_validate(data)
    saved = MetaReadinessStore(active.engine, READINESS, POLICY).save(
        active.request, proposal, proposal_sha256(proposal)
    )
    preview = active.store.preview(
        active.request,
        saved.id,
        active.canary.id,
        review_data(),
        attestation_data(proposal.model_id),
    )
    replacement = active.store.activate(active.request, preview.manifest, preview.manifest_sha256)
    assert replacement.id != active.activation.id
    assert active.dispatcher.get_recorded_observation(lease, "model-0001", step) == observed
    receipt = (
        MetaResearchStore(active.engine).finalize_known_call(run_id, "model-0001").to_response()
    )
    assert receipt["usage"]["actual_microdollars"] == 203
    with pytest.raises(MetaResearchError):
        active.dispatcher.prepare(
            lease,
            "model-0002",
            request,
            research_step_sha256(
                request,
                activation_sha256=value.activation_sha256,
                profile_sha256=value.profile_sha256,
                request_id="model-0002",
            ),
        )
    with Session(active.engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetAccount)) == 1
        assert session.scalar(select(func.sum(BudgetReservation.actual_microdollars))) == 406


def test_failed_canary_cannot_be_turned_into_a_research_activation(readiness):
    engine, probe, dispatcher, request, proposal, saved, _, job, lease = arm(readiness)
    value = probe.load_input(lease)
    grant = dispatcher.prepare(lease)
    dispatcher.complete(grant, observation(value, finish_reason="length"))
    receipt = probe.finalize_known(job.id)
    policy = NativeResearchPolicy(
        paid_research_enabled=True,
        native_account_id=proposal.account.account_id,
        model_id=proposal.model_id,
        api_key=probe.probe_policy.api_key,
    )
    store = MetaResearchStore(engine, policy, READINESS, POLICY)
    with pytest.raises(MetaResearchError, match="meta_research_account_conflict"):
        store.preview(
            request, saved.id, receipt.id, review_data(), attestation_data(proposal.model_id)
        )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(MetaResearchManifestRecord)) == 0


def test_native_history_immutability_downgrade_and_model_schema_match(readiness):
    active = active_research(readiness)
    run_id, lease, value, request, step = native_call(active)
    grant = active.dispatcher.prepare(lease, "model-0001", request, step)
    active.dispatcher.complete(grant, research_observation(value, request))
    MetaResearchStore(active.engine).finalize_known_call(run_id, "model-0001")
    active.store.revoke(active.request, active.activation.id)
    for table in (
        "meta_research_manifests",
        "meta_research_approvals",
        "meta_research_revocations",
        "meta_research_run_bindings",
        "meta_research_call_requests",
        "meta_research_call_receipts",
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            with active.engine.begin() as connection:
                connection.execute(text(f"DELETE FROM {table}"))
    with active.engine.connect() as connection:
        original_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with pytest.raises(DBAPIError, match="Cannot discard native research"):
        command.downgrade(config, "0020_meta_verification")
    with active.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == original_head
    command.check(config)
    BudgetService(active.engine).disable_execution(active.policy.native_account_id)


def test_current_reports_configuration_and_pending_cost_without_claiming_expiry(readiness):
    active = active_research(readiness)
    active.store.research_policy = replace(active.policy, paid_research_enabled=False)
    assert active.store.current(active.request) == {
        "activation": None,
        "reason": "meta_research_unavailable",
    }
    assert active.store.list_activations(active.request)[0].id == active.activation.id
    active.store.research_policy = active.policy
    _, lease, value, request, step = native_call(active)
    grant = active.dispatcher.prepare(lease, "model-0001", request, step)
    assert active.store.current(active.request) == {
        "activation": None,
        "reason": "meta_research_prior_unresolved",
    }
    active.dispatcher.complete(grant, research_observation(value, request))
    assert active.store.current(active.request)["activation"]["id"] == str(active.activation.id)


def test_observed_transport_with_unknown_billing_keeps_outcome_warning(readiness):
    active = active_research(readiness)
    run_id, lease, value, request, step = native_call(active)
    grant = active.dispatcher.prepare(lease, "model-0001", request, step)
    active.dispatcher.complete(
        grant, research_observation(value, request, input_tokens=None, output_tokens=None)
    )
    with Session(active.engine) as session:
        run = session.get(ResearchRun, run_id)
        assert not run.external_request_in_flight
        assert native_run_outcome_unknown(session, run) is True
        assert native_run_controls(session, run)["can_resume"] is False
    receipt = (
        MetaResearchStore(active.engine).finalize_known_call(run_id, "model-0001").to_response()
    )
    assert receipt["usage"]["status"] == "unknown"
    with Session(active.engine) as session:
        assert native_run_outcome_unknown(session, session.get(ResearchRun, run_id)) is True


def test_historical_accounting_does_not_reparse_canary_under_new_semantics(readiness, monkeypatch):
    import polymath.meta_probes as probes

    active = active_research(readiness)
    run_id, lease, value, request, step = native_call(active)
    grant = active.dispatcher.prepare(lease, "model-0001", request, step)

    def changed_canary_semantics(*args, **kwargs):
        raise RuntimeError("synthetic incompatible new canary semantics")

    monkeypatch.setattr(probes, "assess_probe_observation", changed_canary_semantics)
    # This late response and its original accounting cannot depend on the
    # current scientific/probe action parser or any current authority.
    active.dispatcher.complete(grant, research_observation(value, request))
    assert active.store.load_original_input(lease).profile_sha256 == value.profile_sha256
    receipt = (
        MetaResearchStore(active.engine).finalize_known_call(run_id, "model-0001").to_response()
    )
    assert receipt["usage"]["actual_microdollars"] == 203
    with pytest.raises(RuntimeError, match="incompatible new canary"):
        active.store.preview(
            active.request, active.saved.id, active.canary.id, review_data(), attestation_data()
        )
    with Session(active.engine) as session:
        assert session.scalar(select(func.count()).select_from(MetaResearchManifestRecord)) == 1
