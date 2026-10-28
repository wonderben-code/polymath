"""Native scope cannot escape its dedicated dispatch path; all calls are synthetic."""

from dataclasses import replace

import pytest
from sqlalchemy.orm import Session
from test_budget import ledger as ledger_fixture
from test_budget import setup_account

from polymath.budget import BudgetError, NativeProbePermit
from polymath.budget_models import BudgetAccount
from polymath.dispatch import DispatchCoordinator
from polymath.jobs import JobRepository, StateConflict
from polymath.models import ResearchRun

pytestmark = pytest.mark.integration
ledger = ledger_fixture


def native_run(ledger):
    service, engine, _, _ = ledger
    service.create_account("native", hard_cap_microdollars=1000, daily_cap_microdollars=1000)
    with Session(engine) as session, session.begin():
        account = session.get(BudgetAccount, "native")
        account.native_scope = "probe_only"
        account.native_manifest_sha256 = "a" * 64
        account.native_control_epoch = 7
        run = ResearchRun(mode="explore", goal="Synthetic protocol check", task_kind="verify_meta")
        session.add(run)
        session.flush()
        run_id = run.id
    service.configure_run("native", run_id, cap_microdollars=50, max_calls=1)
    return service, run_id, NativeProbePermit("native", run_id, "a" * 64, 7, "b" * 64)


def test_probe_permit_bypasses_only_general_flags(ledger):
    service, run_id, permit = native_run(ledger)
    with Session(service.engine) as session, session.begin():
        reservation = service.reserve_in_session(
            session,
            "native",
            run_id,
            call_key="probe",
            estimated_microdollars=50,
            native_permit=permit,
        )
        assert service.claim_dispatch_in_session(session, reservation.id, native_permit=permit)
        assert not service.claim_dispatch_in_session(session, reservation.id, native_permit=permit)
    state = service.usage("native")
    assert not state.execution_enabled and not state.pricing_verified
    assert state.in_flight == 1 and state.committed_microdollars == 50
    with pytest.raises(BudgetError, match="native_dispatch_scope_required"):
        service.claim_dispatch(reservation.id)


@pytest.mark.parametrize("flag", ["kill_switch", "frozen"])
def test_probe_permit_never_bypasses_global_stop(ledger, flag):
    service, run_id, permit = native_run(ledger)
    with Session(service.engine) as session, session.begin():
        setattr(session.get(BudgetAccount, "native"), flag, True)
    with Session(service.engine) as session, session.begin():
        with pytest.raises(BudgetError, match="operator_kill_switch|account_frozen"):
            service.reserve_in_session(
                session,
                "native",
                run_id,
                call_key="probe",
                estimated_microdollars=50,
                native_permit=permit,
            )
    assert service.usage("native").reservations == 0


@pytest.mark.parametrize(
    "change",
    [
        {"account_id": "other"},
        {"manifest_sha256": "c" * 64},
        {"armed_epoch": 6},
        {"request_hash": "invalid"},
    ],
)
def test_probe_permit_is_bound_to_exact_account_manifest_and_generation(ledger, change):
    service, run_id, permit = native_run(ledger)
    with Session(service.engine) as session, session.begin():
        with pytest.raises(BudgetError, match="native_dispatch_scope_mismatch"):
            service.reserve_in_session(
                session,
                "native",
                run_id,
                call_key="probe",
                estimated_microdollars=50,
                native_permit=replace(permit, **change),
            )
    assert service.usage("native").reservations == 0


def test_native_generic_flags_and_legacy_activation_cannot_escape_scope(ledger):
    service, run_id, _ = native_run(ledger)
    with pytest.raises(BudgetError, match="native_dispatch_scope_required"):
        service.authorize_execution("native", pricing_reference="synthetic")
    with pytest.raises(BudgetError, match="native_dispatch_scope_required"):
        service.set_kill_switch("native", active=False)
    with Session(service.engine) as session, session.begin():
        account = session.get(BudgetAccount, "native")
        account.execution_enabled = account.pricing_verified = True
        account.pricing_reference = "synthetic"
    with pytest.raises(BudgetError, match="native_dispatch_scope_required"):
        service.reserve("native", run_id, call_key="generic", estimated_microdollars=50)
    jobs = JobRepository(service.engine)
    lease = jobs.claim_next("explicit-native-test", task_kinds=("verify_meta",))
    with pytest.raises(BudgetError, match="native_dispatch_scope_required"):
        DispatchCoordinator(service.engine).prepare_model_call(
            lease,
            account_id="native",
            request_id="model-0001",
            request_hash="b" * 64,
            estimated_microdollars=50,
        )
    assert service.usage("native").reservations == 0


def test_unscoped_verify_task_cannot_use_ordinary_budget(ledger):
    service, run_id = setup_account(ledger)
    with Session(service.engine) as session, session.begin():
        session.get(ResearchRun, run_id).task_kind = "verify_meta"
    with pytest.raises(BudgetError, match="native_dispatch_scope_required"):
        service.reserve("meta", run_id, call_key="generic", estimated_microdollars=50)


def test_each_internal_stop_advances_even_noop_and_fences_existing_permit(ledger):
    service, run_id, permit = native_run(ledger)
    service.disable_execution("native")
    service.disable_execution("native")
    service.set_kill_switch("native", active=True)
    service.set_kill_switch("native", active=True)
    with Session(service.engine) as session:
        assert session.get(BudgetAccount, "native").native_control_epoch == 11
    with Session(service.engine) as session, session.begin():
        with pytest.raises(BudgetError, match="native_dispatch_scope_mismatch"):
            service.reserve_in_session(
                session,
                "native",
                run_id,
                call_key="probe",
                estimated_microdollars=50,
                native_permit=permit,
            )


def test_generic_queue_cannot_create_claim_cancel_or_resume_probe(ledger):
    service, run_id, _ = native_run(ledger)
    jobs = JobRepository(service.engine)
    with pytest.raises(ValueError, match="Unsupported task kind"):
        jobs.create(mode="explore", goal="synthetic", task_kind="verify_meta")
    assert jobs.claim_next("ordinary-worker") is None
    with pytest.raises(StateConflict, match="operator control"):
        jobs.request_cancel(run_id)
    with pytest.raises(StateConflict, match="free recorded finalization"):
        jobs.resume_interrupted(run_id, expected_token=0)
    assert jobs.get(run_id).status == "queued"
