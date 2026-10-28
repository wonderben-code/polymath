"""Real PostgreSQL admission/result/reassessment guarantees with synthetic science."""

import json
import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_answer_persistence import QUESTION, answer_result
from test_assessment_api import assessments as assessments_fixture
from test_assessment_controller import AssessmentFixtureModel, AssessmentFixtureTools
from test_assessments import assessment_result
from test_evidence import document
from test_ingestion_api import create as create_watch
from test_ingestion_api import save_fixture_page
from test_run_api import bootstrap
from test_worker import database as database_fixture

from polymath.assessment_contracts import AssessmentSourceSnapshot
from polymath.assessment_controller import AssessmentController
from polymath.assessment_models import AssessmentRequest, FindingAssessment
from polymath.assessments import AssessmentError, AssessmentStore
from polymath.budget_models import BudgetReservation
from polymath.evidence import EvidenceError, EvidenceStore
from polymath.evidence_models import EvidenceResult
from polymath.jobs import JobRepository
from polymath.models import ResearchRun
from polymath.research.meta import ModelReply
from polymath.research.models import ResearchResult
from polymath.scientific_context_contracts import AskReassessmentOverviewIntent
from polymath.scientific_context_models import (
    AskOverviewIntentRow,
    AskReassessmentOverviewIntentRow,
)

assessments = assessments_fixture
database = database_fixture
pytestmark = pytest.mark.integration
ASK_INTENT = {"protocol": "polymath-ask-claim-overview-v1"}
ASSESSMENT_INTENT = {"protocol": "polymath-ask-reassessment-overview-v1"}
ORDER = ("possibility_1", "known_1", "inference_1")


def with_binding(result):
    raw = result.model_dump(mode="json")
    by_id = {claim["id"]: claim["text"] for claim in raw["finding"]["claims"]}
    raw["finding"]["summary"] = "\n\n".join(by_id[key] for key in ORDER)
    raw["overview_binding"] = {"protocol": ASK_INTENT["protocol"], "claim_ids": list(ORDER)}
    return ResearchResult.model_validate_json(json.dumps(raw))


def enqueue_ask(engine, client, *, bound=True):
    response = client.post(
        "/api/v1/runs",
        headers=bootstrap(client),
        json={
            "mode": "ask",
            "goal": QUESTION,
            **({"ask_overview_intent": ASK_INTENT} if bound else {}),
        },
    )
    assert response.status_code == 201, response.text
    run_id = uuid.UUID(response.json()["id"])
    lease = JobRepository(engine).claim_next("synthetic-bound-ask", lease_seconds=300)
    assert lease.run_id == run_id
    return lease, response.json()


def seed_ask(engine, client, *, bound=True):
    lease, summary = enqueue_ask(engine, client, bound=bound)
    result = answer_result()
    if bound:
        result = with_binding(result)
    evidence = EvidenceStore(engine)
    for source in result.sources:
        evidence.save_source(lease, source)
    saved = evidence.save_result(lease, result)
    JobRepository(engine).finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    return saved, summary


def watch_inputs(engine, client, source_id, *, count=1):
    watch = create_watch(client, source_result_id=str(source_id))
    assert watch.status_code == 201, watch.text
    documents = tuple(
        document(
            f"fixture:overview-new-{index}",
            title=f"Synthetic overview source {index}",
            text=f"🌱 New synthetic evidence version {index} bounds the prior analogy.",
        )
        for index in range(count)
    )
    saved = save_fixture_page(engine, client, watch.json()["id"], documents)
    return {
        "source_result_id": str(source_id),
        "watch_id": watch.json()["id"],
        "new_source_version_ids": [str(saved[0].version_id)],
        "expected_head_revision": 0,
    }, tuple(str(item.version_id) for item in saved)


def enqueue_assessment(engine, client, body, *, variant=True):
    response = client.post(
        "/api/v1/assessments/jobs",
        headers=bootstrap(client),
        json={**body, **({"assessment_overview_intent": ASSESSMENT_INTENT} if variant else {})},
    )
    assert response.status_code == 201, response.text
    lease = JobRepository(engine).claim_next(
        "synthetic-bound-ask-reassessment", lease_seconds=300, task_kinds=("reassess_finding",)
    )
    assert str(lease.run_id) == response.json()["id"]
    return lease, response.json()


class BoundAdaptiveModel(AssessmentFixtureModel):
    async def complete(self, messages, *, max_output_tokens):
        reply = await super().complete(messages, max_output_tokens=max_output_tokens)
        action = json.loads(reply.content)
        if action.get("revised_finding") is not None:
            finding = action["revised_finding"]
            finding.pop("summary")
            priority = {"speculative": 0, "known": 1, "postulated": 2}
            finding["overview_claim_ids"] = [
                claim["id"]
                for claim in sorted(finding["claims"], key=lambda claim: priority[claim["label"]])
            ]
        return ModelReply(json.dumps(action), reply.input_tokens, reply.output_tokens)


async def revised_assessment(engine, store, lease):
    basis = store.load_basis(lease)
    sources = {
        ref.retrieval_id: AssessmentSourceSnapshot(
            retrieval_id=ref.retrieval_id,
            version_id=ref.version_id,
            document=store.read_source(lease, ref.retrieval_id),
        )
        for ref in basis.sources
    }
    # This adaptive fixture chooses reads/searches and drafts, using the real
    # controller. Durable source reads are independently recorded above.
    model = BoundAdaptiveModel(revised=True)
    result = await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(
        basis, ask_reassessment_overview_intent=AskReassessmentOverviewIntent()
    )
    assert result.disposition == "revised", result
    with engine.begin() as connection:
        connection.execute(
            update(ResearchRun)
            .where(ResearchRun.id == lease.run_id)
            .values(checkpoint={"assessment_search_purposes": list(result.search_purposes)})
        )
    return result


def test_explicit_ask_admission_and_exact_bound_result_survive_owned_reads(assessments):
    engine, factory = assessments
    client = factory(enabled=True)
    saved, summary = seed_ask(engine, client)
    assert summary["ask_overview_intent"] == ASK_INTENT
    assert "assessment_overview_intent" not in summary
    response = client.get(f"/api/v1/runs/{saved.run_id}/result")
    assert response.status_code == 200 and response.json() == saved.payload
    assert response.json()["overview_binding"]["claim_ids"] == list(ORDER)
    assert client.get(f"/api/v1/runs/{saved.run_id}").json()["ask_overview_intent"] == ASK_INTENT
    with Session(engine) as session:
        row = session.get(AskOverviewIntentRow, saved.run_id)
        assert row.intent == ASK_INTENT
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 0
    foreign = factory(enabled=True)
    bootstrap(foreign)
    assert foreign.get(f"/api/v1/runs/{saved.run_id}/result").status_code == 404


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "explore"},
        {"mode": "surprise"},
        {"ask_overview_intent": None},
        {"ask_overview_intent": {"protocol": "foreign"}},
        {"ask_overview_intent": {**ASK_INTENT, "allowance": 100}},
        {"parent_result_ids": [str(uuid.UUID(int=1)), str(uuid.UUID(int=2))]},
    ],
)
def test_invalid_ask_protocol_rolls_back_before_enqueue(assessments, changes):
    engine, factory = assessments
    client = factory(enabled=True)
    response = client.post(
        "/api/v1/runs",
        headers=bootstrap(client),
        json={"mode": "ask", "goal": QUESTION, "ask_overview_intent": ASK_INTENT, **changes},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_research_request"}
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0
        assert session.scalar(select(func.count()).select_from(AskOverviewIntentRow)) == 0
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 0


@pytest.mark.parametrize("admitted", [True, False])
def test_evidence_store_independently_enforces_original_intent_in_both_directions(
    assessments, admitted
):
    engine, factory = assessments
    client = factory(enabled=True)
    lease, _ = enqueue_ask(engine, client, bound=admitted)
    evidence = EvidenceStore(engine)
    legacy = answer_result()
    bound = with_binding(legacy)
    for source in legacy.sources:
        evidence.save_source(lease, source)
    invalid, valid = (legacy, bound) if admitted else (bound, legacy)
    expected = "ask_overview_binding_required" if admitted else "ask_overview_not_admitted"
    with pytest.raises(EvidenceError, match=expected):
        evidence.save_result(lease, invalid)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(EvidenceResult)) == 0
    saved = evidence.save_result(lease, valid)
    assert ("overview_binding" in saved.payload) is admitted


def test_bound_prior_requires_explicit_assessment_intent_without_consuming_a_job(assessments):
    engine, factory = assessments
    client = factory(enabled=True)
    source, _ = seed_ask(engine, client)
    body, _ = watch_inputs(engine, client, source.id)
    blocked = client.post("/api/v1/assessments/jobs", headers=bootstrap(client), json=body)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json() == {"detail": "ask_reassessment_overview_required"}
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AssessmentRequest)) == 0
        assert (
            session.scalar(select(func.count()).select_from(AskReassessmentOverviewIntentRow)) == 0
        )
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 0
    lease, summary = enqueue_assessment(engine, client, body)
    assert summary["assessment_overview_intent"] == ASSESSMENT_INTENT
    basis = AssessmentStore(engine).load_basis(lease)
    assert basis.prior_overview_binding.claim_ids == ORDER
    with Session(engine) as session:
        request = session.get(AssessmentRequest, lease.run_id)
        row = session.get(AskReassessmentOverviewIntentRow, lease.run_id)
        assert request.basis["prior_overview_binding"] == source.payload["overview_binding"]
        assert row.basis_sha256 == request.basis_sha256


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [True, False])
async def test_explicit_bound_revision_applies_from_new_and_legacy_ask_sources(assessments, bound):
    engine, factory = assessments
    client = factory(enabled=True)
    source, _ = seed_ask(engine, client, bound=bound)
    body, _ = watch_inputs(engine, client, source.id)
    lease, _ = enqueue_assessment(engine, client, body)
    store = AssessmentStore(engine)
    result = await revised_assessment(engine, store, lease)
    saved = store.save(lease, result)
    assert saved.applied and saved.candidate_result_id is not None
    assert saved.payload["assessment_overview_intent"] == ASSESSMENT_INTENT
    assert saved.payload["result"]["revised_result"]["overview_binding"]["claim_ids"]
    assert ("prior_overview_binding" in saved.payload["basis"]) is bound
    current = client.get(f"/api/v1/assessments/findings/{saved.candidate_result_id}")
    assert current.status_code == 200, current.text
    assert current.json()["payload"]["overview_binding"]["claim_ids"]
    assert current.json()["assessment"]["is_supported"]
    history = client.get(f"/api/v1/assessments/{saved.id}")
    assert history.status_code == 200, history.text
    assert history.json()["payload"]["assessment_overview_intent"] == ASSESSMENT_INTENT


@pytest.mark.asyncio
async def test_store_rejects_dropped_revision_binding_even_when_controller_is_bypassed(assessments):
    engine, factory = assessments
    client = factory(enabled=True)
    source, _ = seed_ask(engine, client)
    body, _ = watch_inputs(engine, client, source.id)
    lease, _ = enqueue_assessment(engine, client, body)
    store = AssessmentStore(engine)
    result = await revised_assessment(engine, store, lease)
    raw = result.revised_result.model_dump(mode="json")
    raw.pop("overview_binding")
    from polymath.assessment_contracts import AssessmentResult

    forged = AssessmentResult.model_validate_json(
        json.dumps({**result.model_dump(mode="json"), "revised_result": raw})
    )
    with pytest.raises(AssessmentError, match="assessment_result_integrity_failed"):
        store.save(lease, forged)
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(EvidenceResult)
                .where(EvidenceResult.run_id == lease.run_id)
            )
            == 0
        )
        assert session.scalar(select(func.count()).select_from(FindingAssessment)) == 0
    assert store.save(lease, result).applied


@pytest.mark.asyncio
async def test_unsupported_bound_ask_can_remain_unsupported_then_be_restored_by_a_revision(
    assessments,
):
    engine, factory = assessments
    client = factory(enabled=True)
    source, _ = seed_ask(engine, client)
    body, versions = watch_inputs(engine, client, source.id, count=3)
    store = AssessmentStore(engine)
    for revision, disposition in enumerate(("unsupported", "unchanged")):
        lease, _ = enqueue_assessment(
            engine,
            client,
            {
                **body,
                "expected_head_revision": revision,
                "new_source_version_ids": [versions[revision]],
            },
        )
        basis = store.load_basis(lease)
        assert basis.prior_state == ("supported" if revision == 0 else "unsupported")
        saved = store.save(lease, assessment_result(engine, store, lease, disposition=disposition))
        assert saved.applied
        current = client.get(f"/api/v1/assessments/findings/{source.id}")
        assert current.status_code == 200
        assert current.json()["payload"]["overview_binding"] == source.payload["overview_binding"]
        assert current.json()["assessment"]["is_current"]
        assert not current.json()["assessment"]["is_supported"]
    lease, _ = enqueue_assessment(
        engine,
        client,
        {**body, "expected_head_revision": 2, "new_source_version_ids": [versions[2]]},
    )
    assert store.load_basis(lease).prior_state == "unsupported"
    restored = store.save(lease, await revised_assessment(engine, store, lease))
    assert restored.applied and restored.head_revision == 3
    current = client.get(f"/api/v1/assessments/findings/{restored.candidate_result_id}")
    assert current.status_code == 200
    assert current.json()["assessment"]["is_supported"]
    assert EvidenceStore(engine).get_result(source.run_id, source.id).payload == source.payload
