"""Real PostgreSQL/journal journeys with synthetic Meta and native HTTP fixtures."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_full_text import FullTextFixtureModel, full_text_fixture_transport
from test_literature import atom_entry, atom_feed
from test_memory import assign, owner
from test_worker import database as database_fixture
from test_worker import setup_worker

from polymath.budget import BudgetService
from polymath.evidence import EvidenceStore
from polymath.evidence_models import SourceRetrieval
from polymath.full_text import FullTextStore
from polymath.full_text_models import FullTextSnapshot, RunFullTextIndex
from polymath.jobs import JobRepository
from polymath.literature import OpenLiteratureTools
from polymath.literature.identity import distinct_paper_count
from polymath.research.models import ResearchResult
from polymath.worker import ResearchWorker

database = database_fixture
pytestmark = pytest.mark.integration


def queued(engine):
    token = owner(engine)
    run = JobRepository(engine).create(
        mode="explore", goal="Explore synthetic redundancy across information theory and biology."
    )
    assign(engine, run.id, token)
    return run.id


def replacement(worker, model):
    return ResearchWorker(
        worker.engine,
        model_factory=lambda _: model,
        tools_factory=worker.tools_factory,
        policy=worker.policy,
        limits=worker.limits,
        artifact_limits=worker.artifact_limits,
        assessment_limits=worker.assessment_limits,
        execution_enabled=True,
    )


def worker_for(engine, model, requests, *, transport=None):
    transport = transport or full_text_fixture_transport(requests)
    return setup_worker(
        engine, model, tools_factory=lambda _: OpenLiteratureTools(transport=transport)
    )


def resume(engine, run_id):
    jobs = JobRepository(engine)
    snapshot = jobs.get(run_id)
    assert snapshot.status == "interrupted"
    assert not snapshot.external_request_in_flight
    jobs.resume_interrupted(run_id, expected_token=snapshot.lease_token)


def counts(engine):
    with Session(engine) as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (FullTextSnapshot, RunFullTextIndex, SourceRetrieval)
        )


def assert_complete(engine, run_id, outcome, model, requests, *, html_gets=1):
    assert outcome.status == "succeeded" and outcome.result_id
    stored = EvidenceStore(engine).get_result(run_id, uuid.UUID(outcome.result_id))
    result = ResearchResult.model_validate_json(json.dumps(stored.payload))
    assert result.status == "bridge"
    assert len(result.sources) == len(result.quotes) == 3
    assert distinct_paper_count(result.sources) == 2
    windows = [source for source in result.sources if source.coverage is not None]
    assert len(windows) == 2
    assert len({source.coverage.snapshot_id for source in windows}) == 1
    assert {source.coverage.arxiv_version for source in windows} == {2}
    assert {source.url for source in windows} == {"https://arxiv.org/html/2401.01234v2"}
    assert counts(engine) == (1, 1, 3)
    assert len(EvidenceStore(engine).list_sources(run_id)) == 3
    usage = BudgetService(engine).usage("fixture")
    assert usage.reservations == model.calls == 8
    assert usage.in_flight == 0
    assert sum(request.url.host == "arxiv.org" for request in requests) == html_gets
    snapshot = JobRepository(engine).get(run_id)
    assert snapshot.status == "succeeded" and not snapshot.external_request_in_flight
    observations = snapshot.checkpoint["tool_observations"]
    assert len([key for key in observations if key.startswith("fulltext-index:")]) == 1
    assert len([key for key in observations if key.startswith("fulltext-window:")]) == 2
    assert not any(value.get("error") for value in observations.values())
    return result


@pytest.mark.asyncio
async def test_actual_budgeted_worker_reads_two_saved_windows_and_one_independent_paper(database):
    run_id, model, requests = queued(database), FullTextFixtureModel(), []
    worker = worker_for(database, model, requests)
    outcome = await worker.run_once("fulltext-worker")
    result = assert_complete(database, run_id, outcome, model, requests)
    assert result.sources[0].coverage.start == 0
    assert result.sources[1].coverage.start == result.sources[0].coverage.end


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["snapshot_commit", "index_receipt_commit"])
async def test_index_commit_response_loss_resumes_without_refetching_html(
    database, monkeypatch, stage
):
    run_id, model, requests = queued(database), FullTextFixtureModel(), []
    first = True
    if stage == "snapshot_commit":
        original = FullTextStore.save_snapshot

        def lost(store, *args, **kwargs):
            nonlocal first
            value = original(store, *args, **kwargs)
            if first:
                first = False
                raise RuntimeError("synthetic snapshot commit response lost")
            return value

        monkeypatch.setattr(FullTextStore, "save_snapshot", lost)
    else:
        original = JobRepository.save_checkpoint

        def lost(jobs, *args, **kwargs):
            nonlocal first
            value = original(jobs, *args, **kwargs)
            observations = kwargs["checkpoint"].get("tool_observations", {})
            if first and any(key.startswith("fulltext-index:") for key in observations):
                first = False
                raise RuntimeError("synthetic index receipt response lost")
            return value

        monkeypatch.setattr(JobRepository, "save_checkpoint", lost)
    worker = worker_for(database, model, requests)
    assert (await worker.run_once("losing-index-response")).status == "interrupted"
    assert model.calls == BudgetService(database).usage("fixture").reservations == 2
    assert counts(database) == (1, 1, 0)
    assert sum(request.url.host == "arxiv.org" for request in requests) == 1
    resume(database, run_id)
    outcome = await replacement(worker, model).run_once("index-replacement")
    assert_complete(database, run_id, outcome, model, requests)


@pytest.mark.asyncio
async def test_resolved_locator_checkpoint_loss_replays_exact_version_without_latest_lookup(
    database, monkeypatch
):
    run_id, model, requests = queued(database), FullTextFixtureModel(), []
    native_fixture = full_text_fixture_transport(requests)
    resolutions = 0

    def handler(request):
        nonlocal resolutions
        if request.url.host == "api.openalex.org":
            response = native_fixture.handler(request)
            payload = json.loads(response.content)
            payload["results"][0]["primary_location"] = {
                "landing_page_url": "https://arxiv.org/abs/2401.01234"
            }
            return httpx.Response(200, json=payload)
        if request.url.host == "export.arxiv.org":
            requests.append(request)
            if "id_list" in request.url.params:
                resolutions += 1
                # A second unsafe resolve would select a different latest body.
                return httpx.Response(
                    200,
                    text=atom_feed(
                        atom_entry(
                            source_id=f"http://arxiv.org/abs/2401.01234v{resolutions + 1}",
                            doi="10.1234/synthetic",
                        )
                    ),
                )
            return httpx.Response(200, text=atom_feed())
        return native_fixture.handler(request)

    original, first = JobRepository.save_checkpoint, True

    def lost(jobs, *args, **kwargs):
        nonlocal first
        value = original(jobs, *args, **kwargs)
        observations = kwargs["checkpoint"].get("tool_observations", {})
        if first and any(key.startswith("fulltext-locator:") for key in observations):
            first = False
            raise RuntimeError("synthetic resolved-locator receipt response lost")
        return value

    monkeypatch.setattr(JobRepository, "save_checkpoint", lost)
    worker = worker_for(database, model, requests, transport=httpx.MockTransport(handler))
    assert (await worker.run_once("losing-locator-response")).status == "interrupted"
    assert resolutions == 1
    assert not any(request.url.host == "arxiv.org" for request in requests)
    assert model.calls == BudgetService(database).usage("fixture").reservations == 2
    assert counts(database) == (0, 0, 0)
    resume(database, run_id)
    outcome = await replacement(worker, model).run_once("locator-replacement")
    assert_complete(database, run_id, outcome, model, requests)
    assert resolutions == 1


@pytest.mark.asyncio
async def test_shutdown_mid_html_fetch_stops_before_another_model_call_and_requires_resume(
    database,
):
    run_id, model, requests = queued(database), FullTextFixtureModel(), []
    native_fixture = full_text_fixture_transport(requests)
    started, release = asyncio.Event(), asyncio.Event()
    first = True

    async def handler(request):
        nonlocal first
        if request.url.host == "arxiv.org" and first:
            first = False
            requests.append(request)
            started.set()
            await release.wait()
            pytest.fail("Cancelled synthetic fetch unexpectedly continued")
        return native_fixture.handler(request)

    worker = worker_for(database, model, requests, transport=httpx.MockTransport(handler))
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_until_stopped("stopping-fulltext-worker", stop))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        stop.set()
        await asyncio.wait_for(task, timeout=5)
    finally:
        release.set()
        if not task.done():
            task.cancel()
    assert JobRepository(database).get(run_id).status == "interrupted"
    assert model.calls == BudgetService(database).usage("fixture").reservations == 2
    assert counts(database) == (0, 0, 0)
    fresh = replacement(worker, model)
    assert (await fresh.run_once("before-explicit-resume")).status == "idle"
    assert model.calls == 2
    resume(database, run_id)
    outcome = await fresh.run_once("resumed-fulltext-worker")
    assert_complete(database, run_id, outcome, model, requests, html_gets=2)


@pytest.mark.asyncio
async def test_window_evidence_commit_response_loss_replays_one_exact_retrieval(
    database, monkeypatch
):
    run_id, model, requests = queued(database), FullTextFixtureModel(), []
    original, first = EvidenceStore.save_source, True

    def lost(store, lease, document):
        nonlocal first
        value = original(store, lease, document)
        if first and document.coverage is not None:
            first = False
            raise RuntimeError("synthetic exact-window commit response lost")
        return value

    monkeypatch.setattr(EvidenceStore, "save_source", lost)
    worker = worker_for(database, model, requests)
    assert (await worker.run_once("losing-window-response")).status == "interrupted"
    assert model.calls == BudgetService(database).usage("fixture").reservations == 3
    assert counts(database) == (1, 1, 1)
    committed = EvidenceStore(database).list_sources(run_id)[0].retrieval_id
    resume(database, run_id)
    outcome = await replacement(worker, model).run_once("window-replacement")
    assert_complete(database, run_id, outcome, model, requests)
    assert committed in {
        source.retrieval_id for source in EvidenceStore(database).list_sources(run_id)
    }
