"""Synthetic version-bound reassessment fixtures; no paid or provider requests."""

import asyncio
import copy
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from test_research_controller import bridge, source
from test_research_modes import fixture_answer, fixture_source

from polymath.assessment_contracts import (
    AssessmentBasis,
    AssessmentResult,
    AssessmentSourceReference,
    AssessmentSourceSnapshot,
    ConcludeAssessmentAction,
    validate_assessment_result,
    verify_assessment_quotes,
)
from polymath.assessment_controller import AssessmentController, AssessmentLimits
from polymath.research.controller import _verify_answer_evidence, _verify_evidence
from polymath.research.meta import ModelError, ModelReply
from polymath.research.models import (
    AnswerFinding,
    BridgeFinding,
    FieldSelection,
    MemoryReadReference,
    ProviderFailure,
    SearchResults,
    SourceBibliography,
    SourceDocument,
    SourceReference,
    Usage,
)

QUESTION = "What do synthetic sources support?"


def identifier(value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-assessment:" + value))


def snapshot(document):
    version = identifier(
        document.source_id + ":" + document.content_kind + ":" + document.content_sha256
    )
    retrieval = identifier(document.model_dump_json())
    return AssessmentSourceSnapshot(retrieval_id=retrieval, version_id=version, document=document)


def assessment_basis(
    *,
    mode="explore",
    new_count=1,
    same_paper_revision=False,
    prior_state="supported",
    contradicted=False,
):
    if mode == "ask":
        finding = AnswerFinding.model_validate_json(
            json.dumps(fixture_answer(QUESTION)["conclusion"])
        )
        historical = [snapshot(fixture_source(side)) for side in ("left", "right")]
        quotes = _verify_answer_evidence(
            finding,
            {item.document.source_id: item.document for item in historical},
            {"counterevidence", "prior_work"},
        )
    else:
        finding = BridgeFinding.model_validate_json(json.dumps(bridge()["conclusion"]))
        from test_research_controller import LEFT_QUOTE, RIGHT_QUOTE

        historical = [snapshot(source("left", LEFT_QUOTE)), snapshot(source("right", RIGHT_QUOTE))]
        quotes = _verify_evidence(
            finding,
            {item.document.source_id: item.document for item in historical},
            {"counterevidence", "prior_work"},
        )
    added = []
    for index in range(new_count):
        text = (
            "A newer synthetic report contradicts the earlier proposed correspondence."
            if contradicted
            else f"New synthetic study {index + 1} reports a bounded change "
            "in the observed mechanism."
        )
        original = historical[0].document
        document = SourceDocument(
            source_id=original.source_id if same_paper_revision else f"new-{index + 1}",
            title=f"Synthetic new source {index + 1}",
            url=original.url
            if same_paper_revision
            else f"https://example.org/assessment/new-{index + 1}",
            text=text,
            content_kind="abstract",
            retrieved_at=datetime(2026, 10, 28, tzinfo=UTC),
            extraction_method="synthetic_assessment_fixture/v1",
        )
        added.append(snapshot(document))
    refs = tuple(
        AssessmentSourceReference(
            source_id=item.document.source_id,
            title=item.document.title,
            url=item.document.url,
            bibliography=item.document.bibliography,
            retrieval_id=item.retrieval_id,
            version_id=item.version_id,
            content_sha256=item.document.content_sha256,
            content_kind=item.document.content_kind,
            role="historical" if item in historical else "new",
        )
        for item in (*historical, *added)
    )
    basis = AssessmentBasis(
        prior_reference=MemoryReadReference(
            result_id=identifier("prior-result"),
            run_id=identifier("prior-run"),
            content_sha256="a" * 64,
        ),
        mode=mode,
        prior_state=prior_state,
        prior_finding=finding,
        prior_quotes=quotes,
        field_selections=(
            FieldSelection(
                fields=(finding.field_left, finding.field_right),
                rationale="Synthetic original fields.",
            ),
        )
        if mode == "surprise"
        else (),
        sources=refs,
    )
    return basis, {item.retrieval_id: item for item in (*historical, *added)}


def replace_bibliographies(basis, sources, changes):
    replacements = []
    updated = {}
    for ref in basis.sources:
        doc = sources[ref.retrieval_id].document
        if ref.retrieval_id in changes:
            doc = doc.model_copy(update={"bibliography": changes[ref.retrieval_id]})
        item = snapshot(doc)
        replacements.append(
            AssessmentSourceReference(
                source_id=doc.source_id,
                title=doc.title,
                url=doc.url,
                bibliography=doc.bibliography,
                retrieval_id=item.retrieval_id,
                version_id=item.version_id,
                content_sha256=doc.content_sha256,
                content_kind=doc.content_kind,
                role=ref.role,
            )
        )
        updated[item.retrieval_id] = item
    raw = basis.model_dump(mode="json")
    raw["sources"] = [item.model_dump(mode="json") for item in replacements]
    return AssessmentBasis.model_validate_json(json.dumps(raw)), updated


class AssessmentFixtureTools:
    def __init__(self, sources, *, fresh=None):
        self.sources, self.fresh = sources, fresh
        self.reads, self.searches, self.fresh_reads = [], [], []

    async def read_source_by_retrieval(self, retrieval_id):
        self.reads.append(retrieval_id)
        return self.sources[retrieval_id].document

    async def search(self, query, purpose):
        self.searches.append((query, purpose))
        doc = self.fresh.document if self.fresh else None
        return SearchResults(
            sources=(
                SourceReference(
                    source_id=doc.source_id,
                    title=doc.title,
                    url=doc.url,
                    bibliography=doc.bibliography,
                ),
            )
            if doc is not None
            else ()
        )

    async def read_literature_source(self, source_id):
        self.fresh_reads.append(source_id)
        assert self.fresh and self.fresh.document.source_id == source_id
        return self.fresh


def judgement(basis, observed, *, disposition="unchanged", revised=False):
    citations = []
    old = next(item for item in basis["sources"] if item["role"] == "historical")
    original = next(
        item
        for item in basis["prior_quotes"]
        if item["source_id"] == old["source_id"] and item["source_sha256"] == old["content_sha256"]
    )
    citations.append(
        {
            "retrieval_id": old["retrieval_id"],
            "quote": original["quote"],
            "supports": "The original synthetic premise being reassessed.",
        }
    )
    for ref in basis["sources"]:
        if ref["role"] == "new":
            citations.append(
                {
                    "retrieval_id": ref["retrieval_id"],
                    "quote": observed[ref["retrieval_id"]]["document"]["text"],
                    "supports": "A selected new synthetic version considered in the judgement.",
                }
            )
    result = {
        "kind": "conclude_assessment",
        "disposition": "revised" if revised else disposition,
        "rationale": [
            {
                "text": "A synthetic, source-bound model assessment; "
                "no scientific truth is verified.",
                "citations": citations,
            }
        ],
        "limitations": [
            "Synthetic sources and model classifications cannot establish scientific truth."
        ],
    }
    if revised:
        new = next(item for item in basis["sources"] if item["role"] == "new")
        right = next(
            item
            for item in basis["sources"]
            if item["role"] == "historical" and item["source_id"] != old["source_id"]
        )
        left_doc, right_doc = (
            observed[new["retrieval_id"]]["document"],
            observed[right["retrieval_id"]]["document"],
        )
        finding = copy.deepcopy(basis["prior_finding"])
        if finding["outcome"] == "bridge":
            finding["title"] = "Synthetic revised connection"
            finding["evidence"] = [
                {
                    "source_id": doc["source_id"],
                    "side": side,
                    "quote": doc["text"],
                    "supports": "A current synthetic premise.",
                }
                for doc, side in ((left_doc, "left"), (right_doc, "right"))
            ]
        else:
            for claim in finding["claims"]:
                docs = (left_doc, right_doc) if claim["label"] == "postulated" else (left_doc,)
                claim["evidence"] = [
                    {
                        "source_id": doc["source_id"],
                        "quote": doc["text"],
                        "supports": "A current synthetic answer premise.",
                    }
                    for doc in docs
                ]
        result["revised_finding"] = finding
        result["selected_retrieval_ids"] = [new["retrieval_id"], right["retrieval_id"]]
    return result


class AssessmentFixtureModel:
    """Adaptive test fixture reusable for worker/browser checks, never production intelligence."""

    def __init__(self, *, revised=False):
        self.revised, self.seen = revised, []

    async def complete(self, messages, *, max_output_tokens):
        user = [json.loads(item["content"]) for item in messages if item["role"] == "user"]
        basis = user[0]["assessment_basis"]
        observations = [item["observation"] for item in user if "observation" in item]
        self.seen.append(observations[-1] if observations else None)
        observed = {
            item["retrieval_id"]: item
            for item in observations
            if item.get("kind") == "assessment_source_text"
        }
        required = [item for item in basis["sources"] if item["role"] == "new"]
        old = [item for item in basis["sources"] if item["role"] == "historical"]
        required = old + required if self.revised else old[:1] + required
        unread = [item for item in required if item["retrieval_id"] not in observed]
        purposes = {
            item["purpose"] for item in observations if item.get("kind") == "search_results"
        }
        if unread:
            action = {"kind": "read_source_by_retrieval", "retrieval_id": unread[0]["retrieval_id"]}
        elif self.revised and "counterevidence" not in purposes:
            action = {
                "kind": "search_literature",
                "query": "Synthetic reassessment counterevidence",
                "purpose": "counterevidence",
            }
        elif self.revised and "prior_work" not in purposes:
            action = {
                "kind": "search_literature",
                "query": "Synthetic reassessment prior work",
                "purpose": "prior_work",
            }
        else:
            contradicted = any(
                "contradicts" in item["document"]["text"] for item in observed.values()
            )
            action = judgement(
                basis,
                observed,
                disposition="unsupported" if contradicted else "unchanged",
                revised=self.revised,
            )
        return ModelReply(json.dumps(action), 10, 15)


class SequenceModel:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.seen = []

    async def complete(self, messages, *, max_output_tokens):
        self.seen.append(copy.deepcopy(messages))
        action = next(self.actions)
        if isinstance(action, BaseException):
            raise action
        return ModelReply(action if isinstance(action, str) else json.dumps(action), 10, 15)


def inconclusive():
    return {
        "kind": "conclude_assessment",
        "disposition": "inconclusive",
        "rationale": [
            {"text": "The bounded fixture cannot complete this assessment.", "citations": []}
        ],
        "limitations": ["The prior finding is retained; no complete reassessment is claimed."],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("contradicted", [False, True])
@pytest.mark.parametrize("new_count", [1, 5])
async def test_adaptive_model_revisits_selected_exact_versions_before_judgement(
    contradicted, new_count
):
    basis, sources = assessment_basis(new_count=new_count, contradicted=contradicted)
    tools, model = AssessmentFixtureTools(sources), AssessmentFixtureModel()
    result = await AssessmentController(model, tools).reassess(basis)
    assert result.status == "completed"
    assert result.disposition == ("unsupported" if contradicted else "unchanged")
    assert len(tools.reads) == new_count + 1
    assert len(result.quotes) == new_count + 1
    assert result.usage.model_calls == new_count + 2
    validate_assessment_result(result, basis, {key: sources[key] for key in tools.reads})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["explore", "ask", "surprise"])
async def test_valid_revision_inherits_mode_original_question_fields_and_memory_provenance(mode):
    basis, sources = assessment_basis(mode=mode)
    tools, model = AssessmentFixtureTools(sources), AssessmentFixtureModel(revised=True)
    result = await AssessmentController(model, tools).reassess(basis)
    assert result.status == "completed" and result.disposition == "revised"
    revised = result.revised_result
    assert revised.mode == mode and revised.memory_reads == (basis.prior_reference,)
    assert revised.field_selections == basis.field_selections
    assert revised.status == ("answer" if mode == "ask" else "bridge")
    if mode == "ask":
        assert revised.finding.question == QUESTION
    assert {purpose for _, purpose in tools.searches} == {"counterevidence", "prior_work"}
    validate_assessment_result(result, basis, sources)


@pytest.mark.asyncio
async def test_same_canonical_paper_versions_use_retrieval_handles_without_renaming_paper_ids():
    basis, sources = assessment_basis(same_paper_revision=True)
    model, tools = AssessmentFixtureModel(revised=True), AssessmentFixtureTools(sources)
    result = await AssessmentController(model, tools).reassess(basis)
    assert result.disposition == "revised"
    refs = [item for item in basis.sources if item.source_id == "left"]
    assert len(refs) == 2 and refs[0].retrieval_id != refs[1].retrieval_id
    assert len({item.source_sha256 for item in result.quotes if item.source_id == "left"}) == 2
    chosen = next(item for item in result.revised_result.sources if item.source_id == "left")
    assert (
        chosen.text
        == sources[next(item.retrieval_id for item in refs if item.role == "new")].document.text
    )
    assert {item.source_id for item in result.revised_result.sources} == {"left", "right"}


@pytest.mark.asyncio
async def test_unchanged_unsupported_prior_is_explicit_context_and_not_silently_restored():
    basis, sources = assessment_basis(prior_state="unsupported")
    model = AssessmentFixtureModel()
    result = await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(basis)
    assert result.disposition == "unchanged" and result.revised_result is None
    assert basis.prior_state == "unsupported"


@pytest.mark.asyncio
async def test_honest_inconclusive_can_retain_partial_or_no_reads():
    basis, sources = assessment_basis()
    tools = AssessmentFixtureTools(sources)
    result = await AssessmentController(SequenceModel([inconclusive()]), tools).reassess(basis)
    assert result.status == "completed" and result.disposition == "inconclusive"
    assert result.quotes == () and result.observed_retrieval_ids == ()
    assert tools.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["read_source_by_retrieval", "read_literature_source"])
async def test_foreign_or_undiscovered_reads_are_rejected_before_tool_access(kind):
    basis, sources = assessment_basis()
    tools = AssessmentFixtureTools(sources)
    action = {
        "kind": kind,
        **(
            {"retrieval_id": identifier("foreign")}
            if kind == "read_source_by_retrieval"
            else {"source_id": "private-unknown-source"}
        ),
    }
    model = SequenceModel([action, inconclusive()])
    result = await AssessmentController(model, tools).reassess(basis)
    assert result.disposition == "inconclusive"
    assert tools.reads == tools.fresh_reads == []
    assert "assessment_source_not_" in model.seen[-1][-1]["content"]


@pytest.mark.asyncio
async def test_pinned_read_rejects_body_from_a_different_version_even_with_the_same_paper_id():
    basis, sources = assessment_basis(same_paper_revision=True)
    historical = next(item for item in basis.sources if item.role == "historical")
    new = next(item for item in basis.sources if item.role == "new")

    class WrongVersion(AssessmentFixtureTools):
        async def read_source_by_retrieval(self, retrieval_id):
            return self.sources[new.retrieval_id].document

    model = SequenceModel(
        [
            {"kind": "read_source_by_retrieval", "retrieval_id": historical.retrieval_id},
            inconclusive(),
        ]
    )
    result = await AssessmentController(model, WrongVersion(sources)).reassess(basis)
    assert result.observed_retrieval_ids == ()
    assert "assessment_source_read_failed" in model.seen[-1][-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["new", "historical"])
async def test_complete_judgement_cannot_skip_new_versions_or_the_original_premise(missing):
    basis, sources = assessment_basis()
    actions = [
        {"kind": "read_source_by_retrieval", "retrieval_id": item.retrieval_id}
        for item in basis.sources
    ]
    observed = {
        key: {"document": value.document.model_dump(mode="json")} for key, value in sources.items()
    }
    candidate = judgement(basis.model_dump(mode="json"), observed)
    selected = next(item for item in basis.sources if item.role == missing)
    candidate["rationale"][0]["citations"] = [
        item
        for item in candidate["rationale"][0]["citations"]
        if item["retrieval_id"] != selected.retrieval_id
    ]
    model = SequenceModel([*actions, candidate, inconclusive()])
    result = await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(basis)
    assert result.disposition == "inconclusive"
    expected = (
        "assessment_new_version_coverage_missing"
        if missing == "new"
        else "assessment_historical_premise_missing"
    )
    assert expected in model.seen[-1][-1]["content"]


@pytest.mark.asyncio
async def test_complete_judgement_cannot_append_an_uncited_rationale_paragraph():
    basis, sources = assessment_basis()
    actions = [
        {"kind": "read_source_by_retrieval", "retrieval_id": item.retrieval_id}
        for item in basis.sources
    ]
    observed = {
        key: {"document": value.document.model_dump(mode="json")} for key, value in sources.items()
    }
    candidate = judgement(basis.model_dump(mode="json"), observed)
    candidate["rationale"].append(
        {"text": "An additional substantive judgement has no evidence citation.", "citations": []}
    )
    model = SequenceModel([*actions, candidate, inconclusive()])
    result = await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(basis)
    assert result.disposition == "inconclusive"
    assert "assessment_uncited_judgement" in model.seen[-1][-1]["content"]


@pytest.mark.asyncio
async def test_old_quote_cannot_be_borrowed_for_a_new_retrieval_of_the_same_paper():
    basis, sources = assessment_basis(same_paper_revision=True)
    actions = [
        {"kind": "read_source_by_retrieval", "retrieval_id": item.retrieval_id}
        for item in basis.sources
    ]
    observed = {
        key: {"document": value.document.model_dump(mode="json")} for key, value in sources.items()
    }
    candidate = judgement(basis.model_dump(mode="json"), observed)
    new = next(item for item in basis.sources if item.role == "new")
    candidate["rationale"][0]["citations"][0]["retrieval_id"] = new.retrieval_id
    model = SequenceModel([*actions, candidate, inconclusive()])
    result = await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(basis)
    assert result.disposition == "inconclusive"
    assert "assessment_quotation_not_found" in model.seen[-1][-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem", ["missing_search", "question", "label_upgrade", "field_change", "duplicate_version"]
)
async def test_invalid_replacement_is_rejected_without_laundering_an_assessment_into_a_new_finding(
    problem,
):
    mode = (
        "ask"
        if problem in ("question", "label_upgrade")
        else "surprise"
        if problem == "field_change"
        else "explore"
    )
    basis, sources = assessment_basis(mode=mode, same_paper_revision=problem == "duplicate_version")
    actions = [
        {"kind": "read_source_by_retrieval", "retrieval_id": item.retrieval_id}
        for item in basis.sources
    ]
    if problem != "missing_search":
        actions += [
            {"kind": "search_literature", "query": "Synthetic checks", "purpose": purpose}
            for purpose in ("counterevidence", "prior_work")
        ]
    observed = {
        key: {"document": value.document.model_dump(mode="json")} for key, value in sources.items()
    }
    candidate = judgement(basis.model_dump(mode="json"), observed, revised=True)
    if problem == "question":
        candidate["revised_finding"]["question"] = "A different question?"
    elif problem == "label_upgrade":
        candidate["revised_finding"]["claims"][1]["label"] = "known"
    elif problem == "field_change":
        candidate["revised_finding"]["field_left"] = "A newly invented field"
    elif problem == "duplicate_version":
        candidate["selected_retrieval_ids"].append(basis.sources[0].retrieval_id)
    model = SequenceModel([*actions, candidate, inconclusive()])
    result = await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(basis)
    assert result.disposition == "inconclusive" and result.revised_result is None
    assert any(item.kind == "assessment_action_rejected" for item in result.events)


@pytest.mark.asyncio
async def test_discovered_literature_can_be_read_with_an_app_assigned_durable_handle():
    basis, sources = assessment_basis()
    fresh = snapshot(
        SourceDocument(
            source_id="fresh",
            title="Synthetic discovered paper",
            url="https://example.org/fresh",
            text="This newly discovered synthetic paper is a source snapshot.",
            content_kind="abstract",
            retrieved_at=RETRIEVED_AT,
            extraction_method="synthetic_fixture",
        )
    )
    tools = AssessmentFixtureTools(sources, fresh=fresh)
    model = SequenceModel(
        [
            {
                "kind": "search_literature",
                "query": "Fresh synthetic source",
                "purpose": "counterevidence",
            },
            {"kind": "read_literature_source", "source_id": "fresh"},
            inconclusive(),
        ]
    )
    result = await AssessmentController(model, tools).reassess(basis)
    assert result.observed_retrieval_ids == (fresh.retrieval_id,)
    assert tools.fresh_reads == ["fresh"] and result.search_purposes == ("counterevidence",)


@pytest.mark.asyncio
async def test_selection_rejects_two_alias_versions_beside_an_independent_paper():
    basis, sources = assessment_basis()
    old = next(item for item in basis.sources if item.role == "historical")
    new = next(item for item in basis.sources if item.role == "new")
    shared = SourceBibliography(doi="10.1234/shared")
    basis, sources = replace_bibliographies(
        basis, sources, {old.retrieval_id: shared, new.retrieval_id: shared}
    )
    actions = [
        {"kind": "read_source_by_retrieval", "retrieval_id": item.retrieval_id}
        for item in basis.sources
    ]
    actions += [
        {"kind": "search_literature", "query": "Synthetic scrutiny", "purpose": purpose}
        for purpose in ("counterevidence", "prior_work")
    ]
    observed = {
        key: {"document": value.document.model_dump(mode="json")} for key, value in sources.items()
    }
    candidate = judgement(basis.model_dump(mode="json"), observed, revised=True)
    candidate["selected_retrieval_ids"].append(basis.sources[0].retrieval_id)
    model = SequenceModel([*actions, candidate, inconclusive()])
    result = await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(basis)
    assert result.disposition == "inconclusive" and result.revised_result is None
    assert "assessment_ambiguous_paper_version" in model.seen[-1][-1]["content"]


@pytest.mark.asyncio
async def test_uncited_observed_alias_connector_cannot_be_hidden_by_current_version_selection():
    basis, sources = assessment_basis()
    right = next(
        item for item in basis.sources if item.role == "historical" and item.source_id == "right"
    )
    new = next(item for item in basis.sources if item.role == "new")
    basis, sources = replace_bibliographies(
        basis,
        sources,
        {
            right.retrieval_id: SourceBibliography(openalex_id="W987654"),
            new.retrieval_id: SourceBibliography(doi="10.1234/connected"),
        },
    )
    connector = snapshot(
        SourceDocument(
            source_id="connector",
            title="Synthetic alias connector",
            url="https://example.org/connector",
            text="A synthetic metadata connector links both supposedly independent "
            "paper identifiers.",
            content_kind="abstract",
            retrieved_at=RETRIEVED_AT,
            extraction_method="synthetic_fixture",
            bibliography=SourceBibliography(doi="10.1234/connected", openalex_id="W987654"),
        )
    )
    actions = [
        {"kind": "read_source_by_retrieval", "retrieval_id": item.retrieval_id}
        for item in basis.sources
    ]
    actions += [
        {
            "kind": "search_literature",
            "query": "Synthetic connector discovery",
            "purpose": "discovery",
        },
        {"kind": "read_literature_source", "source_id": "connector"},
    ]
    actions += [
        {"kind": "search_literature", "query": "Synthetic scrutiny", "purpose": purpose}
        for purpose in ("counterevidence", "prior_work")
    ]
    observed = {
        key: {"document": value.document.model_dump(mode="json")} for key, value in sources.items()
    }
    candidate = judgement(basis.model_dump(mode="json"), observed, revised=True)
    model = SequenceModel([*actions, candidate, inconclusive()])
    tools = AssessmentFixtureTools(sources, fresh=connector)
    result = await AssessmentController(model, tools).reassess(basis)
    assert result.disposition == "inconclusive" and result.revised_result is None
    assert connector.retrieval_id in result.observed_retrieval_ids
    assert "distinct_sources_required" in model.seen[-1][-1]["content"]


RETRIEVED_AT = datetime(2026, 10, 28, tzinfo=UTC)


@pytest.mark.asyncio
async def test_source_failure_does_not_manufacture_negative_scientific_evidence():
    basis, sources = assessment_basis()

    class Failing(AssessmentFixtureTools):
        async def read_source_by_retrieval(self, retrieval_id):
            raise RuntimeError("Private provider body that must not leak")

    action = {"kind": "read_source_by_retrieval", "retrieval_id": basis.sources[0].retrieval_id}
    result = await AssessmentController(
        SequenceModel([action, inconclusive()]), Failing(sources)
    ).reassess(basis)
    assert result.disposition == "inconclusive" and not result.quotes
    assert "Private provider body" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_partial_provider_coverage_remains_explicit_and_no_absence_claim_is_forced():
    basis, sources = assessment_basis()

    class Partial(AssessmentFixtureTools):
        async def search(self, query, purpose):
            return SearchResults(
                sources=(),
                provider_failures=(ProviderFailure(provider="openalex", code="access_denied"),),
            )

    model = SequenceModel(
        [
            {"kind": "search_literature", "query": "Synthetic search", "purpose": "prior_work"},
            inconclusive(),
        ]
    )
    result = await AssessmentController(model, Partial(sources)).reassess(basis)
    assert result.disposition == "inconclusive"
    assert "provider_failures" in model.seen[-1][-1]["content"]
    assert "access_denied" in model.seen[-1][-1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "invalid JSON",
        '{"kind":"publish"}',
        '{"kind":"search_literature","query":"bad\\u0000value","purpose":"prior_work"}',
    ],
)
async def test_invalid_or_privileged_actions_stop_with_bounded_repairs(payload):
    basis, sources = assessment_basis()
    model = SequenceModel([payload] * 3)
    result = await AssessmentController(model, AssessmentFixtureTools(sources)).reassess(basis)
    assert result.status == "failed" and result.error_code == "repair_limit"
    assert result.disposition == "inconclusive" and result.usage.model_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [ModelError("model_access_denied"), RuntimeError("private model response")]
)
async def test_model_failure_is_static_and_retains_unknown_usage(failure):
    basis, sources = assessment_basis()
    result = await AssessmentController(
        SequenceModel([failure]), AssessmentFixtureTools(sources)
    ).reassess(basis)
    assert result.status == "failed" and result.disposition == "inconclusive"
    assert result.error_code in ("model_access_denied", "model_failed")
    assert result.usage.calls_without_usage == 1
    assert "private model response" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_context_call_and_model_time_limits_leave_the_prior_finding_untouched():
    basis, sources = assessment_basis()
    model = SequenceModel([])
    result = await AssessmentController(
        model, AssessmentFixtureTools(sources), AssessmentLimits(max_context_characters=1000)
    ).reassess(basis)
    assert (
        result.status == "limit_reached" and result.error_code == "context_limit" and not model.seen
    )
    read = {"kind": "read_source_by_retrieval", "retrieval_id": basis.sources[0].retrieval_id}
    result = await AssessmentController(
        SequenceModel([read]), AssessmentFixtureTools(sources), AssessmentLimits(max_model_calls=1)
    ).reassess(basis)
    assert result.status == "limit_reached" and result.revised_result is None

    class Waiting:
        async def complete(self, messages, *, max_output_tokens):
            await asyncio.Event().wait()

    result = await AssessmentController(
        Waiting(), AssessmentFixtureTools(sources), AssessmentLimits(model_timeout_seconds=0.01)
    ).reassess(basis)
    assert (
        result.error_code == "model_timeout_outcome_unknown"
        and result.usage.calls_without_usage == 1
    )


@pytest.mark.asyncio
async def test_worker_stop_control_is_not_swallowed_as_an_inconclusive_tool_failure():
    class StopWork(BaseException):
        pass

    basis, sources = assessment_basis()

    class Stopped(AssessmentFixtureTools):
        async def read_source_by_retrieval(self, retrieval_id):
            raise StopWork()

    read = {"kind": "read_source_by_retrieval", "retrieval_id": basis.sources[0].retrieval_id}
    with pytest.raises(StopWork):
        await AssessmentController(SequenceModel([read]), Stopped(sources)).reassess(basis)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_model_calls", 13),
        ("max_actions", 0),
        ("max_repairs", True),
        ("max_sources", 201),
        ("max_output_tokens", 4097),
        ("tool_timeout_seconds", float("inf")),
    ],
)
def test_reassessment_limits_remain_within_the_shared_execution_boundary(field, value):
    with pytest.raises(ValueError):
        replace(AssessmentLimits(), **{field: value})


def test_basis_rejects_forged_prior_quotes_or_relabelled_new_versions():
    basis, _ = assessment_basis()
    raw = basis.model_dump(mode="json")
    raw["prior_quotes"][0]["quote"] = "A forged synthetic prior quotation."
    with pytest.raises(ValidationError):
        AssessmentBasis.model_validate_json(json.dumps(raw))
    raw = basis.model_dump(mode="json")
    raw["sources"][-1]["version_id"] = raw["sources"][0]["version_id"]
    with pytest.raises(ValidationError):
        AssessmentBasis.model_validate_json(json.dumps(raw))


def test_durable_read_audit_superset_cannot_hide_alias_context_or_forge_quote_offsets():
    basis, sources = assessment_basis()
    action = ConcludeAssessmentAction.model_validate_json(
        json.dumps(
            judgement(
                basis.model_dump(mode="json"),
                {
                    key: {"document": value.document.model_dump(mode="json")}
                    for key, value in sources.items()
                },
            )
        )
    )
    quotes = verify_assessment_quotes(action.rationale, sources)
    result = AssessmentResult(
        status="completed",
        disposition="unchanged",
        rationale=action.rationale,
        limitations=action.limitations,
        observed_retrieval_ids=tuple(
            item.retrieval_id
            for item in basis.sources
            if item.role == "new" or item == basis.sources[0]
        ),
        quotes=quotes,
        usage=Usage(),
    )
    validate_assessment_result(result, basis, sources)
    corrupt = result.model_copy(
        update={
            "quotes": (
                quotes[0].model_copy(update={"start": 1, "end": quotes[0].end + 1}),
                *quotes[1:],
            )
        }
    )
    with pytest.raises(ValueError, match="quote_integrity"):
        validate_assessment_result(corrupt, basis, sources)
