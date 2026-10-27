"""Real PostgreSQL coordination tests. All prices/responses are synthetic; no model calls."""

import hashlib
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import polymath.dispatch as dispatch_module
from polymath.budget import BudgetError, BudgetService
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.dispatch import DispatchCoordinator, DispatchError
from polymath.jobs import ExternalRequestPending, JobRepository, LeaseLost, StateConflict
from polymath.models import ResearchExternalRequest

pytestmark = pytest.mark.integration
REQUEST_HASH = hashlib.sha256(b"synthetic immutable provider request v1").hexdigest()
OBSERVATION = {"content": "synthetic fixture response", "input_tokens": 3, "output_tokens": 2}


@pytest.fixture
def dispatch(monkeypatch):
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set TEST_DATABASE_URL for real PostgreSQL coordination checks")
    url = make_url(value)
    assert url.drivername == "postgresql+psycopg"
    schema = "polymath_dispatch_" + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_engine(scoped, hide_parameters=True)
    try:
        command.upgrade(config, "head")
        jobs, budget = JobRepository(engine), BudgetService(engine)
        budget.create_account(
            "meta", hard_cap_microdollars=1000, daily_cap_microdollars=1000, max_in_flight=8
        )
        budget.authorize_execution("meta", pricing_reference="synthetic-fixture-pricing")
        yield jobs, budget, DispatchCoordinator(engine), engine, config
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def run(dispatch):
    jobs, budget, _, _, _ = dispatch
    item = jobs.create(mode="explore", goal="Synthetic dispatch transaction exercise")
    budget.configure_run("meta", item.id, cap_microdollars=500, max_calls=8)
    lease = jobs.claim_next("worker-1")
    assert lease is not None and lease.run_id == item.id
    return lease


def grant(dispatch, lease, request_id="model-1"):
    return dispatch[2].prepare_model_call(
        lease,
        account_id="meta",
        request_id=request_id,
        request_hash=REQUEST_HASH,
        estimated_microdollars=50,
    )


def complete(dispatch, item, **overrides):
    fields = {
        "observation": OBSERVATION,
        "actual_microdollars": 10,
        "settlement_key": "synthetic-response-usage",
        "prompt_tokens": 3,
        "completion_tokens": 2,
    }
    fields.update(overrides)
    return dispatch[2].complete(item, **fields)


def expire(dispatch, lease):
    with dispatch[3].begin() as connection:
        connection.execute(
            text(
                "UPDATE research_runs "
                "SET lease_expires_at = clock_timestamp() - interval '1 second' "
                "WHERE id = :id"
            ),
            {"id": lease.run_id},
        )


def test_grant_commits_budget_binding_and_pending_request_atomically(dispatch):
    jobs, budget, coordinator, engine, _ = dispatch
    lease = run(dispatch)
    assert (
        coordinator.get_recorded_response(lease, request_id="model-1", request_hash=REQUEST_HASH)
        is None
    )
    item = grant(dispatch, lease)
    assert item is not None
    assert jobs.get(lease.run_id).external_request_in_flight
    with Session(engine) as session:
        row = session.get(ResearchExternalRequest, (lease.run_id, "model-1"))
        reservation = session.get(BudgetReservation, item.reservation_id)
        assert row.reservation_id == reservation.id
        assert row.dispatch_lease_token == lease.token and row.request_hash == REQUEST_HASH
        assert reservation.status == "in_flight"
    assert budget.usage("meta").committed_microdollars == 50
    assert jobs.get(lease.run_id).checkpoint_version == 0
    with pytest.raises(DispatchError, match="outcome_unknown"):
        coordinator.get_recorded_response(lease, request_id="model-1", request_hash=REQUEST_HASH)


@pytest.mark.parametrize("block", ["cancel", "expired", "disabled", "kill"])
def test_blocked_grant_cannot_leave_half_recorded_work(dispatch, block):
    jobs, budget, _, engine, _ = dispatch
    lease = run(dispatch)
    expected = LeaseLost if block in {"cancel", "expired"} else BudgetError
    if block == "cancel":
        jobs.request_cancel(lease.run_id)
    elif block == "expired":
        expire(dispatch, lease)
    elif block == "disabled":
        budget.disable_execution("meta")
    else:
        budget.set_kill_switch("meta", active=True)
    with pytest.raises(expected):
        grant(dispatch, lease)
    assert budget.usage("meta").reservations == 0
    assert not jobs.get(lease.run_id).external_request_in_flight
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchExternalRequest)) == 0


def test_failure_after_reservation_rolls_back_every_part(dispatch, monkeypatch):
    jobs, budget, _, _, _ = dispatch
    lease = run(dispatch)

    def storage_failure(*args, **kwargs):
        raise RuntimeError("Synthetic storage failure before transaction commit")

    monkeypatch.setattr(dispatch_module, "_event", storage_failure)
    with pytest.raises(RuntimeError):
        grant(dispatch, lease)
    assert budget.usage("meta").reservations == 0
    assert not jobs.get(lease.run_id).external_request_in_flight
    assert len(jobs.events_after(lease.run_id)) == 2


def test_lease_is_rechecked_after_waiting_for_account_lock(dispatch, monkeypatch):
    jobs, budget, coordinator, engine, _ = dispatch
    lease = run(dispatch)
    waiting = Event()
    pid = []
    original = coordinator.budget._account

    def capture_connection(session, account_id):
        pid.append(session.scalar(select(func.pg_backend_pid())))
        waiting.set()
        return original(session, account_id)

    monkeypatch.setattr(coordinator.budget, "_account", capture_connection)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(engine) as holder, holder.begin():
            holder.scalar(select(BudgetAccount).where(BudgetAccount.id == "meta").with_for_update())
            future = pool.submit(grant, dispatch, lease)
            assert waiting.wait(timeout=5)
            deadline = time.monotonic() + 5
            locked = False
            while time.monotonic() < deadline:
                with engine.connect() as connection:
                    locked = connection.scalar(
                        text(
                            "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"
                        ),
                        {"pid": pid[0]},
                    )
                if locked:
                    break
                time.sleep(0.01)
            assert locked, "Preparation must be blocked on the account lock"
            expire(dispatch, lease)
        with pytest.raises(LeaseLost):
            future.result(timeout=10)
    assert budget.usage("meta").reservations == 0
    assert not jobs.get(lease.run_id).external_request_in_flight


def test_concurrent_retry_grants_exactly_once_and_hash_conflicts_are_rejected(dispatch):
    _, budget, coordinator, _, _ = dispatch
    lease = run(dispatch)
    barrier = Barrier(8)

    def attempt(_):
        barrier.wait(timeout=10)
        return grant(dispatch, lease)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    winners = [value for value in results if value is not None]
    assert len(winners) == 1
    assert budget.usage("meta").reservations == 1
    complete(dispatch, winners[0])
    assert grant(dispatch, lease) is None
    assert (
        coordinator.get_recorded_response(lease, request_id="model-1", request_hash=REQUEST_HASH)
        == OBSERVATION
    )
    with pytest.raises(DispatchError, match="identity_conflict"):
        coordinator.prepare_model_call(
            lease,
            account_id="meta",
            request_id="model-1",
            request_hash="a" * 64,
            estimated_microdollars=50,
        )


def test_same_request_id_in_distinct_runs_has_independent_budget_identity(dispatch):
    first = run(dispatch)
    first_grant = grant(dispatch, first)
    second = run(dispatch)
    second_grant = grant(dispatch, second)
    assert first_grant is not None and second_grant is not None
    assert first_grant.reservation_id != second_grant.reservation_id
    assert dispatch[1].usage("meta").reservations == 2


def test_cancel_race_has_one_dispatch_authorization_point(dispatch):
    jobs, budget, _, _, _ = dispatch
    lease = run(dispatch)
    barrier = Barrier(2)

    def prepare():
        barrier.wait(timeout=10)
        try:
            return grant(dispatch, lease)
        except LeaseLost:
            return None

    def cancel():
        barrier.wait(timeout=10)
        return jobs.request_cancel(lease.run_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(prepare)
        cancelled = pool.submit(cancel)
        item = future.result(timeout=15)
        cancelled.result(timeout=15)
    assert budget.usage("meta").reservations == (1 if item else 0)
    with pytest.raises(LeaseLost):
        grant(dispatch, lease, "model-2")
    if item is not None:
        complete(dispatch, item)
        assert budget.usage("meta").committed_microdollars == 10
        snapshot = jobs.get(lease.run_id)
        assert snapshot.status == "cancel_requested"
        assert snapshot.checkpoint_version == 0 and snapshot.outcome is None
        assert not snapshot.external_request_in_flight


def test_expired_worker_response_is_accounted_and_replayed_without_stale_checkpoint(dispatch):
    jobs, budget, coordinator, _, _ = dispatch
    lease = run(dispatch)
    item = grant(dispatch, lease)
    expire(dispatch, lease)
    assert jobs.interrupt_expired() == [lease.run_id]
    complete(dispatch, item)
    snapshot = jobs.get(lease.run_id)
    assert snapshot.status == "interrupted" and snapshot.checkpoint == {}
    assert budget.usage("meta").committed_microdollars == 10
    with pytest.raises(LeaseLost):
        coordinator.get_recorded_response(lease, request_id="model-1", request_hash=REQUEST_HASH)
    jobs.resume_interrupted(lease.run_id, expected_token=snapshot.lease_token)
    new_lease = jobs.claim_next("worker-2")
    assert new_lease is not None
    assert (
        coordinator.get_recorded_response(
            new_lease, request_id="model-1", request_hash=REQUEST_HASH
        )
        == OBSERVATION
    )
    assert grant(dispatch, new_lease) is None
    assert grant(dispatch, new_lease, "model-2") is not None


def test_ambiguous_call_retains_hold_and_legacy_recovery_cannot_bypass_accounting(dispatch):
    jobs, budget, coordinator, _, _ = dispatch
    lease = run(dispatch)
    item = grant(dispatch, lease)
    coordinator.mark_unknown(item, reason="timeout")
    assert budget.usage("meta").committed_microdollars == 50
    assert budget.usage("meta").in_flight == 1
    with pytest.raises(StateConflict, match="coordinated completion"):
        jobs.complete_external_request(lease, request_id="model-1", observation=OBSERVATION)
    with pytest.raises(ExternalRequestPending):
        jobs.finish(lease, status="succeeded", outcome={})
    snapshot = jobs.interrupt(lease)
    with pytest.raises(StateConflict, match="coordinated reconciliation"):
        jobs.reconcile_external(
            lease.run_id,
            expected_token=snapshot.lease_token,
            request_id="model-1",
            outcome="not_sent",
            summary="Unverified synthetic operator claim",
        )
    with pytest.raises(ExternalRequestPending):
        jobs.resume_interrupted(lease.run_id, expected_token=snapshot.lease_token)
    complete(dispatch, item)
    assert budget.usage("meta").committed_microdollars == 10
    assert coordinator.mark_unknown(item, reason="worker_lost").status == "settled"


def test_unknown_usage_records_response_without_inventing_free_cost(dispatch):
    jobs, budget, coordinator, _, _ = dispatch
    lease = run(dispatch)
    item = grant(dispatch, lease)
    unknown = coordinator.complete(item, observation=OBSERVATION, actual_microdollars=None)
    assert unknown.status == "unknown"
    assert budget.usage("meta").committed_microdollars == 50
    assert not jobs.get(lease.run_id).external_request_in_flight
    assert (
        coordinator.get_recorded_response(lease, request_id="model-1", request_hash=REQUEST_HASH)
        == OBSERVATION
    )
    complete(dispatch, item)
    assert budget.usage("meta").committed_microdollars == 10
    with pytest.raises(DispatchError, match="response_conflict"):
        complete(dispatch, item, observation={"content": "Different synthetic response"})


def test_explicit_reconciliation_does_not_enable_historical_redispatch(dispatch):
    jobs, budget, coordinator, _, _ = dispatch
    lease = run(dispatch)
    item = grant(dispatch, lease)
    coordinator.mark_unknown(item, reason="worker_lost")
    snapshot = jobs.interrupt(lease)
    coordinator.reconcile(
        item,
        observation=None,
        actual_microdollars=0,
        evidence_key="synthetic-confirmed-not-sent-evidence",
    )
    assert budget.usage("meta").committed_microdollars == 0
    jobs.resume_interrupted(lease.run_id, expected_token=snapshot.lease_token)
    new_lease = jobs.claim_next("worker-2")
    assert grant(dispatch, new_lease) is None
    with pytest.raises(DispatchError, match="response_unavailable"):
        coordinator.get_recorded_response(
            new_lease, request_id="model-1", request_hash=REQUEST_HASH
        )
    assert grant(dispatch, new_lease, "model-2") is not None
    with pytest.raises(DispatchError, match="reconciliation_conflict"):
        complete(dispatch, item)
    assert budget.usage("meta").frozen
    with Session(dispatch[3]) as session:
        request = session.get(ResearchExternalRequest, (item.run_id, item.request_id))
        assert request.late_observation == OBSERVATION
        assert request.late_usage["actual_microdollars"] == 10
    # A late old completion/timeout must not clear the newer operation's marker.
    coordinator.mark_unknown(item, reason="worker_lost")
    assert jobs.get(lease.run_id).external_request_id == "model-2"
    assert jobs.get(lease.run_id).external_request_in_flight


def test_binding_mismatch_and_invalid_storage_cannot_settle_or_clear_marker(dispatch):
    jobs, budget, coordinator, _, _ = dispatch
    lease = run(dispatch)
    item = grant(dispatch, lease)
    with pytest.raises(DispatchError, match="binding_mismatch"):
        complete(dispatch, replace(item, request_hash="0" * 64))
    with pytest.raises(ValueError, match="256 KiB"):
        complete(dispatch, item, observation={"content": "x" * 262144})
    assert budget.usage("meta").committed_microdollars == 50
    assert jobs.get(lease.run_id).external_request_in_flight


def test_hard_crash_before_mark_unknown_can_be_explicitly_reconciled(dispatch):
    jobs, budget, coordinator, _, _ = dispatch
    lease = run(dispatch)
    original = grant(dispatch, lease)
    expire(dispatch, lease)
    assert jobs.interrupt_expired() == [lease.run_id]
    recovered = coordinator.get_recorded_grant(lease.run_id, request_id="model-1")
    assert recovered == original  # Metadata is not a new dispatch authorization.
    coordinator.reconcile(
        recovered,
        observation=OBSERVATION,
        actual_microdollars=10,
        evidence_key="synthetic-billing-response-after-hard-crash",
        prompt_tokens=3,
        completion_tokens=2,
    )
    assert budget.usage("meta").committed_microdollars == 10
    snapshot = jobs.get(lease.run_id)
    assert not snapshot.external_request_in_flight and snapshot.status == "interrupted"
    jobs.resume_interrupted(lease.run_id, expected_token=snapshot.lease_token)
    new_lease = jobs.claim_next("worker-2")
    assert (
        coordinator.get_recorded_response(
            new_lease, request_id="model-1", request_hash=REQUEST_HASH
        )
        == OBSERVATION
    )
    assert grant(dispatch, new_lease) is None


def test_dispatch_binding_migration_is_reversible_and_matches_metadata(dispatch):
    _, _, _, engine, config = dispatch
    command.check(config)
    command.downgrade(config, "0003_budget_controls")
    assert "reservation_id" not in {
        column["name"] for column in inspect(engine).get_columns("research_external_requests")
    }
    command.upgrade(config, "head")
    command.check(config)
