"""Independent pure-envelope boundaries; hashes are not authority or truth."""

import hashlib
import json

import pytest
from test_ask_overview_contracts import BoundAnswerDraft, draft_data

from polymath.discovery_contracts import StageAssessment, StageCommitReceipt
from polymath.research.models import MemoryReadReference
from polymath.scientific_context_contracts import (
    AppliedAssessmentContext,
    ArtifactScientificContext,
    ScientificAncestor,
    ScientificAssessmentCitation,
    ScientificAssessmentQuote,
    ScientificAssessmentStatement,
    ScientificParentEdge,
    SourceRevisionContext,
    StageOriginContext,
    artifact_context_metadata,
    bind_scientific_context,
    validate_artifact_context_metadata,
)


def identifier(number):
    return f"00000000-0000-0000-0000-{number:012d}"


def reference(number=1, digest="a"):
    return MemoryReadReference(
        result_id=identifier(number),
        run_id=identifier(100 + number),
        content_sha256=digest * 64,
    )


def finding():
    return BoundAnswerDraft(**draft_data()).to_answer()


def context_data(**changes):
    source = reference()
    return {
        "source_reference": source,
        "source_revision": SourceRevisionContext(
            root_reference=source, current_reference=source, revision=0
        ),
        "ancestry_nodes": (ScientificAncestor(reference=source, finding=finding()),),
        **changes,
    }


def statement_quote():
    citation = ScientificAssessmentCitation(
        retrieval_id=identifier(501),
        quote="An exact cited synthetic observation.",
        supports="The model uses this observation as rationale.",
    )
    quote = ScientificAssessmentQuote(
        **citation.model_dump(),
        version_id=identifier(502),
        source={
            "source_id": "fixture:source_a",
            "title": "A synthetic source",
            "url": "https://example.org/synthetic-source",
        },
        source_sha256="f" * 64,
        content_kind="abstract",
        start=0,
        end=len(citation.quote),
    )
    statement = ScientificAssessmentStatement(
        text="A model judgement based on exact cited text.", citations=(citation,)
    )
    return statement, quote


def applied_data(**changes):
    statement, quote = statement_quote()
    return {
        "assessment_id": identifier(700),
        "run_id": identifier(701),
        "payload_sha256": "b" * 64,
        "head_revision": 1,
        "prior_reference": reference(),
        "disposition": "unchanged",
        "rationale": (statement,),
        "quotes": (quote,),
        "limitations": ("This remains an unverified model judgement.",),
        **changes,
    }


def stage_receipt():
    source = reference()
    return StageCommitReceipt(
        stage_id=identifier(800),
        run_id=source.run_id,
        ordinal=0,
        result_id=source.result_id,
        result_sha256=source.content_sha256,
        depth=1,
        assessment=StageAssessment(
            novelty="uncertain",
            novelty_rationale="The bounded search does not establish novelty.",
            confidence="tentative",
            confidence_score=17,
            confidence_scope="uncalibrated_model_judgement",
            confidence_rationale="This is an uncalibrated synthetic judgement.",
            limitations=("Not a scientific verifier or probability of truth.",),
        ),
        parent_refs=(),
        action_ordinal=1,
        model_request_id="model-0001",
    )


def stage_origin(receipt=None):
    receipt = receipt or stage_receipt()
    canonical = json.dumps(
        receipt.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return StageOriginContext(
        receipt=receipt,
        receipt_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        relationship="exact_stage_result",
    )


@pytest.mark.parametrize("field", ["approved", "truth_verified", "is_current", "paid_authority"])
def test_context_cannot_claim_spending_or_scientific_verification_authority(field):
    with pytest.raises(ValueError):
        ArtifactScientificContext(**context_data(**{field: True}))


def test_hash_binding_does_not_contain_an_authority_or_truth_flag():
    context = ArtifactScientificContext(**context_data())
    binding = bind_scientific_context(context)
    assert set(binding.model_dump()) == {"context", "context_sha256"}
    assert binding.context == context
    assert len(binding.context_sha256) == 64
    assert artifact_context_metadata(None) == {}


def test_context_metadata_revalidates_source_hash_and_exact_finding_body():
    binding = bind_scientific_context(ArtifactScientificContext(**context_data()))
    metadata = artifact_context_metadata(binding)
    basis = {
        "reference": reference().model_dump(mode="json"),
        "finding": finding().model_dump(mode="json"),
    }
    validate_artifact_context_metadata(basis | metadata, expected=True)
    for forged in (
        {"reference": reference(digest="b").model_dump(mode="json")},
        {
            "finding": finding()
            .model_copy(update={"summary": "Different scientific text."})
            .model_dump(mode="json")
        },
    ):
        with pytest.raises(ValueError):
            validate_artifact_context_metadata(basis | metadata | forged, expected=True)


def test_explicit_null_metadata_is_not_a_legacy_absent_context():
    assert validate_artifact_context_metadata({}, expected=False) is None
    with pytest.raises(ValueError):
        validate_artifact_context_metadata({"scientific_context": None})


def test_graph_rejects_disconnected_nodes_even_if_their_references_look_valid():
    source = ScientificAncestor(reference=reference(), finding=finding())
    extra = ScientificAncestor(reference=reference(2, "b"), finding=finding())
    with pytest.raises(ValueError):
        ArtifactScientificContext(**context_data(ancestry_nodes=(source, extra)))


def test_graph_checks_exact_run_and_hash_for_each_edge_not_only_result_id():
    source = ScientificAncestor(reference=reference(), finding=finding())
    parent = ScientificAncestor(reference=reference(2, "b"), finding=finding())
    edge = ScientificParentEdge(
        child=source.reference,
        parent=reference(2, "c"),
        ordinal=0,
        kind="premise",
    )
    with pytest.raises(ValueError):
        ArtifactScientificContext(
            **context_data(ancestry_nodes=(source, parent), ancestry_edges=(edge,))
        )


def test_graph_rejects_reachable_cycles_instead_of_merely_deduplicating_nodes():
    first, second = reference(), reference(2, "b")
    nodes = (
        ScientificAncestor(reference=first, finding=finding()),
        ScientificAncestor(reference=second, finding=finding()),
    )
    edges = (
        ScientificParentEdge(child=first, parent=second, ordinal=0, kind="premise"),
        ScientificParentEdge(child=second, parent=first, ordinal=0, kind="premise"),
    )
    with pytest.raises(ValueError):
        ArtifactScientificContext(**context_data(ancestry_nodes=nodes, ancestry_edges=edges))


def test_graph_has_one_deterministic_depth_first_node_and_edge_order():
    first, second, third = reference(), reference(2, "b"), reference(3, "c")
    nodes = tuple(
        ScientificAncestor(reference=ref, finding=finding()) for ref in (first, second, third)
    )
    edges = (
        ScientificParentEdge(child=first, parent=second, ordinal=0, kind="premise"),
        ScientificParentEdge(child=first, parent=third, ordinal=1, kind="premise"),
    )
    ArtifactScientificContext(**context_data(ancestry_nodes=nodes, ancestry_edges=edges))
    with pytest.raises(ValueError):
        ArtifactScientificContext(
            **context_data(ancestry_nodes=(nodes[0], nodes[2], nodes[1]), ancestry_edges=edges)
        )
    with pytest.raises(ValueError):
        ArtifactScientificContext(**context_data(ancestry_nodes=nodes, ancestry_edges=edges[::-1]))


def test_applied_projection_rejects_a_rationale_that_no_genuine_applied_record_can_have():
    with pytest.raises(ValueError):
        AppliedAssessmentContext(
            **applied_data(
                rationale=(ScientificAssessmentStatement(text="An uncited model judgement."),),
                quotes=(),
            )
        )


@pytest.mark.parametrize("applied", [False, 0, 1, "true"])
def test_applied_history_requires_actual_boolean_true_not_truthiness(applied):
    with pytest.raises(ValueError):
        AppliedAssessmentContext(**applied_data(applied=applied))


def test_applied_projection_citations_cannot_be_replaced_by_other_verified_quotes():
    statement, quote = statement_quote()
    changed = quote.model_copy(update={"supports": "A conflicting interpretation."})
    with pytest.raises(ValueError):
        AppliedAssessmentContext(**applied_data(rationale=(statement,), quotes=(changed,)))


@pytest.mark.parametrize("end", [20_001, 10**30])
def test_projected_quote_cannot_extend_beyond_any_durable_source_document(end):
    _, quote = statement_quote()
    with pytest.raises(ValueError):
        ScientificAssessmentQuote.model_validate_json(
            quote.model_copy(update={"start": end - len(quote.quote), "end": end}).model_dump_json()
        )


def test_projected_quote_accepts_exact_global_document_boundary():
    _, quote = statement_quote()
    checked = ScientificAssessmentQuote.model_validate_json(
        quote.model_copy(
            update={"start": 20_000 - len(quote.quote), "end": 20_000}
        ).model_dump_json()
    )
    assert checked.end == 20_000


def test_stage_receipt_confidence_cannot_be_copied_onto_an_ask_answer_node():
    with pytest.raises(ValueError):
        ScientificAncestor(reference=reference(), finding=finding(), stage_origin=stage_origin())


def test_stage_confidence_edit_requires_exact_new_receipt_bytes_not_just_same_result_hash():
    origin = stage_origin()
    changed_assessment = origin.receipt.assessment.model_copy(update={"confidence_score": 99})
    changed_receipt = origin.receipt.model_copy(update={"assessment": changed_assessment})
    with pytest.raises(ValueError):
        StageOriginContext.model_validate_json(
            origin.model_copy(update={"receipt": changed_receipt}).model_dump_json()
        )


def test_applied_history_cannot_restore_unsupported_with_an_unchanged_assessment():
    first = AppliedAssessmentContext(**applied_data(disposition="unsupported"))
    second = AppliedAssessmentContext(
        **applied_data(assessment_id=identifier(702), run_id=identifier(703), head_revision=2)
    )
    with pytest.raises(ValueError):
        SourceRevisionContext(
            root_reference=reference(),
            current_reference=reference(),
            revision=2,
            applied_assessments=(first, second),
        )
