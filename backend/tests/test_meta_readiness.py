"""Preparation-only operator budget administration with synthetic provider facts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from test_meta_readiness_contracts import proposal_data
from test_operator_auth import OTHER_TOKEN, POLICY, login
from test_worker import database as database_fixture

from polymath.budget import BudgetService
from polymath.budget_models import BudgetAccount
from polymath.meta_readiness import MetaReadinessStore, ReadinessError
from polymath.meta_readiness_contracts import (
    MetaReadinessProposal,
    ReadinessPolicy,
    proposal_sha256,
)
from polymath.operator_auth import create_operator_router
from polymath.operator_models import OperatorSession
from polymath.readiness_models import MetaReadinessAudit, MetaReadinessProposalRecord
from polymath.run_api import create_run_router

pytestmark = pytest.mark.integration
database = database_fixture
READINESS = ReadinessPolicy(preparation_enabled=True, allowed_actor_ids=(POLICY.actor_id,))


def make_request(client, headers, **changes):
    values = {**headers, "host": "testserver", "origin": "http://testserver", **changes}
    values["cookie"] = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/budget/proposals",
            "root_path": "",
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "headers": [(key.lower().encode(), value.encode()) for key, value in values.items()],
        }
    )


@pytest.fixture
def readiness(database):
    app = FastAPI()
    app.include_router(create_run_router(database))
    app.include_router(create_operator_router(database, POLICY))
    with TestClient(app) as client:
        headers, _ = login(client)
        store = MetaReadinessStore(database, READINESS, POLICY)
        yield database, store, make_request(client, headers), client, headers


def fresh_proposal(**changes):
    return MetaReadinessProposal.model_validate(proposal_data(now=datetime.now(UTC)) | changes)


def prepare(readiness):
    engine, store, request, _, _ = readiness
    proposal = fresh_proposal()
    preview = store.preview(request, proposal)
    saved = store.save(request, proposal, preview.proposal_sha256)
    return engine, store, request, proposal, preview, saved


def test_preview_has_no_account_or_native_side_effects_and_save_is_disabled(readiness):
    engine, store, request, _, _ = readiness
    proposal = fresh_proposal()
    preview = store.preview(request, proposal)
    assert preview.native_status == "not_run" and preview.execution_available is False
    assert preview.per_call_reservation_microdollars == 308192
    assert preview.max_run_reservation_microdollars == 3698304
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetAccount)) == 0
        assert session.scalar(select(func.count()).select_from(MetaReadinessProposalRecord)) == 0
    saved = store.save(request, proposal, preview.proposal_sha256)
    assert saved.to_response()["native_status"] == "not_run"
    assert saved.to_response()["execution_available"] is False
    assert saved.temporal_status == "current"
    with Session(engine) as session:
        account = session.get(BudgetAccount, proposal.account.account_id)
        assert account.execution_enabled is False and account.pricing_verified is False
        assert account.kill_switch is True and account.pricing_reference is None
        assert account.hard_cap_microdollars == proposal.account.hard_cap_microdollars
        assert session.scalar(select(func.count()).select_from(MetaReadinessAudit)) == 1
    assert store.get_proposal(request, saved.id) == saved


def test_exact_preview_hash_is_required_and_same_account_proposal_is_idempotent(readiness):
    engine, store, request, proposal, preview, saved = prepare(readiness)
    assert store.save(request, proposal, preview.proposal_sha256) == saved
    with pytest.raises(ReadinessError, match="readiness_proposal_changed"):
        store.save(request, proposal, "0" * 64)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(MetaReadinessProposalRecord)) == 1
        assert session.scalar(select(func.count()).select_from(MetaReadinessAudit)) == 1


def test_existing_account_caps_are_never_reset_to_match_new_proposal(readiness):
    engine, store, request, _, _ = readiness
    BudgetService(engine).create_account(
        "fixture-account",
        hard_cap_microdollars=100000000,
        daily_cap_microdollars=25000000,
        max_in_flight=1,
    )
    proposal = fresh_proposal()
    with pytest.raises(ReadinessError, match="readiness_account_conflict"):
        store.preview(request, proposal)
    with pytest.raises(ReadinessError, match="readiness_account_conflict"):
        store.save(request, proposal, proposal_sha256(proposal))
    with Session(engine) as session:
        assert session.get(BudgetAccount, "fixture-account").hard_cap_microdollars == 100000000
        assert session.scalar(select(func.count()).select_from(MetaReadinessProposalRecord)) == 0


def test_enabled_account_requires_explicit_disable_before_new_preparation(readiness):
    engine, store, request, _, _ = readiness
    budget = BudgetService(engine)
    budget.create_account(
        "fixture-account", hard_cap_microdollars=150000000, daily_cap_microdollars=25000000
    )
    budget.authorize_execution("fixture-account", pricing_reference="synthetic-legacy-ledger-flag")
    proposal = fresh_proposal()
    with pytest.raises(ReadinessError, match="readiness_account_enabled"):
        store.save(request, proposal, proposal_sha256(proposal))
    disabled = store.disable(request, "fixture-account")
    assert disabled["execution_enabled"] is False and disabled["kill_switch"] is True
    assert disabled["pricing_verified"] is True
    assert store.save(request, proposal, proposal_sha256(proposal)).proposal == proposal.model_dump(
        mode="json"
    )


def test_existing_disabled_pricing_kill_and_freeze_flags_are_preserved(readiness):
    engine, store, request, _, _ = readiness
    budget = BudgetService(engine)
    budget.create_account(
        "fixture-account", hard_cap_microdollars=150000000, daily_cap_microdollars=25000000
    )
    with engine.begin() as connection:
        connection.execute(
            update(BudgetAccount)
            .where(BudgetAccount.id == "fixture-account")
            .values(
                pricing_verified=True,
                pricing_reference="synthetic-old-facts",
                execution_enabled=False,
                kill_switch=False,
                frozen=True,
                frozen_reason="synthetic_freeze",
            )
        )
    proposal = fresh_proposal()
    store.save(request, proposal, proposal_sha256(proposal))
    view = store.inspect_account(request, "fixture-account")
    assert view["pricing_verified"] is True and view["pricing_reference"] == "synthetic-old-facts"
    assert view["kill_switch"] is False and view["frozen"] is True
    assert view["frozen_reason"] == "synthetic_freeze"
    assert view["execution_available"] is False


def test_budget_role_is_explicit_and_publisher_cookie_alone_is_insufficient(readiness):
    engine, _, request, _, _ = readiness
    for policy, code in (
        (ReadinessPolicy(), "readiness_role_unavailable"),
        (
            ReadinessPolicy(preparation_enabled=True, allowed_actor_ids=("different-admin",)),
            "readiness_actor_forbidden",
        ),
    ):
        store = MetaReadinessStore(engine, policy, POLICY)
        with pytest.raises(ReadinessError, match=code):
            store.preview(request, fresh_proposal())


def test_both_csrf_domains_and_same_origin_are_required_inside_store(readiness):
    _, store, _, client, headers = readiness
    for replacement in (
        {"X-CSRF-Token": "0" * 64},
        {"X-Operator-CSRF-Token": "0" * 64},
        {"origin": "https://foreign.invalid"},
    ):
        with pytest.raises(Exception) as caught:
            store.preview(make_request(client, headers, **replacement), fresh_proposal())
        assert caught.value.status_code == 403


def test_preparation_off_preserves_history_inspection_and_safe_disable(readiness):
    _, store, request, proposal, _, saved = prepare(readiness)
    store.policy = replace(READINESS, preparation_enabled=False)
    with pytest.raises(ReadinessError, match="readiness_preparation_disabled"):
        store.preview(request, proposal)
    assert store.get_proposal(request, saved.id) == saved
    assert store.list_proposals(request) == (saved,)
    assert (
        store.inspect_account(request, proposal.account.account_id)["execution_available"] is False
    )
    assert store.disable(request, proposal.account.account_id)["kill_switch"] is True


def test_record_history_and_account_inventory_are_bounded_and_readonly(readiness):
    _, store, request, _, _ = readiness
    records = []
    for index in range(3):
        proposal = fresh_proposal(model_id=f"synthetic-preparation-{index}")
        records.append(store.save(request, proposal, proposal_sha256(proposal)))
    assert len(store.list_proposals(request, limit=2)) == 2
    assert store.list_proposals(request, before=records[1].id) == (records[0],)
    accounts = store.list_accounts(request)
    assert len(accounts) == 1 and accounts[0]["account_id"] == "fixture-account"
    assert store.list_accounts(request, before="fixture-account") == ()
    assert store.inspect_account(request, "fixture-account")["usage"] == {
        "committed_microdollars": "0",
        "daily_committed_microdollars": "0",
        "counted_spend_microdollars": "0",
        "open_hold_microdollars": "0",
        "unknown_hold_microdollars": "0",
        "in_flight": 0,
        "reservations": 0,
    }


def test_fresh_proposals_enforce_documentary_dates_without_rewriting_history(readiness):
    _, store, request, _, _ = readiness
    now = datetime.now(UTC)
    for changes in (
        {
            "observed_at": (now + timedelta(minutes=1)).isoformat(),
            "valid_until": (now + timedelta(minutes=2)).isoformat(),
        },
        {
            "observed_at": (now - timedelta(minutes=2)).isoformat(),
            "valid_until": (now - timedelta(minutes=1)).isoformat(),
        },
    ):
        proposal = fresh_proposal(**changes)
        with pytest.raises(ReadinessError, match="readiness_invalid_request"):
            store.preview(request, proposal)
        with pytest.raises(ReadinessError, match="readiness_invalid_request"):
            store.save(request, proposal, proposal_sha256(proposal))
    short = fresh_proposal(valid_until=(datetime.now(UTC) + timedelta(seconds=1)).isoformat())
    saved = store.save(request, short, proposal_sha256(short))
    Event().wait(1.05)
    historic = store.get_proposal(request, saved.id)
    assert historic.temporal_status == "expired"
    assert historic.proposal_sha256 == saved.proposal_sha256 and historic.quote == saved.quote
    assert store.save(request, short, proposal_sha256(short)).temporal_status == "expired"


def test_restricted_policy_cap_cannot_change_or_reset_existing_accounts(readiness):
    engine, store, request, proposal, _, saved = prepare(readiness)
    store.policy = replace(READINESS, max_hard_cap_microdollars=100000000)
    assert store.get_proposal(request, saved.id) == saved
    assert store.save(request, proposal, proposal_sha256(proposal)) == saved
    with pytest.raises(ReadinessError, match="readiness_invalid_request"):
        store.preview(request, proposal)
    assert (
        store.inspect_account(request, proposal.account.account_id)["hard_cap_microdollars"]
        == 150000000
    )


def test_rotated_operator_credentials_and_expired_roles_deny_existing_history(readiness):
    engine, store, request, proposal, _, saved = prepare(readiness)
    store.operator_policy = replace(POLICY, bootstrap_token=SecretStr(OTHER_TOKEN))
    with pytest.raises(Exception) as caught:
        store.get_proposal(request, saved.id)
    assert caught.value.status_code == 401
    store.operator_policy = POLICY
    with engine.begin() as connection:
        connection.execute(update(OperatorSession).values(expires_at=func.clock_timestamp()))
    with pytest.raises(Exception) as caught:
        store.inspect_account(request, proposal.account.account_id)
    assert caught.value.status_code == 401


def test_proposal_and_operator_audit_are_database_immutable(readiness):
    engine, store, request, proposal, _, _ = prepare(readiness)
    store.disable(request, proposal.account.account_id)
    for model, values in (
        (MetaReadinessProposalRecord, {"native_status": "not_run"}),
        (MetaReadinessAudit, {"action": "safe_disabled"}),
    ):
        for statement in (update(model).values(**values), delete(model)):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(statement)


def test_migration_matches_models_and_cannot_discard_preparation_history(readiness):
    engine, _, _, _, _, _ = prepare(readiness)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.check(config)
    with engine.connect() as connection:
        before = connection.scalar(text("SELECT version_num FROM alembic_version"))
    with pytest.raises(DBAPIError):
        command.downgrade(config, "0018_public_sharing")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == before


def test_distinct_operator_sessions_concurrently_prepare_one_account_and_proposal(readiness):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    engine, store, request, client, _ = readiness
    proposal, start = fresh_proposal(), Barrier(2)
    with TestClient(client.app) as second_client:
        headers, _ = login(second_client)
        second_request = make_request(second_client, headers)

        def save(item):
            start.wait(timeout=10)
            return store.save(item, proposal, proposal_sha256(proposal))

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(save, request), pool.submit(save, second_request)
            assert first.result(timeout=10) == second.result(timeout=10)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetAccount)) == 1
        assert session.scalar(select(func.count()).select_from(MetaReadinessProposalRecord)) == 1
        assert session.scalar(select(func.count()).select_from(MetaReadinessAudit)) == 1


@pytest.mark.parametrize("operation", ["preview", "save"])
def test_preparation_disabled_during_account_advisory_wait_creates_nothing(readiness, operation):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import event

    engine, store, request, _, _ = readiness
    proposal, waiting = fresh_proposal(), Event()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock" in statement:
            waiting.set()

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with engine.begin() as blocker:
                blocker.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 728319046))"),
                    {"name": "polymath-meta-readiness:" + proposal.account.account_id},
                )
                waiting.clear()
                args = (
                    (request, proposal)
                    if operation == "preview"
                    else (request, proposal, proposal_sha256(proposal))
                )
                future = pool.submit(getattr(store, operation), *args)
                assert waiting.wait(timeout=10)
                store.policy = replace(READINESS, preparation_enabled=False)
            with pytest.raises(ReadinessError, match="readiness_preparation_disabled"):
                future.result(timeout=10)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetAccount)) == 0
        assert session.scalar(select(func.count()).select_from(MetaReadinessProposalRecord)) == 0
        assert session.scalar(select(func.count()).select_from(MetaReadinessAudit)) == 0


def test_operator_policy_rotation_during_account_wait_cannot_prepare_new_record(readiness):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import event

    engine, store, request, proposal, _, _ = prepare(readiness)
    changed = fresh_proposal(model_id="another-synthetic-attested-model")
    waiting = Event()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if "SELECT budget_accounts" in statement and "FOR UPDATE" in statement:
            waiting.set()

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with engine.begin() as blocker:
                blocker.execute(
                    select(BudgetAccount)
                    .where(BudgetAccount.id == proposal.account.account_id)
                    .with_for_update()
                )
                waiting.clear()
                future = pool.submit(store.save, request, changed, proposal_sha256(changed))
                assert waiting.wait(timeout=10)
                store.operator_policy = replace(POLICY, bootstrap_token=SecretStr(OTHER_TOKEN))
            with pytest.raises(Exception) as caught:
                future.result(timeout=10)
            assert caught.value.status_code == 401
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(MetaReadinessProposalRecord)) == 1
        assert session.scalar(select(func.count()).select_from(MetaReadinessAudit)) == 1
        assert session.get(BudgetAccount, proposal.account.account_id).execution_enabled is False


@pytest.mark.parametrize("operation", ["preview", "save"])
def test_proposal_expiring_during_account_wait_is_not_prepared(readiness, operation):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import event

    engine, store, request, _, _ = readiness
    proposal = fresh_proposal(valid_until=(datetime.now(UTC) + timedelta(seconds=2)).isoformat())
    waiting = Event()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock" in statement:
            waiting.set()

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with engine.begin() as blocker:
                blocker.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 728319046))"),
                    {"name": "polymath-meta-readiness:" + proposal.account.account_id},
                )
                waiting.clear()
                args = (
                    (request, proposal)
                    if operation == "preview"
                    else (request, proposal, proposal_sha256(proposal))
                )
                future = pool.submit(getattr(store, operation), *args)
                assert waiting.wait(timeout=10)
                Event().wait(2.05)
            with pytest.raises(ReadinessError, match="readiness_invalid_request"):
                future.result(timeout=10)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetAccount)) == 0
        assert session.scalar(select(func.count()).select_from(MetaReadinessProposalRecord)) == 0
