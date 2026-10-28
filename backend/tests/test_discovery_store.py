"""Real PostgreSQL discovery boundaries with synthetic model/tool observations."""

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_discovery_controller import DiscoveryFixtureModel, finding_data
from test_discovery_worker import queued, worker
from test_worker import database as database_fixture

from polymath.budget import BudgetService
from polymath.discovery_models import (
    DiscoveryIntent,
    DiscoveryStop,
    StageCommit,
    StageContextRecord,
    StageFinalSelection,
    StageObservation,
    StageParent,
)
from polymath.discovery_store import DiscoveryError, DiscoveryStore, StageValidationError
from polymath.evidence import EvidenceError, EvidenceStore
from polymath.evidence_models import EvidenceQuote, EvidenceResult
from polymath.jobs import JobRepository
from polymath.models import ResearchEvent, ResearchRun
from polymath.research.meta import ModelReply

database = database_fixture
pytestmark = pytest.mark.integration


def count(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


async def test_generic_positive_evidence_write_cannot_bypass_ladder_atomic_commit(
    database, monkeypatch
):
    _, run_id = queued(database)
    original, rejected = DiscoveryStore.commit, []

    def try_generic_write(store, lease, packet):
        before = count(database, EvidenceResult)
        with pytest.raises(EvidenceError, match="discovery_stage_commit_required"):
            EvidenceStore(database).save_result(lease, packet.result)
        assert count(database, EvidenceResult) == before
        assert count(database, StageCommit) == before
        rejected.append(packet.model_request_id)
        return original(store, lease, packet)

    monkeypatch.setattr(DiscoveryStore, "commit", try_generic_write)
    model = DiscoveryFixtureModel(stop_after_stage=1)
    outcome = await worker(database, model).run_once("generic-positive-guard")
    assert outcome.status == "succeeded"
    assert rejected == ["model-0007"]
    assert count(database, EvidenceResult) == count(database, StageCommit) == 1
    with Session(database) as session:
        stage = session.scalar(select(StageCommit))
        assert str(stage.result_id) == outcome.result_id
        assert session.get(ResearchRun, run_id).outcome["result_id"] == outcome.result_id
    assert model.calls == BudgetService(database).usage("fixture").reservations == 8


async def test_stage_flush_failure_rolls_back_evidence_quotes_event_and_ack_then_replays_once(
    database, monkeypatch
):
    _, run_id = queued(database)
    original_flush, saw_flushed_result = Session.flush, []

    def fail_stage_flush(session, *args, **kwargs):
        if any(isinstance(row, StageCommit) for row in session.new):
            # The ordinary result and quotes have already been flushed in THIS
            # transaction. Their visibility here must not survive its rollback.
            seen = session.connection().scalar(select(func.count()).select_from(EvidenceResult))
            saw_flushed_result.append(seen)
            raise RuntimeError("synthetic stage-descriptor persistence failure")
        return original_flush(session, *args, **kwargs)

    initial = DiscoveryFixtureModel(stop_after_stage=1)
    instance = worker(database, initial)
    with monkeypatch.context() as patch:
        patch.setattr(Session, "flush", fail_stage_flush)
        outcome = await instance.run_once("atomic-stage-rollback")
    assert outcome.status == "interrupted" and outcome.result_id is None
    assert saw_flushed_result == [1]
    assert initial.calls == BudgetService(database).usage("fixture").reservations == 7
    for model in (EvidenceResult, EvidenceQuote, StageCommit, StageParent, StageFinalSelection):
        assert count(database, model) == 0
    with Session(database) as session:
        run = session.get(ResearchRun, run_id)
        assert not run.checkpoint.get("discovery_acks")
        assert not (run.outcome or {}).get("result_id")
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResearchEvent)
                .where(ResearchEvent.kind == "stage_committed")
            )
            == 0
        )

    # A deliberate resume recovers the exact journalled action. It consumes no
    # second reservation for those seven observed model responses.
    snapshot = JobRepository(database).get(run_id)
    JobRepository(database).resume_interrupted(run_id, expected_token=snapshot.lease_token)
    replacement = DiscoveryFixtureModel(stop_after_stage=1)
    instance.model_factory = lambda _: replacement
    resumed = await instance.run_once("explicit-atomic-recovery")
    assert resumed.status == "succeeded" and resumed.result_id
    assert replacement.calls == 1
    assert BudgetService(database).usage("fixture").reservations == 8
    assert count(database, EvidenceResult) == count(database, StageCommit) == 1


@pytest.mark.parametrize("changed", ["assessment", "finding", "model_request_id"])
async def test_packet_must_match_the_actual_observed_model_action(database, monkeypatch, changed):
    _, run_id = queued(database)
    original, checked = DiscoveryStore.commit, []

    def reject_forged_packet(store, lease, packet):
        if changed == "assessment":
            forged = packet.model_copy(
                update={"assessment": packet.assessment.model_copy(update={"confidence_score": 99})}
            )
        elif changed == "finding":
            forged = packet.model_copy(
                update={
                    "result": packet.result.model_copy(
                        update={
                            "finding": packet.result.finding.model_copy(
                                update={"title": "Synthetic unobserved finding edit"}
                            )
                        }
                    )
                }
            )
        else:
            forged = packet.model_copy(update={"model_request_id": "model-0006"})
        with pytest.raises(DiscoveryError, match="discovery_commit_action_mismatch"):
            original(store, lease, forged)
        assert count(database, EvidenceResult) == count(database, StageCommit) == 0
        checked.append(changed)
        return original(store, lease, packet)

    monkeypatch.setattr(DiscoveryStore, "commit", reject_forged_packet)
    outcome = await worker(database, DiscoveryFixtureModel(stop_after_stage=1)).run_once(
        "observed-action-binding"
    )
    assert outcome.status == "succeeded" and outcome.run_id == str(run_id)
    assert checked == [changed]
    assert count(database, EvidenceResult) == count(database, StageCommit) == 1
    assert BudgetService(database).usage("fixture").reservations == 8


async def test_previous_stage_searches_cannot_satisfy_a_new_context(database, monkeypatch):
    _, run_id = queued(database)
    original_search, original_commit = DiscoveryStore.record_search, DiscoveryStore.commit
    checked = []

    def omit_new_context_search(store, lease, ordinal, *args, **kwargs):
        if ordinal == 1:
            return None
        return original_search(store, lease, ordinal, *args, **kwargs)

    def reject_global_search_flags(store, lease, packet):
        if packet.context.ordinal == 1:
            with pytest.raises(StageValidationError, match="discovery_search_scope_missing"):
                original_commit(store, lease, packet)
            checked.append(packet.model_request_id)
            raise RuntimeError("synthetic stop after scoped-search rejection")
        return original_commit(store, lease, packet)

    monkeypatch.setattr(DiscoveryStore, "record_search", omit_new_context_search)
    monkeypatch.setattr(DiscoveryStore, "commit", reject_global_search_flags)
    model = DiscoveryFixtureModel()
    outcome = await worker(database, model).run_once("stage-local-search-evidence")
    assert outcome.status == "interrupted" and outcome.result_id is None
    assert checked == ["model-0012"]
    assert model.calls == BudgetService(database).usage("fixture").reservations == 12
    assert count(database, StageContextRecord) == 2
    assert count(database, EvidenceResult) == count(database, StageCommit) == 1
    with Session(database) as session:
        searches = session.scalars(
            select(StageObservation).where(StageObservation.kind == "search")
        ).all()
        assert {row.context_ordinal for row in searches} == {0}
        assert {row.payload["purpose"] for row in searches} >= {"counterevidence", "prior_work"}
        assert session.get(ResearchRun, run_id).outcome is None


async def test_duplicate_scientific_body_ignores_later_usage_and_events(database, monkeypatch):
    class RepeatScientificBody(DiscoveryFixtureModel):
        async def complete(self, messages, *, max_output_tokens):
            reply = await super().complete(messages, max_output_tokens=max_output_tokens)
            action = json.loads(reply.content)
            if self.calls == 12 and action["kind"] == "record_bridge":
                action["finding"] = finding_data(0)
                return ModelReply(json.dumps(action), reply.input_tokens, reply.output_tokens)
            return reply

    queued(database)
    original, packets, checked = DiscoveryStore.commit, [], []

    def reject_duplicate(store, lease, packet):
        if packets:
            assert packet.result.finding == packets[0].result.finding
            assert packet.result.quotes == packets[0].result.quotes
            assert packet.context.selection == packets[0].context.selection
            assert packet.result.usage.model_calls > packets[0].result.usage.model_calls
            assert packet.result.events != packets[0].result.events
            with pytest.raises(StageValidationError, match="discovery_duplicate_scientific_body"):
                original(store, lease, packet)
            checked.append(packet.model_request_id)
            raise RuntimeError("synthetic stop after exact scientific duplicate rejection")
        packets.append(packet)
        return original(store, lease, packet)

    monkeypatch.setattr(DiscoveryStore, "commit", reject_duplicate)
    model = RepeatScientificBody()
    outcome = await worker(database, model).run_once("duplicate-science-not-usage")
    assert outcome.status == "interrupted" and outcome.result_id is None
    assert checked == ["model-0012"]
    assert model.calls == BudgetService(database).usage("fixture").reservations == 12
    assert count(database, EvidenceResult) == count(database, StageCommit) == 1


async def test_discovery_records_are_immutable_and_data_blocks_downgrade(database):
    queued(database)
    outcome = await worker(database, DiscoveryFixtureModel()).run_once("immutable-discovery")
    assert outcome.status == "succeeded"
    tables = (
        (DiscoveryIntent, "research_discovery_intents", "protocol"),
        (StageContextRecord, "research_stage_contexts", "ordinal"),
        (StageObservation, "research_stage_observations", "kind"),
        (StageCommit, "research_stage_commits", "ordinal"),
        (StageParent, "research_stage_parents", "ordinal"),
        (StageFinalSelection, "research_stage_final_selections", "stage_id"),
        (DiscoveryStop, "research_discovery_stops", "run_id"),
    )
    before = {table: count(database, model) for model, table, _ in tables}
    assert all(value > 0 for value in before.values())
    for _, table, column in tables:
        for statement in (f"UPDATE {table} SET {column}={column}", f"DELETE FROM {table}"):
            with pytest.raises(DBAPIError, match="immutable"):
                with database.begin() as connection:
                    connection.execute(text(statement))
    assert {table: count(database, model) for model, table, _ in tables} == before
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with database.connect() as connection:
        original_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
    with pytest.raises(DBAPIError, match="Cannot discard discovery"):
        command.downgrade(config, "0021_meta_research")
    with database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == original_head
    command.check(config)


def test_empty_discovery_migration_round_trip_preserves_metadata(database):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.downgrade(config, "0021_meta_research")
    assert not inspect(database).has_table("research_stage_commits")
    command.upgrade(config, "head")
    assert inspect(database).has_table("research_stage_commits")
    command.check(config)
