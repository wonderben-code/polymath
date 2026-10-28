"""Exact classified claim selection through the real controller, no network/DB."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_research_modes import (
    AskFixtureModel,
    ModeFixtureTools,
    SequenceFixtureModel,
    fixture_answer,
    rejection,
    research_prefix,
)

from polymath.artifact_contracts import ARTIFACT_ACTION_ADAPTER
from polymath.assessment_controller import ASSESSMENT_ACTION_ADAPTER
from polymath.discovery_contracts import DiscoveryIntent
from polymath.meta_research_contracts import (
    TASK_KINDS,
    controller_protocol_sha256,
    discovery_controller_sha256,
)
from polymath.research.ask_overview import (
    ASK_OVERVIEW_ACTION_ADAPTER,
    ASK_OVERVIEW_SYSTEM_PROMPT,
    ask_overview_initial_messages,
)
from polymath.research.controller import MODE_INSTRUCTIONS, SYSTEM_PROMPT, ResearchController
from polymath.research.models import (
    ACTION_ADAPTER,
    AnswerFinding,
    AskOverviewBinding,
    ConcludeAction,
    ResearchResult,
    Usage,
)
from polymath.scientific_context_contracts import (
    AskOverviewBinding as SharedBinding,
)
from polymath.scientific_context_contracts import (
    AskOverviewIntent,
    validate_result_overview,
)

QUESTION = "What does synthetic redundancy imply?"
BASELINE = Path(__file__).parent / "fixtures" / "pre_scientific_context_ask.json"


def bound_action(ids=("speculative_1", "known_1", "postulated_1")):
    action = fixture_answer(QUESTION)
    action["conclusion"].pop("summary")
    action["conclusion"]["overview_claim_ids"] = list(ids)
    return action


async def investigate(actions=None, *, intent=True, tools=None, **kwargs):
    model = SequenceFixtureModel([*research_prefix(), *(actions or [bound_action()])])
    result = await ResearchController(model, tools or ModeFixtureTools()).investigate(
        QUESTION,
        mode="ask",
        **({"ask_overview_intent": AskOverviewIntent()} if intent else {}),
        **kwargs,
    )
    return result, model


@pytest.mark.asyncio
async def test_exact_selected_order_retains_all_classifications_and_source_evidence():
    result, model = await investigate()
    finding = result.finding
    assert result.status == "answer"
    assert result.overview_binding.claim_ids == ("speculative_1", "known_1", "postulated_1")
    by_id = {claim.id: claim for claim in finding.claims}
    assert finding.summary == "\n\n".join(
        by_id[key].text for key in result.overview_binding.claim_ids
    )
    assert {claim.label for claim in finding.claims} == {"known", "postulated", "speculative"}
    assert by_id["postulated_1"].derivation
    assert by_id["speculative_1"].caveats
    assert [quote.claim_id for quote in result.quotes] == [
        "known_1",
        "postulated_1",
        "postulated_1",
        "speculative_1",
    ]
    assert result.usage.model_calls == 6
    assert result.usage.input_tokens_reported == 60
    assert result.usage.output_tokens_reported == 90
    assert len(model.observations) == 6
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result
    validate_result_overview(result.model_dump(mode="json"), expected=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,code",
    [
        ("free_summary", "invalid_action_schema"),
        ("missing_selection", "invalid_action_schema"),
        ("duplicate", "ask_overview_duplicate_claim"),
        ("unknown", "ask_overview_unknown_claim"),
        ("too_long", "ask_overview_too_long"),
        ("claim_duplicate", "ask_claim_ids_not_unique"),
        ("postulated_no_derivation", "invalid_action_schema"),
        ("speculative_no_caveat", "invalid_action_schema"),
    ],
)
async def test_invalid_draft_stays_inside_existing_repair_budget(failure, code):
    action = bound_action()
    conclusion = action["conclusion"]
    if failure == "free_summary":
        conclusion["summary"] = "Uncited extra scientific prose."
    elif failure == "missing_selection":
        conclusion.pop("overview_claim_ids")
    elif failure == "duplicate":
        conclusion["overview_claim_ids"] = ["known_1", "known_1"]
    elif failure == "unknown":
        conclusion["overview_claim_ids"] = ["unknown_claim"]
    elif failure == "too_long":
        for claim in conclusion["claims"][:2]:
            claim["text"] = "x" * 1600
        conclusion["overview_claim_ids"] = ["known_1", "postulated_1"]
    elif failure == "claim_duplicate":
        conclusion["claims"][1]["id"] = "known_1"
    elif failure == "postulated_no_derivation":
        conclusion["claims"][1].pop("derivation")
    elif failure == "speculative_no_caveat":
        conclusion["claims"][2].pop("caveats")
    result, model = await investigate([action, rejection()])
    assert result.status == "insufficient_evidence"
    assert result.overview_binding is None
    assert "overview_binding" not in result.model_dump(mode="json")
    assert result.usage.model_calls == 7
    assert model.observations[-1]["error"] == code
    assert sum(event.kind == "action_rejected" for event in result.events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,code",
    [
        ("wrong_question", "answer_question_mismatch"),
        ("invented_quote", "quotation_not_found"),
        ("unread_source", "unread_source"),
        ("one_paper_inference", "postulated_requires_distinct_sources"),
    ],
)
async def test_bound_overview_does_not_bypass_existing_evidence_validation(failure, code):
    action = bound_action()
    conclusion = action["conclusion"]
    if failure == "wrong_question":
        conclusion["question"] = "A different research question?"
    elif failure == "invented_quote":
        conclusion["claims"][0]["evidence"][0]["quote"] = (
            "Invented quotation absent from the saved abstract."
        )
    elif failure == "unread_source":
        conclusion["claims"][0]["evidence"][0]["source_id"] = "invented"
    else:
        conclusion["claims"][1]["evidence"] = conclusion["claims"][1]["evidence"][:1]
    result, model = await investigate([action, rejection()])
    assert result.status == "insufficient_evidence"
    assert model.observations[-1]["error"] == code
    assert result.overview_binding is None


@pytest.mark.asyncio
async def test_bound_overview_still_requires_challenge_and_prior_work_searches():
    model = SequenceFixtureModel([*research_prefix()[:3], bound_action(), rejection()])
    result = await ResearchController(model, ModeFixtureTools()).investigate(
        QUESTION, mode="ask", ask_overview_intent=AskOverviewIntent()
    )
    assert result.status == "insufficient_evidence"
    assert model.observations[-1]["error"] == "missing_required_searches"


@pytest.mark.asyncio
async def test_repeated_schema_failure_stops_at_same_repair_limit_without_extra_call():
    bad = bound_action(("unknown",))
    result, model = await investigate([bad, bad, bad, rejection()])
    assert result.status == "failed" and result.error_code == "repair_limit"
    assert result.usage.model_calls == 8
    assert len(model.observations) == 8
    assert result.overview_binding is None


@pytest.mark.asyncio
async def test_legacy_profile_cannot_accept_new_selection_draft():
    result, model = await investigate([bound_action(), rejection()], intent=False)
    assert result.status == "insufficient_evidence"
    assert model.observations[-1]["error"] == "invalid_action_schema"
    assert "overview_binding" not in result.model_dump(mode="json")


@pytest.mark.asyncio
async def test_new_profile_cannot_fall_back_to_legacy_free_summary_conclusion():
    result, model = await investigate([fixture_answer(QUESTION), rejection()])
    assert result.status == "insufficient_evidence"
    assert model.observations[-1]["error"] == "invalid_action_schema"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "explore"},
        {"mode": "surprise"},
        {"mode": "ask", "discovery": DiscoveryIntent()},
        {
            "mode": "ask",
            "parent_result_ids": (
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
            ),
        },
    ],
)
async def test_wrong_mode_or_other_protocol_rejected_before_any_call(kwargs):
    model = SequenceFixtureModel([])
    with pytest.raises(ValueError, match="ask_overview_wrong_mode"):
        await ResearchController(model, ModeFixtureTools()).investigate(
            QUESTION, ask_overview_intent=AskOverviewIntent(), **kwargs
        )
    assert model.observations == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent",
    [
        {"protocol": "polymath-ask-claim-overview-v1"},
        AskOverviewIntent.model_construct(protocol="forged-protocol"),
    ],
)
async def test_invalid_intent_rejected_before_any_call(intent):
    model = SequenceFixtureModel([])
    with pytest.raises(ValueError):
        await ResearchController(model, ModeFixtureTools()).investigate(
            QUESTION, mode="ask", ask_overview_intent=intent
        )
    assert model.observations == []


@pytest.mark.asyncio
async def test_result_parser_independently_rejects_binding_summary_mismatch_and_wrong_scope():
    result, _ = await investigate()
    raw = result.model_dump(mode="json")
    for change in [
        {"mode": "explore"},
        {"status": "failed"},
        {"overview_binding": None},
        {
            "overview_binding": {
                "protocol": "polymath-ask-claim-overview-v1",
                "claim_ids": ["unknown"],
            }
        },
        {
            "overview_binding": {
                "protocol": "polymath-ask-claim-overview-v1",
                "claim_ids": ["known_1", "known_1"],
            }
        },
    ]:
        with pytest.raises(ValidationError):
            ResearchResult.model_validate_json(json.dumps(raw | change))
    raw["finding"]["summary"] = "Independent free summary is rejected."
    with pytest.raises(ValidationError):
        ResearchResult.model_validate_json(json.dumps(raw))


@pytest.mark.asyncio
async def test_exact_summary_without_binding_retains_legacy_semantics_and_store_requires_intent():
    result, _ = await investigate()
    raw = result.model_dump(mode="json")
    raw.pop("overview_binding")
    old = ResearchResult.model_validate_json(json.dumps(raw))
    assert old.overview_binding is None
    with pytest.raises(ValueError, match="ask_overview_binding_required"):
        validate_result_overview(old.model_dump(mode="json"), expected=True)
    with pytest.raises(ValueError, match="ask_overview_not_admitted"):
        validate_result_overview(result.model_dump(mode="json"), expected=False)


def test_one_shared_binding_type_and_no_free_summary_in_new_schema():
    assert SharedBinding is AskOverviewBinding
    definitions = ASK_OVERVIEW_ACTION_ADAPTER.json_schema()["$defs"]
    assert "AnswerFinding" not in definitions
    assert "summary" not in definitions["BoundAnswerDraft"]["properties"]
    assert set(definitions["BoundAnswerDraft"]["required"]) >= {"claims", "overview_claim_ids"}
    assert "The summary\nis only scope/orientation" in SYSTEM_PROMPT
    assert "The summary\nis only scope/orientation" not in ASK_OVERVIEW_SYSTEM_PROMPT


def test_initial_message_builder_names_exact_protocol_and_schema():
    messages = ask_overview_initial_messages("  " + QUESTION + "  ", memory_available=True)
    assert json.loads(messages[1]["content"]) == {
        "mode": "ask",
        "research_goal": QUESTION,
        "memory_available": True,
        "parent_result_ids": [],
        "ask_overview_intent": {"protocol": "polymath-ask-claim-overview-v1"},
    }
    assert messages[0]["content"].startswith(ASK_OVERVIEW_SYSTEM_PROMPT)
    assert (
        json.loads(messages[0]["content"].split("\nAction schema:\n", 1)[1])
        == ASK_OVERVIEW_ACTION_ADAPTER.json_schema()
    )


@pytest.mark.asyncio
async def test_all_old_action_schemas_prompts_and_static_controller_digests_are_unchanged():
    original = json.loads(BASELINE.read_text())
    assert {kind: controller_protocol_sha256(kind) for kind in TASK_KINDS} == original["profiles"]
    assert discovery_controller_sha256() == original["discovery"]
    assert ACTION_ADAPTER.json_schema() == original["research_schema"]
    assert AnswerFinding.model_json_schema() == original["answer_schema"]
    assert ConcludeAction.model_json_schema() == original["conclude_schema"]
    assert ARTIFACT_ACTION_ADAPTER.json_schema() == original["artifact_schema"]
    assert ASSESSMENT_ACTION_ADAPTER.json_schema() == original["assessment_schema"]
    assert SYSTEM_PROMPT == original["system_prompt"]
    assert MODE_INSTRUCTIONS == original["mode_instructions"]

    class Recording(AskFixtureModel):
        async def complete(self, messages, *, max_output_tokens):
            self.messages.append(copy.deepcopy(messages))
            return await super().complete(messages, max_output_tokens=max_output_tokens)

    model = Recording()
    model.messages = []
    result = await ResearchController(model, ModeFixtureTools()).investigate(QUESTION, mode="ask")
    assert result.model_dump(mode="json") == original["legacy_answer_result"]
    assert model.messages == original["legacy_answer_messages"]


def test_null_and_foreign_overview_protocols_are_not_silently_omitted():
    for binding in [None, {"protocol": "foreign", "claim_ids": ["known_1"]}]:
        with pytest.raises(ValidationError):
            ResearchResult(status="failed", usage=Usage(), overview_binding=binding)
