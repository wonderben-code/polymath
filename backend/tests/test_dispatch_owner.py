"""Browser expiry fences new grants, while late facts/accounting remain durable."""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session
from test_dispatch import OBSERVATION, REQUEST_HASH, complete, grant, run
from test_dispatch import dispatch as dispatch_fixture

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.dispatch import DispatchError
from polymath.models import ResearchExternalRequest, ResearchRun

dispatch = dispatch_fixture
pytestmark = pytest.mark.integration


def bind_owner(dispatch, lease, *, task_kind="research", expired=False):
    owner_hash = hashlib.sha256(b"synthetic browser dispatch owner").hexdigest()
    with Session(dispatch[3]) as session, session.begin():
        session.add(
            BrowserSession(
                token_hash=owner_hash,
                expires_at=datetime.now(UTC) + timedelta(hours=-1 if expired else 1),
            )
        )
        session.flush()
        session.add(ResearchRunAccess(run_id=lease.run_id, session_hash=owner_hash))
        session.get(ResearchRun, lease.run_id).task_kind = task_kind
    return owner_hash


def expire_browser(engine, owner_hash):
    with engine.begin() as connection:
        connection.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == owner_hash)
            .values(expires_at=func.clock_timestamp())
        )


def assert_no_dispatch(dispatch, lease):
    jobs, budget, _, engine, _ = dispatch
    assert budget.usage("meta").reservations == 0
    assert budget.usage("meta").committed_microdollars == 0
    snapshot = jobs.get(lease.run_id)
    assert not snapshot.external_request_in_flight and snapshot.external_request_id is None
    assert len(jobs.events_after(lease.run_id)) == 2
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 0
        assert session.scalar(select(func.count()).select_from(ResearchExternalRequest)) == 0


@pytest.mark.parametrize(
    "task_kind", ["research", "formalise", "write_summary", "write_paper", "reassess_finding"]
)
def test_expired_browser_cannot_authorize_any_new_model_grant(dispatch, task_kind):
    lease = run(dispatch)
    bind_owner(dispatch, lease, task_kind=task_kind, expired=True)
    with pytest.raises(DispatchError, match="^dispatch_owner_expired$"):
        grant(dispatch, lease)
    assert_no_dispatch(dispatch, lease)


def test_reassessment_requires_live_browser_binding_but_legacy_internal_research_still_works(
    dispatch,
):
    lease = run(dispatch)
    with Session(dispatch[3]) as session, session.begin():
        session.get(ResearchRun, lease.run_id).task_kind = "reassess_finding"
    with pytest.raises(DispatchError, match="^dispatch_owner_expired$"):
        grant(dispatch, lease)
    assert_no_dispatch(dispatch, lease)
    with Session(dispatch[3]) as session, session.begin():
        session.get(ResearchRun, lease.run_id).task_kind = "research"
    # This is an already explicitly BudgetService-authorized internal fixture,
    # not an HTTP path, model factory, or new permission to execute paid calls.
    assert grant(dispatch, lease) is not None


def test_browser_expiry_during_actual_account_lock_wait_is_rechecked(dispatch, monkeypatch):
    _, _, coordinator, engine, _ = dispatch
    lease = run(dispatch)
    owner_hash = bind_owner(dispatch, lease)
    waiting, process_ids = Event(), []
    original = coordinator.budget._account

    def capture_connection(session, account_id):
        process_ids.append(session.scalar(select(func.pg_backend_pid())))
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
                        {"pid": process_ids[0]},
                    )
                if locked:
                    break
                time.sleep(0.01)
            assert locked, "The grant must actually be waiting for the budget account lock"
            expire_browser(engine, owner_hash)
        with pytest.raises(DispatchError, match="^dispatch_owner_expired$"):
            future.result(timeout=10)
    assert_no_dispatch(dispatch, lease)


@pytest.mark.parametrize("stage", ["reservation", "claim", "event"])
def test_expiry_before_final_grant_rolls_back_reservation_request_and_event(
    dispatch, monkeypatch, stage
):
    import polymath.dispatch as dispatch_module

    _, _, coordinator, engine, _ = dispatch
    lease = run(dispatch)
    owner_hash = bind_owner(dispatch, lease, task_kind="reassess_finding")
    if stage == "event":
        target, name = dispatch_module, "_event"
    else:
        target = coordinator.budget
        name = "reserve_in_session" if stage == "reservation" else "claim_dispatch_in_session"
    original = getattr(target, name)

    def expire_after_stage(*args, **kwargs):
        result = original(*args, **kwargs)
        expire_browser(engine, owner_hash)
        return result

    monkeypatch.setattr(target, name, expire_after_stage)
    with pytest.raises(DispatchError, match="^dispatch_owner_expired$"):
        grant(dispatch, lease)
    assert_no_dispatch(dispatch, lease)


@pytest.mark.parametrize("usage_known", [False, True])
def test_observed_late_response_and_accounting_remain_available_after_browser_expiry(
    dispatch, usage_known
):
    jobs, budget, coordinator, engine, _ = dispatch
    lease = run(dispatch)
    owner_hash = bind_owner(dispatch, lease, task_kind="reassess_finding")
    item = grant(dispatch, lease)
    assert item is not None
    expire_browser(engine, owner_hash)
    coordinator.mark_unknown(item, reason="timeout")
    if usage_known:
        complete(dispatch, item)
        assert budget.usage("meta").committed_microdollars == 10
    else:
        coordinator.complete(item, observation=OBSERVATION, actual_microdollars=None)
        assert budget.usage("meta").committed_microdollars == 50
    assert not jobs.get(lease.run_id).external_request_in_flight
    assert (
        coordinator.get_recorded_response(lease, request_id="model-1", request_hash=REQUEST_HASH)
        == OBSERVATION
    )
    assert coordinator.get_recorded_grant(lease.run_id, request_id="model-1") == item
    with pytest.raises(DispatchError, match="^dispatch_owner_expired$"):
        grant(dispatch, lease, "model-2")
    assert budget.usage("meta").reservations == 1
    if not usage_known:
        # Recovering known cost is still permitted, and never creates a grant.
        complete(dispatch, item)
        assert budget.usage("meta").committed_microdollars == 10
