"""Exact immutable metadata projections, without asserting DB ownership or truth."""

import hashlib
import json
import uuid
from copy import deepcopy

import pytest
from pydantic import ValidationError

from polymath.discovery_contracts import StageAssessment, StageCommitReceipt
from polymath.research.models import BridgeFinding, MemoryReadReference, SourceReference
from polymath.scientific_context_contracts import (
    AppliedAssessmentContext,
    ArtifactScientificContext,
    ArtifactScientificContextBinding,
    ArtifactScientificContextIntent,
    PriorArtifactContextReference,
    ScientificAncestor,
    ScientificAssessmentCitation,
    ScientificAssessmentQuote,
    ScientificAssessmentStatement,
    ScientificParentEdge,
    SourceRevisionContext,
    StageOriginContext,
    artifact_context_metadata,
    bind_scientific_context,
    canonical_scientific_context_json,
    scientific_context_sha256,
    validate_artifact_context_metadata,
    validate_context_for_finding,
)


def uid(value):
    return str(uuid.UUID(int=value))


def reference(value, **changes):
    return MemoryReadReference(
        **{
            "result_id": uid(value),
            "run_id": uid(1000 + value),
            "content_sha256": f"{value:064x}",
            **changes,
        }
    )


def bridge(title="Original synthetic bridge", **changes):
    return BridgeFinding.model_validate(
        {
            "outcome": "bridge",
            "title": title,
            "field_left": "Information theory",
            "field_right": "Biology",
            "explanation": "This is a model hypothesis for a synthetic fixture.",
            "mappings": [{"left": "encoding", "right": "pathway", "assumption": "bounded"}],
            "open_question": "Does the hypothesis survive a controlled comparison?",
            "strongest_objection": "These systems may have unrelated mechanisms.",
            "objection_verdict": "The relationship remains hypothetical.",
            "novelty_limitations": "A bounded search does not establish novelty.",
            "evidence": [
                {
                    "source_id": "fixture:left",
                    "side": "left",
                    "quote": "Synthetic exact quotation left.",
                    "supports": "Model interpretation of the left quotation.",
                },
                {
                    "source_id": "fixture:right",
                    "side": "right",
                    "quote": "Synthetic exact quotation right.",
                    "supports": "Model interpretation of the right quotation.",
                },
            ],
            **changes,
        }
    )


def digest(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def stage_origin(*, relationship="exact_stage_result", parents=()):
    receipt = StageCommitReceipt(
        stage_id=uid(200),
        run_id=reference(1).run_id,
        ordinal=0,
        result_id=reference(1).result_id,
        result_sha256=reference(1).content_sha256,
        depth=1,
        assessment=StageAssessment(
            novelty="uncertain",
            novelty_rationale="Only bounded synthetic search was performed.",
            confidence="tentative",
            confidence_score=25,
            confidence_scope="uncalibrated_model_judgement",
            confidence_rationale="Synthetic passages establish no scientific truth.",
            limitations=("This score is not calibrated.",),
        ),
        parent_refs=parents,
        action_ordinal=7,
        model_request_id="model-0007",
    )
    return StageOriginContext(
        receipt=receipt,
        receipt_sha256=digest(receipt.model_dump(mode="json")),
        relationship=relationship,
    )


def assessment(*, ordinal=1, prior=None, candidate=None, disposition="revised", **changes):
    prior = prior or reference(1)
    if disposition == "revised":
        candidate = candidate or reference(4)
    quote = ScientificAssessmentQuote(
        retrieval_id=uid(400),
        version_id=uid(401),
        source=SourceReference(
            source_id="fixture:new", title="Synthetic new source", url="https://example.org/paper"
        ),
        source_sha256="c" * 64,
        content_kind="abstract",
        start=2,
        end=37,
        quote="Synthetic new quotation supports λ.",
        supports="The model interprets this passage.",
    )
    citation = ScientificAssessmentCitation(
        retrieval_id=quote.retrieval_id, quote=quote.quote, supports=quote.supports
    )
    return AppliedAssessmentContext(
        **{
            "assessment_id": uid(500 + ordinal),
            "run_id": candidate.run_id if candidate else uid(2000 + ordinal),
            "payload_sha256": "a" * 64,
            "head_revision": ordinal,
            "prior_reference": prior,
            "candidate_reference": candidate,
            "disposition": disposition,
            "rationale": (
                ScientificAssessmentStatement(
                    text="A bounded public model rationale.", citations=(citation,)
                ),
            ),
            "quotes": (quote,),
            "limitations": ("The result remains a model assessment.",),
            **changes,
        }
    )


def context_data(*, revised=False, parents=False):
    parent_refs = (reference(2), reference(3)) if parents else ()
    original_origin = stage_origin(parents=parent_refs)
    original = ScientificAncestor(
        reference=reference(1), finding=bridge(), stage_origin=original_origin
    )
    nodes = [original]
    edges = []
    for index, parent in enumerate(parent_refs):
        nodes.append(ScientificAncestor(reference=parent, finding=bridge(f"Parent {index}")))
        edges.append(
            ScientificParentEdge(child=reference(1), parent=parent, ordinal=index, kind="premise")
        )
    current = reference(4) if revised else reference(1)
    applied = (assessment(),) if revised else ()
    if revised:
        nodes.insert(0, ScientificAncestor(reference=current, finding=bridge("Revised fixture")))
        edges.insert(
            0,
            ScientificParentEdge(
                child=current,
                parent=reference(1),
                ordinal=0,
                kind="reassessment",
                assessment_id=applied[0].assessment_id,
                assessment_sha256=applied[0].payload_sha256,
            ),
        )
    return {
        "source_reference": current,
        "source_revision": SourceRevisionContext(
            root_reference=reference(1),
            current_reference=current,
            revision=len(applied),
            applied_assessments=applied,
        ),
        "stage_origin": original_origin.model_copy(update={"relationship": "reassessed_descendant"})
        if revised
        else original_origin,
        "ancestry_nodes": tuple(nodes),
        "ancestry_edges": tuple(edges),
    }


def as_json_data(data):
    return ArtifactScientificContext(**data).model_dump(mode="json")


def test_versioned_frozen_intent_and_binding_are_only_pure_integrity_claims():
    context = ArtifactScientificContext(**context_data())
    bound = bind_scientific_context(context)
    assert ArtifactScientificContextIntent().protocol == context.protocol
    assert bound.context_sha256 == digest(context.model_dump(mode="json"))
    assert "authority" not in bound.model_dump_json()
    assert "scientific_truth_verified" not in bound.model_dump_json()
    with pytest.raises(ValidationError):
        bound.context_sha256 = "f" * 64
    with pytest.raises(ValidationError):
        ArtifactScientificContextIntent(protocol="fake-v2")


@pytest.mark.parametrize("revised,parents", [(False, False), (False, True), (True, True)])
def test_valid_original_and_revised_contexts_retain_exact_original_confidence(revised, parents):
    context = ArtifactScientificContext(**context_data(revised=revised, parents=parents))
    assert context.stage_origin.receipt.reference == reference(1)
    assert context.stage_origin.receipt.assessment.confidence_score == 25
    assert context.source_reference == reference(4 if revised else 1)
    assert context.stage_origin.relationship == (
        "reassessed_descendant" if revised else "exact_stage_result"
    )
    if revised:
        assert context.ancestry_nodes[0].stage_origin is None
        assert context.source_revision.applied_assessments[0].candidate_reference == reference(4)


def test_ordinary_finding_context_requires_no_stage_or_reassessment():
    context = ArtifactScientificContext(
        source_reference=reference(1),
        source_revision=SourceRevisionContext(
            root_reference=reference(1), current_reference=reference(1), revision=0
        ),
        ancestry_nodes=(ScientificAncestor(reference=reference(1), finding=bridge()),),
    )
    assert context.stage_origin is None and context.ancestry_edges == ()
    assert bind_scientific_context(context).context_sha256


def test_unchanged_assessment_does_not_rewrite_stage_or_prior_formalisation_context():
    data = context_data(revised=True)
    unchanged = assessment(ordinal=2, prior=reference(4), disposition="unchanged")
    data["source_revision"] = SourceRevisionContext(
        root_reference=reference(1),
        current_reference=reference(4),
        revision=2,
        applied_assessments=(assessment(), unchanged),
    )
    data["prior_formalisation"] = PriorArtifactContextReference(
        artifact_id=uid(600),
        source_reference=reference(4),
        payload_sha256="d" * 64,
        basis_sha256="e" * 64,
        context_sha256="f" * 64,
    )
    context = ArtifactScientificContext(**data)
    assert context.stage_origin.receipt.assessment.confidence_score == 25
    assert context.prior_formalisation.context_sha256 == "f" * 64
    assert scientific_context_sha256(context) != context.prior_formalisation.context_sha256


def test_canonical_digest_is_json_order_independent_but_sensitive_to_exact_metadata():
    data = as_json_data(context_data())
    one = ArtifactScientificContext.model_validate_json(json.dumps(data))
    two = ArtifactScientificContext.model_validate_json(
        json.dumps(dict(reversed(list(data.items()))))
    )
    assert canonical_scientific_context_json(one) == canonical_scientific_context_json(two)
    assert scientific_context_sha256(one) == scientific_context_sha256(two)
    changed = deepcopy(data)
    changed["ancestry_nodes"][0]["finding"]["title"] = "A different original finding title"
    three = ArtifactScientificContext.model_validate_json(json.dumps(changed))
    assert scientific_context_sha256(one) != scientific_context_sha256(three)


def test_binding_and_basis_checks_revalidate_even_model_copy_bypasses():
    bound = bind_scientific_context(ArtifactScientificContext(**context_data()))
    with pytest.raises(ValueError, match="scientific_context_hash_mismatch"):
        ArtifactScientificContextBinding(context=bound.context, context_sha256="0" * 64)
    forged = bound.model_copy(update={"context_sha256": "0" * 64})
    with pytest.raises(ValueError):
        artifact_context_metadata(forged)
    validate_context_for_finding(bound, reference(1), bridge())
    with pytest.raises(ValueError, match="scientific_context_basis_mismatch"):
        validate_context_for_finding(bound, reference(1), bridge("Different finding"))


@pytest.mark.parametrize(
    "problem", ["wrong_relation", "wrong_hash", "missing_origin", "wrong_parents"]
)
def test_original_stage_metadata_cannot_be_transferred_or_silently_dropped(problem):
    data = as_json_data(context_data(revised=True, parents=True))
    if problem == "wrong_relation":
        data["stage_origin"]["relationship"] = "exact_stage_result"
    elif problem == "wrong_hash":
        data["stage_origin"]["receipt"]["assessment"]["confidence_score"] = 95
    elif problem == "missing_origin":
        data["stage_origin"] = None
    else:
        data["ancestry_edges"][-1]["kind"] = "revision"
    with pytest.raises(ValidationError):
        ArtifactScientificContext.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "change", [{"applied": False}, {"applied": 1}, {"disposition": "inconclusive"}]
)
def test_only_explicit_applied_non_inconclusive_projection_shapes_are_allowed(change):
    data = assessment().model_dump(mode="json") | change
    with pytest.raises(ValidationError):
        AppliedAssessmentContext.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "problem", ["candidate_missing", "candidate_old", "candidate_run", "quote", "span"]
)
def test_assessment_candidate_and_exact_citation_projection_are_checked(problem):
    data = assessment().model_dump(mode="json")
    if problem == "candidate_missing":
        data["candidate_reference"] = None
    elif problem == "candidate_old":
        data["candidate_reference"] = reference(1).model_dump(mode="json")
    elif problem == "candidate_run":
        data["candidate_reference"]["run_id"] = uid(999)
    elif problem == "quote":
        data["quotes"][0]["supports"] = "A different unsupported interpretation."
    else:
        data["quotes"][0]["end"] += 1
    with pytest.raises(ValidationError):
        AppliedAssessmentContext.model_validate_json(json.dumps(data))


def test_quote_offsets_cannot_exceed_any_saved_document_even_with_matching_span():
    data = assessment().model_dump(mode="json")
    data["quotes"][0]["start"] = 20_000
    data["quotes"][0]["end"] = 20_000 + len(data["quotes"][0]["quote"])
    with pytest.raises(ValidationError):
        AppliedAssessmentContext.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "problem", ["missing", "gap", "stale", "unsupported", "unchanged_unsupported"]
)
def test_applied_history_must_be_complete_contiguous_and_end_at_supported_source(problem):
    original, revised = reference(1), reference(4)
    applied = (assessment(),)
    revision, current = 1, revised
    if problem == "missing":
        revision = 2
    elif problem == "gap":
        applied = (assessment(ordinal=2),)
    elif problem == "stale":
        current = original
    else:
        applied = (assessment(disposition="unsupported"),)
        current = original
        if problem == "unchanged_unsupported":
            applied += (assessment(ordinal=2, disposition="unchanged"),)
            revision = 2
    with pytest.raises(ValidationError):
        SourceRevisionContext(
            root_reference=original,
            current_reference=current,
            revision=revision,
            applied_assessments=applied,
        )


def test_revised_assessment_can_restore_support_after_an_unsupported_head():
    value = SourceRevisionContext(
        root_reference=reference(1),
        current_reference=reference(4),
        revision=2,
        applied_assessments=(assessment(disposition="unsupported"), assessment(ordinal=2)),
    )
    assert value.current_reference == reference(4)


@pytest.mark.parametrize(
    "problem",
    ["duplicate_node", "disconnected", "unknown_parent", "edge_hash", "edge_gap", "node_order"],
)
def test_ancestry_preserves_exact_references_and_deterministic_complete_order(problem):
    data = as_json_data(context_data(parents=True))
    if problem == "duplicate_node":
        data["ancestry_nodes"].append(deepcopy(data["ancestry_nodes"][0]))
    elif problem == "disconnected":
        data["ancestry_nodes"].append(
            ScientificAncestor(reference=reference(9), finding=bridge()).model_dump(mode="json")
        )
    elif problem == "unknown_parent":
        data["ancestry_edges"][0]["parent"] = reference(9).model_dump(mode="json")
    elif problem == "edge_hash":
        data["ancestry_edges"][0]["parent"]["content_sha256"] = "b" * 64
    elif problem == "edge_gap":
        data["ancestry_edges"][0]["ordinal"] = 4
    else:
        data["ancestry_nodes"][1:] = reversed(data["ancestry_nodes"][1:])
    with pytest.raises(ValidationError):
        ArtifactScientificContext.model_validate_json(json.dumps(data))


def test_cycles_cannot_hide_behind_valid_references_or_contiguous_edge_ordinals():
    data = {
        "source_reference": reference(1),
        "source_revision": SourceRevisionContext(
            root_reference=reference(1), current_reference=reference(1), revision=0
        ),
        "ancestry_nodes": tuple(
            ScientificAncestor(reference=reference(index), finding=bridge()) for index in (1, 2)
        ),
        "ancestry_edges": (
            ScientificParentEdge(
                child=reference(1), parent=reference(2), ordinal=0, kind="premise"
            ),
            ScientificParentEdge(
                child=reference(2), parent=reference(1), ordinal=0, kind="premise"
            ),
        ),
    }
    with pytest.raises(ValidationError, match="scientific_ancestry_cycle"):
        ArtifactScientificContext(**data)


def test_revised_source_requires_actual_matching_assessment_edge_in_supplied_graph():
    data = as_json_data(context_data(revised=True))
    data["ancestry_edges"][0]["assessment_sha256"] = "b" * 64
    with pytest.raises(ValidationError, match="scientific_context_assessment_edge_missing"):
        ArtifactScientificContext.model_validate_json(json.dumps(data))


def test_complete_context_bytes_not_unicode_character_count_are_bounded():
    nodes = tuple(
        ScientificAncestor(reference=reference(index), finding=bridge(explanation="😀" * 3000))
        for index in range(1, 25)
    )
    edges = tuple(
        ScientificParentEdge(
            child=reference(index), parent=reference(index + 1), ordinal=0, kind="premise"
        )
        for index in range(1, 24)
    )
    with pytest.raises(ValidationError, match="artifact_context_too_large"):
        ArtifactScientificContext(
            source_reference=reference(1),
            source_revision=SourceRevisionContext(
                root_reference=reference(1), current_reference=reference(1), revision=0
            ),
            ancestry_nodes=nodes,
            ancestry_edges=edges,
        )


@pytest.mark.parametrize(
    "field,value", [("ancestry_nodes", []), ("source_revision", {"revision": True})]
)
def test_structural_bounds_and_integer_strictness_are_not_relaxed(field, value):
    data = as_json_data(context_data())
    if field == "source_revision":
        data[field].update(value)
    else:
        data[field] = value
    with pytest.raises(ValidationError):
        ArtifactScientificContext.model_validate_json(json.dumps(data))


def test_optional_metadata_absence_preserves_old_bytes_and_present_metadata_is_validated():
    old = {
        "reference": reference(1).model_dump(mode="json"),
        "finding": bridge().model_dump(mode="json"),
    }
    old_json = json.dumps(old, sort_keys=True, separators=(",", ":"))
    assert artifact_context_metadata(None) == {}
    assert (
        json.dumps(old | artifact_context_metadata(None), sort_keys=True, separators=(",", ":"))
        == old_json
    )
    assert validate_artifact_context_metadata(old, expected=False) is None
    bound = bind_scientific_context(ArtifactScientificContext(**context_data()))
    new = old | artifact_context_metadata(bound)
    assert validate_artifact_context_metadata(new, expected=True) is None
    assert validate_artifact_context_metadata(new) is None
    for data, expected in ((old, True), (new, False), (old | {"scientific_context": None}, None)):
        with pytest.raises(ValueError):
            validate_artifact_context_metadata(data, expected=expected)


def test_legacy_metadata_cannot_point_context_at_another_finding():
    bound = bind_scientific_context(ArtifactScientificContext(**context_data()))
    data = {
        "reference": reference(2).model_dump(mode="json"),
        "finding": bridge().model_dump(mode="json"),
        **artifact_context_metadata(bound),
    }
    with pytest.raises(ValueError, match="scientific_context_binding_invalid"):
        validate_artifact_context_metadata(data, expected=True)
