"""Real PostgreSQL collection transactions and unchanged historical stage bytes."""

import json
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from test_discovery_controller import DiscoveryFixtureModel
from test_discovery_worker import queued as queued_surprise
from test_discovery_worker import worker as surprise_worker
from test_explore_collection_controller import RecordingModel, prefix, ranking_action, second_branch
from test_explore_worker import queued, worker
from test_memory import owner
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.budget import BudgetService
from polymath.discovery_contracts import DiscoveryIntent
from polymath.discovery_models import DiscoveryIntent as SurpriseIntentRecord
from polymath.discovery_models import StageCommit
from polymath.discovery_store import DiscoveryError, DiscoveryStore
from polymath.explore_collection_admission import (
    bind_explore_collection_intent,
    load_explore_collection_intent,
)
from polymath.explore_collection_contracts import ExploreCollectionIntent
from polymath.explore_collection_models import (
    MODELS,
    ExploreCollectionFinish,
    ExploreCollectionIntentRecord,
    ExploreCollectionStop,
    ExploreRankingRecord,
    ResearchStageOwner,
)
from polymath.explore_collection_store import ExploreCollectionStore
from polymath.jobs import JobRepository
from polymath.meta_research import native_run_input_sha256
from polymath.meta_research_errors import MetaResearchError
from polymath.models import ResearchRun
from polymath.research.discovery import DiscoveryLimits
from polymath.stage_owners import StageOwnerError, has_stage_intent, intent_sha256, load_stage_owner

pytestmark = pytest.mark.integration
database = database_fixture


def snapshot(engine, tables):
    with engine.connect() as connection:
        return {
            table: tuple(
                sorted(
                    connection.scalars(text(f"SELECT row_to_json(t)::text FROM {table} t")).all()
                )
            )
            for table in tables
        }


def foreign_keys(engine, tables):
    with engine.connect() as connection:
        inspector = inspect(connection)
        return {
            table: json.dumps(
                sorted(inspector.get_foreign_keys(table), key=lambda row: row["name"]),
                sort_keys=True,
            )
            for table in tables
        }


async def completed(engine):
    token, run_id = queued(engine)
    model = RecordingModel([*prefix(), *second_branch(), ranking_action])
    result = await worker(engine, model).run_once("store-collection-fixture")
    assert result.status == "succeeded" and model.calls == 13
    return token, run_id


async def test_populated_surprise_migration_round_trip_changes_only_three_owner_fks(database):
    engine = database
    complete_owner, complete_id = queued_surprise(engine)
    instance = surprise_worker(engine, DiscoveryFixtureModel())
    assert (await instance.run_once("old-three-stage-shape")).status == "succeeded"
    partial_owner, partial_id = queued_surprise(engine)
    instance.model_factory = lambda _: DiscoveryFixtureModel()
    instance.limits = DiscoveryLimits(max_model_calls=7, max_actions=20, max_output_tokens=4096)
    assert (await instance.run_once("old-partial-stage-shape")).status == "interrupted"
    store = DiscoveryStore(engine)
    before_detail = (
        store.list_stages(complete_owner, complete_id),
        store.list_stages(partial_owner, partial_id),
    )
    excluded = {model.__tablename__ for model in MODELS} | {"alembic_version"}
    with engine.connect() as connection:
        tables = tuple(
            table for table in inspect(connection).get_table_names() if table not in excluded
        )
    before_rows, before_fks = snapshot(engine, tables), foreign_keys(engine, tables)
    with Session(engine) as session:
        before_inputs = {
            run_id: native_run_input_sha256(session, session.get(ResearchRun, run_id))
            for run_id in (complete_id, partial_id)
        }
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.downgrade(config, "0024_scientific_context")
    assert snapshot(engine, tables) == before_rows
    down_fks = foreign_keys(engine, tables)
    assert {table for table in tables if down_fks[table] != before_fks[table]} == {
        "research_stage_contexts",
        "research_stage_observations",
        "research_stage_commits",
    }
    command.upgrade(config, "head")
    assert snapshot(engine, tables) == before_rows and foreign_keys(engine, tables) == before_fks
    with Session(engine) as session:
        assert {
            run_id: native_run_input_sha256(session, session.get(ResearchRun, run_id))
            for run_id in before_inputs
        } == before_inputs
        assert session.scalar(select(func.count()).select_from(ResearchStageOwner)) == 2
    assert (
        store.list_stages(complete_owner, complete_id),
        store.list_stages(partial_owner, partial_id),
    ) == before_detail
    command.check(config)


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__tablename__)
async def test_every_collection_record_is_immutable(database, model):
    await completed(database)
    table = model.__tablename__
    key = next(iter(model.__table__.primary_key.columns)).name
    with database.connect() as connection:
        assert connection.scalar(text(f"SELECT count(*) FROM {table}")) > 0
    for sql in (f"UPDATE {table} SET {key}={key}", f"DELETE FROM {table}"):
        with pytest.raises(DBAPIError, match="immutable"), database.begin() as connection:
            connection.execute(text(sql))


async def test_historical_collection_readers_survive_browser_expiry_without_new_authority(
    database, monkeypatch
):
    token, run_id = await completed(database)
    store = ExploreCollectionStore(database)
    before = (
        store.summary(token, run_id),
        store.list_stages(token, run_id),
        store.list_rankings(token, run_id),
    )
    money = BudgetService(database).usage("fixture")
    with Session(database) as session, session.begin():
        session.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == token)
            .values(expires_at=func.clock_timestamp() - text("interval '1 day'"))
        )

    def forbidden(*args, **kwargs):
        raise AssertionError("historical reader queried current scientific/native authority")

    # Head/profile mutation cannot rewrite the saved ranking or resolver. Stage
    # detail deliberately computes its separate current-support badge.
    monkeypatch.setattr("polymath.meta_research_contracts.derive_task_profiles", forbidden)
    monkeypatch.setattr(
        "polymath.meta_research_contracts.derive_explore_collection_profile", forbidden
    )
    with Session(database) as session:
        run = session.get(ResearchRun, run_id)
        assert load_stage_owner(session, run).intent == ExploreCollectionIntent()
        assert load_explore_collection_intent(session, run) == ExploreCollectionIntent()
    assert store.summary(token, run_id) == before[0]
    assert store.list_stages(token, run_id) == before[1]
    assert store.list_rankings(token, run_id) == before[2]
    assert BudgetService(database).usage("fixture") == money
    with pytest.raises(DiscoveryError, match="explore_collection_not_found"):
        store.summary(owner(database, "foreign-collection-owner"), run_id)


async def test_finish_flush_failure_rolls_back_ranking_members_ack_and_event_then_replays_free(
    database, monkeypatch
):
    token, run_id = queued(database)
    original_flush, failed = Session.flush, False

    def fail_finish(session, *args, **kwargs):
        nonlocal failed
        if not failed and any(isinstance(row, ExploreCollectionFinish) for row in session.new):
            failed = True
            assert (
                session.connection().scalar(select(func.count()).select_from(ExploreRankingRecord))
                == 1
            )
            raise RuntimeError("synthetic finish flush failure after ranking was staged")
        return original_flush(session, *args, **kwargs)

    model = RecordingModel([*prefix(), *second_branch(), ranking_action])
    instance = worker(database, model)
    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", fail_finish)
        result = await instance.run_once("atomic-rank-and-finish-failure")
    assert result.status == "interrupted" and failed
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(StageCommit)) == 2
        assert session.scalar(select(func.count()).select_from(ExploreRankingRecord)) == 0
        assert session.scalar(select(func.count()).select_from(ExploreCollectionFinish)) == 0
        checkpoint = session.get(ResearchRun, run_id).checkpoint
        assert "explore_ranking_acks" not in checkpoint and "explore_finish_ack" not in checkpoint
    assert not any(
        event.kind in ("bridges_ranked", "collection_finished")
        for event in JobRepository(database).events_after(run_id)
    )
    before = BudgetService(database).usage("fixture")
    snapshot_run = JobRepository(database).get(run_id)
    JobRepository(database).resume_interrupted(run_id, expected_token=snapshot_run.lease_token)
    replay = RecordingModel([])
    instance.model_factory = lambda _: replay
    assert (await instance.run_once("atomic-finish-exact-replay")).status == "succeeded"
    assert replay.calls == 0 and BudgetService(database).usage("fixture") == before
    assert (
        ExploreCollectionStore(database).summary(token, run_id)["selected_final_ranking"]
        is not None
    )


async def test_controller_limit_stop_replays_exactly_without_extra_call_or_duplicate_stop(database):
    token, run_id = queued(database)
    model = RecordingModel([*prefix(), ranking_action])
    instance = worker(database, model, calls=7)
    first = await instance.run_once("collection-limit-first")
    assert first.status == "interrupted" and model.calls == 7
    with Session(database) as session:
        stop = session.get(ExploreCollectionStop, run_id)
        original = (stop.payload, stop.payload_sha256, stop.canonical_payload, stop.created_at)
    before = BudgetService(database).usage("fixture")
    current = JobRepository(database).get(run_id)
    JobRepository(database).resume_interrupted(run_id, expected_token=current.lease_token)
    replay = RecordingModel([])
    instance.model_factory = lambda _: replay
    second = await instance.run_once("same-limit-explicit-replay")
    assert second.status == "interrupted" and replay.calls == 0
    assert BudgetService(database).usage("fixture") == before
    with Session(database) as session:
        stop = session.get(ExploreCollectionStop, run_id)
        assert (
            stop.payload,
            stop.payload_sha256,
            stop.canonical_payload,
            stop.created_at,
        ) == original
        assert session.scalar(select(func.count()).select_from(ExploreCollectionStop)) == 1
    summary = ExploreCollectionStore(database).summary(token, run_id)
    assert summary["selected_final_ranking"] is None and summary["total_usage"]["model_calls"] == 7


def test_new_concrete_intents_require_coherent_shared_registry_and_fixed_mode(database):
    token, run_id = queued(database)
    with Session(database) as session:
        assert load_stage_owner(session, session.get(ResearchRun, run_id)).mode == "explore"
    surprise = DiscoveryIntent()
    with (
        pytest.raises(IntegrityError, match="stage_owner_wrong_surprise_scope"),
        Session(database) as session,
        session.begin(),
    ):
        session.add(
            SurpriseIntentRecord(
                run_id=run_id,
                **surprise.model_dump(mode="json"),
                config_sha256=intent_sha256(surprise),
            )
        )
    ordinary = JobRepository(database).create(mode="explore", goal="Synthetic incomplete restore")
    intent = ExploreCollectionIntent()
    with Session(database) as session, session.begin():
        session.add(
            ExploreCollectionIntentRecord(
                run_id=ordinary.id,
                mode="explore",
                task_kind="research",
                protocol=intent.protocol,
                max_bridges=intent.max_bridges,
                config_sha256=intent_sha256(intent),
            )
        )
    with Session(database) as session:
        assert has_stage_intent(session, ordinary.id)
        with pytest.raises(StageOwnerError):
            load_stage_owner(session, session.get(ResearchRun, ordinary.id))


@pytest.mark.parametrize("damage", ["mode", "hash", "pointer", "both", "no_pointer"])
def test_owner_registry_rejects_malformed_mode_hash_or_pointers(database, damage):
    run = JobRepository(database).create(mode="explore", goal="Synthetic owner constraint")
    intent = ExploreCollectionIntent()
    with Session(database) as session, session.begin():
        session.add(
            ExploreCollectionIntentRecord(
                run_id=run.id,
                mode="explore",
                task_kind="research",
                protocol=intent.protocol,
                max_bridges=3,
                config_sha256=intent_sha256(intent),
            )
        )
    values = dict(
        run_id=run.id,
        mode="explore",
        task_kind="research",
        protocol=intent.protocol,
        config_sha256=intent_sha256(intent),
        explore_intent_run_id=run.id,
    )
    if damage == "mode":
        values["mode"] = "surprise"
    elif damage == "hash":
        values["config_sha256"] = "f" * 64
    elif damage == "pointer":
        values["explore_intent_run_id"] = uuid.uuid4()
    elif damage == "both":
        values["surprise_intent_run_id"] = run.id
    else:
        values["explore_intent_run_id"] = None
    with pytest.raises(IntegrityError), Session(database) as session, session.begin():
        session.add(ResearchStageOwner(**values))


def test_admission_is_initial_exact_idempotent_and_cannot_expand_after_claim(database):
    _, run_id = queued(database)
    with Session(database) as session, session.begin():
        run = session.get(ResearchRun, run_id)
        assert (
            bind_explore_collection_intent(session, run, ExploreCollectionIntent())
            == ExploreCollectionIntent()
        )
        with pytest.raises(MetaResearchError, match="meta_research_input_changed"):
            bind_explore_collection_intent(session, run, ExploreCollectionIntent(max_bridges=4))
    lease = JobRepository(database).claim_next("started-collection-admission")
    assert lease.run_id == run_id
    with Session(database) as session:
        with pytest.raises(MetaResearchError, match="meta_research_input_changed"):
            bind_explore_collection_intent(
                session, session.get(ResearchRun, run_id), ExploreCollectionIntent()
            )


@pytest.mark.parametrize("with_results", [False, True])
async def test_explore_history_blocks_downgrade_without_losing_rows(database, with_results):
    if with_results:
        token, run_id = await completed(database)
    else:
        token, run_id = queued(database)
    before = ExploreCollectionStore(database).summary(token, run_id)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with database.connect() as connection:
        head = connection.scalar(text("SELECT version_num FROM alembic_version"))
    with pytest.raises(DBAPIError, match="Cannot discard Explore"):
        command.downgrade(config, "0024_scientific_context")
    with database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == head
    assert ExploreCollectionStore(database).summary(token, run_id) == before
    command.check(config)


async def test_different_stop_bytes_cannot_replace_the_saved_limit_outcome(database, monkeypatch):
    _, run_id = queued(database)
    observed, original = [], ExploreCollectionStore.record_stop

    def capture(store, lease, outcome):
        observed.append(outcome)
        return original(store, lease, outcome)

    monkeypatch.setattr(ExploreCollectionStore, "record_stop", capture)
    instance = worker(database, RecordingModel([*prefix()]), calls=7)
    assert (await instance.run_once("original-limit-summary")).status == "interrupted"
    with Session(database) as session:
        original_sha = session.get(ExploreCollectionStop, run_id).payload_sha256
    current = JobRepository(database).get(run_id)
    JobRepository(database).resume_interrupted(run_id, expected_token=current.lease_token)
    lease = JobRepository(database).claim_next("conflicting-summary-attempt")
    before = BudgetService(database).usage("fixture")
    changed = observed[0].model_copy(update={"reason": "Different synthetic terminal wording."})
    with pytest.raises(DiscoveryError, match="discovery_stop_replay_conflict"):
        original(ExploreCollectionStore(database), lease, changed)
    with Session(database) as session:
        assert session.get(ExploreCollectionStop, run_id).payload_sha256 == original_sha
    assert BudgetService(database).usage("fixture") == before


async def test_stop_flush_rechecks_expired_browser_before_terminal_checkpoint(
    database, monkeypatch
):
    token, run_id = queued(database)
    original, expired = Session.flush, False

    def expire_after_stop_flush(session, *args, **kwargs):
        nonlocal expired
        inserted = any(isinstance(row, ExploreCollectionStop) for row in session.new)
        result = original(session, *args, **kwargs)
        if inserted and not expired:
            expired = True
            with database.begin() as connection:
                connection.execute(
                    update(BrowserSession)
                    .where(BrowserSession.token_hash == token)
                    .values(expires_at=func.clock_timestamp() - text("interval '1 second'"))
                )
        return result

    monkeypatch.setattr(Session, "flush", expire_after_stop_flush)
    model = RecordingModel([*prefix(), ranking_action])
    result = await worker(database, model).run_once("owner-expired-after-stop-flush")
    assert result.status == "interrupted" and expired
    assert model.calls == BudgetService(database).usage("fixture").reservations == 8
    with Session(database) as session:
        assert session.get(ExploreCollectionStop, run_id) is None
        assert session.get(ResearchRun, run_id).status == "interrupted"
        assert session.scalar(select(func.count()).select_from(StageCommit)) == 1
        # The earlier acknowledged explicit ranking remains immutable audit.
        assert session.scalar(select(func.count()).select_from(ExploreCollectionFinish)) == 1
