"""A real bounded search trace can exhaust write scope without losing history."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_discovery_controller import DiscoveryFixtureModel, DiscoveryFixtureTools, selection_action
from test_discovery_worker import queued
from test_worker import database as database_fixture
from test_worker import setup_worker

from polymath.discovery_models import StageCommit
from polymath.discovery_store import DiscoveryStore, _alias_context, stage_current_supported
from polymath.evidence import EvidenceStore
from polymath.evidence_models import EvidenceResult
from polymath.research.discovery import DiscoveryLimits
from polymath.research.meta import ModelReply
from polymath.research.models import (
    ArxivFullTextLocator,
    FullTextAvailability,
    SearchResults,
    SourceBibliography,
    SourceReference,
)

database = database_fixture
pytestmark = pytest.mark.integration


class ManyIdentityTools(DiscoveryFixtureTools):
    def __init__(self):
        super().__init__()
        for index, (key, doc) in enumerate(tuple(self.documents.items())):
            self.documents[key] = doc.model_copy(
                update={"bibliography": SourceBibliography(doi=f"10.1234/history-identity-{index}")}
            )
        self.search_count = 0

    async def search(self, query, purpose):
        if purpose != "discovery":
            return SearchResults(sources=())
        index = self.search_count
        self.search_count += 1
        sources = []
        availability = []
        for offset in range(10):
            number = index * 10 + offset
            doc = tuple(self.documents.values())[offset] if index == 0 and offset < 3 else None
            identifier = doc.source_id if doc else f"observed-identity-{number}"
            sources.append(
                SourceReference(
                    source_id=identifier,
                    title=doc.title if doc else f"Synthetic identity metadata {number}",
                    url=doc.url if doc else f"https://example.org/identity/{number}",
                    bibliography=SourceBibliography(doi=f"10.1234/history-identity-{number}"),
                )
            )
            availability.append(
                FullTextAvailability(
                    source_id=identifier,
                    status="candidate",
                    locator=ArxivFullTextLocator(arxiv_id=f"2501.{10000 + number}"),
                )
            )
        return SearchResults(sources=tuple(sources), full_text=tuple(availability))


class ObserveAfterStageModel(DiscoveryFixtureModel):
    async def complete(self, messages, *, max_output_tokens):
        observations = [
            json.loads(message["content"]).get("observation")
            for message in messages
            if message["role"] == "user"
        ]
        observations = [value for value in observations if value]
        if any(value.get("kind") == "stage_committed" for value in observations):
            self.calls += 1
            action = (
                selection_action()
                if observations[-1]["kind"] == "stage_committed"
                else {
                    "kind": "search_literature",
                    "query": f"later metadata {self.calls}",
                    "purpose": "discovery",
                }
            )
            return ModelReply(json.dumps(action), 3, 2)
        return await super().complete(messages, max_output_tokens=max_output_tokens)


async def test_later_observed_identity_limit_keeps_original_stage_readable(database):
    token, run_id = queued(database)
    model = ObserveAfterStageModel()
    worker = setup_worker(database, model, tools_factory=lambda _: ManyIdentityTools())
    worker.limits = DiscoveryLimits(max_model_calls=20, max_actions=20, max_output_tokens=4096)
    outcome = await worker.run_once("bounded-history-identities")
    assert outcome.status == "interrupted" and model.calls == 20
    with Session(database) as session:
        stage = session.scalar(select(StageCommit).where(StageCommit.run_id == run_id))
        assert stage is not None
        original_receipt = stage.receipt
        result_id, stage_id = stage.result_id, stage.id
        result = session.get(EvidenceResult, result_id)
        assert len(_alias_context(session, run_id, identity_limit=4096)) > 256
        assert stage_current_supported(session, result) is False
    detail = DiscoveryStore(database).get_stage(token, run_id, stage_id)
    assert detail["status"] == "needs_review"
    assert detail["stage"] == original_receipt
    assert len(detail["primary_paper_groups"]) == 2
    assert detail["result"] == EvidenceStore(database).get_result(run_id, result_id).payload
    assert DiscoveryStore(database).list_stages(token, run_id)["stages"] == [detail]
