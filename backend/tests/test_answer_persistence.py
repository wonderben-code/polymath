"""Durable Ask/Surprise evidence checks using explicitly synthetic findings."""

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from test_evidence import document, result_for
from test_worker import database as database_fixture

from polymath.evidence import EvidenceError, EvidenceStore
from polymath.evidence_models import EvidenceQuote
from polymath.jobs import JobRepository
from polymath.research.models import (
    AnswerClaim,
    AnswerFinding,
    ClaimQuote,
    FieldSelection,
    ResearchResult,
    Usage,
    VerifiedQuote,
)

database = database_fixture
pytestmark = pytest.mark.integration
QUESTION = "What do these synthetic fixtures support?"


def answer_result():
    left = document()
    right = document(
        "fixture:right", text="🌱 Synthetic pathways carry fixture signals under noise."
    )
    docs = {doc.source_id: doc for doc in (left, right)}
    claims = [
        AnswerClaim(
            id="known_1",
            text="The fixture states that redundancy protects messages.",
            label="known",
            evidence=[
                ClaimQuote(
                    source_id=left.source_id,
                    quote=left.text[2:],
                    supports="Synthetic source statement",
                )
            ],
        ),
        AnswerClaim(
            id="inference_1",
            text="The fixtures suggest a hypothetical cross-domain analogy.",
            label="postulated",
            derivation="Compare the two stated noise-protection roles.",
            evidence=[
                ClaimQuote(
                    source_id=d.source_id,
                    quote=d.text[2:],
                    supports="A premise for a synthetic inference",
                )
                for d in (left, right)
            ],
        ),
        AnswerClaim(
            id="possibility_1",
            text="A broader mechanism might exist in this hypothetical setup.",
            label="speculative",
            caveats=["Synthetic fixtures cannot establish a real mechanism."],
            evidence=[
                ClaimQuote(
                    source_id=right.source_id,
                    quote=right.text[2:],
                    supports="Limited hypothetical context",
                )
            ],
        ),
    ]
    verified = []
    for claim in claims:
        for item in claim.evidence:
            doc = docs[item.source_id]
            start = doc.text.find(item.quote)
            verified.append(
                VerifiedQuote(
                    source_id=item.source_id,
                    side="answer",
                    claim_id=claim.id,
                    quote=item.quote,
                    supports=item.supports,
                    start=start,
                    end=start + len(item.quote),
                    source_sha256=doc.content_sha256,
                    content_kind=doc.content_kind,
                )
            )
    return ResearchResult(
        status="answer",
        mode="ask",
        finding=AnswerFinding(
            outcome="answer",
            question=QUESTION,
            summary="Synthetic classification exercise, not scientific truth.",
            claims=claims,
            limitations=["No real literature was used."],
            unanswered_questions=["Would actual literature support this hypothesis?"],
        ),
        quotes=tuple(verified),
        sources=(left, right),
        usage=Usage(model_calls=1),
    )


def prepare(engine, result, *, mode="ask", goal=QUESTION):
    jobs = JobRepository(engine)
    run = jobs.create(mode=mode, goal=goal)
    lease = jobs.claim_next("fixture-answer-worker", lease_seconds=300)
    assert lease.run_id == run.id
    store = EvidenceStore(engine)
    for source in result.sources:
        store.save_source(lease, source)
    return store, lease


def test_answer_claim_labels_and_versioned_quote_links_roundtrip(database):
    result = answer_result()
    store, lease = prepare(database, result)
    saved = store.save_result(lease, result)
    assert saved.payload == result.model_dump(mode="json")
    assert store.get_result(lease.run_id, saved.id).payload == saved.payload
    with Session(database) as session:
        rows = session.scalars(
            select(EvidenceQuote).where(EvidenceQuote.result_id == saved.id)
        ).all()
        assert len(rows) == 4
        assert {row.claim_id for row in rows} == {"known_1", "inference_1", "possibility_1"}
        assert {row.side for row in rows} == {"answer"}


@pytest.mark.parametrize("mutation", ["quote", "claim", "question", "mode"])
def test_forged_answer_associations_roll_back(database, mutation):
    result = answer_result()
    store, lease = prepare(database, result)
    payload = result.model_dump(mode="json")
    if mutation == "quote":
        payload["quotes"][0]["quote"] = "Invented evidence that is not in the source."
    if mutation == "claim":
        payload["quotes"][0]["claim_id"] = "another_claim"
    if mutation == "question":
        payload["finding"]["question"] = "An unrelated synthetic question?"
    if mutation == "mode":
        payload["mode"] = "explore"
    changed = ResearchResult.model_validate_json(json.dumps(payload))
    with pytest.raises(EvidenceError):
        store.save_result(lease, changed)
    assert store.list_results(lease.run_id) == ()
    assert len(store.list_sources(lease.run_id)) == 2


def test_postulated_claim_requires_distinct_canonical_sources(database):
    result = answer_result()
    payload = result.model_dump(mode="json")
    payload["sources"][1]["url"] = payload["sources"][0]["url"]
    result = ResearchResult.model_validate_json(json.dumps(payload))
    store, lease = prepare(database, result)
    with pytest.raises(EvidenceError, match="postulated_distinct_sources_required"):
        store.save_result(lease, result)


def test_surprise_bridge_requires_current_model_field_selection(database):
    left, right = (
        document(),
        document("fixture:right", text="🌱 Synthetic pathways carry fixture signals under noise."),
    )
    bridge = result_for(left, right).model_copy(
        update={
            "mode": "surprise",
            "field_selections": (
                FieldSelection(
                    fields=("Fixture domain A", "Fixture domain B"),
                    rationale="A synthetic candidate direction.",
                ),
                FieldSelection(
                    fields=("Different field C", "Different field D"),
                    rationale="A revised direction.",
                ),
            ),
        }
    )
    store, lease = prepare(database, bridge, mode="surprise", goal="")
    with pytest.raises(EvidenceError, match="bridge_fields_not_selected"):
        store.save_result(lease, bridge)
    valid = bridge.model_copy(update={"field_selections": bridge.field_selections[:1]})
    assert store.save_result(lease, valid).payload["mode"] == "surprise"


def test_incompatible_downgrade_refuses_to_delete_answer_artifacts(database):
    result = answer_result()
    store, lease = prepare(database, result)
    saved = store.save_result(lease, result)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with pytest.raises(RuntimeError, match="would lose data"):
        command.downgrade(config, "0006_run_access")
    assert store.get_result(lease.run_id, saved.id).payload == result.model_dump(mode="json")
    with database.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == ScriptDirectory.from_config(config).get_current_head()
        )
    command.check(config)
