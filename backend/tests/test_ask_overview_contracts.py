"""Isolated contract prototype: exact classified-claim selection, never new prose."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from polymath.research.models import AnswerClaim, ResearchResult, Usage
from polymath.scientific_context_contracts import (
    AskOverviewBinding,
    AskOverviewIntent,
    BoundAnswerDraft,
    result_overview_metadata,
    validate_answer_overview,
    validate_result_overview,
)

PROTOCOL = "polymath-ask-claim-overview-v1"


def claim(identifier="claim_a", text="The first exact supported statement.", **changes):
    return AnswerClaim.model_validate(
        {
            "id": identifier,
            "text": text,
            "label": "known",
            "evidence": [
                {
                    "source_id": "fixture:paper_a",
                    "quote": "An exact quotation from a synthetic source.",
                    "supports": "The model connects this exact quote to its statement.",
                }
            ],
            **changes,
        }
    )


def draft_data(**changes):
    return {
        "outcome": "answer",
        "question": "What do the synthetic observations show?",
        "claims": (
            claim(),
            claim("claim_b", "The second exact statement includes Unicode: λ 😀 e\u0301."),
        ),
        "overview_claim_ids": ("claim_a", "claim_b"),
        "limitations": ("These are synthetic fixture claims, not verified scientific truth.",),
        "unanswered_questions": (),
        **changes,
    }


def result_data(draft=None):
    draft = draft or BoundAnswerDraft(**draft_data())
    result = ResearchResult(status="answer", mode="ask", finding=draft.to_answer(), usage=Usage())
    return result.model_dump(mode="json") | result_overview_metadata(draft.overview_binding())


def test_intent_and_binding_are_explicit_versioned_frozen_contracts():
    intent = AskOverviewIntent()
    binding = AskOverviewBinding(claim_ids=("claim_a",))
    assert intent.protocol == binding.protocol == PROTOCOL
    with pytest.raises(ValidationError):
        intent.protocol = "forged"
    with pytest.raises(ValidationError):
        binding.claim_ids = ("claim_b",)
    with pytest.raises(ValidationError):
        AskOverviewIntent(protocol="polymath-ask-claim-overview-v2")
    with pytest.raises(ValidationError):
        AskOverviewBinding(claim_ids=("claim_a",), summary="Unclassified new prose")


def test_model_selects_exact_claim_text_in_arbitrary_unique_order_without_paraphrase():
    draft = BoundAnswerDraft(**draft_data(overview_claim_ids=("claim_b", "claim_a")))
    answer = draft.to_answer()
    expected = draft.claims[1].text + "\n\n" + draft.claims[0].text
    assert answer.summary == expected
    assert answer.summary.encode("utf-8") == expected.encode("utf-8")
    assert answer.claims == list(draft.claims)
    assert answer.question == draft.question
    assert draft.overview_binding().claim_ids == ("claim_b", "claim_a")
    assert validate_answer_overview(answer, draft.overview_binding()) is None


def test_overview_can_select_a_strict_subset_but_preserves_every_full_claim():
    draft = BoundAnswerDraft(**draft_data(overview_claim_ids=("claim_b",)))
    answer = draft.to_answer()
    assert answer.summary == draft.claims[1].text
    assert len(answer.claims) == 2
    assert draft.overview_binding().claim_ids == ("claim_b",)


def test_no_free_summary_or_unknown_fields_are_accepted_on_draft():
    for field in ("summary", "overview", "unlabelled_scientific_claim"):
        with pytest.raises(ValidationError):
            BoundAnswerDraft(**draft_data(**{field: "Invented uncited prose"}))


@pytest.mark.parametrize("ids", [(), ("claim_a", "claim_a"), ("missing",), ("claim_a", "missing")])
def test_empty_duplicate_and_unknown_selected_claim_ids_are_rejected(ids):
    with pytest.raises(ValueError):
        BoundAnswerDraft(**draft_data(overview_claim_ids=ids)).to_answer()


def test_duplicate_claim_ids_are_rejected_even_when_overview_uses_only_one():
    with pytest.raises(ValueError):
        BoundAnswerDraft(
            **draft_data(
                claims=(claim(), claim(text="A conflicting second text.")),
                overview_claim_ids=("claim_a",),
            )
        )


@pytest.mark.parametrize("ids", [(), ("claim_a", "claim_a"), tuple(f"claim_{i}" for i in range(9))])
def test_binding_alone_requires_one_to_eight_distinct_claim_ids(ids):
    with pytest.raises(ValueError):
        AskOverviewBinding(claim_ids=ids)


def test_exact_3000_character_overview_is_accepted_without_truncation():
    draft = BoundAnswerDraft(
        **draft_data(claims=(claim(text="a" * 1499), claim("claim_b", "b" * 1499)))
    )
    summary = draft.to_answer().summary
    assert len(summary) == 3000
    assert summary == "a" * 1499 + "\n\n" + "b" * 1499


def test_3001_character_overview_is_rejected_instead_of_clipping_claim_text():
    with pytest.raises(ValueError):
        BoundAnswerDraft(
            **draft_data(claims=(claim(text="a" * 1500), claim("claim_b", "b" * 1499)))
        ).to_answer()


@pytest.mark.parametrize(
    "changes",
    [
        {"label": "postulated", "derivation": None},
        {"label": "speculative", "caveats": []},
        {"evidence": []},
        {"label": "independently_verified"},
    ],
)
def test_existing_claim_uncertainty_and_citation_contracts_still_apply(changes):
    raw = claim().model_dump(mode="json") | changes
    data = draft_data()
    data["claims"] = (raw,)
    data["overview_claim_ids"] = ("claim_a",)
    with pytest.raises(ValueError):
        BoundAnswerDraft.model_validate(data)


def test_postulated_and_speculative_overview_keeps_model_disclosures_and_exact_citations():
    first = claim(label="postulated", derivation="A synthetic model derivation, not a proof.")
    second = claim("claim_b", label="speculative", caveats=["Unconfirmed synthetic hypothesis."])
    draft = BoundAnswerDraft(**draft_data(claims=(first, second)))
    answer = draft.to_answer()
    assert answer.claims[0].derivation == first.derivation
    assert answer.claims[1].caveats == second.caveats
    assert answer.claims[0].evidence == first.evidence
    assert answer.claims[1].evidence == second.evidence
    validate_answer_overview(answer, draft.overview_binding())


@pytest.mark.parametrize("mutation", ["summary", "ids", "duplicate", "text"])
def test_independent_validation_rejects_forged_summary_or_binding(mutation):
    draft = BoundAnswerDraft(**draft_data())
    answer = draft.to_answer()
    binding = draft.overview_binding()
    if mutation == "summary":
        answer = answer.model_copy(update={"summary": answer.summary + " New uncited claim."})
    elif mutation == "ids":
        binding = binding.model_copy(update={"claim_ids": ("missing",)})
    elif mutation == "duplicate":
        binding = binding.model_copy(update={"claim_ids": ("claim_a", "claim_a")})
    else:
        changed = answer.claims[0].model_copy(update={"text": "An altered scientific claim."})
        answer = answer.model_copy(update={"claims": [changed, answer.claims[1]]})
    with pytest.raises(ValueError):
        validate_answer_overview(answer, binding)


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "explore"},
        {"mode": "surprise"},
        {"status": "bridge"},
        {"status": "failed", "finding": None},
        {
            "status": "insufficient_evidence",
            "finding": {"outcome": "insufficient_evidence"},
        },
        {"finding": {"outcome": "bridge"}},
    ],
)
def test_result_binding_is_rejected_outside_a_positive_ask_answer(changes):
    with pytest.raises(ValueError):
        validate_result_overview(result_data() | changes)


def test_present_binding_is_validated_even_when_historical_protocol_is_not_expected():
    data = result_data()
    assert validate_result_overview(data) is None
    forged = deepcopy(data)
    forged["finding"]["summary"] += " An extra uncited sentence."
    with pytest.raises(ValueError):
        validate_result_overview(forged)


def test_legacy_absence_roundtrips_exactly_and_is_not_upgraded_by_matching_summary():
    draft = BoundAnswerDraft(**draft_data())
    old = ResearchResult(status="answer", mode="ask", finding=draft.to_answer(), usage=Usage())
    before = old.model_dump_json()
    data = json.loads(before)
    assert result_overview_metadata(None) == {}
    assert validate_result_overview(data) is None
    assert validate_result_overview(data, expected=False) is None
    assert ResearchResult.model_validate_json(json.dumps(data)).model_dump_json() == before
    assert "overview_binding" not in data
    with pytest.raises(ValueError):
        validate_result_overview(data, expected=True)


def test_new_metadata_contains_exact_binding_and_protocol_not_a_null_marker():
    binding = AskOverviewBinding(claim_ids=("claim_b", "claim_a"))
    metadata = result_overview_metadata(binding)
    assert metadata == {
        "overview_binding": {"protocol": PROTOCOL, "claim_ids": ["claim_b", "claim_a"]}
    }
    assert result_overview_metadata(None) == {}


def test_explicit_legacy_profile_forbids_new_binding_and_new_profile_requires_it():
    data = result_data()
    assert validate_result_overview(data, expected=True) is None
    with pytest.raises(ValueError):
        validate_result_overview(data, expected=False)
    data.pop("overview_binding")
    with pytest.raises(ValueError):
        validate_result_overview(data, expected=True)


def test_expected_new_protocol_does_not_force_an_overview_on_a_negative_result():
    assert (
        validate_result_overview(
            {"status": "failed", "mode": "ask", "finding": None}, expected=True
        )
        is None
    )


def test_explicit_null_or_unknown_protocol_is_not_valid_present_metadata():
    data = result_data()
    for binding in (None, {"protocol": "made-up", "claim_ids": ["claim_a"]}):
        with pytest.raises(ValueError):
            validate_result_overview(data | {"overview_binding": binding})
