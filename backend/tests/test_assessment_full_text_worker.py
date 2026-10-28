"""Actual assessment worker with canned native-shaped arXiv HTML and paid-call fixtures."""

import json
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_assessment_controller import AssessmentFixtureModel, inconclusive
from test_assessment_worker import queued, replacement
from test_full_text import full_text_fixture_transport
from test_worker import database as database_fixture
from test_worker import setup_worker

from polymath.assessment_controller import EXCERPT_LIMITATION
from polymath.assessment_models import FindingAssessment
from polymath.assessments import AssessmentStore
from polymath.budget import BudgetService
from polymath.evidence import EvidenceStore
from polymath.full_text_models import RunFullTextIndex
from polymath.jobs import JobRepository
from polymath.literature import OpenLiteratureTools
from polymath.research.meta import ModelReply

database = database_fixture
pytestmark = pytest.mark.integration


class FullTextAssessmentModel(AssessmentFixtureModel):
    """Reads two exact windows, then reassesses real observed pinned sources."""

    def __init__(self, disposition="unchanged"):
        super().__init__(revised=disposition == "revised")
        self.disposition, self.calls = disposition, 0

    async def complete(self, messages, *, max_output_tokens):
        self.calls += 1
        user = [json.loads(message["content"]) for message in messages if message["role"] == "user"]
        basis = user[0]["assessment_basis"]
        observed = [value["observation"] for value in user if "observation" in value]
        discovery = next(
            (
                item
                for item in observed
                if item.get("kind") == "search_results" and item["purpose"] == "discovery"
            ),
            None,
        )
        indexes = [item for item in observed if item.get("kind") == "full_text_index"]
        windows = [item for item in observed if item.get("role") == "full_text_excerpt"]
        if discovery is None:
            action = {
                "kind": "search_literature",
                "query": "Synthetic HTML reassessment",
                "purpose": "discovery",
            }
        elif not indexes:
            action = {"kind": "open_full_text", "source_id": discovery["full_text"][0]["source_id"]}
        else:
            index = indexes[0]
            split = index["headings"][1]["start"]
            ranges = ((0, split), (split, index["total_chars"]))
            read = {
                (item["document"]["coverage"]["start"], item["document"]["coverage"]["end"])
                for item in windows
            }
            unread = next((span for span in ranges if span not in read), None)
            if unread is not None:
                action = {
                    "kind": "read_full_text_window",
                    "snapshot_id": index["snapshot_id"],
                    "start": unread[0],
                    "end": unread[1],
                }
            elif self.disposition == "inconclusive":
                action = inconclusive()
            else:
                response = await super().complete(messages, max_output_tokens=max_output_tokens)
                action = json.loads(response.content)
                if action.get("kind") == "conclude_assessment" and self.revised:
                    first, second = sorted(
                        windows, key=lambda item: item["document"]["coverage"]["start"]
                    )
                    right_ref = [ref for ref in basis["sources"] if ref["role"] == "historical"][1]
                    right = next(
                        item
                        for item in observed
                        if item.get("retrieval_id") == right_ref["retrieval_id"]
                    )
                    action["selected_retrieval_ids"] = [
                        first["retrieval_id"],
                        second["retrieval_id"],
                        right["retrieval_id"],
                    ]
                    action["revised_finding"]["evidence"] = [
                        {
                            "source_id": item["document"]["source_id"],
                            "side": side,
                            "quote": item["document"]["text"][2:],
                            "supports": "Synthetic exact excerpt or independent historical source.",
                        }
                        for item, side in ((first, "left"), (right, "right"), (second, "left"))
                    ]
                    action["rationale"][0]["citations"].extend(
                        [
                            {
                                "retrieval_id": item["retrieval_id"],
                                "quote": item["document"]["text"],
                                "supports": "A synthetic excerpt considered in the judgement.",
                            }
                            for item in (first, second)
                        ]
                    )
        return ModelReply(json.dumps(action), 10, 15)


@pytest.mark.asyncio
@pytest.mark.parametrize("disposition", ["unchanged", "inconclusive", "revised"])
async def test_actual_assessment_worker_indexes_reads_and_applies_only_complete_gated_result(
    database, disposition
):
    token, prior, *_, run_id = queued(database)
    requests = []
    model = FullTextAssessmentModel(disposition)
    worker = setup_worker(
        database,
        model,
        tools_factory=lambda _: OpenLiteratureTools(
            transport=full_text_fixture_transport(requests)
        ),
    )
    outcome = await worker.run_once("synthetic-full-text-assessor")
    assert outcome.status == "succeeded", JobRepository(database).get(run_id).outcome
    saved = AssessmentStore(database).get(token, uuid.UUID(outcome.assessment_id))
    assert saved.disposition == disposition
    assert saved.applied is (disposition != "inconclusive")
    assert EXCERPT_LIMITATION in saved.payload["result"]["limitations"]
    sources = EvidenceStore(database).list_sources(run_id)
    documents = [
        EvidenceStore(database).get_document(run_id, item.retrieval_id) for item in sources
    ]
    excerpts = [doc for doc in documents if doc.coverage is not None]
    assert len(excerpts) == 2
    assert len({doc.coverage.snapshot_id for doc in excerpts}) == 1
    assert len([item for item in saved.payload["observed_sources"] if item.get("coverage")]) == 2
    head = AssessmentStore(database).head(token, prior.id)
    assert head["revision"] == int(disposition != "inconclusive")
    assert head["current_result_id"] == (
        outcome.result_id if disposition == "revised" else str(prior.id)
    )
    if disposition == "revised":
        candidate = EvidenceStore(database).get_result(run_id, uuid.UUID(outcome.result_id))
        assert (
            len([source for source in candidate.payload["sources"] if source.get("coverage")]) == 2
        )
        assert (
            len([item for item in saved.payload["verified_passages"] if item.get("coverage")]) == 2
        )
    assert BudgetService(database).usage("fixture").reservations == model.calls
    assert BudgetService(database).usage("fixture").in_flight == 0
    assert len([request for request in requests if request.url.host == "arxiv.org"]) == 1
    assert all(request.method == "GET" for request in requests)


@pytest.mark.asyncio
async def test_lost_durable_window_handle_response_replays_without_refetch_or_paid_repetition(
    database, monkeypatch
):
    token, prior, *_, run_id = queued(database)
    requests = []
    model = FullTextAssessmentModel()
    worker = setup_worker(
        database,
        model,
        tools_factory=lambda _: OpenLiteratureTools(
            transport=full_text_fixture_transport(requests)
        ),
    )
    original = EvidenceStore.save_source
    saves, lost = {}, False

    def lost_handle(store, lease, document):
        nonlocal lost
        result = original(store, lease, document)
        if document.coverage is not None:
            saves[document.source_id] = saves.get(document.source_id, 0) + 1
            # The index window and its replay receipt are already durable; the
            # assessment-specific handle assignment loses its response next.
            if not lost and saves[document.source_id] == 3:
                lost = True
                raise RuntimeError("Synthetic assessment window handle response lost after commit")
        return result

    monkeypatch.setattr(EvidenceStore, "save_source", lost_handle)
    first = await worker.run_once("synthetic-window-handle-loss")
    assert lost and first.status == "interrupted"
    run = JobRepository(database).get(run_id)
    assert BudgetService(database).usage("fixture").reservations == model.calls == 3
    assert len(EvidenceStore(database).list_sources(run_id)) == 1
    assert AssessmentStore(database).head(token, prior.id)["revision"] == 0
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(FindingAssessment)) == 0
        assert session.scalar(select(func.count()).select_from(RunFullTextIndex)) == 1
    assert not any(
        item.get("error") for item in run.checkpoint.get("tool_observations", {}).values()
    )
    JobRepository(database).resume_interrupted(run_id, expected_token=run.lease_token)
    outcome = await replacement(worker, model).run_once("synthetic-window-handle-replacement")
    assert outcome.status == "succeeded" and outcome.assessment_id
    saved = AssessmentStore(database).get(token, uuid.UUID(outcome.assessment_id))
    assert saved.applied and saved.disposition == "unchanged"
    assert len([item for item in saved.payload["observed_sources"] if item.get("coverage")]) == 2
    assert BudgetService(database).usage("fixture").reservations == model.calls == 7
    assert len([request for request in requests if request.url.host == "arxiv.org"]) == 1
    assert len(EvidenceStore(database).list_sources(run_id)) == 4
