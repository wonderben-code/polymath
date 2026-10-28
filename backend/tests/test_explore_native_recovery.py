"""Original collection-profile replay/accounting, without keys or paid HTTP."""

import asyncio

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from test_explore_collection_api import approve_collection
from test_explore_collection_controller import GOAL
from test_explore_native_worker import admitted
from test_meta_probe_worker import ACTION
from test_meta_readiness import readiness as readiness_fixture
from test_native_research_worker import native as native_fixture
from test_public_delegations import public_active
from test_run_api import bootstrap
from test_scientific_native_recovery import (
    expire_requester,
    prepare_and_observe,
    target_rows,
)
from test_scientific_native_recovery import historical_traps as scientific_historical_traps
from test_scientific_native_worker import scientific_client
from test_worker import database as database_fixture

import polymath.meta_research_contracts as contracts
import polymath.native_research_worker as runtime
import polymath.public_research_contracts as public_contracts
import polymath.research.explore_collection as collection_controller
from polymath.budget import BudgetService
from polymath.explore_collection_contracts import ExploreCollectionIntent
from polymath.jobs import JobRepository
from polymath.meta_readiness_contracts import quote_proposal
from polymath.meta_research import MetaResearchStore
from polymath.meta_research_dispatch import MetaResearchDispatcher
from polymath.native_research_worker import NativeRecordedModel, original_runtime_profile
from polymath.worker import ExecutionStopped, _RunContext

database = database_fixture
native = native_fixture
readiness = readiness_fixture
pytestmark = pytest.mark.integration


def claim_collection(value, run_id):
    lease = JobRepository(value.engine).claim_next(
        "collection-original-profile-recovery",
        lease_seconds=300,
        task_kinds=("research",),
        native_only=True,
        native_account_id=value.policy.native_account_id,
    )
    assert lease is not None and lease.run_id == run_id
    original = value.store.load_original_input(lease)
    profile = original_runtime_profile(original)
    assert original.profile.controller_protocol == "polymath-meta-explore-collection-v1"
    assert original.profile.max_bridges == 3  # The run narrows this original approved maximum.
    messages = collection_controller.explore_collection_initial_messages(
        GOAL, ExploreCollectionIntent(max_bridges=2), memory_available=True
    )
    return lease, original, profile, messages


def historical_traps(monkeypatch):
    forbidden = scientific_historical_traps(monkeypatch)
    for module in (contracts, public_contracts):
        for name in (
            "derive_explore_collection_profile",
            "explore_collection_controller_sha256",
            "minimum_explore_collection_characters",
            "validate_current_explore_collection_profile",
        ):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(collection_controller, "explore_collection_initial_messages", forbidden)
    monkeypatch.setattr(collection_controller, "explore_collection_protocol_payload", forbidden)
    return forbidden


def close_original(value, run_id, *, public=False):
    if public:
        value.delegations.revoke(value.request, value.public_activation.id)
    else:
        value.store.revoke(value.request, value.activation.id)
    expire_requester(value, run_id)


async def known_original_response(value, monkeypatch, capsys, *, public=False):
    with scientific_client(value, public=public) as client:
        headers = bootstrap(client) if public else None
        run_id = admitted(value, client, maximum=2, public=public, headers=headers)
    lease, original, profile, messages = claim_collection(value, run_id)
    before = BudgetService(value.engine).usage(original.account_id)
    _, grant, observation, calls = await prepare_and_observe(value, lease, original, messages)
    await asyncio.to_thread(close_original, value, run_id, public=public)
    forbidden = historical_traps(monkeypatch)
    sql = []

    def record_sql(connection, cursor, statement, parameters, context, executemany):
        sql.append(statement.lower())

    event.listen(Engine, "before_cursor_execute", record_sql)
    try:
        no_key_store = MetaResearchStore(value.engine)
        no_key_dispatcher = MetaResearchDispatcher(value.engine)
        # A granted request's known facts remain billable after both its requester
        # and issuer expire/revoke. No current prompt/profile/key can be required.
        await asyncio.to_thread(no_key_dispatcher.complete, grant, observation)
        after = BudgetService(value.engine).usage(original.account_id)
        assert after.committed_microdollars == before.committed_microdollars + 40
        assert after.in_flight == 0 and after.reservations == before.reservations + 1
        restored = no_key_store.load_original_input(lease)
        assert original_runtime_profile(restored) == profile
        assert restored.profile_sha256 == original.profile_sha256
        assert restored.input_sha256 == original.input_sha256
        replay = NativeRecordedModel(
            _RunContext(value.engine, lease, 300),
            no_key_store,
            no_key_dispatcher,
            profile,
            forbidden,
        )
        response = await replay.complete(
            messages, max_output_tokens=original.profile.max_output_tokens
        )
        assert response.content == ACTION
        assert (response.input_tokens, response.output_tokens) == (10, 15)
        receipt = no_key_store.finalize_known_call(run_id, "model-0001").to_response()
        assert receipt["usage"]["actual_microdollars"] == 40
        assert receipt["scientific_completion_implied"] is False
        settings = runtime._FinalizeDatabaseSettings(
            database_url=value.engine.url.render_as_string(hide_password=False), _env_file=None
        )
        assert (
            await runtime.run(
                settings, once=False, worker_id="free-collection-finalize", finalize_only=run_id
            )
            == 0
        )
        assert "billing finalized" in capsys.readouterr().out
        assert no_key_store.finalize_known_call(run_id, "model-0001").to_response() == receipt
        with pytest.raises(ExecutionStopped):
            await replay.complete(messages, max_output_tokens=original.profile.max_output_tokens)
        rows = target_rows(value, run_id)
        assert rows[0] == "settled" and rows[2] == 40 and rows[3] == "observed"
        assert rows[5:] == (1, 1)
        assert BudgetService(value.engine).usage(original.account_id) == after
        assert len(calls) == 1
        assert not any("finding_heads" in statement for statement in sql)
    finally:
        event.remove(Engine, "before_cursor_execute", record_sql)


async def test_collection_known_observation_settles_and_replays_after_all_authority_closes(
    native, monkeypatch, capsys
):
    approve_collection(native)
    await known_original_response(native, monkeypatch, capsys)


async def test_public_collection_original_observation_has_keyless_db_only_late_accounting(
    readiness, monkeypatch, capsys
):
    public = approve_collection(public_active(readiness, short_issuer=False), public=True)
    await known_original_response(public, monkeypatch, capsys, public=True)


@pytest.mark.parametrize("failure", ["unreported_usage", "lost_before_journal"])
async def test_unknown_collection_protocol_never_resends_or_releases_original_hold(
    native, monkeypatch, capsys, failure
):
    approve_collection(native)
    with scientific_client(native) as client:
        run_id = admitted(native, client, maximum=2)
    lease, original, profile, messages = claim_collection(native, run_id)
    before = BudgetService(native.engine).usage(original.account_id)
    dispatcher, grant, observation, calls = await prepare_and_observe(
        native, lease, original, messages, failure=failure
    )
    if failure == "lost_before_journal":
        # The actual response was not durably recorded before simulated loss.
        dispatcher.mark_unknown(grant, "worker_lost")
    else:
        dispatcher.complete(grant, observation)
    close_original(native, run_id)
    forbidden = historical_traps(monkeypatch)
    no_key_store = MetaResearchStore(native.engine)
    no_key_dispatcher = MetaResearchDispatcher(native.engine)
    restored = no_key_store.load_original_input(lease)
    assert original_runtime_profile(restored) == profile
    replay = NativeRecordedModel(
        _RunContext(native.engine, lease, 300), no_key_store, no_key_dispatcher, profile, forbidden
    )
    for _ in range(2):
        with pytest.raises(ExecutionStopped):
            await replay.complete(messages, max_output_tokens=original.profile.max_output_tokens)
    settings = runtime._FinalizeDatabaseSettings(
        database_url=native.engine.url.render_as_string(hide_password=False), _env_file=None
    )
    assert (
        await runtime.run(
            settings, once=False, worker_id="free-unknown-collection-finalize", finalize_only=run_id
        )
        == 2
    )
    assert "no request sent" in capsys.readouterr().out
    saved = target_rows(native, run_id)
    estimate = quote_proposal(original.proposal).per_call_reservation_microdollars
    assert saved[:3] == ("unknown", estimate, None)
    assert saved[3] == ("prepared" if failure == "lost_before_journal" else "observed")
    assert saved[5] == 1
    after = BudgetService(native.engine).usage(original.account_id)
    assert after.committed_microdollars == before.committed_microdollars + estimate
    assert after.in_flight == 1 and after.reservations == before.reservations + 1
    assert len(calls) == 1
