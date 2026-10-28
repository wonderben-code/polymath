"""Actual operator approval and durable public-delegation boundaries, no live calls."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_meta_probes import attestation_data
from test_meta_readiness import readiness as readiness_fixture
from test_meta_research import active_research, review_data
from test_public_research_contracts import limits_data
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.meta_research import MetaResearchError
from polymath.meta_research_models import MetaResearchApproval
from polymath.operator_models import OperatorSession
from polymath.public_delegations import PublicDelegationStore, validate_public_delegation
from polymath.public_research_contracts import PublicLimits, PublicResearchPolicy
from polymath.public_research_models import PublicResearchDelegation
from polymath.public_resource_controls import (
    PublicResourceControls,
    PublicResourcePolicy,
    derive_network_key,
)

database = database_fixture
readiness = readiness_fixture
pytestmark = pytest.mark.integration


def public_preview(readiness, *, short_issuer=True):
    active = active_research(readiness)
    policy = PublicResearchPolicy(enabled=True)
    active.store.public_policy = policy
    active.store.resource_controls = PublicResourceControls(
        active.engine,
        PublicResourcePolicy(
            enabled=True,
            network_key=derive_network_key(active.operator_policy.bootstrap_token),
            limits=policy.resources,
        ),
    )
    if short_issuer:
        with Session(active.engine) as session, session.begin():
            now = session.scalar(select(func.clock_timestamp()))
            session.execute(update(OperatorSession).values(expires_at=now + timedelta(minutes=1)))
            session.execute(update(BrowserSession).values(expires_at=now + timedelta(seconds=90)))
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    store = PublicDelegationStore(active.store)
    preview = store.preview(
        active.request,
        active.saved.id,
        active.canary.id,
        review_data(),
        attestation_data(active.proposal.model_id),
        PublicLimits.model_validate(limits_data()),
        deadline,
    )
    return SimpleNamespace(
        **(
            vars(active)
            | {
                "parent": active.store,
                "delegations": store,
                "public_policy": policy,
                "public_preview": preview,
                "public_deadline": deadline,
                "issuer_client": readiness[3],
                "issuer_headers": readiness[4],
            }
        )
    )


def public_active(readiness, *, short_issuer=True):
    """Reuse this real approval fixture for public admission/HTTP/runtime tests."""
    value = public_preview(readiness, short_issuer=short_issuer)
    activation = value.delegations.activate(
        value.request, value.public_preview.manifest, value.public_preview.manifest_sha256
    )
    value.public_activation = activation
    value.public_activation_sha256 = value.public_preview.manifest_sha256
    return value


def test_public_preview_does_not_arm_and_is_not_capped_by_issuer_login(readiness):
    value = public_preview(readiness)
    assert value.public_preview.manifest.valid_until == value.public_deadline
    assert value.public_preview.manifest.continues_after_operator_logout is True
    with Session(value.engine) as session:
        account = session.get(BudgetAccount, value.policy.native_account_id)
        assert account.native_control_epoch == 2
        assert session.scalar(select(func.count()).select_from(PublicResearchDelegation)) == 0
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 1
        assert value.public_deadline > session.scalar(select(OperatorSession.expires_at))
        assert value.public_deadline > session.scalar(select(BrowserSession.expires_at))


def test_public_activation_pins_exact_deadline_and_does_not_reset_ledger_or_flags(readiness):
    value = public_active(readiness)
    record = value.public_activation.to_response()
    assert record["delegation_current"] is True
    assert record["audience"] == "public_sessions"
    with Session(value.engine) as session:
        approval = session.scalar(
            select(MetaResearchApproval).where(
                MetaResearchApproval.manifest_id == value.public_activation.id
            )
        )
        sidecar = session.get(PublicResearchDelegation, approval.id)
        account = session.get(BudgetAccount, value.policy.native_account_id)
        assert approval.expires_at == sidecar.valid_until == value.public_deadline
        assert account.native_control_epoch == 3 and account.native_scope == "research"
        assert not account.execution_enabled and not account.pricing_verified
        assert session.scalar(select(func.count()).select_from(BudgetAccount)) == 1
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 1
        assert session.scalar(select(BudgetReservation.actual_microdollars)) == 203


def test_exact_retry_after_public_revoke_never_rearms_or_renews(readiness):
    value = public_active(readiness)
    revoked = value.delegations.revoke(value.request, value.public_activation.id).to_response()
    assert revoked["approval"]["revoked_at"] and not revoked["delegation_current"]
    with Session(value.engine) as session:
        epoch = session.get(BudgetAccount, value.policy.native_account_id).native_control_epoch
    replay = value.delegations.activate(
        value.request, value.public_preview.manifest, value.public_activation_sha256
    ).to_response()
    assert replay["id"] == str(value.public_activation.id)
    assert replay["approval"]["expires_at"] == revoked["approval"]["expires_at"]
    assert not replay["delegation_current"]
    with Session(value.engine) as session:
        account = session.get(BudgetAccount, value.policy.native_account_id)
        assert account.native_control_epoch == epoch and account.kill_switch


def test_public_admin_history_and_stop_remain_available_when_public_execution_disabled(readiness):
    value = public_active(readiness)
    value.parent.public_policy = replace(value.public_policy, enabled=False)
    history = value.delegations.list_activations(value.request)
    assert [record.id for record in history] == [value.public_activation.id]
    assert not history[0].to_response()["delegation_current"]
    assert value.delegations.current(value.request)["activation"]["id"] == str(
        value.public_activation.id
    )
    assert value.delegations.revoke(value.request, value.public_activation.id).to_response()[
        "approval"
    ]["revoked_at"]
    with pytest.raises(MetaResearchError, match="meta_research_not_found"):
        value.delegations.get_activation(value.request, value.activation.id)


def test_public_sidecar_historical_validation_does_not_require_live_issuer(readiness):
    from polymath.meta_research import _activation

    value = public_active(readiness)
    expired = datetime.now(UTC) - timedelta(seconds=1)
    with Session(value.engine) as session, session.begin():
        session.execute(update(OperatorSession).values(expires_at=expired, revoked_at=expired))
        session.execute(update(BrowserSession).values(expires_at=expired))
        approval = session.scalar(
            select(MetaResearchApproval).where(
                MetaResearchApproval.manifest_id == value.public_activation.id
            )
        )
        saved, row, manifest, _ = _activation(session, approval.id)
        assert validate_public_delegation(session, saved, row, manifest).approval_id == approval.id


def test_public_exact_hash_and_live_issuance_auth_cannot_be_skipped(readiness):
    value = public_preview(readiness)
    with pytest.raises(MetaResearchError, match="meta_research_manifest_changed"):
        value.delegations.activate(value.request, value.public_preview.manifest, "0" * 64)
    assert (
        value.issuer_client.post(
            "/api/v1/operator/logout", headers=value.issuer_headers
        ).status_code
        == 200
    )
    with pytest.raises(Exception) as rejected:
        value.delegations.activate(
            value.request, value.public_preview.manifest, value.public_preview.manifest_sha256
        )
    assert getattr(rejected.value, "status_code", None) == 401
    with Session(value.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicResearchDelegation)) == 0


@pytest.mark.parametrize("change", ["browser_expiry", "role", "epoch"])
def test_public_issuance_rechecks_authority_after_actual_account_lock_wait(readiness, change):
    value = public_preview(readiness)
    reached = Event()

    def waiting(_connection, _cursor, statement, _parameters, _context, _many):
        if "FROM budget_accounts" in statement and "FOR UPDATE" in statement:
            reached.set()

    event.listen(value.engine, "before_cursor_execute", waiting)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with Session(value.engine) as blocker, blocker.begin():
                account = blocker.scalar(
                    select(BudgetAccount)
                    .where(BudgetAccount.id == value.policy.native_account_id)
                    .with_for_update()
                )
                reached.clear()
                pending = pool.submit(
                    value.delegations.activate,
                    value.request,
                    value.public_preview.manifest,
                    value.public_preview.manifest_sha256,
                )
                assert reached.wait(timeout=5)
                if change == "browser_expiry":
                    with Session(value.engine) as editor, editor.begin():
                        editor.execute(
                            update(BrowserSession).values(
                                expires_at=datetime.now(UTC) - timedelta(seconds=1)
                            )
                        )
                elif change == "role":
                    value.parent.readiness_policy = replace(
                        value.readiness_policy, allowed_actor_ids=()
                    )
                else:
                    account.native_control_epoch += 1
            with pytest.raises(Exception) as rejected:
                pending.result(timeout=10)
            assert isinstance(rejected.value, MetaResearchError) or (
                getattr(rejected.value, "status_code", None) == 401
            )
    finally:
        event.remove(value.engine, "before_cursor_execute", waiting)
    with Session(value.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicResearchDelegation)) == 0


def test_historical_public_parser_rejects_sidecar_policy_mismatch(readiness):
    from polymath.meta_research import _activation

    value = public_active(readiness)
    with Session(value.engine) as session:
        approval = session.scalar(
            select(MetaResearchApproval).where(
                MetaResearchApproval.manifest_id == value.public_activation.id
            )
        )
        approval_id = approval.id
    with pytest.raises(IntegrityError):
        with Session(value.engine) as session, session.begin():
            session.execute(
                update(PublicResearchDelegation)
                .where(PublicResearchDelegation.approval_id == approval_id)
                .values(public_policy_sha256="0" * 64)
            )
    with Session(value.engine) as session:
        # The immutable DB blocks tampering. Independently prove the historical
        # validator also rejects a mismatched in-memory sidecar before any flush.
        sidecar = session.get(PublicResearchDelegation, approval_id)
        sidecar.public_policy_sha256 = "0" * 64
        with session.no_autoflush:
            with pytest.raises(MetaResearchError, match="meta_research_storage_unavailable"):
                _activation(session, approval_id)


def test_sidecar_presence_cannot_upgrade_private_historical_approval(readiness):
    from polymath.meta_research import _activation

    value = public_active(readiness)
    with Session(value.engine) as session, session.begin():
        private = session.scalar(
            select(MetaResearchApproval).where(
                MetaResearchApproval.manifest_id == value.activation.id
            )
        )
        private_id = private.id
        session.add(
            PublicResearchDelegation(
                approval_id=private.id,
                account_id=value.proposal.account.account_id,
                manifest_sha256=private.manifest_sha256,
                authorization_mode="durable_owner_delegation",
                sponsor_operator_session_hash=private.operator_session_hash,
                sponsor_browser_session_hash=private.browser_session_hash,
                sponsor_actor_id=private.actor_id,
                sponsor_policy_sha256=value.public_preview.manifest.sponsor_policy_sha256,
                public_policy_sha256=value.public_preview.manifest.public_policy_sha256,
                valid_until=private.expires_at,
            )
        )
    with Session(value.engine) as session:
        with pytest.raises(MetaResearchError, match="meta_research_storage_unavailable"):
            _activation(session, private_id)
