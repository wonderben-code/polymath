"""Synthetic assessment index/window actions; no native or model API calls."""

import json

import pytest
from test_assessment_controller import (
    AssessmentFixtureTools,
    SequenceModel,
    assessment_basis,
    inconclusive,
    judgement,
    replace_bibliographies,
    snapshot,
)
from test_evidence import result_for
from test_full_text_propagation import TEXT, window

from polymath.assessment_controller import (
    EXCERPT_LIMITATION,
    AssessmentController,
    AssessmentLimits,
)
from polymath.full_text_contracts import FullTextIndex
from polymath.research.models import (
    ArxivFullTextLocator,
    EvidenceQuote,
    FullTextAvailability,
    SearchResults,
    SourceBibliography,
    SourceReference,
)


class FullTextTools(AssessmentFixtureTools):
    def __init__(self, sources, *, availability="candidate", extra=()):
        super().__init__(sources)
        self.first, self.second = window(), window(second=True)
        self.reference = SourceReference(
            source_id="arxiv:2401.01234v2",
            title=self.first.title,
            url="https://arxiv.org/abs/2401.01234v2",
            bibliography=self.first.bibliography,
        )
        self.index = FullTextIndex(
            source=self.reference,
            arxiv_id="2401.01234",
            arxiv_version=2,
            snapshot_id=self.first.coverage.snapshot_id,
            snapshot_sha256=self.first.coverage.snapshot_sha256,
            url=self.first.url,
            raw_html_sha256="c" * 64,
            retrieved_at=self.first.retrieved_at,
            total_chars=len(TEXT),
            headings=(),
            limitations=("Synthetic HTML; equations and figures may be omitted.",),
        )
        self.availability, self.extra = availability, extra
        self.opens, self.windows = [], []

    async def search(self, query, purpose):
        self.searches.append((query, purpose))
        availability = (
            ()
            if self.availability is None
            else (
                FullTextAvailability(
                    source_id=self.reference.source_id,
                    status=self.availability,
                    locator=ArxivFullTextLocator(arxiv_id="2401.01234", arxiv_version=2)
                    if self.availability == "candidate"
                    else None,
                ),
            )
        )
        return SearchResults(sources=(self.reference,), full_text=availability)

    async def open_full_text(self, source_id):
        self.opens.append(source_id)
        return self.index

    async def read_full_text_window(self, snapshot_id, start, end):
        self.windows.append((snapshot_id, start, end))
        document = next(
            doc
            for doc in (self.first, self.second)
            if (doc.coverage.start, doc.coverage.end) == (start, end)
        )
        return snapshot(document)

    async def identity_context(self):
        return self.extra


def search(purpose="discovery"):
    return {"kind": "search_literature", "query": "Synthetic bounded full text", "purpose": purpose}


def open_action(tools):
    return {"kind": "open_full_text", "source_id": tools.reference.source_id}


def read_action(document):
    return {
        "kind": "read_full_text_window",
        "snapshot_id": document.coverage.snapshot_id,
        "start": document.coverage.start,
        "end": document.coverage.end,
    }


def observations(model):
    return [
        json.loads(item["content"])["observation"]
        for item in model.seen[-1]
        if item["role"] == "user" and "observation" in json.loads(item["content"])
    ]


@pytest.mark.asyncio
async def test_opened_index_is_metadata_and_never_a_source_read():
    basis, sources = assessment_basis()
    tools = FullTextTools(sources)
    model = SequenceModel([search(), open_action(tools), inconclusive()])
    result = await AssessmentController(model, tools).reassess(basis)
    assert result.status == "completed" and result.disposition == "inconclusive"
    assert result.observed_retrieval_ids == result.quotes == ()
    assert tools.opens == [tools.reference.source_id] and tools.windows == []
    index = next(item for item in observations(model) if item.get("kind") == "full_text_index")
    assert "no source text" in index["scope"]
    assert "text" not in index and index["total_chars"] == len(TEXT)


@pytest.mark.asyncio
async def test_exact_windows_have_durable_handles_and_explicit_excerpt_limitations():
    basis, sources = assessment_basis()
    tools = FullTextTools(sources)
    model = SequenceModel(
        [
            search(),
            open_action(tools),
            read_action(tools.first),
            read_action(tools.second),
            inconclusive(),
        ]
    )
    result = await AssessmentController(model, tools).reassess(basis)
    assert len(result.observed_retrieval_ids) == 2
    assert EXCERPT_LIMITATION in result.limitations
    reads = [item for item in observations(model) if item.get("role") == "full_text_excerpt"]
    assert len(reads) == 2
    assert all(
        item["document"]["coverage"]["snapshot_id"] == tools.index.snapshot_id for item in reads
    )
    assert all(item["retrieval_id"] in result.observed_retrieval_ids for item in reads)
    assert all(EXCERPT_LIMITATION in item["limitations"] for item in reads)
    assert len([event for event in result.events if event.kind == "full_text_excerpt_read"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("availability", [None, "ambiguous"])
async def test_open_requires_successful_current_unambiguous_candidate(availability):
    basis, sources = assessment_basis()
    tools = FullTextTools(sources, availability=availability)
    model = SequenceModel([search(), open_action(tools), inconclusive()])
    result = await AssessmentController(model, tools).reassess(basis)
    assert result.disposition == "inconclusive" and not result.observed_retrieval_ids
    assert tools.opens == tools.windows == []
    assert any(
        item.get("error") == "assessment_full_text_not_discovered" for item in observations(model)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["undiscovered", "unopened", "out_of_range"])
async def test_foreign_or_unopened_actions_are_rejected_before_tool_access(problem):
    basis, sources = assessment_basis()
    tools = FullTextTools(sources)
    if problem == "undiscovered":
        actions = [open_action(tools)]
    elif problem == "unopened":
        actions = [read_action(tools.first)]
    else:
        actions = [search(), open_action(tools), {**read_action(tools.first), "end": len(TEXT) + 1}]
    model = SequenceModel([*actions, inconclusive()])
    result = await AssessmentController(model, tools).reassess(basis)
    assert result.disposition == "inconclusive" and not result.observed_retrieval_ids
    assert tools.windows == []
    if problem == "undiscovered":
        assert tools.opens == []


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["source", "version", "dict"])
async def test_index_must_match_frozen_discovery_and_version(problem):
    basis, sources = assessment_basis()
    tools = FullTextTools(sources)
    if problem == "source":
        tools.index = tools.index.model_copy(
            update={
                "source": tools.reference.model_copy(
                    update={"title": "Different synthetic metadata"}
                )
            }
        )
    elif problem == "version":
        tools.index = tools.index.model_copy(update={"arxiv_version": 3})
    else:
        tools.index = tools.index.model_dump(mode="json")
    model = SequenceModel([search(), open_action(tools), inconclusive()])
    result = await AssessmentController(model, tools).reassess(basis)
    assert not result.observed_retrieval_ids
    assert any(
        item.get("error") == "assessment_full_text_open_failed" for item in observations(model)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["title", "coverage", "not_snapshot"])
async def test_window_must_match_exact_opened_index_metadata_range_and_handles(problem):
    basis, sources = assessment_basis()
    tools = FullTextTools(sources)
    original_read = tools.read_full_text_window

    async def forged(snapshot_id, start, end):
        observed = await original_read(snapshot_id, start, end)
        if problem == "not_snapshot":
            return observed.document
        doc = observed.document
        document = doc.model_copy(
            update=(
                {"title": "Forged metadata"}
                if problem == "title"
                else {"coverage": doc.coverage.model_copy(update={"snapshot_sha256": "f" * 64})}
            )
        )
        return observed.model_copy(update={"document": document})

    tools.read_full_text_window = forged
    model = SequenceModel([search(), open_action(tools), read_action(tools.first), inconclusive()])
    result = await AssessmentController(model, tools).reassess(basis)
    assert result.observed_retrieval_ids == ()
    assert any(
        item.get("error") == "assessment_full_text_read_failed" for item in observations(model)
    )


@pytest.mark.asyncio
async def test_source_cap_blocks_new_window_before_tool_and_allows_identical_replay():
    basis, sources = assessment_basis()
    tools = FullTextTools(sources)
    model = SequenceModel(
        [
            search(),
            open_action(tools),
            read_action(tools.first),
            read_action(tools.first),
            read_action(tools.second),
            inconclusive(),
        ]
    )
    result = await AssessmentController(model, tools, AssessmentLimits(max_sources=1)).reassess(
        basis
    )
    assert result.disposition == "inconclusive" and len(result.observed_retrieval_ids) == 1
    assert tools.windows == [(tools.index.snapshot_id, 0, len(tools.first.text))] * 2


@pytest.mark.asyncio
async def test_model_action_ceiling_includes_index_and_window_and_reports_partial_scope():
    basis, sources = assessment_basis()
    tools = FullTextTools(sources)
    model = SequenceModel([search(), open_action(tools), read_action(tools.first), inconclusive()])
    result = await AssessmentController(model, tools, AssessmentLimits(max_actions=3)).reassess(
        basis
    )
    assert result.status == "limit_reached" and result.usage.model_calls == 3
    assert len(result.observed_retrieval_ids) == 1 and EXCERPT_LIMITATION in result.limitations


@pytest.mark.asyncio
async def test_multiple_windows_can_be_current_in_revised_finding_without_becoming_two_papers():
    basis, sources = assessment_basis()
    tools = FullTextTools(sources)
    first, second = snapshot(tools.first), snapshot(tools.second)
    all_observed = {**sources, first.retrieval_id: first, second.retrieval_id: second}
    right = next(item for item in sources.values() if item.document.source_id == "right")
    initial = result_for(first.document, right.document)
    finding = initial.finding.model_copy(
        update={
            "evidence": [
                *initial.finding.evidence,
                EvidenceQuote(
                    source_id=second.document.source_id,
                    side="left",
                    quote=second.document.text[2:],
                    supports="An exact synthetic second-window caveat.",
                ),
            ]
        }
    )
    action = judgement(
        basis.model_dump(mode="json"),
        {key: item.model_dump(mode="json") for key, item in all_observed.items()},
    )
    action.update(
        disposition="revised",
        selected_retrieval_ids=[first.retrieval_id, second.retrieval_id, right.retrieval_id],
        revised_finding=finding.model_dump(mode="json"),
    )
    action["rationale"][0]["citations"].extend(
        [
            {
                "retrieval_id": item.retrieval_id,
                "quote": item.document.text,
                "supports": "An exact synthetic full-text excerpt.",
            }
            for item in (first, second)
        ]
    )
    model = SequenceModel(
        [
            search(),
            open_action(tools),
            read_action(tools.first),
            read_action(tools.second),
            *(
                {"kind": "read_source_by_retrieval", "retrieval_id": ref.retrieval_id}
                for ref in basis.sources
            ),
            search("counterevidence"),
            search("prior_work"),
            action,
        ]
    )
    result = await AssessmentController(model, tools).reassess(basis)
    assert result.status == "completed" and result.disposition == "revised"
    assert len([doc for doc in result.revised_result.sources if doc.coverage]) == 2
    assert EXCERPT_LIMITATION in result.limitations
    assert len([quote for quote in result.quotes if quote.coverage]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", ["unavailable", "overflow"])
async def test_complete_identity_context_is_required_and_never_silently_truncated(extra):
    basis, sources = assessment_basis()
    tools = FullTextTools(sources)

    async def identity_context():
        if extra == "unavailable":
            raise RuntimeError("Synthetic private provider detail must not be echoed")
        return tuple(
            SourceReference(
                source_id=f"extra-{index}",
                title="Synthetic connector",
                url=f"https://example.org/extra/{index}",
            )
            for index in range(257)
        )

    tools.identity_context = identity_context
    model = SequenceModel([inconclusive()])
    result = await AssessmentController(model, tools, AssessmentLimits(max_repairs=0)).reassess(
        basis
    )
    assert result.status == "failed" and result.error_code == "repair_limit"
    assert "private provider detail" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_identity_only_connector_changes_independence_without_becoming_a_read():
    basis, sources = assessment_basis()
    new = next(ref for ref in basis.sources if ref.role == "new")
    right = next(ref for ref in basis.sources if ref.source_id == "right")
    basis, sources = replace_bibliographies(
        basis,
        sources,
        {
            new.retrieval_id: SourceBibliography(doi="10.1234/synthetic"),
            right.retrieval_id: SourceBibliography(arxiv_id="2401.01234"),
        },
    )
    connector = SourceReference(
        source_id="identity-only-index",
        title="Synthetic index identity connector",
        url="https://example.org/identity-only",
        bibliography=SourceBibliography(doi="10.1234/synthetic", arxiv_id="2401.01234"),
    )
    tools = FullTextTools(sources, extra=(connector,))
    candidate = judgement(
        basis.model_dump(mode="json"),
        {key: item.model_dump(mode="json") for key, item in sources.items()},
        revised=True,
    )
    actions = [
        {"kind": "read_source_by_retrieval", "retrieval_id": ref.retrieval_id}
        for ref in basis.sources
    ]
    model = SequenceModel(
        [*actions, search("counterevidence"), search("prior_work"), candidate, inconclusive()]
    )
    result = await AssessmentController(model, tools).reassess(basis)
    assert result.disposition == "inconclusive" and result.revised_result is None
    assert len(result.observed_retrieval_ids) == len(sources)
    assert any(item.get("error") == "distinct_sources_required" for item in observations(model))
    assert not any(
        item.get("document", {}).get("source_id") == connector.source_id
        for item in observations(model)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("include_scope", [False, True])
async def test_excerpt_scope_is_retained_at_the_limitations_bound_without_dropping_caveats(
    include_scope,
):
    basis, sources = assessment_basis()
    tools = FullTextTools(sources)
    conclusion = inconclusive()
    conclusion["limitations"] = [f"Synthetic retained caveat {index}." for index in range(7)] + [
        EXCERPT_LIMITATION if include_scope else "An eighth synthetic caveat requiring a repair."
    ]
    model = SequenceModel(
        [
            search(),
            open_action(tools),
            read_action(tools.first),
            conclusion,
            *([] if include_scope else [inconclusive()]),
        ]
    )
    result = await AssessmentController(model, tools).reassess(basis)
    assert result.status == "completed" and EXCERPT_LIMITATION in result.limitations
    if include_scope:
        assert result.limitations == tuple(conclusion["limitations"])
    else:
        assert any(
            item.get("error") == "assessment_excerpt_scope_required" for item in observations(model)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["open", "read", "identity"])
async def test_worker_stop_control_propagates_across_fulltext_tools(stage):
    class StopControl(BaseException):
        pass

    async def stopped(*args):
        raise StopControl()

    basis, sources = assessment_basis()
    tools = FullTextTools(sources)
    if stage == "open":
        tools.open_full_text = stopped
        actions = [search(), open_action(tools)]
    elif stage == "read":
        tools.read_full_text_window = stopped
        actions = [search(), open_action(tools), read_action(tools.first)]
    else:
        tools.identity_context = stopped
        actions = [inconclusive()]
    with pytest.raises(StopControl):
        await AssessmentController(SequenceModel(actions), tools).reassess(basis)
