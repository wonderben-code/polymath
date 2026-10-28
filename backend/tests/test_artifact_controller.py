"""Synthetic artifact fixtures verify integrity/control, not scientific truth."""

import asyncio
import copy
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError
from test_research_controller import bridge
from test_research_modes import fixture_answer

from polymath.artifact_contracts import (
    ArtifactBasis,
    ArtifactPassage,
    ArtifactResult,
    CitedText,
    Definition,
    DocumentDraft,
    DocumentSection,
    FormalisationDraft,
    validate_artifact_result,
)
from polymath.artifact_controller import ArtifactController, ArtifactLimits
from polymath.math_checks import FiniteConjecture, check_conjecture
from polymath.research.meta import ModelError, ModelReply
from polymath.research.models import AnswerFinding, BridgeFinding, MemoryReadReference, Usage

SOURCE_RESULT_ID = "a1111111-1111-4111-8111-111111111111"
SOURCE_RUN_ID = "b2222222-2222-4222-8222-222222222222"
PRIOR_FORMALISATION_ID = "c3333333-3333-4333-8333-333333333333"


def artifact_basis(*, answer=False, formalisation=None, formalisation_checks=()) -> ArtifactBasis:
    finding = (
        AnswerFinding.model_validate_json(
            json.dumps(fixture_answer("What do synthetic sources support?")["conclusion"])
        )
        if answer
        else BridgeFinding.model_validate_json(json.dumps(bridge()["conclusion"]))
    )
    if isinstance(finding, AnswerFinding):
        evidence = [(claim.id, item) for claim in finding.claims for item in claim.evidence]
    else:
        evidence = [(None, item) for item in finding.evidence]
    return ArtifactBasis(
        reference=MemoryReadReference(
            result_id=SOURCE_RESULT_ID, run_id=SOURCE_RUN_ID, content_sha256="a" * 64
        ),
        finding=finding,
        passages=tuple(
            ArtifactPassage(
                ordinal=ordinal,
                source_id=item.source_id,
                title=f"Synthetic artifact source {item.source_id}",
                url=f"https://example.org/synthetic-artifacts/{item.source_id}",
                content_sha256=("b" if item.source_id == "left" else "c") * 64,
                content_kind="abstract",
                quote=item.quote,
                supports=item.supports,
                claim_id=claim_id,
            )
            for ordinal, (claim_id, item) in enumerate(evidence)
        ),
        formalisation_id=PRIOR_FORMALISATION_ID if formalisation is not None else None,
        formalisation=formalisation,
        formalisation_checks=formalisation_checks,
    )


def cited(
    text="Synthetic model inference remains unverified.", *, label="postulated", ordinals=(0,)
):
    return CitedText(text=text, label=label, citation_ordinals=ordinals)


def document_draft(kind="summary", *, ordinals=(0, 1), label="postulated") -> DocumentDraft:
    headings = (
        ("Background", "Findings", "Evidence", "Limitations")
        if kind == "paper"
        else (
            "What the evidence says",
            "What remains open",
        )
    )
    return DocumentDraft(
        kind=kind,
        title=f"Synthetic artifact {kind}",
        abstract=(cited(ordinals=ordinals, label=label),),
        sections=tuple(
            DocumentSection(heading=heading, paragraphs=(cited(ordinals=ordinals, label=label),))
            for heading in headings
        ),
        limitations=["Synthetic sources cannot establish a scientific finding."],
    )


def encoded_conjecture(*, false=False, upper=2) -> FiniteConjecture:
    return FiniteConjecture.model_validate_json(
        json.dumps(
            {
                "variables": [{"name": "n", "lower": -2, "upper": upper}],
                "assumptions": [],
                "conclusion": {
                    "left": {
                        "op": "add",
                        "left": {"op": "variable", "name": "n"},
                        "right": {"op": "constant", "value": 0 if not false else 1},
                    },
                    "operator": "eq",
                    "right": {"op": "variable", "name": "n"},
                },
            }
        )
    )


def formalisation_draft(conjecture=None, *, ordinals=(0, 1)) -> FormalisationDraft:
    return FormalisationDraft(
        title="Synthetic open conjecture",
        definitions=(
            Definition(
                symbol="n",
                meaning=cited("An encoded finite-domain integer variable.", ordinals=ordinals),
            ),
        ),
        assumptions=(
            cited(
                "The proposed correspondence is an unverified model assumption.", ordinals=ordinals
            ),
        ),
        conjecture=cited(
            "An exact finite encoding can test part of this synthetic conjecture.",
            ordinals=ordinals,
        ),
        proof_attempt=(
            cited("This is a synthetic proof attempt, not an accepted proof.", ordinals=ordinals),
        ),
        limitations=[
            "A finite-domain check is not a general proof or validation of the prose encoding."
        ],
        finite_conjecture=conjecture,
    )


class FixtureCheckTool:
    def __init__(self):
        self.calls = []

    async def check(self, conjecture):
        self.calls.append(conjecture)
        return check_conjecture(conjecture)


class ArtifactFixtureModel:
    """Adaptive fixture useful for worker/browser tests; never a live model."""

    def __init__(self):
        self.seen = []

    async def complete(self, messages, *, max_output_tokens):
        user = [json.loads(item["content"]) for item in messages if item["role"] == "user"]
        kind = user[0]["kind"]
        observation = user[-1].get("observation")
        self.seen.append(observation)
        if kind == "formalise" and observation is None:
            action = {
                "kind": "check_conjecture",
                "conjecture": encoded_conjecture().model_dump(mode="json"),
            }
        else:
            if kind == "formalise":
                draft = formalisation_draft(
                    FiniteConjecture.model_validate_json(json.dumps(observation["conjecture"]))
                )
            else:
                draft = document_draft("summary" if kind == "write_summary" else "paper")
            action = {"kind": "conclude", "draft": draft.model_dump(mode="json")}
        return ModelReply(json.dumps(action), 10, 15)


class SequenceModel:
    def __init__(self, actions, *, usage=True):
        self.actions = iter(actions)
        self.seen = []
        self.usage = usage

    async def complete(self, messages, *, max_output_tokens):
        self.seen.append(copy.deepcopy(messages))
        value = next(self.actions)
        if isinstance(value, BaseException):
            raise value
        return ModelReply(
            value if isinstance(value, str) else json.dumps(value),
            10 if self.usage else None,
            15 if self.usage else None,
        )


def conclude(draft):
    return {"kind": "conclude", "draft": draft.model_dump(mode="json")}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["write_summary", "write_paper", "formalise"])
async def test_complete_source_bound_artifact_uses_actual_observations(kind):
    model, checks, events = ArtifactFixtureModel(), FixtureCheckTool(), []

    async def on_event(item):
        events.append(item)

    result = await ArtifactController(model, check_tool=checks, on_event=on_event).generate(
        kind, artifact_basis()
    )
    assert result.status == "ready"
    assert tuple(events) == result.events
    assert result.events[-1].kind == "artifact_draft_ready"
    validate_artifact_result(result, artifact_basis())
    assert result.usage.model_calls == (2 if kind == "formalise" else 1)
    assert result.usage.input_tokens_reported == 10 * result.usage.model_calls
    if kind == "formalise":
        assert len(checks.calls) == 1
        assert model.seen[-1]["status"] == "no_counterexample_in_domain"
        assert result.checks[0].proof_status == "not_a_general_proof"
    else:
        assert not result.checks and not checks.calls


@pytest.mark.asyncio
async def test_plain_open_conjecture_does_not_require_an_invented_check():
    result = await ArtifactController(SequenceModel([conclude(formalisation_draft())])).generate(
        "formalise", artifact_basis()
    )
    assert result.status == "ready"
    assert result.draft.finite_conjecture is None
    assert result.checks == ()
    assert "proof" not in result.model_dump().keys()


@pytest.mark.asyncio
async def test_model_revises_an_encoding_after_real_counterexample_observation():
    false, corrected = encoded_conjecture(false=True), encoded_conjecture()

    class RevisingFixture:
        seen = []

        async def complete(self, messages, *, max_output_tokens):
            observation = json.loads(messages[-1]["content"]).get("observation")
            self.seen.append(observation)
            if observation is None:
                action = {"kind": "check_conjecture", "conjecture": false.model_dump(mode="json")}
            elif observation["status"] == "counterexample_found":
                action = {
                    "kind": "check_conjecture",
                    "conjecture": corrected.model_dump(mode="json"),
                }
            else:
                action = conclude(formalisation_draft(corrected))
            return ModelReply(json.dumps(action), 1, 1)

    model = RevisingFixture()
    result = await ArtifactController(model, check_tool=FixtureCheckTool()).generate(
        "formalise", artifact_basis()
    )
    assert result.status == "ready"
    assert [item.status for item in result.checks] == [
        "counterexample_found",
        "no_counterexample_in_domain",
    ]
    assert model.seen[1]["witness"] is not None
    assert result.draft.finite_conjecture == corrected


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_kind", ["write_summary", "write_paper"])
async def test_mathematical_tool_cannot_be_requested_by_write_tasks(bad_kind):
    check = {"kind": "check_conjecture", "conjecture": encoded_conjecture().model_dump(mode="json")}
    model, tool = (
        SequenceModel([check, {"kind": "insufficient", "reason": "Fixture rejection."}]),
        (FixtureCheckTool()),
    )
    result = await ArtifactController(model, check_tool=tool).generate(bad_kind, artifact_basis())
    assert result.status == "insufficient_evidence"
    assert tool.calls == []
    assert "check_wrong_artifact_kind" in model.seen[-1][-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["citation", "kind", "unperformed_check"])
async def test_invalid_evidence_or_task_output_is_repaired_without_false_ready(problem):
    draft = (
        formalisation_draft(encoded_conjecture())
        if problem == "unperformed_check"
        else document_draft("paper" if problem == "kind" else "summary", ordinals=(63,))
    )
    model = SequenceModel(
        [conclude(draft), {"kind": "insufficient", "reason": "Need more evidence."}]
    )
    result = await ArtifactController(model).generate(
        "formalise" if problem == "unperformed_check" else "write_summary", artifact_basis()
    )
    assert result.status == "insufficient_evidence"
    assert result.draft is None
    assert result.usage.model_calls == 2
    assert any(item.kind == "artifact_action_rejected" for item in result.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["formalise", "write_summary", "write_paper"])
async def test_bridge_artifact_requires_citations_from_both_original_sides(kind):
    draft = (
        formalisation_draft(ordinals=(0,))
        if kind == "formalise"
        else document_draft("paper" if kind == "write_paper" else "summary", ordinals=(0,))
    )
    model = SequenceModel(
        [conclude(draft), {"kind": "insufficient", "reason": "Both bridge sides are needed."}]
    )
    result = await ArtifactController(model).generate(kind, artifact_basis())
    assert result.status == "insufficient_evidence"
    assert "bridge_citation_coverage_missing" in model.seen[-1][-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ordinal", [1, 3])
async def test_inherited_postulated_or_speculative_claim_cannot_be_upgraded(ordinal):
    draft = document_draft(label="known", ordinals=(ordinal,))
    model = SequenceModel(
        [conclude(draft), {"kind": "insufficient", "reason": "Retain uncertainty."}]
    )
    result = await ArtifactController(model).generate("write_summary", artifact_basis(answer=True))
    assert result.status == "insufficient_evidence"
    assert "citation_label_upgrade" in model.seen[-1][-1]["content"]


@pytest.mark.asyncio
async def test_known_original_claim_remains_eligible_with_its_original_label():
    model = SequenceModel([conclude(document_draft(label="known", ordinals=(0,)))])
    result = await ArtifactController(model).generate("write_summary", artifact_basis(answer=True))
    assert result.status == "ready"


@pytest.mark.asyncio
async def test_prior_formalisation_is_structured_untrusted_context_not_verified_knowledge():
    basis = artifact_basis(formalisation=formalisation_draft())
    model = SequenceModel([conclude(document_draft())])
    result = await ArtifactController(model).generate("write_summary", basis)
    assert result.status == "ready"
    payload = json.loads(model.seen[0][1]["content"])["research_basis"]
    assert payload["formalisation_id"] == PRIOR_FORMALISATION_ID
    assert payload["formalisation"]["conjecture"]["label"] == "postulated"
    assert "open/unverified" in model.seen[0][0]["content"]


@pytest.mark.asyncio
async def test_writing_context_preserves_prior_finite_counterexample_and_exact_encoding():
    encoding = encoded_conjecture(false=True)
    check = check_conjecture(encoding)
    basis = artifact_basis(
        formalisation=formalisation_draft(encoding), formalisation_checks=(check,)
    )
    model = SequenceModel([conclude(document_draft("paper"))])
    result = await ArtifactController(model).generate("write_paper", basis)
    assert result.status == "ready" and result.checks == ()
    payload = json.loads(model.seen[0][1]["content"])["research_basis"]
    assert payload["formalisation_checks"][0] == check.model_dump(mode="json")
    assert payload["formalisation_checks"][0]["status"] == "counterexample_found"
    assert "actual prior formalisation_checks" in model.seen[0][0]["content"]


@pytest.mark.asyncio
async def test_duplicate_check_reuses_a_result_without_repeating_the_tool():
    encoded = encoded_conjecture()
    action = {"kind": "check_conjecture", "conjecture": encoded.model_dump(mode="json")}
    tool = FixtureCheckTool()
    result = await ArtifactController(
        SequenceModel([action, action, conclude(formalisation_draft(encoded))]), check_tool=tool
    ).generate("formalise", artifact_basis())
    assert result.status == "ready"
    assert len(result.checks) == len(tool.calls) == 1


@pytest.mark.asyncio
async def test_fourth_distinct_check_is_rejected_and_does_not_call_the_tool():
    actions = [
        {
            "kind": "check_conjecture",
            "conjecture": encoded_conjecture(upper=n).model_dump(mode="json"),
        }
        for n in (1, 2, 3, 4)
    ]
    actions.append({"kind": "insufficient", "reason": "Bounded check exploration stopped."})
    model, tool = SequenceModel(actions), FixtureCheckTool()
    result = await ArtifactController(model, check_tool=tool).generate(
        "formalise", artifact_basis()
    )
    assert result.status == "insufficient_evidence"
    assert len(tool.calls) == len(result.checks) == 3
    assert "finite_check_limit" in model.seen[-1][-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["mismatched", "fabricated", "timeout", "missing"])
async def test_unavailable_or_untrustworthy_check_never_enters_authoritative_results(problem):
    class BadTool:
        async def check(self, conjecture):
            if problem == "timeout":
                await asyncio.Event().wait()
            result = check_conjecture(encoded_conjecture(false=problem == "mismatched"))
            return (
                result.model_copy(update={"assignments_tested": 999})
                if (problem == "fabricated")
                else result
            )

    action = {
        "kind": "check_conjecture",
        "conjecture": encoded_conjecture().model_dump(mode="json"),
    }
    model = SequenceModel([action, {"kind": "insufficient", "reason": "No trustworthy check."}])
    result = await ArtifactController(
        model,
        ArtifactLimits(tool_timeout_seconds=0.01),
        check_tool=None if problem == "missing" else BadTool(),
    ).generate("formalise", artifact_basis())
    assert result.status == "insufficient_evidence"
    assert result.checks == ()
    assert "finite_check_failed" in model.seen[-1][-1]["content"]


@pytest.mark.asyncio
async def test_finite_encoding_cannot_change_after_a_check_without_a_new_check():
    encoded = encoded_conjecture()
    model = SequenceModel(
        [
            {"kind": "check_conjecture", "conjecture": encoded.model_dump(mode="json")},
            conclude(formalisation_draft(encoded_conjecture(false=True))),
            {"kind": "insufficient", "reason": "The revision has not been checked."},
        ]
    )
    result = await ArtifactController(model, check_tool=FixtureCheckTool()).generate(
        "formalise", artifact_basis()
    )
    assert result.status == "insufficient_evidence"
    assert "finite_conjecture_not_checked" in model.seen[-1][-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    ["not json", '{"kind":"publish"}', '{"kind":"insufficient","reason":"bad\\u0000text"}'],
)
async def test_untrusted_action_schema_is_bounded_and_does_not_echo_bad_text(payload):
    model = SequenceModel([payload, payload, payload])
    result = await ArtifactController(model).generate("write_summary", artifact_basis())
    assert result.status == "failed" and result.error_code == "repair_limit"
    assert result.usage.model_calls == 3
    assert all(payload not in event.summary for event in result.events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [ModelError("model_access_denied"), RuntimeError("private provider body")]
)
async def test_model_errors_are_sanitized(error):
    result = await ArtifactController(SequenceModel([error])).generate(
        "write_summary", artifact_basis()
    )
    assert result.status == "failed"
    assert result.error_code in {"model_access_denied", "model_failed"}
    assert "private provider body" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_model_timeout_is_an_unknown_external_outcome_not_a_zero_cost_retry():
    class Waiting:
        async def complete(self, messages, *, max_output_tokens):
            await asyncio.Event().wait()

    result = await ArtifactController(
        Waiting(), ArtifactLimits(model_timeout_seconds=0.01)
    ).generate("write_summary", artifact_basis())
    assert result.status == "failed"
    assert result.error_code == "model_timeout_outcome_unknown"
    assert result.usage.calls_without_usage == result.usage.model_calls == 1


@pytest.mark.asyncio
async def test_context_and_action_limits_stop_without_fabricating_a_document():
    model = SequenceModel([])
    result = await ArtifactController(model, ArtifactLimits(max_context_characters=1000)).generate(
        "write_summary", artifact_basis()
    )
    assert result.status == "limit_reached" and result.error_code == "context_limit"
    assert model.seen == []
    action = {
        "kind": "check_conjecture",
        "conjecture": encoded_conjecture().model_dump(mode="json"),
    }
    result = await ArtifactController(
        SequenceModel([action]), ArtifactLimits(max_actions=1), check_tool=FixtureCheckTool()
    ).generate("formalise", artifact_basis())
    assert result.status == "limit_reached" and result.error_code == "action_or_call_limit"
    assert len(result.checks) == 1 and result.draft is None


@pytest.mark.asyncio
async def test_missing_usage_is_preserved_not_assumed_to_be_zero():
    result = await ArtifactController(
        SequenceModel([conclude(document_draft())], usage=False)
    ).generate("write_summary", artifact_basis())
    assert result.status == "ready"
    assert result.usage.calls_without_usage == 1
    assert result.usage.input_tokens_reported == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,tokens", [("x" * 16001, (1, 1)), ("", (1, 1)), ("{}", (-1, 1)), ("{}", (True, 1))]
)
async def test_invalid_reply_content_or_usage_is_not_accepted(content, tokens):
    class BadReply:
        async def complete(self, messages, *, max_output_tokens):
            return ModelReply(content, *tokens)

    result = await ArtifactController(BadReply()).generate("write_summary", artifact_basis())
    assert result.status == "failed" and result.error_code == "model_invalid_response"


@pytest.mark.asyncio
async def test_worker_control_flow_cannot_be_swallowed_as_a_tool_observation():
    class StopWork(BaseException):
        pass

    class StoppedTool:
        async def check(self, conjecture):
            raise StopWork()

    action = {
        "kind": "check_conjecture",
        "conjecture": encoded_conjecture().model_dump(mode="json"),
    }
    with pytest.raises(StopWork):
        await ArtifactController(SequenceModel([action]), check_tool=StoppedTool()).generate(
            "formalise", artifact_basis()
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_model_calls", 0),
        ("max_model_calls", True),
        ("max_actions", 13),
        ("max_output_tokens", 4097),
        ("max_repairs", -1),
        ("max_context_characters", 90001),
        ("tool_timeout_seconds", float("nan")),
        ("model_timeout_seconds", 0),
    ],
)
def test_artifact_limits_reject_unbounded_or_ambiguous_values(field, value):
    with pytest.raises(ValueError):
        replace(ArtifactLimits(), **{field: value})


@pytest.mark.parametrize(
    "change", ["known_conjecture", "known_proof", "duplicate_symbol", "unverified_status"]
)
def test_formalisation_contract_cannot_declare_a_verified_proof(change):
    raw = formalisation_draft().model_dump(mode="json")
    if change == "known_conjecture":
        raw["conjecture"]["label"] = "known"
    elif change == "known_proof":
        raw["proof_attempt"][0]["label"] = "known"
    elif change == "duplicate_symbol":
        raw["definitions"] += copy.deepcopy(raw["definitions"])
    else:
        raw["proof_status"] = "verified"
    with pytest.raises(ValidationError):
        FormalisationDraft.model_validate_json(json.dumps(raw))


def test_paper_contract_requires_complete_core_sections_and_distinct_citations():
    raw = document_draft("paper").model_dump(mode="json")
    raw["sections"] = raw["sections"][:2]
    with pytest.raises(ValidationError):
        DocumentDraft.model_validate_json(json.dumps(raw))
    with pytest.raises(ValidationError):
        cited(ordinals=(0, 0))
    with pytest.raises(ValidationError):
        cited(ordinals=(True,))


def test_persistence_helper_rechecks_check_integrity_even_for_unready_outcomes():
    valid = check_conjecture(encoded_conjecture())
    result = ArtifactResult(
        kind="formalise", status="limit_reached", checks=(valid,), usage=Usage()
    )
    corrupt = result.model_copy(update={"checks": (valid.model_copy(update={"lhs": "999"}),)})
    with pytest.raises(ValueError):
        validate_artifact_result(corrupt, artifact_basis())


def test_basis_prevents_unbound_formalisation_and_answer_passages():
    raw = artifact_basis().model_dump(mode="json")
    raw["formalisation_id"] = PRIOR_FORMALISATION_ID
    with pytest.raises(ValidationError):
        ArtifactBasis.model_validate_json(json.dumps(raw))

    encoding = encoded_conjecture()
    with pytest.raises(ValidationError):
        artifact_basis(formalisation=formalisation_draft(encoding))
    with pytest.raises(ValidationError):
        artifact_basis(formalisation_checks=(check_conjecture(encoding),))
    raw = artifact_basis(answer=True).model_dump(mode="json")
    raw["passages"][0]["claim_id"] = "invented_claim"
    with pytest.raises(ValidationError):
        ArtifactBasis.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field", ["source_id", "quote", "supports", "ordinal"])
def test_basis_passages_cannot_rebind_the_original_evidence(field):
    raw = artifact_basis().model_dump(mode="json")
    raw["passages"][0][field] = 63 if field == "ordinal" else "Different synthetic evidence"
    with pytest.raises(ValidationError):
        ArtifactBasis.model_validate_json(json.dumps(raw))


def test_original_quote_whitespace_is_preserved_exactly():
    raw = artifact_basis().model_dump(mode="json")
    quote = " " + raw["finding"]["evidence"][0]["quote"] + "\n"
    raw["finding"]["evidence"][0]["quote"] = quote
    raw["passages"][0]["quote"] = quote
    validated = ArtifactBasis.model_validate_json(json.dumps(raw))
    assert validated.passages[0].quote == quote


def test_write_task_cannot_claim_a_new_authoritative_check():
    with pytest.raises(ValidationError):
        ArtifactResult(
            kind="write_summary",
            status="ready",
            draft=document_draft(),
            checks=(check_conjecture(encoded_conjecture()),),
            usage=Usage(),
        )
