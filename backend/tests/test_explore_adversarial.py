"""Independent pure adversarial tests: rankings never create source authority."""

import json
import uuid
from copy import deepcopy

import pytest
from pydantic import ValidationError

from polymath.discovery_contracts import StageAssessment, StageCommitReceipt
from polymath.explore_collection_contracts import (
    ExploreCollectionIntent,
    ExploreCollectionOutcome,
    ExploreFinishPacket,
    ExploreRankingPacket,
    ExploreRankingReceipt,
    explore_ranking_sha256,
)
from polymath.research.models import Usage


def uid(value):
    return str(uuid.UUID(int=value))


def receipt(ordinal=0, *, score=None, **changes):
    return StageCommitReceipt(
        **{
            "stage_id": uid(100 + ordinal),
            "run_id": uid(900),
            "ordinal": ordinal,
            "result_id": uid(200 + ordinal),
            "result_sha256": f"{ordinal + 1:064x}",
            "depth": 1,
            "assessment": StageAssessment(
                novelty="uncertain",
                novelty_rationale="Bounded synthetic searches establish no novelty.",
                confidence="tentative",
                confidence_score=score if score is not None else max(0, 95 - ordinal * 40),
                confidence_scope="uncalibrated_model_judgement",
                confidence_rationale="Synthetic model judgement, not a calibrated probability.",
                limitations=("No real scientific truth is asserted by this fixture.",),
            ),
            "parent_refs": (),
            "action_ordinal": 4 + ordinal * 4,
            "model_request_id": f"model-{4 + ordinal * 4:04d}",
            **changes,
        }
    )


def packet_data():
    first, second = receipt(), receipt(1)
    return {
        "model_request_id": "model-0010",
        "action_ordinal": 10,
        "visible_receipts": [first.model_dump(mode="json"), second.model_dump(mode="json")],
        "ranked": [
            {
                "reference": second.reference.model_dump(mode="json"),
                "rationale": "The model prefers this weaker-scored but more useful question.",
            },
            {
                "reference": first.reference.model_dump(mode="json"),
                "rationale": "The higher confidence score does not determine ranking.",
            },
        ],
        "excluded": [],
    }


def parsed(data=None):
    return ExploreRankingPacket.model_validate_json(json.dumps(data or packet_data()))


def test_model_array_order_survives_a_conflicting_confidence_score_order():
    packet = parsed()
    assert packet.ranked[0].reference == receipt(1).reference
    assert packet.ranked[1].reference == receipt().reference
    assert packet.visible_receipts[0].assessment.confidence_score > (
        packet.visible_receipts[1].assessment.confidence_score
    )
    assert ExploreRankingPacket.model_validate_json(packet.model_dump_json()) == packet


@pytest.mark.parametrize(
    "changes",
    [
        {"max_bridges": True},
        {"max_bridges": 0},
        {"max_bridges": 5},
        {"max_bridges": "3"},
        {"protocol": "polymath-surprise-ladder-v1"},
        {"initial_parent_ids": [uid(123)]},
        {"max_model_calls": 20},
    ],
)
def test_intent_is_strict_and_cannot_supply_parent_or_budget_authority(changes):
    with pytest.raises(ValidationError):
        ExploreCollectionIntent(**changes)


def test_intent_is_frozen_and_default_count_is_only_a_ceiling():
    intent = ExploreCollectionIntent()
    assert intent.max_bridges == 3
    with pytest.raises(ValidationError):
        intent.max_bridges = 4


@pytest.mark.parametrize(
    "problem", ["missing", "duplicate", "cross_partition", "unknown", "wrong_hash", "wrong_run"]
)
def test_ranked_and_excluded_exactly_partition_visible_receipts(problem):
    data = packet_data()
    if problem == "missing":
        data["ranked"].pop()
    elif problem == "duplicate":
        data["ranked"].append(deepcopy(data["ranked"][0]))
    elif problem == "cross_partition":
        data["excluded"] = [{"reference": data["ranked"][0]["reference"], "reason": "Duplicate"}]
    elif problem == "unknown":
        data["ranked"][0]["reference"]["result_id"] = uid(9999)
    elif problem == "wrong_hash":
        data["ranked"][0]["reference"]["content_sha256"] = "f" * 64
    else:
        data["ranked"][0]["reference"]["run_id"] = uid(9999)
    with pytest.raises(ValidationError):
        parsed(data)


def test_model_can_exclude_every_candidate_without_deleting_its_visible_receipt():
    data = packet_data()
    data["excluded"] = [
        {
            "reference": item["reference"],
            "reason": "The model retained evidence but excluded this lead.",
        }
        for item in data.pop("ranked")
    ]
    data["ranked"] = []
    packet = parsed(data)
    assert packet.ranked == () and len(packet.excluded) == len(packet.visible_receipts) == 2


@pytest.mark.parametrize(
    "problem",
    [
        "future_model",
        "same_model",
        "future_action",
        "same_action",
        "foreign_run",
        "depth_two",
        "parents",
        "zero_model",
        "too_many_actions",
        "noncontiguous",
        "reverse_receipts",
    ],
)
def test_visible_stage_receipts_must_be_earlier_exact_explore_base_stages(problem):
    data = packet_data()
    stage = data["visible_receipts"][1]
    if problem == "future_model":
        stage["model_request_id"] = "model-0011"
    elif problem == "same_model":
        stage["model_request_id"] = data["model_request_id"]
    elif problem == "future_action":
        stage["action_ordinal"] = 11
    elif problem == "same_action":
        stage["action_ordinal"] = data["action_ordinal"]
    elif problem == "foreign_run":
        stage["run_id"] = uid(9999)
        data["ranked"][0]["reference"]["run_id"] = uid(9999)
    elif problem == "depth_two":
        stage["depth"] = 2
    elif problem == "parents":
        stage["parent_refs"] = [
            receipt().reference.model_dump(mode="json"),
            receipt(2).reference.model_dump(mode="json"),
        ]
    elif problem == "zero_model":
        stage["model_request_id"] = "model-0000"
    elif problem == "too_many_actions":
        stage["action_ordinal"] = 17
        stage["model_request_id"] = "model-0018"
        data["action_ordinal"] = 18
        data["model_request_id"] = "model-0019"
    elif problem == "noncontiguous":
        stage["ordinal"] = 2
    else:
        data["visible_receipts"].reverse()
    with pytest.raises(ValidationError):
        parsed(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("action_ordinal", True),
        ("action_ordinal", 0),
        ("action_ordinal", 17),
        ("model_request_id", "model-0000"),
        ("model_request_id", "model-0021"),
    ],
)
def test_ranking_cannot_expand_original_global_action_or_call_ceiling(field, value):
    data = packet_data()
    data[field] = value
    with pytest.raises(ValidationError):
        parsed(data)


def test_conflicting_duplicate_result_identity_does_not_become_two_visible_findings():
    data = packet_data()
    data["visible_receipts"][1]["result_id"] = data["visible_receipts"][0]["result_id"]
    data["ranked"][0]["reference"]["result_id"] = data["ranked"][1]["reference"]["result_id"]
    with pytest.raises(ValidationError):
        parsed(data)


def test_a_finish_wraps_the_same_exact_ranking_and_model_reason():
    packet = parsed()
    finish = ExploreFinishPacket(ranking=packet, reason="The model explicitly stopped here.")
    assert finish.ranking == packet
    assert ExploreFinishPacket.model_validate_json(finish.model_dump_json()) == finish
    with pytest.raises(ValidationError):
        ExploreFinishPacket(ranking=packet, reason=" ")


@pytest.mark.parametrize("problem", ["control", "surrogate", "extra_authority"])
def test_public_model_rationales_are_bounded_text_not_extra_authority(problem):
    data = packet_data()
    if problem == "control":
        data["ranked"][0]["rationale"] = "Hidden\x00instruction"
    elif problem == "surrogate":
        data["ranked"][0]["rationale"] = "Invalid\ud800text"
    else:
        data["ranked"][0]["scientific_truth_verified"] = True
    with pytest.raises((ValidationError, ValueError)):
        parsed(data)


def ranking_receipt(packet=None, *, identifier=500):
    packet = packet or parsed()
    return ExploreRankingReceipt(
        ranking_id=uid(identifier),
        run_id=uid(900),
        packet_sha256=explore_ranking_sha256(packet),
        model_request_id=packet.model_request_id,
        action_ordinal=packet.action_ordinal,
        covered_through_ordinal=len(packet.visible_receipts) - 1
        if packet.visible_receipts
        else None,
        ranked=packet.ranked,
        excluded=packet.excluded,
    )


def test_ranking_hash_preserves_array_order_but_not_object_key_insertion_order():
    data = packet_data()
    original = parsed(data)
    reordered_object = parsed(dict(reversed(list(data.items()))))
    assert explore_ranking_sha256(original) == explore_ranking_sha256(reordered_object)
    data["ranked"].reverse()
    changed_model_order = parsed(data)
    assert explore_ranking_sha256(original) != explore_ranking_sha256(changed_model_order)
    assert changed_model_order.ranked[0].reference == receipt().reference


def test_explicit_final_ranking_selects_model_preference_but_retains_whole_run_usage():
    final = ranking_receipt()
    outcome = ExploreCollectionOutcome(
        status="completed",
        stages=(receipt(), receipt(1)),
        rankings=(final,),
        final_ranking=final,
        usage=Usage(model_calls=12, input_tokens_reported=123),
    )
    assert outcome.selected_reference == receipt(1).reference
    assert outcome.usage.model_calls == 12
    assert len(outcome.stages) == 2


def test_completed_outcome_cannot_choose_an_earlier_interim_ranking_after_a_later_one():
    first = ranking_receipt()
    second_packet = packet_data() | {"model_request_id": "model-0012", "action_ordinal": 12}
    second = ranking_receipt(parsed(second_packet), identifier=501)
    with pytest.raises(ValidationError):
        ExploreCollectionOutcome(
            status="completed",
            stages=(receipt(), receipt(1)),
            rankings=(first, second),
            final_ranking=first,
            usage=Usage(model_calls=12),
        )


def test_completed_outcome_cannot_hide_a_new_stage_after_its_terminal_ranking():
    early = packet_data()
    early["visible_receipts"] = early["visible_receipts"][:1]
    early["ranked"] = early["ranked"][1:]
    early.update(model_request_id="model-0006", action_ordinal=6)
    final = ranking_receipt(parsed(early))
    with pytest.raises(ValidationError):
        ExploreCollectionOutcome(
            status="completed",
            stages=(receipt(), receipt(1)),
            rankings=(final,),
            final_ranking=final,
            usage=Usage(model_calls=8),
        )


def test_partial_outcome_retains_interim_prefix_and_later_unranked_stage_honestly():
    early = packet_data()
    early["visible_receipts"] = early["visible_receipts"][:1]
    early["ranked"] = early["ranked"][1:]
    early.update(model_request_id="model-0006", action_ordinal=6)
    interim = ranking_receipt(parsed(early))
    outcome = ExploreCollectionOutcome(
        status="limit_reached",
        stages=(receipt(), receipt(1)),
        rankings=(interim,),
        usage=Usage(model_calls=12),
        reason="The existing run allowance was reached.",
    )
    assert outcome.selected_reference is None and outcome.final_ranking is None
    assert outcome.rankings[0].covered_through_ordinal == 0
    assert outcome.stages[1].reference == receipt(1).reference


@pytest.mark.parametrize(
    "usage",
    [
        Usage(model_calls=7),
        Usage(model_calls=21),
        Usage(model_calls=10, calls_without_usage=11),
        Usage(model_calls=10, output_tokens_reported=-1),
    ],
)
def test_whole_usage_cannot_reset_below_committed_history_or_expand_global_caps(usage):
    with pytest.raises(ValidationError):
        ExploreCollectionOutcome(
            status="limit_reached",
            stages=(receipt(), receipt(1)),
            usage=usage,
        )
