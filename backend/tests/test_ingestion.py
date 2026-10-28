"""Real PostgreSQL watch/cursor boundaries; sources are explicit synthetic fixtures."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_evidence import document
from test_memory import assign, owner
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.evidence import EvidenceStore
from polymath.ingestion import IngestionError, IngestionRepository
from polymath.ingestion_models import (
    IngestionPage,
    IngestionProviderGate,
    IngestionTick,
    LiteratureWatch,
    WatchSeenSourceVersion,
)
from polymath.jobs import JobRepository, LeaseLost
from polymath.models import ResearchRun

database = database_fixture
pytestmark = pytest.mark.integration


def create_watch(engine, *, identity="watch-owner", label="Synthetic watch"):
    repository = IngestionRepository(engine)
    token = owner(engine, identity)
    watch = repository.create(token, label=label, query="Synthetic bounded evidence")
    return repository, token, watch


def start(engine, *, identity="watch-owner"):
    repository, token, watch = create_watch(engine, identity=identity)
    tick = repository.run_now(token, watch.id)
    lease = JobRepository(engine).claim_next(
        "synthetic-ingestion", lease_seconds=300, task_kinds=("ingest_literature",)
    )
    assert lease.run_id == tick.run_id
    return repository, token, watch, lease


def finish_and_allow_next(engine, watch_id, lease):
    JobRepository(engine).finish(
        lease,
        status="succeeded",
        outcome={
            "ingestion_status": "partial",
            "model_calls": 0,
        },
    )
    with engine.begin() as connection:
        connection.execute(
            update(LiteratureWatch)
            .where(LiteratureWatch.id == watch_id)
            .values(last_run_at=func.clock_timestamp() - text("interval '61 seconds'"))
        )


def test_watch_scope_window_and_source_body_are_actual_persisted_readings(database):
    repository, token, watch, lease = start(database)
    initial = repository.snapshot(lease)
    assert initial.cursors["openalex"].until - initial.cursors["openalex"].since == timedelta(
        days=7
    )
    with Session(database) as session:
        assert watch.expires_at == session.get(BrowserSession, token).expires_at
        assert session.get(ResearchRun, lease.run_id).task_kind == "ingest_literature"
    source = document()
    artifact = EvidenceStore(database).save_source(lease, source)
    page = repository.commit_page(
        lease,
        "openalex",
        initial.cursors["openalex"],
        None,
        (artifact,),
        {"discovered": 1},
        exhausted=True,
    )
    assert page.added_sources == 1
    saved = repository.list_sources(token, watch.id)
    assert len(saved) == 1 and saved[0].id == artifact.version_id
    assert "text" not in saved[0].to_response()
    body = repository.get_source_document(token, watch.id, artifact.version_id)
    assert body["document"] == source.model_dump(mode="json")
    assert body["content_sha256"] == source.content_sha256
    assert body["assessment_status"] == "unassessed"
    assert token not in str(watch.to_response())
    assert repository.snapshot(lease).completed_providers == ("openalex",)
    assert repository.snapshot(lease).provider_results["openalex"]["added_sources"] == 1


def test_concurrent_manual_and_scheduled_enqueue_do_not_duplicate_a_tick(database):
    repository, token, watch = create_watch(database)
    barrier = Barrier(2)

    def attempt():
        barrier.wait(timeout=5)
        try:
            return repository.run_now(token, watch.id).run_id
        except IngestionError as error:
            assert str(error) in {"ingestion_active_run_limit", "watch_check_throttled"}
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(value is not None for value in outcomes) == 1
    assert repository.schedule_due() == ()
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(IngestionTick)) == 1


def test_scheduler_skips_busy_owners_before_bounded_candidate_limit(database):
    repository = IngestionRepository(database)
    busy = owner(database, "busy-owner")
    for index in range(5):
        repository.create(busy, label=f"Busy watch {index}", query="Synthetic evidence")
    paid = JobRepository(database).create(mode="explore", goal="Synthetic separate research")
    assign(database, paid.id, busy)
    eligible, eligible_owner, watch = create_watch(database, identity="eligible-owner")
    ticks = eligible.schedule_due(limit=5)
    assert len(ticks) == 1 and ticks[0].watch_id == watch.id
    assert eligible.get(eligible_owner, watch.id).active_run_id == ticks[0].run_id


def test_scheduler_skip_locked_and_no_catchup_storm(database):
    repository = IngestionRepository(database)
    watches = []
    for index in range(6):
        _, _, watch = create_watch(database, identity=f"scheduler-owner-{index}")
        watches.append(watch)
    with database.begin() as blocker:
        blocker.execute(
            select(LiteratureWatch).where(LiteratureWatch.id == watches[0].id).with_for_update()
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            ticks = pool.submit(repository.schedule_due, limit=5).result(timeout=10)
        assert len(ticks) == 5
        assert watches[0].id not in {tick.watch_id for tick in ticks}
    assert len(repository.schedule_due(limit=5)) == 1
    assert repository.schedule_due(limit=5) == ()
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(IngestionTick)) == 6


def test_pause_atomically_fences_active_tick_and_preserves_cursors(database):
    repository, token, watch, lease = start(database)
    before = repository.snapshot(lease).cursors
    paused = repository.pause(token, watch.id)
    assert not paused.enabled and paused.active_run_id is None
    assert JobRepository(database).get(lease.run_id).status == "cancelled"
    with pytest.raises((IngestionError, LeaseLost)):
        repository.acquire_provider_slot(lease, "arxiv")
    assert repository.get(token, watch.id).cursors == before
    assert repository.resume(token, watch.id).enabled
    with pytest.raises(IngestionError, match="watch_check_throttled"):
        repository.run_now(token, watch.id)


def test_atomic_page_failure_rolls_back_all_additions_and_cursor(database):
    repository, token, watch, lease = start(database)
    expected = repository.snapshot(lease).cursors["openalex"]
    source = EvidenceStore(database).save_source(lease, document())
    forged = replace(source, version_id=uuid.uuid4(), retrieval_id=uuid.uuid4())
    with pytest.raises(IngestionError, match="ingestion_source_not_read"):
        repository.commit_page(lease, "openalex", expected, "next-page", (source, forged))
    assert repository.get(token, watch.id).cursors["openalex"] == expected
    assert repository.list_sources(token, watch.id) == ()
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(IngestionPage)) == 0
        assert session.scalar(select(func.count()).select_from(WatchSeenSourceVersion)) == 0


def test_concurrent_page_replay_is_idempotent_but_second_page_same_tick_is_refused(database):
    repository, token, watch, lease = start(database)
    expected = repository.snapshot(lease).cursors["openalex"]
    source = EvidenceStore(database).save_source(lease, document())
    barrier = Barrier(2)

    def commit():
        barrier.wait(timeout=5)
        return repository.commit_page(lease, "openalex", expected, "next-page", (source,))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(commit), pool.submit(commit)
        assert first.result(timeout=10) == second.result(timeout=10)
    assert repository.get(token, watch.id).source_count == 1
    with pytest.raises(IngestionError, match="ingestion_page_conflict"):
        repository.commit_page(lease, "openalex", expected, "another-page", (source,))
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(IngestionPage)) == 1


def test_changed_retrieval_time_is_not_a_new_source_version(database):
    repository, token, watch, lease = start(database)
    original = document()
    source = EvidenceStore(database).save_source(lease, original)
    repository.commit_page(
        lease,
        "openalex",
        repository.snapshot(lease).cursors["openalex"],
        None,
        (source,),
        exhausted=True,
    )
    finish_and_allow_next(database, watch.id, lease)
    repository.run_now(token, watch.id)
    replacement = JobRepository(database).claim_next(
        "next-tick", lease_seconds=300, task_kinds=("ingest_literature",)
    )
    reread = EvidenceStore(database).save_source(
        replacement,
        original.model_copy(
            update={
                "retrieved_at": original.retrieved_at + timedelta(hours=1),
                "title": "Updated public title",
            }
        ),
    )
    assert reread.version_id == source.version_id and reread.retrieval_id != source.retrieval_id
    page = repository.commit_page(
        replacement,
        "openalex",
        repository.snapshot(replacement).cursors["openalex"],
        None,
        (reread,),
        exhausted=True,
    )
    assert page.added_sources == 0 and repository.get(token, watch.id).source_count == 1
    assert repository.get_source_document(token, watch.id, source.version_id)[
        "document"
    ] == original.model_dump(mode="json")


def test_failed_provider_retains_window_while_completed_provider_rolls_with_overlap(database):
    repository, token, watch, lease = start(database)
    original = repository.snapshot(lease).cursors
    repository.commit_page(lease, "openalex", original["openalex"], None, (), exhausted=True)
    repository.record_provider_failure(lease, "arxiv", original["arxiv"], "access_denied")
    snapshot = repository.snapshot(lease)
    assert snapshot.cursors["arxiv"] == original["arxiv"]
    assert snapshot.provider_results["arxiv"]["status"] == "failed"
    finish_and_allow_next(database, watch.id, lease)
    repository.run_now(token, watch.id)
    next_lease = JobRepository(database).claim_next(
        "next-window", lease_seconds=300, task_kinds=("ingest_literature",)
    )
    cursors = repository.snapshot(next_lease).cursors
    assert cursors["arxiv"] == original["arxiv"]
    assert cursors["openalex"].since == original["openalex"].until - timedelta(days=1)
    assert cursors["openalex"].until > original["openalex"].until


@pytest.mark.parametrize("phase", ["before_tick", "active_tick"])
def test_mutable_cursor_cannot_override_immutable_tick_and_page_history(database, phase):
    repository, token, watch = create_watch(database)
    lease = None
    if phase == "active_tick":
        repository.run_now(token, watch.id)
        lease = JobRepository(database).claim_next(
            "cursor-probe", lease_seconds=300, task_kinds=("ingest_literature",)
        )
    value = {provider: cursor.to_dict() for provider, cursor in watch.cursors.items()}
    value["openalex"]["revision"] = 99
    with database.begin() as connection:
        connection.execute(
            update(LiteratureWatch)
            .where(LiteratureWatch.id == watch.id)
            .values(provider_cursors=value)
        )
    with pytest.raises(IngestionError, match="ingestion_cursor_changed"):
        repository.check_owner(lease) if lease else repository.run_now(token, watch.id)


@pytest.mark.parametrize("expiry", ["browser", "lease"])
def test_expiry_after_waiting_for_provider_gate_prevents_new_permission(
    database, monkeypatch, expiry
):
    import polymath.ingestion as ingestions
    import polymath.jobs as jobs

    repository, token, _, lease = start(database)
    reached, future = Event(), [None]
    original_context, original_job_now = ingestions._worker_context, jobs._now

    def context(*args, **kwargs):
        result = original_context(*args, **kwargs)
        reached.set()
        return result

    monkeypatch.setattr(ingestions, "_worker_context", context)
    monkeypatch.setattr(jobs, "_now", lambda session: future[0] or original_job_now(session))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.begin() as blocker:
            blocker.execute(
                select(IngestionProviderGate)
                .where(IngestionProviderGate.provider == "arxiv")
                .with_for_update()
            )
            pending = pool.submit(repository.acquire_provider_slot, lease, "arxiv")
            assert reached.wait(timeout=5)
            if expiry == "browser":
                blocker.execute(
                    update(BrowserSession)
                    .where(BrowserSession.token_hash == token)
                    .values(expires_at=func.clock_timestamp() - text("interval '1 second'"))
                )
            else:
                future[0] = lease.expires_at + timedelta(seconds=1)
        with pytest.raises((IngestionError, LeaseLost)):
            pending.result(timeout=10)
    with Session(database) as session:
        assert session.get(IngestionProviderGate, "arxiv").sequence == 0


def test_global_provider_gate_spaces_two_independent_workers(database):
    repository, _, _, first = start(database, identity="gate-owner-one")
    _, _, _, second = start(database, identity="gate-owner-two")
    assert repository.acquire_provider_slot(first, "arxiv") == 0
    delay = repository.acquire_provider_slot(second, "arxiv")
    assert 0 < delay <= 3
    with Session(database) as session:
        assert session.get(IngestionProviderGate, "arxiv").sequence == 1


def test_expired_free_ticks_are_cancelled_without_touching_other_research(database):
    repository, token, watch, lease = start(database, identity="expiring-owner")
    original = document()
    artifact = EvidenceStore(database).save_source(lease, original)
    repository.commit_page(
        lease,
        "openalex",
        repository.snapshot(lease).cursors["openalex"],
        None,
        (artifact,),
        exhausted=True,
    )
    paid = JobRepository(database).create(mode="explore", goal="Synthetic protected research job")
    assign(database, paid.id, token)
    _, live_owner, eligible = create_watch(database, identity="live-owner")
    with database.begin() as connection:
        connection.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == token)
            .values(expires_at=func.clock_timestamp() - text("interval '1 second'"))
        )
    ticks = repository.schedule_due()
    assert len(ticks) == 1 and ticks[0].watch_id == eligible.id
    assert JobRepository(database).get(lease.run_id).status == "cancelled"
    assert JobRepository(database).get(paid.id).status == "queued"
    with Session(database) as session:
        expired = session.get(LiteratureWatch, watch.id)
        assert not expired.enabled and expired.active_run_id is None
        assert session.scalar(select(func.count()).select_from(IngestionTick)) == 2
        assert session.scalar(select(func.count()).select_from(IngestionPage)) == 1
        assert session.scalar(select(func.count()).select_from(WatchSeenSourceVersion)) == 1
    assert EvidenceStore(database).get_document(lease.run_id, artifact.retrieval_id) == original
    assert repository.get(live_owner, eligible.id).active_run_id == ticks[0].run_id


def test_source_cap_rolls_back_overflow_and_blocks_later_get_permissions(database, monkeypatch):
    import polymath.ingestion as ingestions

    monkeypatch.setattr(ingestions, "MAX_WATCH_SOURCE_VERSIONS", 1)
    repository, token, watch, lease = start(database)
    expected = repository.snapshot(lease).cursors["openalex"]
    store = EvidenceStore(database)
    artifacts = tuple(store.save_source(lease, document(f"fixture:{index}")) for index in range(2))
    with pytest.raises(IngestionError, match="ingestion_watch_source_limit"):
        repository.commit_page(lease, "openalex", expected, None, artifacts, exhausted=True)
    assert repository.get(token, watch.id).source_count == 0
    assert repository.get(token, watch.id).cursors["openalex"] == expected
    repository.commit_page(lease, "openalex", expected, None, (artifacts[0],), exhausted=True)
    assert repository.snapshot(lease).watch.remaining_source_capacity == 0
    with pytest.raises(IngestionError, match="ingestion_watch_source_limit"):
        repository.acquire_provider_slot(lease, "arxiv")


@pytest.mark.parametrize("model", [IngestionTick, IngestionPage, WatchSeenSourceVersion])
def test_tick_page_and_source_history_are_immutable(database, model):
    repository, _, _, lease = start(database)
    source = EvidenceStore(database).save_source(lease, document())
    repository.commit_page(
        lease,
        "openalex",
        repository.snapshot(lease).cursors["openalex"],
        None,
        (source,),
        exhausted=True,
    )
    with pytest.raises(IntegrityError), database.begin() as connection:
        connection.execute(delete(model))


def test_downgrade_refuses_to_discard_watch_or_ingestion_history(database):
    create_watch(database)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with pytest.raises(Exception, match="Cannot discard literature watch or ingestion history"):
        command.downgrade(config, "0014_source_bibliography")
