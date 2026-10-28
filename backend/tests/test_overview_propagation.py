"""Classified overviews survive private reads and map only to public local IDs."""

import hashlib
import json
import uuid
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_sharing_projection import answer_result, bridge_result

import polymath.knowledge as knowledge
from polymath.evidence import _canonical, _hash
from polymath.memory import MemoryError, _finding, _overview_binding, _reference
from polymath.research.models import (
    AskOverviewBinding,
    MemoryFinding,
    ResearchResult,
)
from polymath.sharing_contracts import (
    PublicFindingProjection,
    PublicOverviewBinding,
    classified_answer_navigation_summary,
)
from polymath.sharing_projection import (
    SharingProjectionError,
    build_public_projection,
    canonical_projection_json,
    projection_sha256,
)


def bound_answer():
    result = answer_result()
    ids = (result.finding.claims[2].id, result.finding.claims[0].id)
    by_id = {claim.id: claim for claim in result.finding.claims}
    finding = result.finding.model_copy(
        update={"summary": "\n\n".join(by_id[identifier].text for identifier in ids)}
    )
    return ResearchResult.model_validate_json(
        result.model_copy(
            update={"finding": finding, "overview_binding": AskOverviewBinding(claim_ids=ids)}
        ).model_dump_json()
    )


def stored_row(result):
    payload = {"result": result.model_dump(mode="json"), "source_artifacts": []}
    return SimpleNamespace(
        id=uuid.UUID("12345678-1234-1234-1234-123456789abc"),
        run_id=uuid.UUID("12345678-1234-1234-1234-123456789def"),
        status=result.status,
        payload=payload,
        content_sha256=_hash(_canonical(payload)),
    )


def memory_finding(result=None):
    result = result or bound_answer()
    row = stored_row(result)
    return MemoryFinding(
        reference=_reference(row, result.finding),
        finding=result.finding,
        sources=(),
        **({"overview_binding": result.overview_binding} if result.overview_binding else {}),
    )


def test_public_binding_is_outer_and_maps_exact_private_order_to_local_ids():
    result = bound_answer()
    projection = build_public_projection(result)
    data = projection.model_dump(mode="json")
    assert data["overview_binding"] == {
        "protocol": "polymath-ask-claim-overview-v1",
        "claim_ids": ["claim-3", "claim-1"],
    }
    assert "overview_binding" not in data["finding"]
    assert projection.finding.summary == result.finding.summary
    assert len(projection.finding.claims) == 3 and len(projection.citations) == 4
    assert [claim.label for claim in projection.finding.claims] == [
        "known",
        "postulated",
        "speculative",
    ]
    public_json = canonical_projection_json(projection)
    for identifier in result.overview_binding.claim_ids:
        assert identifier not in public_json
    for forbidden in ("source_result_id", "run_id", "assessment_id", "scientific_context"):
        assert forbidden not in public_json
    assert PublicFindingProjection.model_validate_json(public_json) == projection


def test_public_overview_does_not_change_the_answer_contract_or_unselected_claims():
    result = bound_answer()
    data = build_public_projection(result).model_dump(mode="json")
    assert set(data["finding"]) == {
        "kind",
        "question",
        "summary",
        "claims",
        "limitations",
        "unanswered_questions",
    }
    assert data["finding"]["claims"][1]["text"] == result.finding.claims[1].text
    assert data["finding"]["claims"][1]["derivation"] == result.finding.claims[1].derivation


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("answer", "f1f5e55afa81946727cb759c22ede58956a9c6b0e49fd332c527223ebbededf4"),
        ("bridge", "5da79c520b06df1d5bd26e30ac42b7f853ddebaebe84f6e0ea275565f304183b"),
    ],
)
def test_legacy_public_bytes_match_actual_d2_archive(kind, expected):
    # Independently captured from read-only git archive6e3d75b in a fresh process,
    # using its unchanged test_sharing_projection fixtures and old implementation.
    result = answer_result() if kind == "answer" else bridge_result()
    projection = build_public_projection(result)
    assert "overview_binding" not in projection.model_dump(mode="json")
    assert projection_sha256(projection) == expected


@pytest.mark.parametrize(
    "binding",
    [
        None,
        {"claim_ids": ["PRIVATE_KNOWN_ID"]},
        {"claim_ids": ["claim-1", "claim-1"]},
        {"claim_ids": ["claim-8"]},
        {"claim_ids": ["claim-1"], "run_id": "private"},
        {"protocol": "unrecognized", "claim_ids": ["claim-3", "claim-1"]},
    ],
)
def test_public_parser_rejects_null_private_unknown_duplicate_or_injected_bindings(binding):
    data = build_public_projection(bound_answer()).model_dump(mode="json")
    data["overview_binding"] = binding
    with pytest.raises(ValidationError):
        PublicFindingProjection.model_validate_json(json.dumps(data))


def test_public_parser_binds_order_and_summary_without_paraphrase():
    data = build_public_projection(bound_answer()).model_dump(mode="json")
    for changed in (
        {"overview_binding": {"claim_ids": ["claim-1", "claim-3"]}},
        {"finding": data["finding"] | {"summary": "An extra unclassified claim."}},
    ):
        with pytest.raises(ValidationError):
            PublicFindingProjection.model_validate_json(json.dumps(data | changed))


def test_public_bridge_cannot_carry_answer_overview_metadata():
    data = build_public_projection(bridge_result()).model_dump(mode="json")
    data["overview_binding"] = {"claim_ids": ["claim-1"]}
    with pytest.raises(ValidationError):
        PublicFindingProjection.model_validate_json(json.dumps(data))


def test_projection_builder_revalidates_model_copy_before_publicizing_bad_summary():
    result = bound_answer()
    forged = result.model_copy(
        update={"finding": result.finding.model_copy(update={"summary": "Fake"})}
    )
    with pytest.raises(SharingProjectionError, match="public_source_integrity_failed"):
        build_public_projection(forged)
    good = build_public_projection(result)
    forged_public = good.model_copy(
        update={"overview_binding": PublicOverviewBinding(claim_ids=("claim-1",))}
    )
    with pytest.raises(ValidationError):
        canonical_projection_json(forged_public)


def test_memory_full_read_preserves_binding_beside_finding_but_navigation_is_neutral():
    result = bound_answer()
    memory = memory_finding(result)
    data = memory.model_dump(mode="json")
    assert data["overview_binding"] == result.overview_binding.model_dump(mode="json")
    assert data["finding"] == result.finding.model_dump(mode="json")
    assert memory.reference.summary == classified_answer_navigation_summary(
        claim.label for claim in result.finding.claims
    )
    assert all(claim.text not in memory.reference.summary for claim in result.finding.claims)
    assert MemoryFinding.model_validate_json(memory.model_dump_json()) == memory


@pytest.mark.parametrize("problem", ["null", "unknown", "summary", "bridge"])
def test_memory_model_and_stored_loader_reject_mis_scoped_binding(problem):
    memory = memory_finding()
    data = memory.model_dump(mode="json")
    if problem == "null":
        data["overview_binding"] = None
    elif problem == "unknown":
        data["overview_binding"]["claim_ids"] = ["claim_missing"]
    elif problem == "summary":
        data["finding"]["summary"] = "Unclassified rewrite."
    else:
        data["finding"] = bridge_result().finding.model_dump(mode="json")
        data["reference"]["outcome"] = "bridge"
    with pytest.raises(ValidationError):
        MemoryFinding.model_validate_json(json.dumps(data))


def test_legacy_memory_bytes_and_navigation_stay_exactly_as_d2():
    result = answer_result()
    memory = memory_finding(result)
    assert "overview_binding" not in memory.model_dump(mode="json")
    assert memory.reference.summary == result.finding.summary[:1000]
    # Captured independently from the same actual D2 archive as public digests.
    data = memory.model_dump(mode="json")
    data["reference"]["content_sha256"] = "f" * 64
    actual = hashlib.sha256(_canonical(data).encode()).hexdigest()
    assert actual == "f83b1ee6d2b751d4d6ab78e55f3f52cf28fbf9ed4465acb79f38ed0ef8624d6d"


def test_stored_memory_binding_cannot_be_dropped_to_a_legacy_parse_on_corruption():
    row = stored_row(bound_answer())
    row.payload["result"]["overview_binding"] = None
    row.content_sha256 = _hash(_canonical(row.payload))
    with pytest.raises(MemoryError, match="memory_finding_unavailable"):
        _finding(row)
    with pytest.raises(MemoryError, match="memory_finding_unavailable"):
        _overview_binding(row, answer_result().finding)


def test_knowledge_bound_navigation_is_neutral_and_legacy_summary_is_unchanged(monkeypatch):
    result = bound_answer()
    row = stored_row(result)
    monkeypatch.setattr(
        knowledge,
        "result_assessment_state",
        lambda *args: {
            "root_result_id": str(row.id),
            "state": "supported",
            "is_current": True,
            "is_supported": True,
        },
    )
    session = SimpleNamespace(
        scalar=lambda _: None, get=lambda *_: SimpleNamespace(task_kind="research")
    )
    item = knowledge._finding_item(row, session, "synthetic-owner")
    assert item["summary"] == classified_answer_navigation_summary(
        claim.label for claim in result.finding.claims
    )
    assert all(claim.text not in item["summary"] for claim in result.finding.claims)
    legacy = answer_result()
    assert (
        knowledge._finding_item(stored_row(legacy), session, "synthetic-owner")["summary"]
        == legacy.finding.summary[:240]
    )
    invalid = deepcopy(row)
    invalid.payload["result"]["finding"]["summary"] = "An extra unknown statement."
    invalid.content_sha256 = _hash(_canonical(invalid.payload))
    assert knowledge._finding_item(invalid, session, "synthetic-owner") is None


@pytest.mark.parametrize("labels", [(), ("unknown",), (True,), ("known",) * 9])
def test_navigation_summary_accepts_only_bounded_actual_classifications(labels):
    with pytest.raises(ValueError, match="invalid_navigation_claims"):
        classified_answer_navigation_summary(labels)
