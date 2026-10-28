"""Model-selected exact excerpts and metadata-only indexes; synthetic fixtures only."""

import json

import pytest
from test_full_text_propagation import FIRST, TEXT, WHEN, window
from test_research_controller import RIGHT_QUOTE, SequenceModel, bridge, source
from test_research_modes import fixture_answer

from polymath.full_text_contracts import FullTextHeading, FullTextIndex
from polymath.research import ModelReply, ResearchController
from polymath.research.controller import (
    _complete_identity_context,
    _verify_answer_evidence,
    _verify_evidence,
)
from polymath.research.models import (
    AnswerFinding,
    ArxivFullTextLocator,
    BridgeFinding,
    FullTextAvailability,
    SearchResults,
    SourceBibliography,
    SourceReference,
)

GOAL = "What is the scope of this synthetic comparison?"
INSUFFICIENT = {
    "kind": "conclude",
    "conclusion": {
        "outcome": "insufficient_evidence",
        "reason": "Synthetic coverage is incomplete.",
    },
}


class FullTextTools:
    def __init__(self, *, version=2, extra=()):
        self.document = window()
        self.reference = SourceReference(
            source_id="arxiv:2401.01234",
            title=self.document.title,
            url="https://arxiv.org/abs/2401.01234",
            bibliography=SourceBibliography(arxiv_id="2401.01234", arxiv_version=version),
        )
        self.availability = FullTextAvailability(
            source_id=self.reference.source_id,
            status="candidate",
            locator=ArxivFullTextLocator(arxiv_id="2401.01234", arxiv_version=version),
        )
        self.index = FullTextIndex(
            arxiv_id="2401.01234",
            arxiv_version=2,
            url="https://arxiv.org/html/2401.01234v2",
            snapshot_id=self.document.coverage.snapshot_id,
            snapshot_sha256=self.document.coverage.snapshot_sha256,
            total_chars=len(TEXT),
            raw_html_sha256="a" * 64,
            retrieved_at=WHEN,
            headings=(FullTextHeading(title=FIRST, level=1, start=0, end=len(FIRST)),),
            limitations=("Synthetic HTML excerpts omit figures and do not verify equations.",),
            source=self.reference,
        )
        self.extra = extra
        self.opens, self.ranges, self.reads = [], [], []

    async def search(self, query, purpose):
        right = source("right", RIGHT_QUOTE)
        return SearchResults(
            sources=(
                self.reference,
                SourceReference(
                    source_id=right.source_id,
                    title=right.title,
                    url=right.url,
                    bibliography=right.bibliography,
                ),
            ),
            full_text=(self.availability,),
        )

    async def open_full_text(self, source_id):
        self.opens.append(source_id)
        return self.index

    async def read_full_text_window(self, snapshot_id, start, end):
        self.ranges.append((snapshot_id, start, end))
        return self.document

    async def read(self, source_id):
        self.reads.append(source_id)
        return source("right", RIGHT_QUOTE)

    async def identity_context(self):
        return self.extra


def finding(document, *, mode="explore"):
    action = fixture_answer(GOAL) if mode == "ask" else bridge()
    if mode == "ask":
        for claim in action["conclusion"]["claims"]:
            for quote in claim["evidence"]:
                is_left = quote["source_id"] in ("left", document.source_id)
                quote["quote"] = document.text if is_left else RIGHT_QUOTE
                if is_left:
                    quote["source_id"] = document.source_id
    else:
        action["conclusion"]["evidence"][0].update(
            source_id=document.source_id, quote=document.text
        )
    return action


def search(purpose="discovery"):
    return {"kind": "search_literature", "query": "synthetic excerpt", "purpose": purpose}


def open_action(tools):
    return {"kind": "open_full_text", "source_id": tools.reference.source_id}


def range_action(tools):
    return {
        "kind": "read_full_text_window",
        "snapshot_id": tools.index.snapshot_id,
        "start": 0,
        "end": len(FIRST),
    }


def positive_actions(tools, *, conclusion=None):
    return [
        search(),
        open_action(tools),
        range_action(tools),
        {"kind": "read_source", "source_id": "right"},
        search("counterevidence"),
        search("prior_work"),
        conclusion or finding(tools.document),
    ]


class AdaptiveFullTextModel:
    """Select a range from the observed heading index, without precomputed IDs."""

    def __init__(self, mode):
        self.mode, self.seen, self.document = mode, [], None

    async def complete(self, messages, *, max_output_tokens):
        observation = json.loads(messages[-1]["content"]).get("observation")
        self.seen.append(observation)
        if observation is None and self.mode == "surprise":
            action = {
                "kind": "select_fields",
                "fields": ["Information theory", "Biology"],
                "rationale": "Compare bounded synthetic representations.",
            }
        elif observation is None or observation.get("kind") == "fields_selected":
            action = search()
        elif observation.get("kind") == "search_results" and observation["purpose"] == "discovery":
            action = {
                "kind": "open_full_text",
                "source_id": observation["full_text"][0]["source_id"],
            }
        elif observation.get("kind") == "full_text_index":
            heading = observation["headings"][0]
            action = {
                "kind": "read_full_text_window",
                "snapshot_id": observation["snapshot_id"],
                "start": heading["start"],
                "end": heading["end"],
            }
        elif observation.get("read_scope") == "full_text_excerpt":
            self.document = observation
            action = {"kind": "read_source", "source_id": "right"}
        elif observation.get("kind") == "source_text":
            action = search("counterevidence")
        elif observation.get("purpose") == "counterevidence":
            action = search("prior_work")
        else:
            from polymath.research.models import SourceDocument

            document = SourceDocument.model_validate_json(
                json.dumps(
                    {
                        key: value
                        for key, value in self.document.items()
                        if key not in {"kind", "content_sha256", "read_scope", "limitations"}
                    }
                )
            )
            action = finding(document, mode=self.mode)
        return ModelReply(json.dumps(action), 4, 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["explore", "ask", "surprise"])
async def test_model_selects_exact_heading_range_and_retains_excerpt_scope_in_all_modes(mode):
    tools, model = FullTextTools(), AdaptiveFullTextModel(mode)
    result = await ResearchController(model, tools).investigate(GOAL, mode=mode)
    assert result.status == ("answer" if mode == "ask" else "bridge")
    assert tools.ranges == [(tools.index.snapshot_id, 0, len(FIRST))]
    assert tools.opens == [tools.reference.source_id] and tools.reads == ["right"]
    assert result.sources[0].coverage == tools.document.coverage
    assert result.quotes[0].quote == FIRST
    assert any(event.kind == "full_text_opened" for event in result.events)
    assert any(
        event.kind == "full_text_window_read" and "not a whole-paper read" in event.summary
        for event in result.events
    )
    index = next(item for item in model.seen if item and item.get("kind") == "full_text_index")
    assert "text" not in index


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["open", "unopened", "past_end"])
async def test_undiscovered_unopened_and_out_of_bounds_actions_never_call_the_read_tool(kind):
    tools = FullTextTools()
    if kind == "open":
        actions = [open_action(tools)]
    elif kind == "unopened":
        actions = [range_action(tools)]
    else:
        actions = [search(), open_action(tools), {**range_action(tools), "end": len(TEXT) + 1}]
    result = await ResearchController(SequenceModel([*actions, INSUFFICIENT]), tools).investigate(
        GOAL
    )
    assert result.status == "insufficient_evidence" and not tools.ranges and not result.sources
    assert any(event.kind == "action_rejected" for event in result.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["version", "reference", "coverage", "title", "retrieved_at"])
async def test_index_and_window_provenance_conflicts_fail_without_authorizing_evidence(change):
    tools = FullTextTools()
    if change == "version":
        tools.availability = tools.availability.model_copy(
            update={"locator": ArxivFullTextLocator(arxiv_id="2401.01234", arxiv_version=1)}
        )
    elif change == "reference":
        tools.index = tools.index.model_copy(
            update={"source": tools.reference.model_copy(update={"title": "Another private title"})}
        )
    elif change == "coverage":
        tools.document = tools.document.model_copy(
            update={
                "coverage": tools.document.coverage.model_copy(update={"snapshot_sha256": "b" * 64})
            }
        )
    elif change == "title":
        tools.document = tools.document.model_copy(update={"title": "Wrong saved title"})
    else:
        from datetime import timedelta

        tools.document = tools.document.model_copy(
            update={"retrieved_at": WHEN + timedelta(days=1)}
        )
    result = await ResearchController(
        SequenceModel([search(), open_action(tools), range_action(tools), INSUFFICIENT]), tools
    ).investigate(GOAL)
    assert result.status == "insufficient_evidence" and not result.sources
    assert any(event.kind == "tool_failed" for event in result.events)


@pytest.mark.asyncio
async def test_opening_only_an_index_never_authorizes_a_heading_quotation():
    tools = FullTextTools()
    conclusion = finding(tools.document)
    result = await ResearchController(
        SequenceModel(
            [
                search(),
                open_action(tools),
                {"kind": "read_source", "source_id": "right"},
                search("counterevidence"),
                search("prior_work"),
                conclusion,
                INSUFFICIENT,
            ]
        ),
        tools,
    ).investigate(GOAL)
    assert result.status == "insufficient_evidence" and not tools.ranges
    assert [item.source_id for item in result.sources] == ["right"]


@pytest.mark.asyncio
async def test_unversioned_frozen_locator_may_resolve_to_one_explicit_version():
    tools = FullTextTools(version=None)
    result = await ResearchController(SequenceModel(positive_actions(tools)), tools).investigate(
        GOAL
    )
    assert result.status == "bridge" and result.sources[0].coverage.arxiv_version == 2


@pytest.mark.asyncio
async def test_complete_supplemental_alias_context_can_refute_apparent_independence():
    connector = SourceReference(
        source_id="fixture:connector",
        title="Synthetic identity only",
        url="https://example.org/connector",
        bibliography=SourceBibliography(arxiv_id="2401.01234", doi="10.1234/synthetic"),
    )
    tools = FullTextTools(extra=(connector,))
    original_read = tools.read

    async def read(source_id):
        return (await original_read(source_id)).model_copy(
            update={"bibliography": SourceBibliography(doi="10.1234/synthetic")}
        )

    # Freeze the same DOI metadata in discovery and reading.
    original_search = tools.search

    async def discovery(query, purpose):
        found = await original_search(query, purpose)
        return found.model_copy(
            update={
                "sources": (
                    found.sources[0],
                    found.sources[1].model_copy(
                        update={"bibliography": SourceBibliography(doi="10.1234/synthetic")}
                    ),
                )
            }
        )

    tools.read, tools.search = read, discovery
    result = await ResearchController(
        SequenceModel([*positive_actions(tools), INSUFFICIENT]), tools
    ).investigate(GOAL)
    assert result.status == "insufficient_evidence"
    assert len(result.sources) == 2 and connector.source_id not in {
        item.source_id for item in result.sources
    }
    assert any(event.kind == "action_rejected" for event in result.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["open_full_text", "read_full_text_window", "identity_context"])
async def test_worker_stop_signals_propagate_before_any_further_model_call(method):
    class StopWorker(BaseException):
        pass

    tools = FullTextTools()

    async def stop(*args):
        raise StopWorker()

    setattr(tools, method, stop)
    model = SequenceModel(positive_actions(tools))
    with pytest.raises(StopWorker):
        await ResearchController(model, tools).investigate(GOAL)
    assert (
        model.calls
        == {"open_full_text": 2, "read_full_text_window": 3, "identity_context": 7}[method]
    )


def test_context_deduplicates_identical_alias_sets_but_never_drops_distinct_connectors():
    tools = FullTextTools()
    reference = tools.reference
    assert _complete_identity_context((reference,), (), (reference,) * 255) == (reference,)
    extra = tuple(
        SourceReference(
            source_id=f"fixture:{index}",
            title="Synthetic identity",
            url=f"https://example.org/{index}",
        )
        for index in range(256)
    )
    with pytest.raises(ValueError, match="identity_context_limit"):
        _complete_identity_context((reference,), (), extra)


@pytest.mark.parametrize("mode", ["explore", "ask"])
def test_positive_verification_rejects_mixed_paper_snapshots_before_persistence(mode):
    first = window()
    other = window(second=True, snapshot_id="92222222-2222-4222-8222-222222222222")
    action = finding(first, mode=mode)["conclusion"]
    extra = {
        "source_id": other.source_id,
        "quote": other.text,
        "supports": "Another version of the same synthetic paper is not primary evidence.",
    }
    if mode == "ask":
        action["claims"][0]["evidence"].append(extra)
        proposed, verify = AnswerFinding.model_validate(action), _verify_answer_evidence
    else:
        action["evidence"].append({**extra, "side": "left"})
        proposed, verify = BridgeFinding.model_validate(action), _verify_evidence
    documents = {item.source_id: item for item in (first, other, source("right", RIGHT_QUOTE))}
    with pytest.raises(ValueError, match="selected_paper_version_conflict"):
        verify(proposed, documents, {"counterevidence", "prior_work"})
