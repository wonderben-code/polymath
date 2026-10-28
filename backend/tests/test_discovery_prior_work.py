"""Known-prior-work evidence can cite an exact HTML window of the searched paper."""

import json

import pytest
from sqlalchemy.orm import Session
from test_discovery_controller import assessment_data, selection_action
from test_discovery_worker import queued
from test_evidence import document, result_for
from test_full_text_store import extracted, reference
from test_worker import database as database_fixture

from polymath.discovery_contracts import StageAssessment, StageCommitPacket, StageContext
from polymath.discovery_store import DiscoveryStore, StageValidationError
from polymath.evidence import EvidenceStore
from polymath.full_text import FullTextStore
from polymath.jobs import JobRepository
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.research.models import FieldSelection, SearchResults, SourceReference
from polymath.worker import _digest

database = database_fixture
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("same_paper", [True, False])
def test_known_prior_work_uses_cited_html_window_identity_not_raw_locator_id(database, same_paper):
    _, run_id = queued(database)
    lease = JobRepository(database).claim_next("known-html-prior", lease_seconds=300)
    full_text = FullTextStore(database)
    body = extracted()
    index = full_text.save_snapshot(lease, reference(), body)
    left = full_text.read_window(lease, index.snapshot_id, 0, body.total_chars)
    right = document("other-independent-paper")
    EvidenceStore(database).save_source(lease, right)
    selection = FieldSelection(
        fields=("Fixture domain A", "Fixture domain B"), rationale=selection_action()["rationale"]
    )
    result = result_for(left, right).model_copy(
        update={"mode": "surprise", "field_selections": (selection,)}
    )
    assessment = StageAssessment.model_validate_json(
        json.dumps(assessment_data() | {"novelty": "known"})
    )
    hit = (
        reference()
        if same_paper
        else SourceReference(
            source_id="unrelated-paper",
            title="Unrelated prior work",
            url="https://example.org/unrelated-paper",
        )
    )
    searches = (
        ("model-0002", "counterevidence", SearchResults(sources=())),
        ("model-0003", "prior_work", SearchResults(sources=(hit,))),
    )
    actions = {
        "model-0001": selection_action() | {"fields": list(selection.fields)},
        "model-0004": {
            "kind": "record_bridge",
            "finding": result.finding.model_dump(mode="json"),
            "assessment": assessment.model_dump(mode="json"),
        },
    }
    for request_id, purpose, _ in searches:
        actions[request_id] = {"kind": "search_literature", "query": purpose, "purpose": purpose}
    with Session(database) as session, session.begin():
        run = session.get(ResearchRun, run_id)
        cache = {}
        for ordinal, (_, purpose, found) in enumerate(searches, 1):
            cache[f"tool-{ordinal:04d}"] = {
                "hash": _digest({"kind": "search", "query": purpose, "purpose": purpose}),
                "payload": found.model_dump(mode="json"),
            }
        run.checkpoint = {"tool_observations": cache}
        for request_id, action in actions.items():
            session.add(
                ResearchExternalRequest(
                    run_id=run_id,
                    request_id=request_id,
                    status="observed",
                    observation={"content": json.dumps(action)},
                )
            )
    store = DiscoveryStore(database)
    context = StageContext(
        ordinal=0, model_request_id="model-0001", action_ordinal=1, selection=selection
    )
    store.begin_context(lease, context)
    for request_id, purpose, found in searches:
        store.record_search(lease, 0, request_id, purpose, purpose, found)
    packet = StageCommitPacket(
        context=context,
        model_request_id="model-0004",
        action_ordinal=4,
        result=result,
        assessment=assessment,
    )
    if not same_paper:
        with pytest.raises(StageValidationError, match="discovery_prior_work_quote_missing"):
            store.commit(lease, packet)
    else:
        saved = store.commit(lease, packet)
        assert saved.assessment.novelty == "known"
        assert left.source_id != hit.source_id
