"""Synthetic model-memory checks; saved hypotheses never become primary evidence."""

import hashlib
import json

import pytest
from pydantic import ValidationError
from test_research_controller import LEFT_QUOTE, RIGHT_QUOTE, Tools, bridge, source

from polymath.research import ModelReply, ResearchController, ResearchLimits
from polymath.research.models import (
    ACTION_ADAPTER,
    BridgeFinding,
    MemoryFinding,
    MemoryFindingReference,
    MemorySearchResults,
    MemorySourceReference,
    ResearchResult,
    SourceReference,
)

FIRST_RESULT = "00000000-0000-4000-8000-000000000001"
SECOND_RESULT = "00000000-0000-4000-8000-000000000002"
UNKNOWN_RESULT = "00000000-0000-4000-8000-000000000099"
FIRST_RUN = "10000000-0000-4000-8000-000000000001"
SECOND_RUN = "10000000-0000-4000-8000-000000000002"
LEFT_RETRIEVAL = "20000000-0000-4000-8000-000000000001"
RIGHT_RETRIEVAL = "20000000-0000-4000-8000-000000000002"
OTHER_LEFT_RETRIEVAL = "20000000-0000-4000-8000-000000000003"
OTHER_RIGHT_RETRIEVAL = "20000000-0000-4000-8000-000000000004"


def insufficient(reason="The synthetic evidence remains insufficient."):
    return {
        "kind": "conclude",
        "conclusion": {"outcome": "insufficient_evidence", "reason": reason},
    }


def search_memory():
    return {"kind": "search_memory", "query": "fixture redundancy"}


def read_finding(result_id=FIRST_RESULT):
    return {"kind": "read_memory_finding", "result_id": result_id}


def read_memory_source(result_id=FIRST_RESULT, retrieval_id=LEFT_RETRIEVAL):
    return {
        "kind": "read_memory_source",
        "result_id": result_id,
        "retrieval_id": retrieval_id,
    }


def required_searches():
    return [
        {"kind": "search_literature", "query": "fixture objections", "purpose": "counterevidence"},
        {"kind": "search_literature", "query": "fixture connections", "purpose": "prior_work"},
    ]


def memory_finding(result_id, run_id, left_id, right_id, retrieval_ids):
    finding = bridge()["conclusion"]
    finding["evidence"][0]["source_id"] = left_id
    finding["evidence"][1]["source_id"] = right_id
    finding["title"] = f"Synthetic saved hypothesis {left_id}"
    finding = BridgeFinding.model_validate(finding)
    documents = (source(left_id, LEFT_QUOTE), source(right_id, RIGHT_QUOTE))
    reference = MemoryFindingReference(
        result_id=result_id,
        run_id=run_id,
        content_sha256=hashlib.sha256(finding.model_dump_json().encode()).hexdigest(),
        title=finding.title,
        summary="Synthetic prior model hypothesis; its uncertainty is retained.",
        outcome="bridge",
    )
    saved = MemoryFinding(
        reference=reference,
        finding=finding,
        sources=tuple(
            MemorySourceReference(
                retrieval_id=retrieval_id,
                source=SourceReference(
                    source_id=document.source_id, title=document.title, url=document.url
                ),
                content_sha256=document.content_sha256,
                content_kind=document.content_kind,
            )
            for retrieval_id, document in zip(retrieval_ids, documents, strict=True)
        ),
    )
    return saved, dict(zip(retrieval_ids, documents, strict=True))


class MemoryFixture:
    """Explicitly synthetic source snapshots, never network-backed research."""

    def __init__(self):
        first, first_sources = memory_finding(
            FIRST_RESULT, FIRST_RUN, "left", "right", (LEFT_RETRIEVAL, RIGHT_RETRIEVAL)
        )
        second, second_sources = memory_finding(
            SECOND_RESULT,
            SECOND_RUN,
            "other-left",
            "other-right",
            (OTHER_LEFT_RETRIEVAL, OTHER_RIGHT_RETRIEVAL),
        )
        self.findings = {FIRST_RESULT: first, SECOND_RESULT: second}
        self.documents = {FIRST_RESULT: first_sources, SECOND_RESULT: second_sources}
        self.searches = []
        self.finding_reads = []
        self.source_reads = []

    async def search(self, query):
        self.searches.append(query)
        return MemorySearchResults(
            findings=tuple(item.reference for item in self.findings.values()), truncated=False
        )

    async def read_finding(self, result_id):
        self.finding_reads.append(result_id)
        return self.findings[result_id]

    async def read_source(self, result_id, retrieval_id):
        self.source_reads.append((result_id, retrieval_id))
        return self.documents[result_id][retrieval_id]


class ObservingSequence:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.observations = []
        self.calls = 0

    async def complete(self, messages, *, max_output_tokens):
        self.calls += 1
        self.observations.append(json.loads(messages[-1]["content"]))
        return ModelReply(json.dumps(next(self.actions)), 10, 15)


class MemoryAwareFixture:
    """The fixture chooses subsequent actions from actual returned memory IDs."""

    async def complete(self, messages, *, max_output_tokens):
        observation = json.loads(messages[-1]["content"]).get("observation")
        if observation is None:
            action = search_memory()
        elif observation.get("kind") == "memory_search_results":
            action = read_finding(observation["findings"][0]["result_id"])
        elif observation.get("kind") == "memory_finding":
            action = read_memory_source(
                observation["reference"]["result_id"], observation["sources"][0]["retrieval_id"]
            )
        elif observation.get("kind") == "source_text" and observation["source_id"] == "left":
            action = read_memory_source(FIRST_RESULT, RIGHT_RETRIEVAL)
        elif observation.get("kind") == "source_text":
            action = required_searches()[0]
        elif observation.get("purpose") == "counterevidence":
            action = required_searches()[1]
        else:
            action = bridge()
        return ModelReply(json.dumps(action), 10, 15)


@pytest.mark.asyncio
async def test_model_revisits_hypothesis_and_reads_original_evidence_before_citing():
    memory, literature = MemoryFixture(), Tools()
    result = await ResearchController(MemoryAwareFixture(), literature, memory=memory).investigate(
        "Investigate a synthetic saved lead"
    )
    assert result.status == "bridge"
    assert memory.finding_reads == [FIRST_RESULT]
    assert memory.source_reads == [(FIRST_RESULT, LEFT_RETRIEVAL), (FIRST_RESULT, RIGHT_RETRIEVAL)]
    assert literature.reads == []
    assert [purpose for _, purpose in literature.searches] == ["counterevidence", "prior_work"]
    assert {document.source_id for document in result.sources} == {"left", "right"}
    assert len(result.memory_reads) == 1
    assert result.memory_reads[0].result_id == FIRST_RESULT
    assert (
        result.memory_reads[0].content_sha256
        == memory.findings[FIRST_RESULT].reference.content_sha256
    )
    assert all(
        item.source_sha256 in {s.content_sha256 for s in result.sources} for item in result.quotes
    )
    assert [event.kind for event in result.events][:4] == [
        "memory_searched",
        "memory_finding_read",
        "memory_source_read",
        "memory_source_read",
    ]


@pytest.mark.asyncio
async def test_saved_finding_prose_is_not_quote_eligible_source_text():
    memory = MemoryFixture()
    result = await ResearchController(
        ObservingSequence([search_memory(), read_finding(), *required_searches(), bridge()]),
        Tools(),
        ResearchLimits(max_repairs=0),
        memory=memory,
    ).investigate("Synthetic unsupported citation attempt")
    assert result.status == "failed"
    assert result.error_code == "repair_limit"
    assert not result.sources
    assert not result.quotes
    assert len(result.memory_reads) == 1


@pytest.mark.asyncio
async def test_memory_search_does_not_substitute_for_counterevidence_or_prior_work():
    result = await ResearchController(
        ObservingSequence(
            [
                search_memory(),
                read_finding(),
                read_memory_source(),
                read_memory_source(FIRST_RESULT, RIGHT_RETRIEVAL),
                bridge(),
            ]
        ),
        Tools(),
        ResearchLimits(max_repairs=0),
        memory=MemoryFixture(),
    ).investigate("Synthetic incomplete scrutiny")
    assert result.status == "failed"
    assert not result.quotes


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [read_finding(UNKNOWN_RESULT), read_memory_source()])
async def test_undiscovered_memory_objects_never_reach_memory_tools(action):
    memory = MemoryFixture()
    result = await ResearchController(
        ObservingSequence([action]), Tools(), ResearchLimits(max_repairs=0), memory=memory
    ).investigate("Synthetic fabricated memory identifier")
    assert result.error_code == "repair_limit"
    assert memory.finding_reads == memory.source_reads == []


@pytest.mark.asyncio
async def test_source_retrieval_must_belong_to_the_observed_finding():
    memory = MemoryFixture()
    result = await ResearchController(
        ObservingSequence(
            [
                search_memory(),
                read_finding(),
                read_memory_source(FIRST_RESULT, OTHER_RIGHT_RETRIEVAL),
            ]
        ),
        Tools(),
        ResearchLimits(max_repairs=0),
        memory=memory,
    ).investigate("Synthetic cross-finding retrieval mixup")
    assert result.error_code == "repair_limit"
    assert not memory.source_reads


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["text", "title", "url", "content_kind", "source_id"])
async def test_memory_source_must_match_observed_identity_hash_and_kind(mismatch):
    memory = MemoryFixture()
    original = memory.documents[FIRST_RESULT][LEFT_RETRIEVAL]
    changes = {
        "text": "This replacement source is a different saved version.",
        "title": "A different metadata snapshot",
        "url": "https://example.org/different-paper",
        "content_kind": "full_text",
        "source_id": "other-source",
    }
    memory.documents[FIRST_RESULT][LEFT_RETRIEVAL] = original.model_copy(
        update={mismatch: changes[mismatch]}
    )
    model = ObservingSequence(
        [search_memory(), read_finding(), read_memory_source(), insufficient()]
    )
    result = await ResearchController(model, Tools(), memory=memory).investigate("Fixture mismatch")
    assert result.status == "insufficient_evidence"
    assert not result.sources
    assert model.observations[-1]["observation"] == {"error": "memory_source_read_failed"}


@pytest.mark.asyncio
async def test_read_finding_must_match_immutable_search_reference():
    class ChangedMemory(MemoryFixture):
        async def read_finding(self, result_id):
            saved = await super().read_finding(result_id)
            return saved.model_copy(
                update={
                    "reference": saved.reference.model_copy(update={"content_sha256": "f" * 64})
                }
            )

    model = ObservingSequence([search_memory(), read_finding(), insufficient()])
    result = await ResearchController(model, Tools(), memory=ChangedMemory()).investigate("Fixture")
    assert not result.memory_reads
    assert model.observations[-1]["observation"] == {"error": "memory_finding_read_failed"}


@pytest.mark.asyncio
async def test_source_version_conflict_retains_original_and_cannot_change_existing_quotes():
    class NewerLiterature(Tools):
        async def read(self, source_id):
            return source(source_id, "A newer source snapshot changed the prior evidence.")

    model = ObservingSequence(
        [
            search_memory(),
            read_finding(),
            read_memory_source(),
            {"kind": "search_literature", "query": "fixture", "purpose": "discovery"},
            {"kind": "read_source", "source_id": "left"},
            insufficient(),
        ]
    )
    result = await ResearchController(model, NewerLiterature(), memory=MemoryFixture()).investigate(
        "Fixture version collision"
    )
    assert result.sources[0].text == LEFT_QUOTE
    assert model.observations[-1]["observation"] == {"error": "source_retrieval_failed"}


@pytest.mark.asyncio
async def test_memory_version_conflict_with_literature_retains_first_snapshot():
    class NewerMemory(MemoryFixture):
        def __init__(self):
            super().__init__()
            document = source("left", "A different immutable saved source version.")
            self.documents[FIRST_RESULT][LEFT_RETRIEVAL] = document
            saved = self.findings[FIRST_RESULT]
            self.findings[FIRST_RESULT] = saved.model_copy(
                update={
                    "sources": (
                        saved.sources[0].model_copy(
                            update={"content_sha256": document.content_sha256}
                        ),
                        saved.sources[1],
                    )
                }
            )

    model = ObservingSequence(
        [
            {"kind": "search_literature", "query": "fixture", "purpose": "discovery"},
            {"kind": "read_source", "source_id": "left"},
            search_memory(),
            read_finding(),
            read_memory_source(),
            insufficient(),
        ]
    )
    result = await ResearchController(model, Tools(), memory=NewerMemory()).investigate("Fixture")
    assert result.sources[0].text == LEFT_QUOTE
    assert model.observations[-1]["observation"] == {"error": "memory_source_read_failed"}


@pytest.mark.asyncio
async def test_memory_tool_failure_and_unavailable_memory_are_sanitized():
    class FailingMemory(MemoryFixture):
        async def search(self, query):
            raise RuntimeError("private-provider-fixture-content")

    for memory in (None, FailingMemory()):
        model = ObservingSequence([search_memory(), insufficient()])
        result = await ResearchController(model, Tools(), memory=memory).investigate("Fixture")
        assert result.status == "insufficient_evidence"
        assert not result.memory_reads
        assert model.observations[-1]["observation"] == {"error": "memory_search_failed"}
        assert "private-provider" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_surprise_requires_field_selection_before_memory_actions():
    memory = MemoryFixture()
    result = await ResearchController(
        ObservingSequence([search_memory()]), Tools(), ResearchLimits(max_repairs=0), memory=memory
    ).investigate("", mode="surprise")
    assert result.error_code == "repair_limit"
    assert not memory.searches


@pytest.mark.asyncio
async def test_ask_can_revisit_memory_without_being_forced_to_emit_a_bridge():
    result = await ResearchController(
        ObservingSequence([search_memory(), read_finding(), insufficient()]),
        Tools(),
        memory=MemoryFixture(),
    ).investigate("What does the synthetic finding leave unresolved?", mode="ask")
    assert result.mode == "ask"
    assert result.status == "insufficient_evidence"
    assert result.memory_reads[0].result_id == FIRST_RESULT


def parent_actions():
    conclusion = bridge()
    conclusion["conclusion"]["evidence"][1]["source_id"] = "other-right"
    return [
        read_finding(FIRST_RESULT),
        read_finding(SECOND_RESULT),
        read_memory_source(),
        read_memory_source(SECOND_RESULT, OTHER_RIGHT_RETRIEVAL),
        *required_searches(),
        conclusion,
    ]


@pytest.mark.asyncio
async def test_selected_parents_are_pinned_leads_with_traceable_original_evidence():
    memory = MemoryFixture()
    model = ObservingSequence(parent_actions())
    result = await ResearchController(model, Tools(), memory=memory).investigate(
        "Investigate a synthetic connection across saved bridges",
        parent_result_ids=(FIRST_RESULT, SECOND_RESULT),
    )
    assert result.status == "bridge"
    assert memory.searches == []
    assert {item.result_id for item in result.memory_reads} == {FIRST_RESULT, SECOND_RESULT}
    assert {item.source_id for item in result.quotes} == {"left", "other-right"}
    assert model.observations[0]["parent_result_ids"] == [FIRST_RESULT, SECOND_RESULT]


@pytest.mark.asyncio
async def test_parent_must_be_read_before_a_positive_higher_level_finding():
    actions = [
        search_memory(),
        read_finding(),
        read_memory_source(),
        read_memory_source(FIRST_RESULT, RIGHT_RETRIEVAL),
        *required_searches(),
        bridge(),
    ]
    result = await ResearchController(
        ObservingSequence(actions), Tools(), ResearchLimits(max_repairs=0), memory=MemoryFixture()
    ).investigate("Fixture", parent_result_ids=(FIRST_RESULT, SECOND_RESULT))
    assert result.status == "failed"
    assert not result.quotes


@pytest.mark.asyncio
async def test_reading_all_parents_does_not_excuse_missing_parent_evidence():
    actions = [
        read_finding(),
        read_finding(SECOND_RESULT),
        read_memory_source(),
        read_memory_source(FIRST_RESULT, RIGHT_RETRIEVAL),
        *required_searches(),
        bridge(),
    ]
    result = await ResearchController(
        ObservingSequence(actions), Tools(), ResearchLimits(max_repairs=0), memory=MemoryFixture()
    ).investigate("Fixture", parent_result_ids=(FIRST_RESULT, SECOND_RESULT))
    assert result.status == "failed"
    assert len(result.memory_reads) == 2
    assert not result.quotes


@pytest.mark.asyncio
async def test_unavailable_selected_parent_can_end_honestly_without_a_positive_finding():
    model = ObservingSequence([read_finding(UNKNOWN_RESULT), insufficient()])
    result = await ResearchController(model, Tools(), memory=MemoryFixture()).investigate(
        "Fixture", parent_result_ids=(FIRST_RESULT, UNKNOWN_RESULT)
    )
    assert result.status == "insufficient_evidence"
    assert not result.memory_reads
    assert model.observations[-1]["observation"] == {"error": "memory_finding_read_failed"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parents,mode",
    [
        ((FIRST_RESULT,), "explore"),
        ((FIRST_RESULT, FIRST_RESULT), "explore"),
        ((FIRST_RESULT, SECOND_RESULT), "ask"),
        ((FIRST_RESULT, SECOND_RESULT), "surprise"),
        ((FIRST_RESULT, "not-a-uuid"), "explore"),
        ([FIRST_RESULT, SECOND_RESULT], "explore"),
    ],
)
async def test_invalid_parent_contract_is_rejected_before_any_model_call(parents, mode):
    model = ObservingSequence([])
    with pytest.raises(ValueError):
        await ResearchController(model, Tools(), memory=MemoryFixture()).investigate(
            "Fixture", mode=mode, parent_result_ids=parents
        )
    assert model.calls == 0


def test_memory_contract_rejects_unknown_actions_identifiers_and_duplicate_snapshots():
    for action in [
        {"kind": "search_memory", "query": "bad\u0000query"},
        {"kind": "search_memory", "query": "bad\nquery"},
        {"kind": "search_memory", "query": "x" * 201},
        {"kind": "read_memory_finding", "result_id": "not-a-uuid"},
        {"kind": "read_memory_source", "result_id": FIRST_RESULT, "retrieval_id": 1},
        {"kind": "search_memory", "query": "fixture", "owner": "someone-else"},
    ]:
        with pytest.raises(ValidationError):
            ACTION_ADAPTER.validate_json(json.dumps(action))
    fixture = MemoryFixture().findings[FIRST_RESULT]
    with pytest.raises(ValidationError):
        MemoryFinding.model_validate({**fixture.model_dump(), "sources": (fixture.sources[0],) * 2})


def test_old_result_payloads_remain_readable_without_memory_fields():
    parsed = ResearchResult.model_validate_json(
        json.dumps(
            {
                "status": "insufficient_evidence",
                "usage": {},
                "finding": {"outcome": "insufficient_evidence", "reason": "Older saved fixture"},
            }
        )
    )
    assert parsed.memory_reads == ()
