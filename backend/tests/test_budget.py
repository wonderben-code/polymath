"""Real PostgreSQL budget races. No paid calls or provider requests occur here."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from polymath.budget import BudgetError, BudgetService
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.models import ResearchRun

pytestmark = pytest.mark.integration


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 27, 12, tzinfo=UTC)

    def __call__(self):
        return self.now


@pytest.fixture
def ledger(monkeypatch):
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL budget checks")
    url = make_url(value)
    assert url.drivername == "postgresql+psycopg"
    schema = "polymath_budget_test_" + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_engine(scoped, hide_parameters=True)
    clock = Clock()
    try:
        command.upgrade(config, "head")
        yield BudgetService(engine, clock=clock), engine, clock, config
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def setup_account(ledger, *, hard=1000, daily=1000, concurrency=10, run_cap=1000, calls=20):
    service, engine, _, _ = ledger
    with Session(engine) as session, session.begin():
        row = ResearchRun(mode="explore", goal="Budget test; no model calls")
        session.add(row)
        session.flush()
        run_id = row.id
    service.create_account(
        "meta",
        hard_cap_microdollars=hard,
        daily_cap_microdollars=daily,
        max_in_flight=concurrency,
    )
    service.authorize_execution("meta", pricing_reference="synthetic-fixture-pricing-v1")
    service.configure_run("meta", run_id, cap_microdollars=run_cap, max_calls=calls)
    return service, run_id


def reserve(service, run_id, key="call-1", amount=50):
    return service.reserve("meta", run_id, call_key=key, estimated_microdollars=amount)


def assert_code(code, function, *args, **kwargs):
    with pytest.raises(BudgetError) as caught:
        function(*args, **kwargs)
    assert caught.value.code == code


def test_migrations_match_metadata_and_are_reversible(ledger):
    _, engine, _, config = ledger
    command.upgrade(config, "head")
    command.check(config)
    assert {"budget_accounts", "budget_run_limits", "budget_reservations"}.issubset(
        inspect(engine).get_table_names()
    )
    command.downgrade(config, "0002_durable_jobs")
    assert "budget_accounts" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    command.check(config)


def test_account_defaults_disable_paid_execution_and_require_pricing_reference(ledger):
    service, engine, _, _ = ledger
    with Session(engine) as session, session.begin():
        row = ResearchRun(mode="ask", goal="Synthetic budget fixture")
        session.add(row)
        session.flush()
        run_id = row.id
    service.create_account("meta", hard_cap_microdollars=100, daily_cap_microdollars=100)
    service.configure_run("meta", run_id, cap_microdollars=100, max_calls=3)
    state = service.usage("meta")
    assert not state.execution_enabled and not state.pricing_verified
    assert_code("paid_execution_disabled", reserve, service, run_id)
    with pytest.raises(ValueError):
        service.authorize_execution("meta", pricing_reference="")
    assert service.usage("meta").reservations == 0


@pytest.mark.parametrize("bad", [True, False, 1.1, "100", -1, 9_000_000_000_000_001])
def test_money_is_bounded_nonnegative_integer_not_bool_or_float(ledger, bad):
    service, _, _, _ = ledger
    with pytest.raises(ValueError):
        service.create_account("meta", hard_cap_microdollars=bad, daily_cap_microdollars=100)


def test_reservation_is_idempotent_but_changed_identity_conflicts(ledger):
    service, run_id = setup_account(ledger)
    first = reserve(service, run_id)
    again = reserve(service, run_id)
    assert first.id == again.id
    assert first.newly_reserved and not again.newly_reserved
    assert service.usage("meta").committed_microdollars == 50
    assert service.usage("meta").reservations == 1
    assert_code("reservation_idempotency_conflict", reserve, service, run_id, amount=51)
    assert_code("reservation_idempotency_conflict", reserve, service, uuid.uuid4())


def test_dispatch_claim_is_single_use_even_across_threads(ledger):
    service, run_id = setup_account(ledger)
    item = reserve(service, run_id)
    barrier = Barrier(8)

    def claim():
        barrier.wait()
        return service.claim_dispatch(item.id)

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _: claim(), range(8)))
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 7
    assert not service.claim_dispatch(item.id)


def test_global_cap_cannot_be_overspent_by_concurrent_reservations(ledger):
    service, run_id = setup_account(ledger, hard=100)
    barrier = Barrier(8)

    def attempt(index):
        barrier.wait()
        try:
            return reserve(service, run_id, key=f"concurrent-{index}", amount=60)
        except BudgetError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, range(8)))
    assert sum(not isinstance(item, str) for item in outcomes) == 1
    assert outcomes.count("global_budget_exceeded") == 7
    assert service.usage("meta").committed_microdollars == 60


def test_concurrency_limit_is_atomic_and_unknowns_keep_slots(ledger):
    service, run_id = setup_account(ledger, concurrency=2)
    barrier = Barrier(8)

    def attempt(index):
        barrier.wait()
        try:
            return reserve(service, run_id, key=f"concurrent-{index}", amount=1)
        except BudgetError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, range(8)))
    accepted = [item for item in outcomes if not isinstance(item, str)]
    assert len(accepted) == 2
    assert outcomes.count("concurrency_limit") == 6
    for item in accepted:
        assert service.claim_dispatch(item.id)
        service.mark_unknown(item.id, reason="timeout")
    assert_code("concurrency_limit", reserve, service, run_id, key="another", amount=1)
    assert service.usage("meta").in_flight == 2


def test_same_idempotency_key_under_concurrency_creates_one_reservation(ledger):
    service, run_id = setup_account(ledger)
    barrier = Barrier(8)

    def attempt(_):
        barrier.wait()
        return reserve(service, run_id)

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, range(8)))
    assert len({item.id for item in outcomes}) == 1
    assert sum(item.newly_reserved for item in outcomes) == 1
    assert service.usage("meta").reservations == 1


def test_settlement_records_actual_usage_and_is_idempotent(ledger):
    service, run_id = setup_account(ledger)
    item = reserve(service, run_id)
    assert service.claim_dispatch(item.id)
    result = service.settle(
        item.id,
        actual_microdollars=30,
        settlement_key="provider-request-1",
        prompt_tokens=100,
        completion_tokens=20,
    )
    assert result.status == "settled" and result.actual_microdollars == 30
    assert service.usage("meta").committed_microdollars == 30
    assert service.usage("meta").in_flight == 0
    assert (
        service.settle(
            item.id,
            actual_microdollars=30,
            settlement_key="provider-request-1",
            prompt_tokens=100,
            completion_tokens=20,
        )
        == result
    )
    assert_code(
        "settlement_idempotency_conflict",
        service.settle,
        item.id,
        actual_microdollars=31,
        settlement_key="provider-request-1",
        prompt_tokens=100,
        completion_tokens=20,
    )
    assert_code(
        "settlement_idempotency_conflict",
        service.settle,
        item.id,
        actual_microdollars=30,
        settlement_key="different-receipt",
        prompt_tokens=100,
        completion_tokens=20,
    )


def test_conflicting_settlement_race_records_exactly_one_outcome(ledger):
    service, run_id = setup_account(ledger)
    item = reserve(service, run_id)
    assert service.claim_dispatch(item.id)
    barrier = Barrier(2)

    def settle(amount):
        barrier.wait()
        try:
            return service.settle(
                item.id, actual_microdollars=amount, settlement_key=f"receipt-{amount}"
            )
        except BudgetError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(settle, [20, 30]))
    assert outcomes.count("settlement_idempotency_conflict") == 1
    accepted = next(item for item in outcomes if not isinstance(item, str))
    assert service.usage("meta").committed_microdollars == accepted.actual_microdollars


def test_unknown_outcome_requires_explicit_reconciliation_not_zero_inference(ledger):
    service, run_id = setup_account(ledger)
    item = reserve(service, run_id, amount=80)
    assert service.claim_dispatch(item.id)
    unknown = service.mark_unknown(item.id, reason="usage_unavailable")
    assert unknown.status == "unknown" and unknown.actual_microdollars is None
    assert service.usage("meta").committed_microdollars == 80
    assert_code(
        "invalid_settlement_state",
        service.settle,
        item.id,
        actual_microdollars=0,
        settlement_key="not-a-response",
    )
    with pytest.raises(ValueError):
        service.reconcile(item.id, actual_microdollars=0, evidence_key="")
    reconciled = service.reconcile(
        item.id, actual_microdollars=0, evidence_key="billing-no-charge-1"
    )
    assert reconciled.status == "settled"
    assert service.usage("meta").committed_microdollars == 0
    assert service.usage("meta").in_flight == 0
    assert (
        service.reconcile(item.id, actual_microdollars=0, evidence_key="billing-no-charge-1")
        == reconciled
    )
    assert_code(
        "settlement_idempotency_conflict",
        service.reconcile,
        item.id,
        actual_microdollars=1,
        evidence_key="billing-no-charge-1",
    )


def test_unresolved_prior_day_reservations_and_reconciled_actual_count_today(ledger):
    service, run_id = setup_account(ledger, hard=1000, daily=100)
    _, _, clock, _ = ledger
    item = reserve(service, run_id, amount=70)
    assert service.claim_dispatch(item.id)
    service.mark_unknown(item.id, reason="timeout")
    clock.now += timedelta(days=1)
    assert service.usage("meta").daily_committed_microdollars == 70
    assert_code("daily_budget_exceeded", reserve, service, run_id, key="next", amount=31)
    service.reconcile(item.id, actual_microdollars=60, evidence_key="billing-cost-60")
    state = service.usage("meta")
    assert state.committed_microdollars == 60 and state.daily_committed_microdollars == 60
    assert_code("daily_budget_exceeded", reserve, service, run_id, key="next", amount=41)
    assert reserve(service, run_id, key="next", amount=40).estimated_microdollars == 40
    clock.now += timedelta(days=100)
    # Open holds never expire into free credit, even long after a rollover.
    state = service.usage("meta")
    assert state.committed_microdollars == 100 and state.daily_committed_microdollars == 40


def test_delayed_dispatch_is_attributed_to_dispatch_and_settlement_day(ledger):
    service, run_id = setup_account(ledger, daily=100)
    _, engine, clock, _ = ledger
    item = reserve(service, run_id, amount=80)
    clock.now += timedelta(days=1)
    assert service.claim_dispatch(item.id)
    service.settle(item.id, actual_microdollars=70, settlement_key="receipt-70")
    assert service.usage("meta").daily_committed_microdollars == 70
    assert_code("daily_budget_exceeded", reserve, service, run_id, key="next", amount=31)
    with Session(engine) as session:
        row = session.get(BudgetReservation, item.id)
        assert row.reserved_day == clock.now.date()
        assert row.settled_day == clock.now.date()


def test_spending_cap_and_clock_use_utc_not_local_calendar_day(ledger):
    service, run_id = setup_account(ledger)
    _, _, clock, _ = ledger
    clock.now = datetime(2026, 10, 28, 0, 30, tzinfo=timezone(timedelta(hours=7)))
    item = reserve(service, run_id)
    assert str(item.reserved_day) == "2026-10-27"
    clock.now = datetime(2026, 10, 28)
    with pytest.raises(ValueError):
        service.usage("meta")


def test_per_run_cost_and_call_limits_do_not_reset_on_settlement(ledger):
    service, run_id = setup_account(ledger, run_cap=60, calls=2)
    item = reserve(service, run_id, amount=40)
    assert service.claim_dispatch(item.id)
    service.settle(item.id, actual_microdollars=30, settlement_key="receipt-30")
    assert_code("run_budget_exceeded", reserve, service, run_id, key="next", amount=31)
    second = reserve(service, run_id, key="next", amount=30)
    assert service.claim_dispatch(second.id)
    service.settle(second.id, actual_microdollars=0, settlement_key="receipt-zero")
    assert_code("run_call_limit", reserve, service, run_id, key="third", amount=1)
    service.configure_run("meta", run_id, cap_microdollars=60, max_calls=2)
    assert_code(
        "run_configuration_conflict",
        service.configure_run,
        "meta",
        run_id,
        cap_microdollars=60,
        max_calls=3,
    )


def test_kill_switch_stops_reservations_and_pending_dispatch_but_allows_ledger_settlement(ledger):
    service, run_id = setup_account(ledger)
    first = reserve(service, run_id)
    assert service.claim_dispatch(first.id)
    second = reserve(service, run_id, key="pending")
    service.set_kill_switch("meta", active=True)
    assert_code("operator_kill_switch", reserve, service, run_id, key="new")
    assert reserve(service, run_id, key="pending").id == second.id  # lookup, not permission
    assert_code("operator_kill_switch", service.claim_dispatch, second.id)
    service.settle(first.id, actual_microdollars=30, settlement_key="receipt-after-kill")
    assert service.usage("meta").committed_microdollars == 80
    service.set_kill_switch("meta", active=False)
    assert service.claim_dispatch(second.id)
    service.disable_execution("meta")
    assert_code("paid_execution_disabled", reserve, service, run_id, key="disabled")


def test_actual_charge_over_cap_is_recorded_and_freezes_future_dispatch(ledger):
    service, run_id = setup_account(ledger, hard=100)
    _, engine, _, _ = ledger
    item = reserve(service, run_id, amount=60)
    assert service.claim_dispatch(item.id)
    settled = service.settle(item.id, actual_microdollars=110, settlement_key="over-cap-receipt")
    assert settled.actual_microdollars == 110
    state = service.usage("meta")
    assert state.committed_microdollars == 110 and state.frozen
    assert_code("account_frozen", reserve, service, run_id, key="next", amount=1)
    with Session(engine) as session:
        assert session.get(BudgetAccount, "meta").frozen_reason == "actual_cost_exceeded_cap"


def test_underestimated_worst_case_also_freezes_for_pricing_review(ledger):
    service, run_id = setup_account(ledger)
    _, engine, _, _ = ledger
    item = reserve(service, run_id, amount=30)
    assert service.claim_dispatch(item.id)
    service.settle(item.id, actual_microdollars=40, settlement_key="pricing-mismatch-receipt")
    assert service.usage("meta").frozen
    with Session(engine) as session:
        account = session.get(BudgetAccount, "meta")
        assert account.frozen_reason == "actual_cost_exceeded_reservation"


def test_invalid_settlement_values_do_not_release_reservation(ledger):
    service, run_id = setup_account(ledger)
    item = reserve(service, run_id)
    assert service.claim_dispatch(item.id)
    for value in (True, -1, 1.5):
        with pytest.raises(ValueError):
            service.settle(item.id, actual_microdollars=value, settlement_key="receipt")
    with pytest.raises(ValueError):
        service.settle(item.id, actual_microdollars=10, settlement_key="receipt", prompt_tokens=1)
    assert service.usage("meta").committed_microdollars == 50
    assert service.usage("meta").in_flight == 1
