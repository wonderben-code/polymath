"""Real PostgreSQL tests for queue ownership, persistence, and conservative recovery."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url

from polymath.jobs import (
    CheckpointConflict,
    ExternalRequestPending,
    JobLease,
    JobRepository,
    LeaseLost,
    StateConflict,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch):
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL for real PostgreSQL queue tests")
    url = make_url(database_url)
    assert url.drivername == "postgresql+psycopg"
    schema = "polymath_jobs_" + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_engine(scoped, hide_parameters=True)
    try:
        command.upgrade(config, "head")
        yield JobRepository(engine), engine, config
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def expire(engine: Engine, lease: JobLease) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE research_runs "
                "SET lease_expires_at = clock_timestamp() - interval '1 second' "
                "WHERE id = :run_id"
            ),
            {"run_id": lease.run_id},
        )


def test_concurrent_claims_have_exactly_one_winner(jobs) -> None:
    repository, _, _ = jobs
    run = repository.create(mode="explore", goal="Investigate a real concept")
    start = Barrier(8)

    def attempt(index: int):
        start.wait(timeout=10)
        return repository.claim_next(f"worker-{index}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(attempt, range(8)))
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0].run_id == run.id
    assert repository.get(run.id).lease_token == 1
    assert [event.kind for event in repository.events_after(run.id)] == ["queued", "running"]


def test_heartbeat_checkpoint_versions_and_retained_evidence(jobs) -> None:
    repository, _, _ = jobs
    run = repository.create(mode="ask", goal="What evidence would change this answer?")
    lease = repository.claim_next("worker", lease_seconds=30)
    refreshed = repository.heartbeat(lease, lease_seconds=90)
    assert refreshed.token == lease.token
    assert refreshed.expires_at > lease.expires_at
    assert (
        repository.save_checkpoint(
            lease,
            expected_version=0,
            checkpoint={"hypotheses": ["A", "B"], "next_action": "seek_counterevidence"},
            evidence_refs=["paper:one", "passage:two"],
        )
        == 1
    )
    with pytest.raises(CheckpointConflict):
        repository.save_checkpoint(lease, expected_version=0, checkpoint={"stale": True})
    restored = JobRepository(repository.engine).get(run.id)
    assert restored.checkpoint_version == 1
    assert restored.checkpoint["next_action"] == "seek_counterevidence"
    assert restored.evidence_refs == ["paper:one", "passage:two"]


def test_events_are_ordered_idempotent_and_replayable(jobs) -> None:
    repository, _, _ = jobs
    run = repository.create(mode="surprise", goal="")
    lease = repository.claim_next("worker")

    def append(index: int):
        return repository.append_event(
            lease,
            event_key=f"source:{index}",
            kind="source_read",
            summary=f"Read source {index}",
            artifact_refs=[f"paper:{index}"],
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(append, range(12)))
    assert len({event.sequence for event in outcomes}) == 12
    duplicate = append(0)
    assert duplicate == next(event for event in outcomes if event.event_key == "source:0")
    with pytest.raises(StateConflict):
        repository.append_event(
            lease, event_key="source:0", kind="source_read", summary="Different content"
        )
    with ThreadPoolExecutor(max_workers=8) as executor:
        identical = list(
            executor.map(
                lambda _: repository.append_event(
                    lease, event_key="shared", kind="source_read", summary="One shared observation"
                ),
                range(8),
            )
        )
    assert len({event.sequence for event in identical}) == 1
    events = repository.events_after(run.id, sequence=2, limit=100)
    assert [event.sequence for event in events] == list(range(3, 16))
    assert repository.events_after(run.id, sequence=events[-1].sequence) == []


def test_expiry_fences_old_worker_and_requires_explicit_resume(jobs) -> None:
    repository, engine, _ = jobs
    run = repository.create(mode="explore", goal="Persist the research state")
    lease = repository.claim_next("same-worker-id")
    repository.save_checkpoint(lease, expected_version=0, checkpoint={"papers_read": ["one"]})
    expire(engine, lease)
    with pytest.raises(LeaseLost):
        repository.heartbeat(lease)
    with pytest.raises(LeaseLost):
        repository.save_checkpoint(lease, expected_version=1, checkpoint={"overwrite": True})
    assert repository.interrupt_expired() == [run.id]
    assert repository.interrupt_expired() == []
    assert repository.claim_next("new-worker") is None
    interrupted = repository.get(run.id)
    assert interrupted.status == "interrupted"
    assert interrupted.interruption_reason == "worker_lease_expired"
    assert interrupted.checkpoint == {"papers_read": ["one"]}
    with pytest.raises(StateConflict):
        repository.resume_interrupted(run.id, expected_token=lease.token)
    repository.resume_interrupted(run.id, expected_token=interrupted.lease_token)
    replacement = repository.claim_next("same-worker-id")
    assert replacement.token > lease.token
    with pytest.raises(LeaseLost):
        repository.append_event(lease, event_key="late", kind="source_read", summary="Late result")
    assert repository.get(run.id).checkpoint == {"papers_read": ["one"]}


def test_ambiguous_external_work_is_never_automatically_retried(jobs) -> None:
    repository, engine, _ = jobs
    run = repository.create(mode="explore", goal="Avoid a duplicate paid call")
    lease = repository.claim_next("worker")
    repository.save_checkpoint(
        lease,
        expected_version=0,
        checkpoint={"observed_sources": ["one"]},
        evidence_refs=["paper:one"],
    )
    repository.begin_external_request(lease, request_id="model-request-one")
    with pytest.raises(ExternalRequestPending):
        repository.begin_external_request(lease, request_id="model-request-two")
    with pytest.raises(ExternalRequestPending):
        repository.finish(lease, status="failed", outcome={"reason": "timeout"})
    expire(engine, lease)
    repository.interrupt_expired()
    interrupted = repository.get(run.id)
    assert interrupted.status == "interrupted"
    assert interrupted.interruption_reason == "external_outcome_unknown"
    assert interrupted.external_request_in_flight
    assert repository.claim_next("replacement") is None
    with pytest.raises(ExternalRequestPending):
        repository.resume_interrupted(run.id, expected_token=interrupted.lease_token)
    with pytest.raises(LeaseLost):
        repository.complete_external_request(
            lease, request_id="model-request-one", observation={"late": "answer"}
        )
    with pytest.raises(StateConflict):
        repository.reconcile_external(
            run.id,
            expected_token=interrupted.lease_token,
            request_id="different-request",
            outcome="not_sent",
            summary="Wrong request must not be reconciled",
        )
    reconciled = repository.reconcile_external(
        run.id,
        expected_token=interrupted.lease_token,
        request_id="model-request-one",
        outcome="succeeded",
        summary="Provider result retrieved and retained in source storage",
    )
    assert reconciled.status == "interrupted"  # Reconciliation itself never queues paid work.
    assert not reconciled.external_request_in_flight
    assert (
        repository.get_external_request(run.id, request_id="model-request-one").reconciliation[
            "outcome"
        ]
        == "succeeded"
    )
    assert reconciled.evidence_refs == ["paper:one"]
    assert repository.claim_next("replacement") is None
    repository.resume_interrupted(run.id, expected_token=reconciled.lease_token)
    replacement = repository.claim_next("replacement")
    with pytest.raises(StateConflict):
        repository.begin_external_request(replacement, request_id="model-request-one")


def test_observed_external_outcome_and_completed_result_survive_restart(jobs) -> None:
    repository, _, _ = jobs
    run = repository.create(mode="explore", goal="Keep the observed evidence")
    lease = repository.claim_next("worker")
    repository.begin_external_request(lease, request_id="request")
    repository.complete_external_request(
        lease,
        request_id="request",
        observation={"source_id": "one", "passage": "quoted text"},
        evidence_refs=["paper:one", "passage:one"],
    )
    snapshot = repository.get(run.id)
    assert snapshot.checkpoint_version == 0
    assert (
        repository.get_external_request(run.id, request_id="request").observation["source_id"]
        == "one"
    )
    repository.finish(lease, status="succeeded", outcome={"verdict": "insufficient_evidence"})
    restarted = JobRepository(repository.engine).get(run.id)
    assert restarted.status == "succeeded"
    assert restarted.outcome == {"verdict": "insufficient_evidence"}
    assert restarted.evidence_refs == ["paper:one", "passage:one"]
    assert repository.claim_next("other") is None
    with pytest.raises(LeaseLost):
        repository.finish(lease, status="failed", outcome={"replace": "result"})


def test_cancellation_immediately_fences_worker_and_retains_ambiguous_cost(jobs) -> None:
    repository, _, _ = jobs
    run = repository.create(mode="ask", goal="Stop safely")
    lease = repository.claim_next("worker")
    repository.save_checkpoint(
        lease, expected_version=0, checkpoint={"kept": True}, evidence_refs=["paper:one"]
    )
    repository.begin_external_request(lease, request_id="already-sent")
    cancelled = repository.request_cancel(run.id)
    assert cancelled.status == "cancel_requested"
    assert cancelled.lease_token > lease.token
    for operation in (
        lambda: repository.heartbeat(lease),
        lambda: repository.save_checkpoint(lease, expected_version=1, checkpoint={}),
        lambda: repository.begin_external_request(lease, request_id="new-call"),
        lambda: repository.finish(lease, status="succeeded", outcome={}),
    ):
        with pytest.raises(LeaseLost):
            operation()
    final = repository.finalize_cancellation(run.id)
    assert final.status == "cancelled"
    assert final.external_request_in_flight
    assert final.evidence_refs == ["paper:one"]
    assert final.checkpoint == {"kept": True}
    assert repository.finalize_cancellation(run.id).status == "cancelled"
    assert repository.request_cancel(run.id).lease_token == final.lease_token
    assert repository.claim_next("new") is None
    reconciled = repository.reconcile_external(
        run.id,
        expected_token=final.lease_token,
        request_id="already-sent",
        outcome="failed",
        summary="Provider confirmed the operation failed",
    )
    assert reconciled.status == "cancelled"
    with pytest.raises(StateConflict):
        repository.resume_interrupted(run.id, expected_token=reconciled.lease_token)


def test_queued_cancellation_and_cooperative_shutdown(jobs) -> None:
    repository, _, _ = jobs
    queued = repository.create(mode="surprise", goal="")
    assert repository.request_cancel(queued.id).status == "cancelled"
    assert repository.claim_next("worker") is None
    run = repository.create(mode="explore", goal="Resume a cooperative shutdown")
    lease = repository.claim_next("worker")
    stopped = repository.interrupt(lease)
    assert stopped.status == "interrupted"
    assert stopped.interruption_reason == "worker_shutdown"
    assert repository.claim_next("replacement") is None
    with pytest.raises(LeaseLost):
        repository.heartbeat(lease)
    repository.resume_interrupted(run.id, expected_token=stopped.lease_token)
    assert repository.claim_next("replacement").run_id == run.id


def test_durable_job_migration_round_trip_is_repeatable(jobs) -> None:
    _, engine, config = jobs
    command.upgrade(config, "0002_durable_jobs")
    command.downgrade(config, "0001_research_runs")
    assert "research_events" not in inspect(engine).get_table_names()
    columns = {column["name"] for column in inspect(engine).get_columns("research_runs")}
    assert "lease_token" not in columns
    command.upgrade(config, "0002_durable_jobs")
    command.upgrade(config, "0002_durable_jobs")
    columns = {column["name"] for column in inspect(engine).get_columns("research_runs")}
    assert {"lease_token", "checkpoint", "evidence_refs", "outcome"} <= columns
    assert "research_events" in inspect(engine).get_table_names()


def test_invalid_checkpoint_and_bad_finish_state_do_not_mutate_run(jobs) -> None:
    repository, _, _ = jobs
    run = repository.create(mode="explore", goal="Validate persisted data")
    lease = repository.claim_next("worker")
    with pytest.raises(ValueError):
        repository.save_checkpoint(lease, expected_version=0, checkpoint={"bad": float("nan")})
    with pytest.raises(ValueError):
        repository.finish(lease, status="queued", outcome={})
    assert repository.get(run.id).status == "running"
    assert repository.get(run.id).checkpoint_version == 0


def test_full_checkpoint_does_not_block_external_observation_or_reconciliation(jobs) -> None:
    repository, _, _ = jobs
    run = repository.create(mode="explore", goal="Keep checkpoint and outcomes separately bounded")
    lease = repository.claim_next("worker")
    full_checkpoint = {"retained_state": "x" * 262100}
    repository.save_checkpoint(lease, expected_version=0, checkpoint=full_checkpoint)
    repository.begin_external_request(lease, request_id="first")
    repository.complete_external_request(
        lease, request_id="first", observation={"response": "y" * 150000}
    )
    assert repository.get(run.id).checkpoint == full_checkpoint
    assert (
        len(repository.get_external_request(run.id, request_id="first").observation["response"])
        == 150000
    )
    repository.begin_external_request(lease, request_id="second")
    interrupted = repository.interrupt(lease)
    repository.reconcile_external(
        run.id,
        expected_token=interrupted.lease_token,
        request_id="second",
        outcome="not_sent",
        summary="An operator confirmed the request was never sent",
    )
    assert repository.get(run.id).checkpoint == full_checkpoint
    assert not repository.get(run.id).external_request_in_flight
    assert repository.get_external_request(run.id, request_id="first").status == "observed"
    assert repository.get_external_request(run.id, request_id="second").status == "reconciled"


def test_integer_knobs_reject_boolean_and_fractional_values(jobs) -> None:
    repository, _, _ = jobs
    run = repository.create(mode="explore", goal="Reject ambiguous numeric configuration")
    lease = repository.claim_next("worker")
    for invalid in (True, False, 1.5):
        for operation in (
            lambda value: repository.claim_next("other", lease_seconds=value),
            lambda value: repository.heartbeat(lease, lease_seconds=value),
            lambda value: repository.save_checkpoint(lease, expected_version=value, checkpoint={}),
            lambda value: repository.events_after(run.id, sequence=value),
            lambda value: repository.events_after(run.id, limit=value),
            lambda value: repository.interrupt_expired(limit=value),
            lambda value: repository.resume_interrupted(run.id, expected_token=value),
        ):
            with pytest.raises(ValueError):
                operation(invalid)
    for invalid in (None, 0):
        with pytest.raises(ValueError):
            repository.create(mode="surprise", goal=invalid)
    assert repository.get(run.id).status == "running"
    assert repository.get(run.id).checkpoint_version == 0
