"""Intent, normal-result commits, ordered ranking receipts and bounded replay."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_explore_adversarial import packet_data, receipt, uid
from test_sharing_projection import bridge_result

from polymath.discovery_contracts import (
    DISCOVERY_ACTION_ADAPTER,
    StageAssessment,
    StageCommitPacket,
    StageCommitReceipt,
    StageContext,
)
from polymath.explore_collection_contracts import (
    EXPLORE_ACTION_ADAPTER,
    ExploreCollectionIntent,
    ExploreCollectionOutcome,
    ExploreCommitPacket,
    ExploreFinishPacket,
    ExploreRankingPacket,
    ExploreRankingReceipt,
    ExploreStopPacket,
    canonical_explore_commit_json,
    canonical_explore_ranking_json,
    explore_commit_sha256,
    explore_finish_sha256,
    explore_ranking_sha256,
    validate_explore_admission,
    validate_explore_commit,
    validate_explore_commit_receipt,
    validate_explore_context,
    validate_explore_ranking,
    validate_explore_ranking_receipt,
    validate_explore_receipts,
)
from polymath.research.models import FieldSelection, ResearchEvent, Usage


def commit_packet(*, memory_lead=False):
    current = bridge_result()
    current = current.model_copy(
        update={
            "usage": Usage(model_calls=4, input_tokens_reported=20, output_tokens_reported=40),
            "memory_reads": (receipt().reference,) if memory_lead else (),
        }
    )
    context = StageContext(
        ordinal=0,
        model_request_id="model-0001",
        action_ordinal=1,
        selection=FieldSelection(
            fields=(current.finding.field_left, current.finding.field_right),
            rationale="Model-authored fields for this user concept.",
        ),
    )
    return ExploreCommitPacket(
        context=context,
        model_request_id="model-0004",
        action_ordinal=4,
        result=current,
        assessment=receipt().assessment,
    )


def ranking_receipt(packet=None, *, ordinal=0):
    packet = packet or ExploreRankingPacket.model_validate_json(json.dumps(packet_data()))
    return ExploreRankingReceipt(
        ranking_id=uid(1000 + ordinal),
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


def test_intent_is_explicit_frozen_and_grants_no_money_or_initial_parent_authority():
    intent = ExploreCollectionIntent()
    assert intent.model_dump() == {"protocol": "polymath-explore-collection-v1", "max_bridges": 3}
    validate_explore_admission(intent, mode="explore", task_kind="research")
    with pytest.raises(ValidationError):
        intent.max_bridges = 4
    for mode, kind, parents in (
        ("surprise", "research", ()),
        ("ask", "research", ()),
        ("explore", "formalise", ()),
        ("explore", "research", (uid(1),)),
        ("explore", "research", False),
    ):
        with pytest.raises(ValueError):
            validate_explore_admission(intent, mode=mode, task_kind=kind, parent_result_ids=parents)


def test_commit_is_a_normal_explore_result_with_separate_branch_scope_and_shared_assessment():
    packet = commit_packet()
    assert packet.result.mode == "explore" and packet.result.field_selections == ()
    assert packet.context.parent_refs == ()
    assert isinstance(packet.assessment, StageAssessment)
    assert ExploreCommitPacket.model_validate_json(packet.model_dump_json()) == packet
    validate_explore_context(packet.context)
    validate_explore_commit(packet, intent=ExploreCollectionIntent())
    validate_explore_commit_receipt(packet, receipt())


def test_recorded_memory_leads_remain_possible_without_becoming_selected_parents():
    packet = commit_packet(memory_lead=True)
    assert packet.result.memory_reads == (receipt().reference,)
    assert packet.context.parent_refs == ()
    assert receipt().parent_refs == ()
    validate_explore_commit(packet, intent=ExploreCollectionIntent())


@pytest.mark.parametrize("change", ["mode", "fields", "branch", "revision", "usage", "parents"])
def test_positive_commit_shape_rejects_cross_mode_parent_and_counter_forgery(change):
    data = commit_packet().model_dump(mode="json")
    if change == "mode":
        data["result"]["mode"] = "surprise"
    elif change == "fields":
        data["result"]["field_selections"] = [data["context"]["selection"]]
    elif change == "branch":
        data["context"]["selection"]["fields"] = ["Unrelated A", "Unrelated B"]
    elif change == "revision":
        data["revision_of"] = receipt().reference.model_dump(mode="json")
    elif change == "usage":
        data["result"]["usage"]["model_calls"] = 1
    else:
        data["context"]["parent_refs"] = [
            receipt().reference.model_dump(mode="json"),
            receipt(1).reference.model_dump(mode="json"),
        ]
    with pytest.raises(ValidationError):
        ExploreCommitPacket.model_validate_json(json.dumps(data))


def test_actual_tighter_run_ceiling_is_independent_of_the_hard_four_bridge_bound():
    one = ExploreCollectionIntent(max_bridges=1)
    validate_explore_receipts((receipt(),), intent=one)
    with pytest.raises(ValueError, match="explore_receipt_limit"):
        validate_explore_receipts((receipt(), receipt(1)), intent=one)
    next_packet = commit_packet().model_copy(
        update={
            "model_request_id": "model-0008",
            "action_ordinal": 8,
            "result": commit_packet().result.model_copy(update={"usage": Usage(model_calls=8)}),
        }
    )
    with pytest.raises(ValueError, match="explore_receipt_limit"):
        validate_explore_commit(next_packet, intent=one, previous_receipts=(receipt(),))


def test_canonical_hashes_revalidate_types_and_preserve_model_array_order():
    commit = commit_packet()
    expected = hashlib.sha256(canonical_explore_commit_json(commit).encode()).hexdigest()
    assert explore_commit_sha256(commit) == expected
    packet = ExploreRankingPacket.model_validate_json(json.dumps(packet_data()))
    raw = packet.model_dump(mode="json")
    reordered_keys = dict(reversed(list(raw.items())))
    assert explore_ranking_sha256(packet) == explore_ranking_sha256(
        ExploreRankingPacket.model_validate_json(json.dumps(reordered_keys))
    )
    raw["ranked"] = list(reversed(raw["ranked"]))
    different_model_order = ExploreRankingPacket.model_validate_json(json.dumps(raw))
    assert explore_ranking_sha256(packet) != explore_ranking_sha256(different_model_order)
    assert hashlib.sha256(
        canonical_explore_ranking_json(packet).encode()
    ).hexdigest() == explore_ranking_sha256(packet)
    assert explore_finish_sha256(ExploreFinishPacket(ranking=packet, reason="Model stopped."))
    forged = commit.model_copy(update={"action_ordinal": True})
    with pytest.raises(ValidationError):
        explore_commit_sha256(forged)


def test_trusted_visible_prefix_check_is_additional_to_self_consistent_model_partition():
    packet = ExploreRankingPacket.model_validate_json(json.dumps(packet_data()))
    validate_explore_ranking(
        packet,
        visible_receipts=packet.visible_receipts,
        intent=ExploreCollectionIntent(max_bridges=2),
    )
    with pytest.raises(ValueError, match="explore_ranking_visibility_mismatch"):
        validate_explore_ranking(
            packet, visible_receipts=(receipt(),), intent=ExploreCollectionIntent()
        )
    # A pure packet alone cannot prove it represents an actual committed DB row.
    assert not hasattr(packet, "authorized") and not hasattr(packet, "budget")


def test_ranking_receipt_is_bound_to_exact_packet_members_order_and_hash():
    packet = ExploreRankingPacket.model_validate_json(json.dumps(packet_data()))
    actual = ranking_receipt(packet)
    validate_explore_ranking_receipt(packet, actual, run_id=uid(900))
    assert actual.selected_reference == packet.ranked[0].reference
    for changed in (
        {"packet_sha256": "f" * 64},
        {"run_id": uid(999)},
        {"ranked": tuple(reversed(actual.ranked))},
        {"model_request_id": "model-0011", "action_ordinal": 11},
    ):
        with pytest.raises(ValueError):
            validate_explore_ranking_receipt(packet, actual.model_copy(update=changed))


def test_completed_collection_selects_only_the_model_first_ranked_reference_with_whole_usage():
    packet = ExploreRankingPacket.model_validate_json(json.dumps(packet_data()))
    ranked = ranking_receipt(packet)
    outcome = ExploreCollectionOutcome(
        status="completed",
        stages=packet.visible_receipts,
        rankings=(ranked,),
        final_ranking=ranked,
        usage=Usage(model_calls=10, input_tokens_reported=200, output_tokens_reported=400),
    )
    assert ExploreStopPacket is ExploreCollectionOutcome
    assert outcome.selected_reference == packet.ranked[0].reference
    assert outcome.selected_reference == receipt(1).reference
    assert outcome.usage.model_calls == 10
    assert (
        packet.visible_receipts[0].assessment.confidence_score
        > packet.visible_receipts[1].assessment.confidence_score
    )


def test_empty_and_all_excluded_finishes_never_invent_a_preferred_bridge():
    empty = ExploreRankingPacket(model_request_id="model-0001", action_ordinal=1)
    empty_receipt = ranking_receipt(empty)
    outcome = ExploreCollectionOutcome(
        status="completed",
        rankings=(empty_receipt,),
        final_ranking=empty_receipt,
        usage=Usage(model_calls=1),
    )
    assert outcome.selected_reference is None and outcome.stages == ()
    data = packet_data()
    data["excluded"] = [
        {"reference": item["reference"], "reason": item["rationale"]} for item in data["ranked"]
    ]
    data["ranked"] = []
    packet = ExploreRankingPacket.model_validate_json(json.dumps(data))
    actual = ranking_receipt(packet)
    outcome = ExploreCollectionOutcome(
        status="completed",
        stages=packet.visible_receipts,
        rankings=(actual,),
        final_ranking=actual,
        usage=Usage(model_calls=10),
    )
    assert outcome.selected_reference is None and len(outcome.stages) == 2


@pytest.mark.parametrize("change", ["low_calls", "over_calls", "unsafe_tokens", "bad_events"])
def test_whole_run_outcome_counters_and_events_cannot_be_reset_or_unbounded(change):
    packet = ExploreRankingPacket.model_validate_json(json.dumps(packet_data()))
    actual = ranking_receipt(packet)
    usage = Usage(model_calls=10)
    events = ()
    if change == "low_calls":
        usage = Usage(model_calls=8)
    elif change == "over_calls":
        usage = Usage(model_calls=21)
    elif change == "unsafe_tokens":
        usage = Usage(model_calls=10, input_tokens_reported=2**53)
    else:
        events = (ResearchEvent(sequence=1, kind="x" * 65, summary="Too long kind"),)
    with pytest.raises(ValidationError):
        ExploreCollectionOutcome(
            status="completed",
            stages=packet.visible_receipts,
            rankings=(actual,),
            final_ranking=actual,
            usage=usage,
            events=events,
        )


def test_new_actions_reuse_note_and_fields_but_cannot_forge_receipts_or_parent_selection():
    parsed = EXPLORE_ACTION_ADAPTER.validate_json(
        json.dumps(
            {
                "kind": "record_bridge",
                "finding": commit_packet().result.finding.model_dump(mode="json"),
                "assessment": receipt().assessment.model_dump(mode="json"),
            }
        )
    )
    assert parsed.kind == "record_bridge"
    for value in (
        {"kind": "select_parents", "parents": []},
        {
            "kind": "record_bridge",
            "finding": parsed.finding.model_dump(mode="json"),
            "assessment": parsed.assessment.model_dump(mode="json"),
            "revision_of": receipt().reference.model_dump(mode="json"),
        },
        {
            "kind": "finish_ranked",
            "ranking": {"visible_receipts": []},
            "reason": "Injected authority",
        },
        {"kind": "conclude", "conclusion": parsed.finding.model_dump(mode="json")},
    ):
        with pytest.raises(ValidationError):
            EXPLORE_ACTION_ADAPTER.validate_json(json.dumps(value))
    for kind in ("read_memory_finding", "read_memory_source"):
        value = {"kind": kind, "result_id": uid(200)}
        if kind == "read_memory_source":
            value["retrieval_id"] = uid(201)
        assert EXPLORE_ACTION_ADAPTER.validate_json(json.dumps(value)).kind == kind


def test_old_surprise_schema_and_shared_stage_types_are_untouched():
    saved = json.loads(
        (Path(__file__).parent / "fixtures/pre_scientific_context_baseline.json").read_text()
    )
    old = saved["fixtures"]["action_schemas"]["discovery"]
    canonical = json.dumps(
        DISCOVERY_ACTION_ADAPTER.json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert canonical == old["canonical_json"]
    assert hashlib.sha256(canonical.encode()).hexdigest() == old["sha256"]
    assert set(StageCommitPacket.model_fields) == {
        "context",
        "model_request_id",
        "action_ordinal",
        "result",
        "assessment",
        "revision_of",
    }
    assert set(StageCommitReceipt.model_fields) == {
        "stage_id",
        "run_id",
        "ordinal",
        "result_id",
        "result_sha256",
        "depth",
        "assessment",
        "parent_refs",
        "action_ordinal",
        "model_request_id",
    }
