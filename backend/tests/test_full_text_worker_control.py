"""Frozen identity reads stop safely before repair dispatch, using synthetic HTTP."""

import asyncio
from dataclasses import replace
from threading import Event

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_full_text import FullTextFixtureModel
from test_full_text_worker import assert_complete, counts, queued, replacement, resume, worker_for
from test_worker import database as database_fixture

from polymath.budget import BudgetService
from polymath.evidence_models import EvidenceResult
from polymath.full_text import FullTextStore
from polymath.jobs import JobRepository

database = database_fixture
pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["response_loss", "timeout"])
async def test_identity_context_failure_requires_explicit_resume_without_new_charge(
    database, monkeypatch, stage
):
    run_id, model, requests = queued(database), FullTextFixtureModel(), []
    worker = worker_for(database, model, requests)
    worker.limits = replace(worker.limits, tool_timeout_seconds=0.5)
    original = FullTextStore.owned_identity_context
    read_finished, release, thread_finished = Event(), Event(), Event()
    first = True

    def lost_after_read(store, lease):
        nonlocal first
        result = original(store, lease)
        if first:
            first = False
            read_finished.set()
            if stage == "response_loss":
                raise RuntimeError("PRIVATE-CONTEXT-RESPONSE-LOSS")
            # The read transaction has completed, but its thread response has
            # not reached the controller when wait_for cancels the wrapper.
            release.wait(timeout=5)
            thread_finished.set()
        return result

    monkeypatch.setattr(FullTextStore, "owned_identity_context", lost_after_read)
    try:
        outcome = await worker.run_once("identity-control-first-worker")
        assert outcome.status == "interrupted"
        assert read_finished.is_set()
        assert model.calls == BudgetService(database).usage("fixture").reservations == 8
        assert BudgetService(database).usage("fixture").in_flight == 0
        assert counts(database) == (1, 1, 3)
        with Session(database) as session:
            assert session.scalar(select(func.count()).select_from(EvidenceResult)) == 0
        jobs = JobRepository(database)
        snapshot = jobs.get(run_id)
        assert snapshot.status == "interrupted" and not snapshot.external_request_in_flight
        assert "PRIVATE-CONTEXT-RESPONSE-LOSS" not in str(snapshot.checkpoint)
        events = jobs.events_after(run_id)
        assert not any(event.kind in ("action_rejected", "tool_failed") for event in events)
        assert not any("PRIVATE-CONTEXT-RESPONSE-LOSS" in event.summary for event in events)
        # A normal worker poll must not silently retry the interrupted job.
        assert (await worker.run_once("identity-control-automatic-poll")).status == "idle"
        assert model.calls == 8
    finally:
        release.set()
        if stage == "timeout" and read_finished.is_set():
            assert await asyncio.to_thread(thread_finished.wait, 2)

    resume(database, run_id)
    completed = await replacement(worker, model).run_once("identity-control-explicit-resume")
    assert_complete(database, run_id, completed, model, requests)
    assert model.calls == BudgetService(database).usage("fixture").reservations == 8
