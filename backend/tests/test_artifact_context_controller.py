"""Synthetic injected model; no model construction, provider, DB, or live calls."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_artifact_controller import (
    ArtifactFixtureModel,
    FixtureCheckTool,
    SequenceModel,
    artifact_basis,
    conclude,
    document_draft,
    formalisation_draft,
)
from test_artifact_scientific_context import as_json_data, context_data, uid

from polymath.artifact_context_rendering import render_artifact_scientific_context
from polymath.artifact_contracts import (
    ARTIFACT_ACTION_ADAPTER,
    ArtifactBasis,
    ArtifactPassage,
)
from polymath.artifact_controller import (
    KIND_INSTRUCTIONS,
    SCIENTIFIC_CONTEXT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    ArtifactController,
    ArtifactLimits,
    artifact_initial_messages,
    artifact_scientific_context_protocol_payload,
    artifact_static_system_message,
    minimum_artifact_scientific_context_characters,
)
from polymath.artifacts import _link, _text
from polymath.meta_research_contracts import controller_protocol_sha256
from polymath.research.models import SourceBibliography, SourceCoverage
from polymath.scientific_context_contracts import (
    ArtifactScientificContext,
    PriorArtifactContextReference,
    bind_scientific_context,
)

BASELINE = json.loads(
    (Path(__file__).parent / "fixtures/pre_scientific_context_baseline.json").read_text()
)["fixtures"]


def context_basis(*, revised=False, parents=False, context=None, formalisation=None):
    context = context or ArtifactScientificContext.model_validate_json(
        json.dumps(as_json_data(context_data(revised=revised, parents=parents)))
    )
    finding = context.ancestry_nodes[0].finding
    basis = ArtifactBasis(
        reference=context.source_reference,
        finding=finding,
        passages=tuple(
            ArtifactPassage(
                ordinal=ordinal,
                source_id=item.source_id,
                title="Synthetic original source " + item.source_id,
                url="https://example.org/sources/" + item.source_id,
                content_sha256="c" * 64,
                content_kind="abstract",
                quote=item.quote,
                supports=item.supports,
            )
            for ordinal, item in enumerate(finding.evidence)
        ),
        scientific_context=bind_scientific_context(context),
        **(
            {
                "formalisation_id": context.prior_formalisation.artifact_id,
                "formalisation": formalisation,
            }
            if formalisation is not None
            else {}
        ),
    )
    return basis


@pytest.mark.parametrize("kind", ("formalise", "write_summary", "write_paper"))
def test_absent_context_preserves_archived_d2_basis_messages_and_schema(kind):
    basis = artifact_basis()
    saved = BASELINE["artifacts"]["bridge/" + kind]
    assert basis.model_dump(mode="json") == saved["basis"]["value"]
    assert "scientific_context" not in basis.model_dump()
    assert artifact_initial_messages(kind, basis) == saved["initial_request"]["value"]["messages"]
    assert ARTIFACT_ACTION_ADAPTER.json_schema() == BASELINE["action_schemas"]["artifact"]["value"]
    assert render_artifact_scientific_context(basis, text=_text, link=_link) == ""


@pytest.mark.parametrize("kind", tuple(BASELINE["task_profiles"]))
def test_all_five_original_controller_digests_match_true_archived_d2_baseline(kind):
    assert (
        controller_protocol_sha256(kind)
        == BASELINE["task_profiles"][kind]["controller_protocol_sha256"]
    )


@pytest.mark.parametrize("kind", ("formalise", "write_summary", "write_paper"))
def test_context_uses_distinct_fixed_prompt_and_exact_saved_basis_without_new_actions(kind):
    basis = context_basis(revised=True, parents=True)
    messages = artifact_initial_messages(kind, basis)
    assert messages[0] == artifact_static_system_message(kind, scientific_context=True)
    assert messages[0]["content"].startswith(SCIENTIFIC_CONTEXT_SYSTEM_PROMPT)
    assert SCIENTIFIC_CONTEXT_SYSTEM_PROMPT.startswith(SYSTEM_PROMPT)
    assert json.loads(messages[1]["content"])["research_basis"] == basis.model_dump(mode="json")
    payload = artifact_scientific_context_protocol_payload(kind)
    assert payload["controller_protocol"] == "polymath-meta-artifact-scientific-context-v1"
    assert payload["task_kind"] == kind and payload["kind_instructions"] == KIND_INSTRUCTIONS[kind]
    assert payload["action_schema"] == BASELINE["action_schemas"]["artifact"]["value"]
    assert "ArtifactScientificContext" in payload["basis_schema"]["$defs"]
    wrapper = json.dumps({"kind": kind, "research_basis": {}})
    assert minimum_artifact_scientific_context_characters(kind) == len(
        messages[0]["content"]
    ) + len(wrapper)
    assert "not a general proof" in messages[0]["content"]
    assert "historical" in messages[0]["content"] and "uncalibrated" in messages[0]["content"]


@pytest.mark.parametrize("mutation", ("reference", "finding", "hash", "explicit_null"))
def test_context_cannot_be_rebound_or_silently_discarded(mutation):
    raw = context_basis().model_dump(mode="json")
    if mutation == "reference":
        raw["reference"]["content_sha256"] = "0" * 64
    elif mutation == "finding":
        raw["finding"]["title"] = "Substituted scientific claim"
    elif mutation == "hash":
        raw["scientific_context"]["context_sha256"] = "0" * 64
    else:
        raw["scientific_context"] = None
    with pytest.raises(ValidationError):
        ArtifactBasis.model_validate_json(json.dumps(raw))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("formalise", "write_summary", "write_paper"))
async def test_existing_controller_actions_and_finite_checker_work_with_pinned_context(kind):
    model, checks = ArtifactFixtureModel(), FixtureCheckTool()
    result = await ArtifactController(model, check_tool=checks).generate(kind, context_basis())
    assert result.status == "ready"
    assert result.usage.model_calls == (2 if kind == "formalise" else 1)
    assert len(result.checks) == (1 if kind == "formalise" else 0)
    if result.checks:
        assert result.checks[0].proof_status == "not_a_general_proof"


@pytest.mark.asyncio
async def test_model_cannot_edit_the_context_or_ancestry_in_a_conclusion():
    action = conclude(document_draft())
    action["scientific_context"] = {"confidence_score": 100}
    model = SequenceModel([action, conclude(document_draft())])
    result = await ArtifactController(model).generate("write_summary", context_basis())
    assert result.status == "ready" and result.usage.model_calls == 2
    assert [event.kind for event in result.events].count("artifact_action_rejected") == 1
    assert "scientific_context" not in result.model_dump(mode="json")


@pytest.mark.asyncio
async def test_dynamic_context_overflow_stops_before_any_injected_completion():
    model = SequenceModel([])
    result = await ArtifactController(model, ArtifactLimits(max_context_characters=1000)).generate(
        "write_summary", context_basis(revised=True, parents=True)
    )
    assert result.status == "limit_reached" and result.usage.model_calls == 0
    assert model.seen == []


def test_markdown_distinguishes_revised_source_score_and_exact_parent_hashes():
    basis = context_basis(revised=True, parents=True)
    output = render_artifact_scientific_context(basis, text=_text, link=_link)
    context = basis.scientific_context.context
    assert basis.scientific_context.context_sha256 in output
    assert "historical and is not the revised finding's confidence" in output
    assert "25/100 (uncalibrated model judgement, not a probability)" in output
    for node in context.ancestry_nodes:
        assert node.reference.result_id in output and node.reference.content_sha256 in output
    for assessment in context.source_revision.applied_assessments:
        assert assessment.payload_sha256 in output
        assert _text(assessment.rationale[0].text) in output
        assert _text(assessment.quotes[0].quote) in output
        assert assessment.quotes[0].retrieval_id in output
    assert "premise:" in output and "reassessment:" in output


def test_markdown_escapes_all_context_prose_and_preserves_exact_full_text_scope():
    raw = copy.deepcopy(as_json_data(context_data(revised=True)))
    assessment = raw["source_revision"]["applied_assessments"][0]
    assessment["rationale"][0]["text"] = "<script>bad()</script> [forged](javascript:bad) # heading"
    coverage = SourceCoverage(
        snapshot_id=uid(800),
        snapshot_sha256="f" * 64,
        arxiv_id="2401.00001",
        arxiv_version=2,
        start=10,
        end=100,
        total=1000,
    )
    quote = assessment["quotes"][0]
    quote["content_kind"] = "full_text"
    quote["source"] = {
        "source_id": f"arxiv-html:{coverage.snapshot_id}:10:100",
        "title": "<img src=x onerror=bad()> *source*",
        "url": "https://arxiv.org/html/2401.00001v2",
        "bibliography": SourceBibliography(arxiv_id="2401.00001", arxiv_version=2).model_dump(
            mode="json"
        ),
        "coverage": coverage.model_dump(mode="json"),
    }
    basis = context_basis(context=ArtifactScientificContext.model_validate_json(json.dumps(raw)))
    output = render_artifact_scientific_context(basis, text=_text, link=_link)
    assert "<script>" not in output and "<img " not in output
    assert "[forged](javascript:bad)" not in output
    assert "FULL TEXT EXCERPT" in output and "[10, 100) of 1000" in output
    assert "[2, 37)" in output and coverage.snapshot_sha256 in output
    assert "does not indicate that the whole paper was read" in output
    assert "may omit equations, figures and table layout" in output


def test_prior_formalisation_original_context_hash_stays_distinct_from_new_context():
    context = ArtifactScientificContext.model_validate_json(
        json.dumps(as_json_data(context_data(revised=True)))
    )
    prior = PriorArtifactContextReference(
        artifact_id=uid(900),
        source_reference=context.source_reference,
        payload_sha256="a" * 64,
        basis_sha256="b" * 64,
        context_sha256="e" * 64,
    )
    context = ArtifactScientificContext.model_validate_json(
        context.model_copy(update={"prior_formalisation": prior}).model_dump_json()
    )
    basis = context_basis(context=context, formalisation=formalisation_draft())
    output = render_artifact_scientific_context(basis, text=_text, link=_link)
    assert prior.context_sha256 in output and basis.scientific_context.context_sha256 in output
    assert prior.context_sha256 != basis.scientific_context.context_sha256
    assert "original context is distinct" in output
