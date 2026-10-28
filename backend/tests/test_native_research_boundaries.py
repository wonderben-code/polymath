"""Independent native admission/grant recovery checks, with no live provider calls."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from pydantic import SecretStr
from sqlalchemy import event, func, select, text, update
from sqlalchemy.orm import Session
from test_meta_probes import attestation_data
from test_meta_readiness import readiness as readiness_fixture
from test_meta_research import (
    active_research,
    enqueue_native,
    native_call,
    research_observation,
    review_data,
)
from test_native_research_worker import native as native_fixture
from test_worker import database as database_fixture

import polymath.meta_research_dispatch as dispatch_module
from polymath.budget import BudgetError, BudgetService
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.dispatch import DispatchCoordinator
from polymath.jobs import JobRepository, LeaseLost
from polymath.meta_research import MetaResearchError
from polymath.meta_research_contracts import research_step_sha256
from polymath.meta_research_dispatch import MetaResearchDispatcher
from polymath.meta_research_models import NativeResearchCallRequest, NativeResearchRunBinding
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.run_api import QUEUE_LOCK_KEY

database = database_fixture
readiness = readiness_fixture
native = native_fixture
pytestmark = pytest.mark.integration


def count(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize("change", ["key", "flag", "role", "preparation", "cancel", "lease"])
def test_native_grant_rechecks_live_authority_after_real_account_wait(
    readiness, monkeypatch, change
):
    active = active_research(readiness)
    _, lease, _, request, step = native_call(active)
    baseline = count(active.engine, BudgetReservation)
    waiting = Event()
    if change in {"cancel", "lease"}:
        original_input = dispatch_module._original_input

        def pin_cached_run(session, *args, **kwargs):
            value = original_input(session, *args, **kwargs)
            # Pin the pre-lock ORM instance strongly. This proves an explicit
            # refresh, rather than passing because its weak cache was collected.
            session.info["pinned_native_run"] = session.get(ResearchRun, value.run_id)
            return value

        monkeypatch.setattr(dispatch_module, "_original_input", pin_cached_run)

    def reached(_conn, _cursor, statement, _parameters, _context, _many):
        if "FROM budget_accounts" in statement and "FOR UPDATE" in statement:
            waiting.set()

    with Session(active.engine) as blocker:
        blocker.begin()
        blocker.scalar(
            select(BudgetAccount)
            .where(BudgetAccount.id == active.policy.native_account_id)
            .with_for_update()
        )
        event.listen(active.engine, "before_cursor_execute", reached)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(active.dispatcher.prepare, lease, "model-0001", request, step)
                try:
                    assert waiting.wait(5), "Native grant did not reach the held account lock"
                    if change == "key":
                        active.dispatcher.store.research_policy = replace(
                            active.policy, api_key=SecretStr("synthetic-rotated-native-key")
                        )
                    elif change == "flag":
                        active.dispatcher.store.research_policy = replace(
                            active.policy, paid_research_enabled=False
                        )
                    elif change == "role":
                        active.dispatcher.store.readiness_policy = replace(
                            active.readiness_policy, allowed_actor_ids=()
                        )
                    elif change == "preparation":
                        active.dispatcher.store.readiness_policy = replace(
                            active.readiness_policy, preparation_enabled=False
                        )
                    elif change == "cancel":
                        JobRepository(active.engine).request_cancel(lease.run_id)
                    else:
                        with Session(active.engine) as editor, editor.begin():
                            editor.execute(
                                update(ResearchRun)
                                .where(ResearchRun.id == lease.run_id)
                                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
                            )
                finally:
                    blocker.commit()
                with pytest.raises((MetaResearchError, LeaseLost)):
                    pending.result(timeout=5)
        finally:
            blocker.rollback()
            event.remove(active.engine, "before_cursor_execute", reached)
    assert count(active.engine, BudgetReservation) == baseline
    assert count(active.engine, NativeResearchCallRequest) == 0


@pytest.mark.parametrize("change", ["flag", "role", "input"])
def test_native_resume_rechecks_authority_after_real_shared_queue_wait(readiness, change):
    active = active_research(readiness)
    run_id, lease, _, _, _ = native_call(active)
    jobs = JobRepository(active.engine)
    jobs.interrupt(lease)
    with Session(active.engine) as session:
        run = session.get(ResearchRun, run_id)
        token = run.lease_token
        epoch = session.get(BudgetAccount, active.policy.native_account_id).native_control_epoch
    waiting = Event()

    def reached(_conn, _cursor, statement, parameters, _context, _many):
        if "pg_advisory_xact_lock" in statement and parameters.get("key") == QUEUE_LOCK_KEY:
            waiting.set()

    def resume():
        with Session(active.engine) as session, session.begin():
            return active.store.resume_in_session(
                session, active.request, run_id, token, active.activation_sha256
            ).id

    with Session(active.engine) as blocker:
        blocker.begin()
        blocker.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK_KEY})
        event.listen(active.engine, "before_cursor_execute", reached)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(resume)
                try:
                    assert waiting.wait(5), "Native resume did not reach shared queue lock"
                    if change == "flag":
                        active.store.research_policy = replace(
                            active.policy, paid_research_enabled=False
                        )
                    elif change == "role":
                        active.store.readiness_policy = replace(
                            active.readiness_policy, allowed_actor_ids=()
                        )
                    else:
                        with Session(active.engine) as editor, editor.begin():
                            editor.execute(
                                update(ResearchRun)
                                .where(ResearchRun.id == run_id)
                                .values(goal="Changed while native resume waited")
                            )
                finally:
                    blocker.commit()
                with pytest.raises(MetaResearchError):
                    pending.result(timeout=5)
        finally:
            blocker.rollback()
            event.remove(active.engine, "before_cursor_execute", reached)
    with Session(active.engine) as session:
        run = session.get(ResearchRun, run_id)
        assert run.status == "interrupted" and run.lease_token == token
        assert (
            session.get(BudgetAccount, active.policy.native_account_id).native_control_epoch
            == epoch
        )
    assert count(active.engine, NativeResearchCallRequest) == 0


def test_native_binding_blocks_legacy_claim_and_budget_even_if_account_scope_is_lost(readiness):
    active = active_research(readiness)
    run_id = enqueue_native(active)
    jobs = JobRepository(active.engine)
    legacy = jobs.create(mode="ask", goal="Synthetic unbound legacy job")
    assert jobs.claim_next("legacy-worker").run_id == legacy.id
    lease = jobs.claim_next("native-worker", native_only=True)
    assert lease.run_id == run_id
    assert jobs.claim_next("second-legacy-worker") is None
    with Session(active.engine) as session, session.begin():
        account = session.get(BudgetAccount, active.policy.native_account_id)
        account.native_scope = None
        account.execution_enabled = account.pricing_verified = True
        account.pricing_reference = "synthetic-accidental-legacy-flags"
    budget = BudgetService(active.engine)
    with pytest.raises(BudgetError, match="native_dispatch_scope_required"):
        budget.reserve(
            active.policy.native_account_id, run_id, call_key="generic", estimated_microdollars=1
        )
    with pytest.raises(BudgetError, match="native_dispatch_scope_required"):
        DispatchCoordinator(active.engine).prepare_model_call(
            lease,
            account_id=active.policy.native_account_id,
            request_id="generic",
            request_hash="a" * 64,
            estimated_microdollars=1,
        )
    assert count(active.engine, BudgetReservation) == 1
    assert count(active.engine, NativeResearchCallRequest) == 0


def two_concurrent_slots(data):
    data["account"]["max_in_flight"] = 2


@pytest.mark.parametrize("native", [two_concurrent_slots], indirect=True)
@pytest.mark.asyncio
async def test_interrupted_prepared_call_blocks_new_admission_even_with_an_unused_slot(native):
    run_id, lease, _, request, step = native_call(native)
    dispatcher = MetaResearchDispatcher(
        native.engine, native.policy, native.readiness_policy, native.operator_policy
    )
    grant = dispatcher.prepare(lease, "model-0001", request, step)
    JobRepository(native.engine).interrupt(lease)
    with Session(native.engine) as session:
        reservation = session.get(BudgetReservation, grant.reservation_id)
        journal = session.get(ResearchExternalRequest, (run_id, "model-0001"))
        assert reservation.status == "in_flight" and journal.status == "prepared"
        assert session.get(BudgetAccount, native.policy.native_account_id).max_in_flight == 2
    with pytest.raises(MetaResearchError, match="meta_research_prior_unresolved"):
        enqueue_native(native)
    assert count(native.engine, NativeResearchRunBinding) == 1
    assert (
        count(native.engine, BudgetReservation) == 2
    )  # Existing canary plus retained native hold.


@pytest.mark.parametrize(
    "constraint", ["available_credit_microdollars", "remaining_provider_allowance_microdollars"]
)
def test_settled_native_usage_cannot_reuse_same_attested_funding(readiness, constraint):
    active = active_research(readiness)
    initial = BudgetService(active.engine).usage(active.policy.native_account_id)
    # Use the exact per-call quote rather than a guessed amount or token ratio.
    from polymath.meta_readiness_contracts import quote_proposal

    reserve = quote_proposal(active.proposal).per_call_reservation_microdollars
    cap = initial.committed_microdollars + reserve + 202
    preview = active.store.preview(
        active.request,
        active.saved.id,
        active.canary.id,
        review_data(),
        attestation_data(active.proposal.model_id, **{constraint: cap}),
    )
    active.activation = active.store.activate(
        active.request, preview.manifest, preview.manifest_sha256
    )
    active.activation_sha256 = preview.manifest_sha256
    run_id, lease, value, request, step = native_call(active)
    grant = active.dispatcher.prepare(lease, "model-0001", request, step)
    active.dispatcher.complete(grant, research_observation(value, request))
    usage = BudgetService(active.engine).usage(active.policy.native_account_id)
    assert usage.committed_microdollars == initial.committed_microdollars + 203
    assert usage.in_flight == 0
    second_step = research_step_sha256(
        request,
        activation_sha256=value.activation_sha256,
        profile_sha256=value.profile_sha256,
        request_id="model-0002",
    )
    with pytest.raises(MetaResearchError, match="meta_research_account_conflict"):
        active.dispatcher.prepare(lease, "model-0002", request, second_step)
    assert count(active.engine, BudgetReservation) == 2
    with Session(active.engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(NativeResearchCallRequest)
                .where(NativeResearchCallRequest.run_id == run_id)
            )
            == 1
        )
