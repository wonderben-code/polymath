"""Independent one-call authority/recovery boundaries; real PostgreSQL, mock Meta only."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import event, func, select, text, update
from sqlalchemy.orm import Session
from test_meta_probe_worker import (
    ACTION,
    mock_native,
    response_body,
)
from test_meta_probe_worker import probe as probe_fixture
from test_meta_probe_worker import (
    worker as make_worker,
)
from test_meta_probes import attestation_data, probe_ready
from test_meta_readiness import readiness as readiness_fixture
from test_run_api import bootstrap
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.budget import BudgetService
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.jobs import JobRepository
from polymath.meta_probe_contracts import probe_manifest_sha256
from polymath.meta_probe_dispatch import MetaProbeDispatcher
from polymath.meta_probe_models import MetaProbeApproval, MetaProbeRequest
from polymath.meta_probe_worker import MetaProbeWorker
from polymath.meta_probes import MetaProbeError
from polymath.meta_readiness import MetaReadinessStore
from polymath.models import ResearchExternalRequest
from polymath.operator_models import OperatorSession

database = database_fixture
readiness = readiness_fixture
probe = probe_fixture
pytestmark = pytest.mark.integration


def count(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def account_state(probe):
    with Session(probe.engine) as session:
        account = session.get(BudgetAccount, probe.policy.native_account_id)
        return account.native_control_epoch, account.kill_switch


@pytest.mark.parametrize("stop", ["kill", "disable", "readiness", "cancel"])
def test_identical_arm_retry_cannot_undo_any_later_stop_even_when_repeated(probe, stop):
    budget = BudgetService(probe.engine)
    initial_epoch, _ = account_state(probe)
    for _ in range(2):
        if stop == "kill":
            budget.set_kill_switch(probe.policy.native_account_id, active=True)
        elif stop == "disable":
            budget.disable_execution(probe.policy.native_account_id)
        elif stop == "readiness":
            MetaReadinessStore(probe.engine, probe.readiness_policy, probe.operator_policy).disable(
                probe.request, probe.policy.native_account_id
            )
        else:
            probe.store.cancel(probe.request, probe.run_id)
    stopped = account_state(probe)
    assert stopped[0] == initial_epoch + 2
    retry = probe.store.arm_and_enqueue(
        probe.request, probe.manifest, probe_manifest_sha256(probe.manifest)
    )
    assert retry.id == probe.run_id
    assert account_state(probe) == stopped
    assert count(probe.engine, MetaProbeApproval) == count(probe.engine, MetaProbeRequest) == 1
    assert count(probe.engine, BudgetReservation) == 0
    if stop != "cancel":
        lease = JobRepository(probe.engine).claim_next(
            "stopped-canary", task_kinds=("verify_meta",)
        )
        dispatcher = MetaProbeDispatcher(
            probe.engine, probe.policy, probe.readiness_policy, probe.operator_policy
        )
        with pytest.raises(MetaProbeError):
            dispatcher.prepare(lease)
    assert count(probe.engine, ResearchExternalRequest) == 0


@pytest.mark.parametrize("change", ["key", "paid_flag", "role", "preparation"])
def test_current_authority_is_rechecked_after_actual_account_lock_wait(probe, change):
    lease = JobRepository(probe.engine).claim_next("waiting-canary", task_kinds=("verify_meta",))
    dispatcher = MetaProbeDispatcher(
        probe.engine, probe.policy, probe.readiness_policy, probe.operator_policy
    )
    waiting = Event()

    def observe_wait(_connection, _cursor, statement, _parameters, _context, _executemany):
        if "FROM budget_accounts" in statement and "FOR UPDATE" in statement:
            waiting.set()

    with Session(probe.engine) as blocker:
        blocker.begin()
        blocker.scalar(
            select(BudgetAccount)
            .where(BudgetAccount.id == probe.policy.native_account_id)
            .with_for_update()
        )
        event.listen(probe.engine, "before_cursor_execute", observe_wait)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(dispatcher.prepare, lease)
                try:
                    assert waiting.wait(5), "Native dispatcher never reached the held account lock"
                    if change == "key":
                        dispatcher.store.probe_policy = replace(
                            probe.policy, api_key=SecretStr("synthetic-rotated-credential")
                        )
                    elif change == "paid_flag":
                        dispatcher.store.probe_policy = replace(
                            probe.policy, paid_probe_enabled=False
                        )
                    elif change == "role":
                        dispatcher.store.readiness_policy = replace(
                            probe.readiness_policy, allowed_actor_ids=()
                        )
                    else:
                        dispatcher.store.readiness_policy = replace(
                            probe.readiness_policy, preparation_enabled=False
                        )
                finally:
                    # Release even when the synchronization assertion fails;
                    # executor shutdown must not wait behind our own lock.
                    blocker.commit()
                with pytest.raises(MetaProbeError):
                    future.result(timeout=5)
        finally:
            blocker.rollback()
            event.remove(probe.engine, "before_cursor_execute", observe_wait)
    assert (
        count(probe.engine, BudgetReservation) == count(probe.engine, ResearchExternalRequest) == 0
    )


def test_same_lease_can_mint_only_one_grant_under_concurrent_dispatch(probe):
    lease = JobRepository(probe.engine).claim_next("concurrent-canary", task_kinds=("verify_meta",))
    dispatcher = MetaProbeDispatcher(
        probe.engine, probe.policy, probe.readiness_policy, probe.operator_policy
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(dispatcher.prepare, lease) for _ in range(2)]
        grants = [future.result(timeout=5) for future in futures]
    assert sum(grant is not None for grant in grants) == 1
    assert (
        count(probe.engine, BudgetReservation) == count(probe.engine, ResearchExternalRequest) == 1
    )
    assert BudgetService(probe.engine).usage(probe.policy.native_account_id).in_flight == 1


@pytest.mark.parametrize("kind", ["credential", "provider_account"])
@pytest.mark.asyncio
async def test_missing_immutable_ledger_identity_fails_closed_before_factory(
    probe, monkeypatch, kind
):
    # Simulate an incomplete restore in this disposable schema. Application paths
    # cannot delete these rows; the actual immutable trigger remains enforced.
    with probe.engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE meta_native_identity_bindings "
                "DISABLE TRIGGER immutable_meta_native_identity_bindings"
            )
        )
        connection.execute(
            text("DELETE FROM meta_native_identity_bindings WHERE kind = :kind"), {"kind": kind}
        )
        connection.execute(
            text(
                "ALTER TABLE meta_native_identity_bindings "
                "ENABLE TRIGGER immutable_meta_native_identity_bindings"
            )
        )
    native = mock_native(monkeypatch, body=response_body(probe))
    result = await make_worker(probe).run_once("incomplete-restored-canary")
    assert result.status == "interrupted"
    assert not native.constructed and not native.requests
    assert (
        count(probe.engine, BudgetReservation) == count(probe.engine, ResearchExternalRequest) == 0
    )


def test_even_owning_browser_and_budget_operator_cannot_use_generic_run_lifecycle(probe):
    paths = ("", "/result", "/events")
    for suffix in paths:
        response = probe.client.get(f"/api/v1/runs/{probe.run_id}{suffix}")
        assert response.status_code == 404
    for suffix, body in (("/cancel", {}), ("/resume", {"expected_token": 0})):
        response = probe.client.post(
            f"/api/v1/runs/{probe.run_id}{suffix}", headers=bootstrap(probe.client), json=body
        )
        assert response.status_code == 404
    assert probe.client.get("/api/v1/runs").json()["runs"] == []
    assert JobRepository(probe.engine).get(probe.run_id).status == "queued"
    assert (
        count(probe.engine, BudgetReservation) == count(probe.engine, ResearchExternalRequest) == 0
    )


@pytest.mark.asyncio
async def test_already_authorized_actual_reply_is_counted_and_finalized_after_all_authority_is_lost(
    probe, monkeypatch
):
    instance = make_worker(probe)

    async def late_response(_request):
        await asyncio.to_thread(probe.store.cancel, probe.request, probe.run_id)
        with Session(probe.engine) as session, session.begin():
            session.execute(update(OperatorSession).values(revoked_at=func.clock_timestamp()))
            session.execute(
                update(BrowserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        no_key = replace(probe.policy, paid_probe_enabled=False, api_key=None, model_id=None)
        instance.probe_policy = no_key
        instance.store.probe_policy = instance.dispatcher.store.probe_policy = no_key
        instance.store.operator_policy = instance.dispatcher.store.operator_policy = None
        return httpx.Response(200, json=response_body(probe))

    native = mock_native(monkeypatch, handler=late_response)
    outcome = await instance.run_once("known-late-canary")
    assert outcome.status == "finalized" and outcome.native_status == "passed"
    record = await instance.finalize_known(probe.run_id)
    assert record.receipt_id == outcome.receipt_id
    assert JobRepository(probe.engine).get(probe.run_id).status == "cancelled"
    with Session(probe.engine) as session:
        reservation = session.scalar(select(BudgetReservation))
        assert reservation.status == "settled" and reservation.actual_microdollars == 40
        account = session.get(BudgetAccount, probe.policy.native_account_id)
        assert (
            account.kill_switch and not account.execution_enabled and not account.pricing_verified
        )
        assert session.scalar(select(func.count()).select_from(ResearchExternalRequest)) == 1
    assert len(native.requests) == len(native.constructed) == 1
    assert (await instance.run_once("disabled-after-late-facts")).status == "disabled"
    assert len(native.requests) == len(native.constructed) == 1


@pytest.mark.parametrize(
    "constraint", ["available_credit_microdollars", "remaining_provider_allowance_microdollars"]
)
@pytest.mark.asyncio
async def test_same_attestation_cannot_reuse_credit_after_a_prior_probe_settles(
    readiness, monkeypatch, constraint
):
    engine, store, _dispatcher, request, proposal, saved_readiness, initial = probe_ready(readiness)
    reserve = initial.manifest.reservation_microdollars
    attestation = attestation_data(proposal.model_id, **{constraint: reserve + 39})
    preview = store.preview(request, saved_readiness.id, attestation)
    job = store.arm_and_enqueue(request, preview.manifest, preview.manifest_sha256)
    body = {
        "model": proposal.model_id,
        "choices": [{"finish_reason": "stop", "message": {"content": ACTION}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 15},
    }
    native = mock_native(monkeypatch, body=body)
    worker = MetaProbeWorker(
        engine,
        probe_policy=store.probe_policy,
        readiness_policy=store.readiness_policy,
        operator_policy=store.operator_policy,
    )
    result = await worker.run_once("credit-already-consumed")
    assert result.status == "finalized" and result.run_id == str(job.id)
    usage = BudgetService(engine).usage(proposal.account.account_id)
    assert usage.committed_microdollars == 40 and usage.in_flight == 0
    # No holds remain. Looking only at holds would incorrectly admit another
    # reserve even though 40 + reserve is one microdollar above this same cap.
    with pytest.raises(MetaProbeError, match="meta_probe_account_conflict"):
        store.preview(request, saved_readiness.id, attestation)
    assert count(engine, BudgetReservation) == 1
    assert len(native.constructed) == len(native.requests) == 1
