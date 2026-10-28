"""Synthetic one-call consent/dispatch with the real PostgreSQL monetary ledger."""

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_meta_readiness import READINESS, prepare
from test_meta_readiness import readiness as readiness_fixture
from test_operator_auth import POLICY
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.budget import BudgetError, BudgetService
from polymath.budget_models import BudgetAccount, BudgetReservation, BudgetRunLimit
from polymath.dispatch import DispatchCoordinator
from polymath.jobs import JobRepository
from polymath.meta_probe_contracts import (
    MetaObservation,
    MetaProbePolicy,
)
from polymath.meta_probe_dispatch import MetaProbeDispatcher
from polymath.meta_probe_models import (
    MetaNativeIdentityBinding,
    MetaProbeManifestRecord,
    MetaProbeReceipt,
)
from polymath.meta_probes import MetaProbeError, MetaProbeStore
from polymath.models import ResearchExternalRequest
from polymath.operator_models import OperatorSession

pytestmark = pytest.mark.integration
readiness = readiness_fixture
database = database_fixture


def attestation_data(model_id="fixture-attested-model", **changes):
    now = datetime.now(UTC)
    return {
        "provider_billing_account_id": "synthetic-billing-account",
        "model_id": model_id,
        "observed_at": (now - timedelta(seconds=5)).isoformat(),
        "valid_until": (now + timedelta(minutes=10)).isoformat(),
        "available_credit_microdollars": 150_000_000,
        "remaining_provider_allowance_microdollars": 150_000_000,
        "provider_hard_cap_microdollars": 150_000_000,
        "evidence": [
            {"url": "https://example.org/synthetic-provider-account", "content_sha256": "d" * 64}
        ],
        **changes,
    }


def probe_ready(readiness):
    engine, _, request, proposal, _, saved = prepare(readiness)
    policy = MetaProbePolicy(
        paid_probe_enabled=True,
        native_account_id=proposal.account.account_id,
        model_id=proposal.model_id,
        api_key=SecretStr("synthetic-meta-key-no-native-calls"),
    )
    store = MetaProbeStore(engine, policy, READINESS, POLICY)
    dispatcher = MetaProbeDispatcher(engine, policy, READINESS, POLICY)
    preview = store.preview(request, saved.id, attestation_data(proposal.model_id))
    return engine, store, dispatcher, request, proposal, saved, preview


def arm(readiness):
    values = probe_ready(readiness)
    engine, store, dispatcher, request, _, _, preview = values
    job = store.arm_and_enqueue(request, preview.manifest, preview.manifest_sha256)
    lease = JobRepository(engine).claim_next("synthetic-probe-worker", task_kinds=("verify_meta",))
    assert lease is not None and lease.run_id == job.id
    return (*values, job, lease)


def observation(value, **changes):
    now = datetime.now(UTC)
    content = (
        '{"kind":"conclude","conclusion":{"outcome":"insufficient_evidence",'
        '"reason":"Protocol check only."}}'
    )
    data = {
        "request_sha256": value.manifest.request_sha256,
        "requested_model_id": value.proposal.model_id,
        "observed_model_id": value.proposal.model_id,
        "http_status": 200,
        "finish_reason": "stop",
        "content": content,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "body_sha256": "a" * 64,
        "body_complete": True,
        "input_tokens": 101,
        "output_tokens": 51,
        "started_at": now.isoformat(),
        "finished_at": now.isoformat(),
        **changes,
    }
    return MetaObservation.model_validate(data)


def test_preview_is_readonly_and_arm_is_exact_one_scoped_epoch(readiness):
    engine, store, _, request, proposal, _, preview = probe_ready(readiness)
    with Session(engine) as session:
        account = session.get(BudgetAccount, proposal.account.account_id)
        assert account.kill_switch and account.native_control_epoch == 0
        assert session.scalar(select(func.count()).select_from(MetaProbeManifestRecord)) == 0
    job = store.arm_and_enqueue(request, preview.manifest, preview.manifest_sha256)
    assert job.to_response()["status"] == "queued"
    assert job.to_response()["research_execution_available"] is False
    assert store.arm_and_enqueue(request, preview.manifest, preview.manifest_sha256).id == job.id
    with Session(engine) as session:
        account = session.get(BudgetAccount, proposal.account.account_id)
        assert not account.kill_switch and account.native_control_epoch == 1
        assert not account.execution_enabled and not account.pricing_verified
        assert account.native_scope == "probe_only"
        assert session.get(BudgetRunLimit, (account.id, job.id)).max_calls == 1
        assert session.scalar(select(func.count()).select_from(MetaNativeIdentityBinding)) == 2
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 0


def test_single_grant_and_known_free_finalize_no_general_authority(readiness):
    engine, store, dispatcher, request, proposal, _, _, job, lease = arm(readiness)
    value = store.load_input(lease)
    assert dispatcher.get_recorded_observation(lease) is None
    grant = dispatcher.prepare(lease)
    assert grant is not None and dispatcher.prepare(lease) is None
    with pytest.raises(BudgetError, match="native_dispatch_scope_required"):
        DispatchCoordinator(engine).prepare_model_call(
            lease,
            account_id=proposal.account.account_id,
            request_id="forged",
            request_hash="f" * 64,
            estimated_microdollars=1,
        )
    recorded = observation(value)
    settled = dispatcher.complete(grant, recorded)
    assert settled.actual_microdollars == 203  # separately rounded 101 + 102
    assert dispatcher.get_recorded_observation(lease) == recorded
    assert store.get_job(request, job.id).to_response()["can_finalize"] is True
    receipt = MetaProbeStore(engine).finalize_known(job.id)
    assert receipt.to_response()["native_status"] == "passed"
    assert receipt.to_response()["research_execution_available"] is False
    assert "content" not in receipt.to_response()["observation"]
    assert MetaProbeStore(engine).finalize_known(job.id).id == receipt.id
    assert store.get_job(request, job.id).to_response()["status"] == "succeeded"
    with Session(engine) as session:
        account = session.get(BudgetAccount, proposal.account.account_id)
        assert not account.execution_enabled and not account.pricing_verified
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 1


@pytest.mark.parametrize("change", ["kill", "key", "role", "preparation", "epoch"])
def test_fresh_grant_revalidates_current_authority_without_reserving(readiness, change):
    engine, store, dispatcher, _, proposal, _, _, _, lease = arm(readiness)
    if change == "kill":
        BudgetService(engine).set_kill_switch(proposal.account.account_id, active=True)
    elif change == "epoch":
        with Session(engine) as session, session.begin():
            session.execute(update(BudgetAccount).values(native_control_epoch=2))
    elif change == "key":
        dispatcher.store.probe_policy = replace(
            store.probe_policy, api_key=SecretStr("rotated-key")
        )
    elif change == "role":
        dispatcher.store.readiness_policy = replace(READINESS, allowed_actor_ids=())
    else:
        dispatcher.store.readiness_policy = replace(READINESS, preparation_enabled=False)
    with pytest.raises(MetaProbeError):
        dispatcher.prepare(lease)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 0
        assert session.scalar(select(func.count()).select_from(ResearchExternalRequest)) == 0


def test_cancel_after_grant_retains_known_late_cost_and_free_receipt(readiness):
    engine, store, dispatcher, request, proposal, _, preview, job, lease = arm(readiness)
    value = store.load_input(lease)
    grant = dispatcher.prepare(lease)
    cancelled = store.cancel(request, job.id)
    assert cancelled.to_response()["status"] == "cancelled"
    assert store.arm_and_enqueue(request, preview.manifest, preview.manifest_sha256).id == job.id
    dispatcher.complete(grant, observation(value))
    receipt = MetaProbeStore(engine).finalize_known(job.id)
    assert receipt.to_response()["native_status"] == "passed"
    assert store.get_job(request, job.id).to_response()["status"] == "cancelled"
    with Session(engine) as session:
        account = session.get(BudgetAccount, proposal.account.account_id)
        assert account.kill_switch and account.native_control_epoch == 2
        assert session.scalar(select(BudgetReservation.actual_microdollars)) == 203


def test_unknown_outcome_retains_hold_and_blocks_resend_and_new_arm(readiness):
    engine, store, dispatcher, request, proposal, saved, _, job, lease = arm(readiness)
    grant = dispatcher.prepare(lease)
    dispatcher.mark_unknown(grant, "timeout")
    with pytest.raises(MetaProbeError, match="meta_probe_outcome_unknown"):
        dispatcher.get_recorded_observation(lease)
    with pytest.raises(MetaProbeError, match="meta_probe_outcome_unknown"):
        MetaProbeStore(engine).finalize_known(job.id)
    store.cancel(request, job.id)
    preview = store.preview(request, saved.id, attestation_data(proposal.model_id))
    with pytest.raises(MetaProbeError, match="meta_probe_prior_unresolved"):
        store.arm_and_enqueue(request, preview.manifest, preview.manifest_sha256)
    with Session(engine) as session:
        row = session.scalar(select(BudgetReservation))
        assert row.status == "unknown" and row.actual_microdollars is None


@pytest.mark.parametrize(
    "changes,status,cost,frozen",
    [
        ({"input_tokens": 300001}, "failed", 300103, True),
        ({"observed_model_id": "other-model"}, "unknown", None, True),
        ({"observed_model_id": None}, "unknown", None, False),
        ({"input_tokens": None, "output_tokens": None}, "unknown", None, False),
        ({"finish_reason": "length"}, "failed", 203, False),
        ({"input_tokens": 2**63 - 1}, "unknown", None, True),
    ],
)
def test_receipt_uses_actual_transport_facts_and_conservative_accounting(
    readiness, changes, status, cost, frozen
):
    engine, store, dispatcher, _, proposal, _, _, job, lease = arm(readiness)
    value = store.load_input(lease)
    grant = dispatcher.prepare(lease)
    dispatcher.complete(grant, observation(value, **changes))
    receipt = MetaProbeStore(engine).finalize_known(job.id).to_response()
    assert receipt["native_status"] == status
    assert receipt["assessment"]["actual_microdollars"] == cost
    count = changes.get("input_tokens", 101)
    assert receipt["assessment"]["input_tokens"] == (str(count) if count is not None else None)
    with Session(engine) as session:
        assert session.get(BudgetAccount, proposal.account.account_id).frozen is frozen
        row = session.scalar(select(BudgetReservation))
        assert row.actual_microdollars == cost
        assert row.status == ("settled" if cost is not None else "unknown")


def test_manifest_hash_and_epoch_are_exact_and_disabled_default(readiness):
    engine, store, _, request, _, saved, preview = probe_ready(readiness)
    with pytest.raises(MetaProbeError, match="meta_probe_manifest_changed"):
        store.arm_and_enqueue(request, preview.manifest, "a" * 64)
    with pytest.raises(MetaProbeError, match="meta_probe_unavailable"):
        MetaProbeStore(engine, readiness_policy=READINESS, operator_policy=POLICY).preview(
            request, saved.id, attestation_data()
        )
    BudgetService(engine).disable_execution(preview.manifest.account_id)
    with pytest.raises(MetaProbeError, match="meta_probe_manifest_changed"):
        store.arm_and_enqueue(request, preview.manifest, preview.manifest_sha256)


def test_role_removal_has_distinct_code_while_paid_disable_keeps_private_history(readiness):
    engine, store, _, request, _, _, preview = probe_ready(readiness)
    job = store.arm_and_enqueue(request, preview.manifest, preview.manifest_sha256)
    store.probe_policy = replace(store.probe_policy, paid_probe_enabled=False)
    assert store.get_job(request, job.id).id == job.id
    assert store.list_jobs(request)[0].id == job.id
    store.readiness_policy = replace(READINESS, allowed_actor_ids=())
    with pytest.raises(MetaProbeError, match="readiness_role_unavailable"):
        store.get_job(request, job.id)
    with pytest.raises(MetaProbeError, match="readiness_role_unavailable"):
        store.list_jobs(request)


def test_immutable_records_and_downgrade_guard_schema_check(readiness):
    engine, _, _, _, _, _, _, job, _ = arm(readiness)
    for table in (
        "meta_probe_manifests",
        "meta_probe_approvals",
        "meta_probe_requests",
        "meta_native_identity_bindings",
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            with engine.begin() as connection:
                connection.execute(text(f"DELETE FROM {table}"))
    with engine.connect() as connection:
        original_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with pytest.raises(DBAPIError, match="Cannot discard native verification"):
        command.downgrade(config, "0019_meta_readiness")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == original_head
    command.check(config)
    assert isinstance(job.id, uuid.UUID)


def test_chargeable_transport_fact_is_saved_before_action_semantics(readiness, monkeypatch):
    import polymath.meta_probes as module

    engine, store, dispatcher, _, _, _, _, job, lease = arm(readiness)
    value = store.load_input(lease)
    grant = dispatcher.prepare(lease)
    original = module.assess_probe_observation

    def broken_semantics(*args):
        raise RuntimeError("synthetic semantic failure")

    monkeypatch.setattr(module, "assess_probe_observation", broken_semantics)
    dispatcher.complete(grant, observation(value))
    with pytest.raises(RuntimeError, match="synthetic semantic failure"):
        MetaProbeStore(engine).finalize_known(job.id)
    with Session(engine) as session:
        assert session.scalar(select(BudgetReservation.actual_microdollars)) == 203
        assert session.scalar(select(ResearchExternalRequest.status)) == "observed"
    monkeypatch.setattr(module, "assess_probe_observation", original)
    assert MetaProbeStore(engine).finalize_known(job.id).to_response()["native_status"] == "passed"


@pytest.mark.parametrize("change", ["preparation", "browser"])
def test_arm_rechecks_live_authority_after_account_lock_wait(readiness, monkeypatch, change):
    import polymath.meta_probes as module
    from polymath.run_api import _PrivateHTTPException

    engine, store, _, request, proposal, _, preview = probe_ready(readiness)
    reached = Event()
    original = module._account_lock

    def waiting(session, account_id):
        reached.set()
        return original(session, account_id)

    monkeypatch.setattr(module, "_account_lock", waiting)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with Session(engine) as locker, locker.begin():
            locker.scalar(
                select(BudgetAccount)
                .where(BudgetAccount.id == proposal.account.account_id)
                .with_for_update()
            )
            future = executor.submit(
                store.arm_and_enqueue, request, preview.manifest, preview.manifest_sha256
            )
            assert reached.wait(5)
            if change == "preparation":
                store.readiness_policy = replace(READINESS, preparation_enabled=False)
            else:
                with Session(engine) as session, session.begin():
                    owner = session.scalar(select(OperatorSession.browser_session_hash))
                    session.execute(
                        update(BrowserSession)
                        .where(BrowserSession.token_hash == owner)
                        .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
                    )
        expected = MetaProbeError if change == "preparation" else _PrivateHTTPException
        with pytest.raises(expected):
            future.result(timeout=5)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(MetaProbeManifestRecord)) == 0
        account = session.get(BudgetAccount, proposal.account.account_id)
        assert account.kill_switch and account.native_control_epoch == 0


def test_saved_receipt_revalidates_actual_journal_not_only_its_own_hash(readiness):
    from polymath.evidence import _canonical, _hash

    engine, store, dispatcher, request, _, _, _, job, lease = arm(readiness)
    value = store.load_input(lease)
    grant = dispatcher.prepare(lease)
    dispatcher.complete(grant, observation(value, finish_reason="length"))
    receipt = MetaProbeStore(engine).finalize_known(job.id)
    with Session(engine) as session, session.begin():
        row = session.get(MetaProbeReceipt, receipt.id)
        forged = row.payload | {"native_status": "passed"}
        session.execute(text("ALTER TABLE meta_probe_receipts DISABLE TRIGGER USER"))
        session.execute(
            update(MetaProbeReceipt)
            .where(MetaProbeReceipt.id == receipt.id)
            .values(payload=forged, payload_sha256=_hash(_canonical(forged)))
        )
        session.execute(text("ALTER TABLE meta_probe_receipts ENABLE TRIGGER USER"))
    with pytest.raises(MetaProbeError, match="meta_probe_storage_unavailable"):
        store.get_receipt(request, receipt.id)
