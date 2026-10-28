"""Opt-in bound Ask replacement with original source/assessment invariants."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_assessment_controller import (
    AssessmentFixtureModel,
    AssessmentFixtureTools,
    SequenceModel,
    assessment_basis,
    inconclusive,
    judgement,
)

from polymath.assessment_contracts import AssessmentBasis, validate_assessment_result
from polymath.assessment_controller import AssessmentController
from polymath.assessment_overview import (
    ASK_REASSESSMENT_OVERVIEW_ACTION_ADAPTER,
    ASK_REASSESSMENT_OVERVIEW_SYSTEM_PROMPT,
    ask_reassessment_overview_initial_messages,
)
from polymath.research.meta import ModelReply
from polymath.research.models import AnswerFinding, AskOverviewBinding
from polymath.scientific_context_contracts import AskReassessmentOverviewIntent

ORDER = ("speculative_1", "known_1", "postulated_1")
BASELINE = Path(__file__).parent / "fixtures" / "pre_scientific_context_ask.json"


def pinned_basis(*, prior_state="supported", bound=True):
    basis, sources = assessment_basis(mode="ask", prior_state=prior_state)
    if bound:
        raw = basis.model_dump(mode="json")
        by_id = {claim.id: claim for claim in basis.prior_finding.claims}
        raw["prior_finding"]["summary"] = "\n\n".join(by_id[key].text for key in ORDER)
        raw["prior_overview_binding"] = {
            "protocol": "polymath-ask-claim-overview-v1",
            "claim_ids": list(ORDER),
        }
        basis = AssessmentBasis.model_validate_json(json.dumps(raw))
    return basis, sources


def bound_judgement(basis, sources):
    action = judgement(
        basis.model_dump(mode="json"),
        {key: item.model_dump(mode="json") for key, item in sources.items()},
        revised=True,
    )
    action["revised_finding"].pop("summary")
    action["revised_finding"]["overview_claim_ids"] = list(ORDER)
    return action


def prefix(basis):
    return [
        *(
            {"kind": "read_source_by_retrieval", "retrieval_id": ref.retrieval_id}
            for ref in basis.sources
        ),
        {
            "kind": "search_literature",
            "purpose": "counterevidence",
            "query": "Synthetic counterevidence",
        },
        {"kind": "search_literature", "purpose": "prior_work", "query": "Synthetic prior work"},
    ]


class BoundAdaptiveModel(AssessmentFixtureModel):
    async def complete(self, messages, *, max_output_tokens):
        reply = await super().complete(messages, max_output_tokens=max_output_tokens)
        action = json.loads(reply.content)
        if action.get("revised_finding") is not None:
            action["revised_finding"].pop("summary")
            action["revised_finding"]["overview_claim_ids"] = list(ORDER)
        return ModelReply(json.dumps(action), reply.input_tokens, reply.output_tokens)


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_state", ["supported", "unsupported"])
@pytest.mark.parametrize("bound", [True, False])
async def test_revised_ask_uses_exact_selected_text_under_explicit_protocol(prior_state, bound):
    basis, sources = pinned_basis(prior_state=prior_state, bound=bound)
    original = basis.model_dump_json()
    model = BoundAdaptiveModel(revised=True)
    result = await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(
        basis, ask_reassessment_overview_intent=AskReassessmentOverviewIntent()
    )
    assert result.status == "completed" and result.disposition == "revised"
    revised = result.revised_result
    assert isinstance(revised.finding, AnswerFinding)
    assert revised.overview_binding == AskOverviewBinding(claim_ids=ORDER)
    by_id = {claim.id: claim for claim in revised.finding.claims}
    assert revised.finding.summary == "\n\n".join(by_id[key].text for key in ORDER)
    assert {claim.label for claim in revised.finding.claims} == {
        "known",
        "postulated",
        "speculative",
    }
    assert revised.memory_reads == (basis.prior_reference,)
    assert result.usage.model_calls == 6
    assert result.usage.input_tokens_reported == 60
    assert result.usage.output_tokens_reported == 90
    assert len(revised.quotes) == 4
    assert basis.model_dump_json() == original
    validate_assessment_result(result, basis, sources, expected_overview=True)
    with pytest.raises(ValueError):
        validate_assessment_result(result, basis, sources, expected_overview=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_state", ["supported", "unsupported"])
async def test_unchanged_keeps_original_result_binding_and_unsupported_state(prior_state):
    basis, sources = pinned_basis(prior_state=prior_state)
    original = basis.model_dump_json()
    result = await AssessmentController(
        BoundAdaptiveModel(), AssessmentFixtureTools(sources)
    ).reassess(basis, ask_reassessment_overview_intent=AskReassessmentOverviewIntent())
    assert result.status == "completed" and result.disposition == "unchanged"
    assert result.revised_result is None
    assert basis.prior_state == prior_state
    assert basis.prior_overview_binding.claim_ids == ORDER
    assert basis.model_dump_json() == original
    validate_assessment_result(result, basis, sources, expected_overview=True)


@pytest.mark.asyncio
async def test_bound_prior_cannot_use_old_controller_even_if_model_would_return_unchanged():
    basis, sources = pinned_basis()
    model = BoundAdaptiveModel()
    with pytest.raises(ValueError, match="ask_reassessment_overview_required"):
        await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(basis)
    assert model.seen == []


@pytest.mark.asyncio
async def test_overview_reassessment_profile_rejects_bridge_basis_before_any_call():
    basis, sources = assessment_basis()
    model = BoundAdaptiveModel()
    with pytest.raises(ValueError, match="ask_reassessment_overview_wrong_mode"):
        await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(
            basis, ask_reassessment_overview_intent=AskReassessmentOverviewIntent()
        )
    assert model.seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent",
    [
        {"protocol": "polymath-ask-reassessment-overview-v1"},
        AskReassessmentOverviewIntent.model_construct(protocol="forged"),
    ],
)
async def test_invalid_reassessment_intent_rejected_before_model_call(intent):
    basis, sources = pinned_basis()
    model = BoundAdaptiveModel()
    with pytest.raises(ValueError):
        await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(
            basis, ask_reassessment_overview_intent=intent
        )
    assert model.seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,code",
    [
        ("free_summary", "invalid_assessment_action_schema"),
        ("missing_ids", "invalid_assessment_action_schema"),
        ("duplicate", "ask_overview_duplicate_claim"),
        ("unknown", "ask_overview_unknown_claim"),
        ("too_long", "ask_overview_too_long"),
        ("invented_quote", "quotation_not_found"),
        ("wrong_question", "assessment_question_mismatch"),
        ("label_upgrade", "assessment_claim_label_upgrade"),
        ("unread_selected", "assessment_source_not_read"),
    ],
)
async def test_revised_overview_retains_real_repair_and_source_validation(failure, code):
    basis, sources = pinned_basis()
    action = bound_judgement(basis, sources)
    candidate = action["revised_finding"]
    if failure == "free_summary":
        candidate["summary"] = "Extra uncited summary."
    elif failure == "missing_ids":
        candidate.pop("overview_claim_ids")
    elif failure == "duplicate":
        candidate["overview_claim_ids"] = ["known_1", "known_1"]
    elif failure == "unknown":
        candidate["overview_claim_ids"] = ["unknown"]
    elif failure == "too_long":
        candidate["claims"][0]["text"] = "x" * 1600
        candidate["claims"][1]["text"] = "y" * 1600
        candidate["overview_claim_ids"] = ["known_1", "postulated_1"]
    elif failure == "invented_quote":
        candidate["claims"][0]["evidence"][0]["quote"] = (
            "Absent synthetic quotation that was never read."
        )
    elif failure == "wrong_question":
        candidate["question"] = "Different question?"
    elif failure == "label_upgrade":
        candidate["claims"][2]["label"] = "known"
    elif failure == "unread_selected":
        action["selected_retrieval_ids"] = ["00000000-0000-0000-0000-000000000000"]
    model = SequenceModel([*prefix(basis), action, inconclusive()])
    result = await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(
        basis, ask_reassessment_overview_intent=AskReassessmentOverviewIntent()
    )
    assert result.status == "completed" and result.disposition == "inconclusive"
    assert result.revised_result is None
    observation = json.loads(model.seen[-1][-1]["content"])["observation"]
    assert observation["error"] == code
    assert result.usage.model_calls == 7


def test_basis_pins_exact_prior_binding_without_changing_absent_json():
    basis, _ = pinned_basis()
    raw = basis.model_dump(mode="json")
    assert AssessmentBasis.model_validate_json(json.dumps(raw)) == basis
    for change in [
        {"prior_overview_binding": None},
        {"prior_overview_binding": {"protocol": "foreign", "claim_ids": ["known_1"]}},
        {
            "prior_overview_binding": {
                "protocol": "polymath-ask-claim-overview-v1",
                "claim_ids": ["unknown"],
            }
        },
    ]:
        with pytest.raises(ValidationError):
            AssessmentBasis.model_validate_json(json.dumps(raw | change))
    raw["prior_finding"]["summary"] = "Unbound different scientific statement."
    with pytest.raises(ValidationError):
        AssessmentBasis.model_validate_json(json.dumps(raw))
    legacy, _ = pinned_basis(bound=False)
    assert "prior_overview_binding" not in legacy.model_dump(mode="json")


def test_new_assessment_schema_has_bound_draft_and_no_legacy_answer_summary():
    defs = ASK_REASSESSMENT_OVERVIEW_ACTION_ADAPTER.json_schema()["$defs"]
    assert "BoundAnswerDraft" in defs
    assert "AnswerFinding" not in defs
    assert "summary" not in defs["BoundAnswerDraft"]["properties"]


def test_reassessment_message_builder_binds_basis_and_exact_intent():
    basis, _ = pinned_basis()
    messages = ask_reassessment_overview_initial_messages(basis)
    assert messages[0]["content"].startswith(ASK_REASSESSMENT_OVERVIEW_SYSTEM_PROMPT)
    assert json.loads(messages[1]["content"]) == {
        "assessment_basis": basis.model_dump(mode="json"),
        "ask_reassessment_overview_intent": {"protocol": "polymath-ask-reassessment-overview-v1"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["explore", "ask"])
async def test_legacy_assessment_basis_messages_and_result_are_exact(mode):
    baseline = json.loads(BASELINE.read_text())
    basis, sources = assessment_basis(mode=mode)

    class Recording(AssessmentFixtureModel):
        async def complete(self, messages, *, max_output_tokens):
            self.messages.append(copy.deepcopy(messages))
            return await super().complete(messages, max_output_tokens=max_output_tokens)

    model = Recording(revised=True)
    model.messages = []
    result = await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(basis)
    assert basis.model_dump(mode="json") == baseline[f"legacy_{mode}_assessment_basis"]
    assert result.model_dump(mode="json") == baseline[f"legacy_{mode}_assessment_result"]
    assert model.messages == baseline[f"legacy_{mode}_assessment_messages"]


def test_fixed_reassessment_wrapper_sizes_without_constructing_science():
    messages = ask_reassessment_overview_initial_messages()
    assert json.loads(messages[1]["content"]) == {
        "assessment_basis": {},
        "ask_reassessment_overview_intent": {"protocol": "polymath-ask-reassessment-overview-v1"},
    }


@pytest.mark.asyncio
async def test_independent_assessment_validation_cannot_drop_bound_revision_metadata():
    basis, sources = pinned_basis(bound=False)
    result = await AssessmentController(
        BoundAdaptiveModel(revised=True), AssessmentFixtureTools(sources)
    ).reassess(basis, ask_reassessment_overview_intent=AskReassessmentOverviewIntent())
    raw = result.model_dump(mode="json")
    raw["revised_result"].pop("overview_binding")
    from polymath.assessment_contracts import AssessmentResult

    forged = AssessmentResult.model_validate_json(json.dumps(raw))
    with pytest.raises(ValueError, match="ask_overview_binding_required"):
        validate_assessment_result(forged, basis, sources, expected_overview=True)
