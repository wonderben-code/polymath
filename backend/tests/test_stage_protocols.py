"""Real strict adapters and actual legacy controller fixtures, with no PG/HTTP."""

import json
import uuid
from types import SimpleNamespace

import pytest
from test_discovery_controller import run_fixture
from test_evidence import document, result_for

from polymath.discovery_contracts import (
    DISCOVERY_ACTION_ADAPTER,
    DiscoveryIntent,
    StageAssessment,
    StageContext,
    discovery_packet_sha256,
)
from polymath.evidence import _canonical, _hash
from polymath.explore_collection_contracts import ExploreCollectionIntent, ExploreCommitPacket
from polymath.research.models import FieldSelection, MemoryReadReference, Usage
from polymath.stage_owners import StageOwnerBinding
from polymath.stage_protocols import StageProtocolError, resolve_stage_protocol


def protocol(selected_mode="explore", **changes):
    mode = selected_mode
    intent = DiscoveryIntent() if mode == "surprise" else ExploreCollectionIntent()
    run = SimpleNamespace(id=uuid.uuid4(), mode=mode, task_kind="research")
    owner = StageOwnerBinding(
        run.id,
        mode,
        "research",
        intent.protocol,
        _hash(_canonical(intent.model_dump(mode="json"))),
        intent,
    )
    owner = SimpleNamespace(**(vars(owner) | changes))
    return resolve_stage_protocol(None, run, owner_loader=lambda *_: owner)


def explore_packet(*, memory_reads=()):
    result = result_for(document("left"), document("right", text="Different synthetic evidence."))
    selection = FieldSelection(
        fields=(result.finding.field_left, result.finding.field_right),
        rationale="A synthetic branch chosen by the fixture model.",
    )
    context = StageContext(
        ordinal=0,
        model_request_id="model-0001",
        action_ordinal=1,
        selection=selection,
    )
    assessment = StageAssessment(
        novelty="uncertain",
        novelty_rationale="A bounded fixture search proves no novelty.",
        confidence="tentative",
        confidence_score=17,
        confidence_scope="uncalibrated_model_judgement",
        confidence_rationale="Synthetic evidence cannot establish scientific truth.",
        limitations=("A fixture is not a scientific discovery.",),
    )
    return ExploreCommitPacket(
        context=context,
        model_request_id="model-0004",
        action_ordinal=4,
        result=result.model_copy(
            update={"usage": Usage(model_calls=4), "memory_reads": memory_reads}
        ),
        assessment=assessment,
    )


@pytest.mark.parametrize("mode", ("surprise", "explore"))
def test_resolver_uses_exact_concrete_owner_and_modes_have_separate_limits(mode):
    resolved = protocol(mode)
    assert resolved.mode == mode
    assert resolved.max_stages == 3
    assert resolved.max_depth == (2 if mode == "surprise" else 1)
    if mode == "surprise":
        assert resolved.additional_identity_references(None, None) == ()


@pytest.mark.parametrize(
    "change",
    (
        {"task_kind": "write_summary"},
        {"mode": "ask"},
        {"config_sha256": "0" * 64},
        {"run_id": uuid.UUID(int=99)},
        {"protocol": "polymath-surprise-ladder-v1"},
    ),
)
def test_mismatched_registry_handshake_never_selects_an_adapter(change):
    with pytest.raises((ValueError, StageProtocolError)):
        protocol("explore", **change)


def test_ordinary_run_has_no_stage_protocol():
    run = SimpleNamespace(id=uuid.uuid4(), mode="explore", task_kind="research")
    assert resolve_stage_protocol(None, run, owner_loader=lambda *_: None) is None


async def test_all_legacy_surprise_stages_keep_exact_packet_bytes_hashes_and_scope():
    outcome, _, _, sink = await run_fixture()
    assert len(outcome.stages) == 3
    hooks = protocol("surprise")
    for packet, receipt in zip(sink.packets, sink.receipts, strict=True):
        reconstructed = hooks.parse_packet(packet)
        assert reconstructed.model_dump_json() == packet.model_dump_json()
        assert hooks.packet_sha256(packet) == discovery_packet_sha256(packet)
        old_body = {
            "finding": packet.result.finding.model_dump(mode="json"),
            "quotes": [quote.model_dump(mode="json") for quote in packet.result.quotes],
            "fields": list(packet.context.selection.fields),
        }
        assert hooks.scientific_sha256(packet.context, packet.result) == _hash(_canonical(old_body))
        assert hooks.revision_reference(packet) is packet.revision_of
        hooks.validate_saved_stage(receipt, packet.context, packet.result)
        action = {
            "kind": "record_bridge",
            "finding": packet.result.finding.model_dump(mode="json"),
            "assessment": packet.assessment.model_dump(mode="json"),
        }
        parsed = hooks.parse_action(json.dumps(action))
        assert parsed == DISCOVERY_ACTION_ADAPTER.validate_json(json.dumps(action))
        hooks.validate_commit_action(packet, parsed)


def test_explore_positive_packet_is_a_base_bridge_with_no_surprise_field_history():
    hooks, packet = protocol(), explore_packet()
    loaded = hooks.validate_result_branch_fields(packet)
    assert loaded.result.mode == "explore" and loaded.result.field_selections == ()
    assert not hasattr(loaded, "revision_of") and hooks.revision_reference(loaded) is None
    assert hooks.commit_depth([]) == 1
    with pytest.raises(StageProtocolError):
        hooks.commit_depth([("forged", "parent")])
    with pytest.raises(StageProtocolError, match="ranking_required"):
        hooks.require_surprise_terminal_protocol()


@pytest.mark.parametrize("field", ("field_left", "field_right"))
def test_stored_explore_finding_cannot_escape_its_model_selected_branch(field):
    hooks, packet = protocol(), explore_packet()
    wrong = packet.result.model_copy(
        update={"finding": packet.result.finding.model_copy(update={field: "An unselected field"})}
    )
    with pytest.raises(StageProtocolError):
        hooks.validate_result_scope(packet.context, wrong)


def test_explore_memory_leads_do_not_become_selected_stage_parents(monkeypatch):
    from polymath.evidence import EvidenceStore

    reference = MemoryReadReference(
        result_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        content_sha256="a" * 64,
    )
    packet = explore_packet(memory_reads=(reference,))
    seen = []
    monkeypatch.setattr(EvidenceStore, "_validate_memory_reads", lambda *args: seen.append(args))
    keys = protocol().memory_keys("actual-session", "actual-run", packet.result, {})
    assert len(seen) == 1
    assert keys == ((reference.result_id, reference.run_id, reference.content_sha256),)
    assert packet.context.parent_refs == ()
    assert packet.result.memory_reads == (reference,)


def test_surprise_memory_keys_stay_scoped_and_never_call_ordinary_memory_validator(monkeypatch):
    from polymath.evidence import EvidenceStore

    monkeypatch.setattr(
        EvidenceStore, "_validate_memory_reads", lambda *_: pytest.fail("old protocol changed")
    )
    reference = MemoryReadReference(
        result_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        content_sha256="b" * 64,
    )
    assert protocol("surprise").memory_keys(
        None, None, None, {"read": SimpleNamespace(reference=reference)}
    ) == ((reference.result_id, reference.run_id, reference.content_sha256),)


def test_explore_literal_dedup_ignores_unused_branch_fields_and_memory_leads():
    hooks, packet = protocol(), explore_packet()
    digest = hooks.scientific_sha256(packet.context, packet.result)
    expanded = packet.context.model_copy(
        update={
            "selection": packet.context.selection.model_copy(
                update={"fields": (*packet.context.selection.fields, "An unused extra field")}
            )
        }
    )
    reference = MemoryReadReference(
        result_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        content_sha256="c" * 64,
    )
    remembered = packet.result.model_copy(update={"memory_reads": (reference,)})
    assert hooks.scientific_sha256(expanded, remembered) == digest
    mapped = packet.result.finding.model_copy(deep=True)
    mapped.mappings[0] = mapped.mappings[0].model_copy(
        update={"assumption": "A different actual mapping"}
    )
    assert (
        hooks.scientific_sha256(
            packet.context, packet.result.model_copy(update={"finding": mapped})
        )
        != digest
    )
    original = packet.result.quotes[0]
    shortened = original.model_copy(
        update={"quote": original.quote[1:], "start": original.start + 1}
    )
    requoted = packet.result.model_copy(update={"quotes": (shortened, *packet.result.quotes[1:])})
    assert hooks.scientific_sha256(packet.context, requoted) != digest
