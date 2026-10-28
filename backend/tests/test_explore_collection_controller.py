"""Pure synthetic Explore state/loop behavior; no database or external request."""

import copy
import hashlib
import json
import uuid
from pathlib import Path

import pytest
from test_discovery_controller import (
    RUN_ID,
    THIRD_QUOTE,
    DiscoveryFixtureTools,
    assessment_data,
    selection_action,
)
from test_research_controller import RIGHT_QUOTE, bridge
from test_research_memory import (
    FIRST_RESULT,
    SECOND_RESULT,
    MemoryFixture,
    read_finding,
    read_memory_source,
    required_searches,
    search_memory,
)

from polymath.discovery_contracts import DiscoveryIntent, StageCommitReceipt
from polymath.explore_collection_contracts import (
    ExploreCollectionIntent,
    ExploreCollectionOutcome,
    ExploreFinishPacket,
    ExploreRankingReceipt,
    explore_commit_sha256,
    explore_ranking_sha256,
)
from polymath.meta_research_contracts import (
    SCIENTIFIC_PROFILE_ROWS,
    TASK_KINDS,
    controller_protocol_sha256,
    discovery_controller_sha256,
    scientific_controller_sha256,
)
from polymath.research import ModelReply, ResearchController, ResearchLimits
from polymath.research.discovery import DiscoveryLimits
from polymath.research.explore_collection import (
    EXPLORE_COLLECTION_SYSTEM_PROMPT,
    explore_collection_initial_messages,
)
from polymath.research.models import (
    AnswerClaim,
    AnswerFinding,
    AskOverviewBinding,
    ClaimQuote,
    MemoryFinding,
    MemoryFindingReference,
    MemorySearchResults,
    MemorySourceReference,
    SourceBibliography,
    SourceReference,
)
from polymath.scientific_context_contracts import AskOverviewIntent

GOAL = "How does redundancy protect structure under noise?"
BASELINE = Path(__file__).parent / "fixtures" / "pre_explore_controller_baseline.json"


def fixture_id(value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "polymath-explore-controller-fixture:" + value))


def record_bridge(index=0, *, confidence=None):
    value = copy.deepcopy(bridge()["conclusion"])
    value["title"] = f"Synthetic Explore bridge{index + 1}"
    value["explanation"] = f"Synthetic branch{index + 1} examines the original redundancy question."
    if index:
        value["field_right"] = "Ecology"
        value["evidence"][1] |= {"source_id": "third", "quote": THIRD_QUOTE}
    assessment = assessment_data()
    assessment["confidence_score"] = (
        confidence if confidence is not None else 80 if index == 0 else 25
    )
    return {"kind": "record_bridge", "finding": value, "assessment": assessment}


def selection(index=0):
    value = selection_action()
    if index:
        value["fields"] = ["Information theory", "Ecology"]
    value["rationale"] = "Study the original redundancy goal in these fields."
    return value


def insufficient():
    return {
        "kind": "conclude",
        "conclusion": {
            "outcome": "insufficient_evidence",
            "reason": "The bounded synthetic search does not justify further bridges.",
        },
    }


def prefix(*, notes=False):
    actions = [selection()]
    if notes:
        actions.extend(
            [
                {
                    "kind": "record_note",
                    "hypothesis_id": "weak_lead",
                    "state": "proposed",
                    "summary": "A weak synthetic lead.",
                    "reason": "Test the actual stated mechanism.",
                },
                {
                    "kind": "record_note",
                    "hypothesis_id": "weak_lead",
                    "state": "discarded",
                    "summary": "Discard the weak lead.",
                    "reason": "The comparison does not retain the original mechanism.",
                },
            ]
        )
    return [
        *actions,
        {
            "kind": "search_literature",
            "query": "synthetic redundancy sources",
            "purpose": "discovery",
        },
        {"kind": "read_source", "source_id": "left"},
        {"kind": "read_source", "source_id": "right"},
        *required_searches(),
        record_bridge(),
    ]


def second_branch():
    return [
        selection(1),
        {"kind": "read_source", "source_id": "third"},
        *required_searches(),
        record_bridge(1),
    ]


def refs(messages):
    return [
        {
            "result_id": item["result_id"],
            "run_id": item["run_id"],
            "content_sha256": item["result_sha256"],
        }
        for message in messages
        if message["role"] == "user"
        if (item := json.loads(message["content"]).get("observation", {})).get("kind")
        == "stage_committed"
    ]


def ranking_action(messages, *, finish=True, order=None, excluded=()):
    visible = refs(messages)
    indices = list(reversed(range(len(visible)))) if order is None else list(order)
    ranking = {
        "ranked": [
            {
                "reference": visible[index],
                "rationale": "The model prefers relevance over its earlier confidence score.",
            }
            for index in indices
        ],
        "excluded": [
            {
                "reference": visible[index],
                "reason": "The model excludes this weaker saved candidate.",
            }
            for index in excluded
        ],
    }
    return {
        "kind": "finish_ranked" if finish else "rank_saved",
        "ranking": ranking,
        **({"reason": "Finish with this explicit model-authored ordering."} if finish else {}),
    }


class RecordingModel:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.messages = []
        self.calls = 0

    async def complete(self, messages, *, max_output_tokens):
        self.calls += 1
        self.messages.append(copy.deepcopy(messages))
        assert json.loads(messages[1]["content"])["research_goal"] == GOAL
        action = next(self.actions)
        if callable(action):
            action = action(messages)
        if isinstance(action, BaseException):
            raise action
        return ModelReply(json.dumps(action, ensure_ascii=False), 10, 15)


class FixtureSink:
    def __init__(self, *, memory=None, lost_stage=False, lost_rank=False, lost_finish=False):
        self.contexts = []
        self.packets = []
        self.receipts = []
        self.ranking_packets = []
        self.rankings = []
        self.finishes = []
        self.memory = memory
        self.stage_keys = {}
        self.rank_keys = {}
        self.lost_stage = lost_stage
        self.lost_rank = lost_rank
        self.lost_finish = lost_finish

    async def begin_context(self, context):
        self.contexts.append(context)

    async def commit(self, packet):
        key = (packet.model_request_id, packet.action_ordinal)
        digest = explore_commit_sha256(packet)
        if key in self.stage_keys:
            previous, receipt = self.stage_keys[key]
            assert previous == digest
            return receipt
        if self.memory is not None:
            assert {ref.result_id for ref in packet.result.memory_reads} == set(
                self.memory.finding_reads
            )
        index = len(self.receipts)
        receipt = StageCommitReceipt(
            stage_id=fixture_id(f"stage{index}"),
            run_id=RUN_ID,
            ordinal=index,
            result_id=fixture_id(f"result{index}"),
            result_sha256=hashlib.sha256(packet.result.model_dump_json().encode()).hexdigest(),
            depth=1,
            assessment=packet.assessment,
            parent_refs=(),
            action_ordinal=packet.action_ordinal,
            model_request_id=packet.model_request_id,
        )
        self.stage_keys[key] = (digest, receipt)
        self.packets.append(packet)
        self.receipts.append(receipt)
        if self.lost_stage:
            self.lost_stage = False
            raise RuntimeError("synthetic lost stage acknowledgement")
        return receipt

    async def rank(self, packet):
        key = (packet.model_request_id, packet.action_ordinal)
        digest = explore_ranking_sha256(packet)
        if key in self.rank_keys:
            previous, receipt = self.rank_keys[key]
            assert previous == digest
            return receipt
        receipt = ExploreRankingReceipt(
            ranking_id=fixture_id(f"ranking:{key}"),
            run_id=RUN_ID,
            packet_sha256=digest,
            model_request_id=packet.model_request_id,
            action_ordinal=packet.action_ordinal,
            covered_through_ordinal=len(packet.visible_receipts) - 1
            if packet.visible_receipts
            else None,
            ranked=packet.ranked,
            excluded=packet.excluded,
        )
        self.rank_keys[key] = (digest, receipt)
        self.ranking_packets.append(packet)
        self.rankings.append(receipt)
        if self.lost_rank:
            self.lost_rank = False
            raise RuntimeError("synthetic lost ranking acknowledgement")
        return receipt

    async def finish(self, packet):
        assert isinstance(packet, ExploreFinishPacket)
        receipt = await self.rank(packet.ranking)
        if packet not in self.finishes:
            self.finishes.append(packet)
        if self.lost_finish:
            self.lost_finish = False
            raise RuntimeError("synthetic lost finish acknowledgement")
        return receipt


async def run(actions, *, tools=None, memory=None, sink=None, limits=None, intent=None):
    tools = tools or DiscoveryFixtureTools()
    sink = sink or FixtureSink(memory=memory)
    model = RecordingModel(actions)
    result = await ResearchController(
        model,
        tools,
        limits or ResearchLimits(max_model_calls=20, max_actions=16),
        memory=memory,
        explore_collection_sink=sink,
    ).investigate(
        GOAL, mode="explore", explore_collection_intent=intent or ExploreCollectionIntent()
    )
    return result, model, tools, sink


@pytest.mark.asyncio
async def test_two_branches_discarded_lead_and_model_order_share_exact_sixteen_actions():
    outcome, model, _, sink = await run(
        [
            *prefix(notes=True),
            *second_branch(),
            lambda messages: ranking_action(messages, finish=False),
            lambda messages: ranking_action(messages),
        ]
    )
    assert isinstance(outcome, ExploreCollectionOutcome)
    assert outcome.status == "completed" and len(outcome.stages) == 2
    assert outcome.usage.model_calls == 16 and model.calls == 16
    assert (
        outcome.usage.input_tokens_reported == 160 and outcome.usage.output_tokens_reported == 240
    )
    assert [entry.reference for entry in outcome.final_ranking.ranked] == [
        outcome.stages[1].reference,
        outcome.stages[0].reference,
    ]
    assert (
        outcome.stages[0].assessment.confidence_score
        > outcome.stages[1].assessment.confidence_score
    )
    assert outcome.selected_reference == outcome.stages[1].reference
    assert all(
        packet.result.mode == "explore" and packet.result.field_selections == ()
        for packet in sink.packets
    )
    assert all(receipt.depth == 1 and receipt.parent_refs == () for receipt in outcome.stages)
    assert [context.selection.fields for context in sink.contexts] == [
        ("Information theory", "Biology"),
        ("Information theory", "Ecology"),
    ]
    contexts = [
        json.loads(message["content"])["observation"]
        for message in model.messages[-1]
        if message["role"] == "user"
        if json.loads(message["content"]).get("observation", {}).get("kind") == "stage_context"
    ]
    assert len(contexts) == 2 and all(context["research_goal"] == GOAL for context in contexts)
    assert len([event for event in outcome.events if event.kind == "research_note"]) == 2
    assert len(sink.rankings) == 2 and len(sink.finishes) == 1


@pytest.mark.asyncio
async def test_partial_after_interim_rank_keeps_later_bridge_unranked_without_silent_selection():
    actions = [*prefix(), lambda messages: ranking_action(messages, finish=False), *second_branch()]
    outcome, model, _, sink = await run(
        actions, limits=ResearchLimits(max_model_calls=len(actions), max_actions=16)
    )
    assert outcome.status == "limit_reached" and len(outcome.stages) == 2
    assert outcome.final_ranking is None and outcome.selected_reference is None
    assert len(outcome.rankings) == 1 and outcome.rankings[0].covered_through_ordinal == 0
    assert outcome.rankings[0].ranked[0].reference == outcome.stages[0].reference
    assert len(sink.finishes) == 0 and model.calls == len(actions)


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [True, False])
async def test_empty_or_all_excluded_finish_has_no_invented_preferred_finding(empty):
    actions = [] if empty else prefix()
    actions.append(
        lambda messages: ranking_action(messages, order=(), excluded=range(len(refs(messages))))
    )
    outcome, _, _, sink = await run(actions)
    assert outcome.status == "completed" and outcome.selected_reference is None
    assert len(outcome.stages) == (0 if empty else 1)
    assert outcome.final_ranking.ranked == ()
    assert len(outcome.final_ranking.excluded) == (0 if empty else 1)
    assert len(sink.finishes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("after_stage", [False, True])
async def test_insufficient_conclusion_retains_partial_bridges_without_a_ranking(after_stage):
    outcome, _, _, sink = await run([*(prefix() if after_stage else []), insufficient()])
    assert outcome.status == "insufficient_evidence"
    assert len(outcome.stages) == int(after_stage)
    assert outcome.final_ranking is None and outcome.selected_reference is None
    assert not sink.finishes


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["omitted", "unknown", "wrong_hash", "duplicate"])
async def test_bad_ranking_is_repaired_before_any_ranking_write(bad):
    def invalid(messages):
        action = ranking_action(messages)
        if bad == "omitted":
            action["ranking"]["ranked"] = []
        elif bad == "unknown":
            action["ranking"]["ranked"][0]["reference"]["result_id"] = fixture_id("unknown")
        elif bad == "wrong_hash":
            action["ranking"]["ranked"][0]["reference"]["content_sha256"] = "f" * 64
        else:
            action["ranking"]["excluded"] = [
                {"reference": action["ranking"]["ranked"][0]["reference"], "reason": "duplicate"}
            ]
        return action

    outcome, model, _, sink = await run(
        [*prefix(), invalid, lambda messages: ranking_action(messages)]
    )
    assert outcome.status == "completed" and len(sink.rankings) == 1
    assert outcome.usage.model_calls == len(prefix()) + 2
    assert any(event.kind == "action_rejected" for event in outcome.events)


@pytest.mark.asyncio
async def test_each_branch_requires_fresh_challenge_and_prior_searches():
    outcome, model, _, sink = await run([*prefix(), selection(1), record_bridge(1), insufficient()])
    assert outcome.status == "insufficient_evidence" and len(outcome.stages) == 1
    assert len(sink.packets) == 1
    last = model.messages[-1]
    assert any(
        json.loads(message["content"]).get("observation", {}).get("error")
        == "missing_required_searches"
        for message in last
        if message["role"] == "user"
    )


@pytest.mark.asyncio
async def test_max_bridges_is_a_ceiling_without_automatic_paid_finish_or_selection():
    outcome, model, _, sink = await run(
        [*prefix(), *second_branch(), insufficient()], intent=ExploreCollectionIntent(max_bridges=1)
    )
    assert outcome.status == "insufficient_evidence" and len(outcome.stages) == 1
    assert not sink.finishes and outcome.selected_reference is None
    assert model.calls == len(prefix()) + len(second_branch()) + 1


@pytest.mark.asyncio
async def test_hypothesis_text_stays_complete_in_observation_with_bounded_event_header():
    note = {
        "kind": "record_note",
        "hypothesis_id": "long",
        "state": "proposed",
        "summary": "s" * 1000,
        "reason": "r" * 1000,
    }
    outcome, model, _, _ = await run([selection(), note, insufficient()])
    events = [event for event in outcome.events if event.kind == "research_note"]
    assert len(events) == 1 and len(events[0].summary) <= 2000
    assert note["summary"] in events[0].summary
    assert any(
        json.loads(message["content"]).get("observation", {}).get("summary") == note["summary"]
        and json.loads(message["content"]).get("observation", {}).get("reason") == note["reason"]
        for message in model.messages[-1]
        if message["role"] == "user"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["stage", "rank", "finish"])
async def test_unknown_persistence_acknowledgements_propagate_without_paid_repair(fault):
    sink = FixtureSink(
        lost_stage=fault == "stage", lost_rank=fault == "rank", lost_finish=fault == "finish"
    )
    actions = [
        *prefix(),
        lambda messages: ranking_action(messages, finish=fault != "rank"),
        insufficient(),
    ]
    model = RecordingModel(actions)
    with pytest.raises(RuntimeError, match="synthetic lost"):
        await ResearchController(
            model,
            DiscoveryFixtureTools(),
            ResearchLimits(max_model_calls=20, max_actions=16),
            explore_collection_sink=sink,
        ).investigate(GOAL, explore_collection_intent=ExploreCollectionIntent())
    assert model.calls == len(prefix()) + (fault != "stage")
    assert len(sink.receipts) == 1


@pytest.mark.asyncio
async def test_controller_retains_ordinary_bridge_and_bound_ask_memory_reads_across_branches():
    memory = MemoryFixture()
    original = memory.findings[SECOND_RESULT]
    doc = next(iter(memory.documents[SECOND_RESULT].values()))
    claim = AnswerClaim(
        id="known_memory",
        text="A synthetic Ask lead retains its source.",
        label="known",
        evidence=[
            ClaimQuote(
                source_id=doc.source_id, quote=doc.text, supports="Original synthetic premise."
            )
        ],
    )
    finding = AnswerFinding(
        outcome="answer",
        question="What might redundancy preserve?",
        summary=claim.text,
        claims=[claim],
        limitations=["Synthetic lead, not independent evidence."],
    )
    reference = MemoryFindingReference(
        **{
            **original.reference.model_dump(),
            "title": finding.question,
            "summary": "1 classified claim; open exact evidence.",
            "outcome": "answer",
        }
    )
    memory.findings[SECOND_RESULT] = MemoryFinding(
        reference=reference,
        finding=finding,
        sources=original.sources,
        overview_binding=AskOverviewBinding(claim_ids=("known_memory",)),
    )
    actions = [
        selection(),
        search_memory(),
        read_finding(),
        read_memory_source(retrieval_id=list(memory.documents[FIRST_RESULT])[0]),
        read_memory_source(retrieval_id=list(memory.documents[FIRST_RESULT])[1]),
        *required_searches(),
        record_bridge(),
        selection(),
        read_finding(SECOND_RESULT),
        read_memory_source(SECOND_RESULT, list(memory.documents[SECOND_RESULT])[1]),
        *required_searches(),
    ]
    second = record_bridge()
    second["finding"]["title"] = "Second bridge based on an ordinary Ask memory source"
    second["finding"]["evidence"][1] |= {"source_id": "other-right", "quote": RIGHT_QUOTE}
    actions.extend([second, lambda messages: ranking_action(messages)])
    outcome, _, _, sink = await run(actions, memory=memory)
    assert outcome.status == "completed" and len(outcome.stages) == 2
    assert [item.result_id for item in sink.packets[0].result.memory_reads] == [FIRST_RESULT]
    assert [item.result_id for item in sink.packets[1].result.memory_reads] == [
        FIRST_RESULT,
        SECOND_RESULT,
    ]
    assert memory.finding_reads == [FIRST_RESULT, SECOND_RESULT]
    assert all(not receipt.parent_refs for receipt in outcome.stages)


@pytest.mark.asyncio
async def test_memory_alias_metadata_is_seen_even_without_requoting_that_ask_lead():
    tools = DiscoveryFixtureTools()
    tools.documents["left"] = tools.documents["left"].model_copy(
        update={"bibliography": SourceBibliography(doi="10.5555/explore-left")}
    )
    tools.documents["right"] = tools.documents["right"].model_copy(
        update={"bibliography": SourceBibliography(arxiv_id="2401.12345")}
    )
    original_search = tools.search

    async def search(query, purpose):
        result = await original_search(query, purpose)
        return [
            SourceReference(
                source_id=item.source_id,
                title=item.title,
                url=item.url,
                bibliography=tools.documents[item.source_id].bibliography,
            )
            for item in result
        ]

    tools.search = search
    connector = SourceReference(
        source_id="connector",
        title="Synthetic saved Ask source connector",
        url="https://example.org/connector",
        bibliography=SourceBibliography(doi="10.5555/explore-left", arxiv_id="2401.12345"),
    )
    claim = AnswerClaim(
        id="known",
        text="The saved Ask lead is a hypothesis.",
        label="known",
        evidence=[
            ClaimQuote(
                source_id="connector",
                quote="A synthetic connector source passage.",
                supports="A saved source lead.",
            )
        ],
    )
    finding = AnswerFinding(
        outcome="answer",
        question="An earlier question?",
        summary=claim.text,
        claims=[claim],
        limitations=["Synthetic fixture only."],
    )
    saved = MemoryFinding(
        reference=MemoryFindingReference(
            result_id=fixture_id("memory-connector"),
            run_id=fixture_id("older-run"),
            content_sha256="e" * 64,
            title=finding.question,
            summary="1 classified claim; inspect source.",
            outcome="answer",
        ),
        finding=finding,
        overview_binding=AskOverviewBinding(claim_ids=("known",)),
        sources=(
            MemorySourceReference(
                retrieval_id=fixture_id("connector-retrieval"),
                source=connector,
                content_sha256="d" * 64,
                content_kind="abstract",
            ),
        ),
    )

    class Memory:
        finding_reads = []

        async def search(self, query):
            return MemorySearchResults(findings=(saved.reference,), truncated=False)

        async def read_finding(self, result_id):
            self.finding_reads.append(result_id)
            return saved

    memory = Memory()
    outcome, model, _, sink = await run(
        [
            *prefix(),
            selection(),
            search_memory(),
            read_finding(saved.reference.result_id),
            *required_searches(),
            record_bridge(),
            insufficient(),
        ],
        tools=tools,
        memory=memory,
    )
    assert outcome.status == "insufficient_evidence" and len(sink.packets) == 1
    assert any(
        json.loads(message["content"]).get("observation", {}).get("error")
        == "distinct_sources_required"
        for message in model.messages[-1]
        if message["role"] == "user"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "ask"},
        {"mode": "surprise"},
        {"mode": "explore", "parent_result_ids": (fixture_id("p1"), fixture_id("p2"))},
        {"mode": "explore", "discovery": DiscoveryIntent()},
        {"mode": "ask", "ask_overview_intent": AskOverviewIntent()},
    ],
)
async def test_incompatible_scope_stops_before_any_model_or_store_action(kwargs):
    model = RecordingModel([])
    sink = FixtureSink()
    with pytest.raises(ValueError):
        await ResearchController(
            model, DiscoveryFixtureTools(), explore_collection_sink=sink
        ).investigate(GOAL, explore_collection_intent=ExploreCollectionIntent(), **kwargs)
    assert model.calls == 0 and sink.contexts == []


@pytest.mark.asyncio
async def test_surprise_twenty_action_limit_cannot_leak_into_new_explore_protocol():
    model = RecordingModel([])
    with pytest.raises(ValueError, match="explore_collection_action_limit"):
        await ResearchController(
            model, DiscoveryFixtureTools(), DiscoveryLimits(), explore_collection_sink=FixtureSink()
        ).investigate(GOAL, explore_collection_intent=ExploreCollectionIntent())
    assert model.calls == 0


def test_goal_and_immutable_intent_are_in_exact_initial_messages():
    intent = ExploreCollectionIntent(max_bridges=4)
    messages = explore_collection_initial_messages(" " + GOAL + " ", intent, memory_available=True)
    assert json.loads(messages[1]["content"]) == {
        "mode": "explore",
        "research_goal": GOAL,
        "memory_available": True,
        "parent_result_ids": [],
        "explore_collection_intent": intent.model_dump(mode="json"),
    }
    assert messages[0]["content"].startswith(EXPLORE_COLLECTION_SYSTEM_PROMPT)


def test_all_preexisting_profiles_and_action_schemas_remain_identical():
    baseline = json.loads(BASELINE.read_text())
    assert {kind: controller_protocol_sha256(kind) for kind in TASK_KINDS} == baseline["profiles"]
    assert discovery_controller_sha256() == baseline["discovery"]
    assert {
        f"{capability}:{kind}": scientific_controller_sha256(capability, kind)
        for capability, kind, _, _ in SCIENTIFIC_PROFILE_ROWS
    } == baseline["scientific_profiles"]
    from polymath.artifact_contracts import ARTIFACT_ACTION_ADAPTER
    from polymath.assessment_controller import ASSESSMENT_ACTION_ADAPTER
    from polymath.assessment_overview import ASK_REASSESSMENT_OVERVIEW_ACTION_ADAPTER
    from polymath.discovery_contracts import DISCOVERY_ACTION_ADAPTER
    from polymath.research.ask_overview import ASK_OVERVIEW_ACTION_ADAPTER
    from polymath.research.models import ACTION_ADAPTER

    assert {
        "research": ACTION_ADAPTER.json_schema(),
        "discovery": DISCOVERY_ACTION_ADAPTER.json_schema(),
        "ask_overview": ASK_OVERVIEW_ACTION_ADAPTER.json_schema(),
        "artifacts": ARTIFACT_ACTION_ADAPTER.json_schema(),
        "assessment": ASSESSMENT_ACTION_ADAPTER.json_schema(),
        "assessment_overview": ASK_REASSESSMENT_OVERVIEW_ACTION_ADAPTER.json_schema(),
    } == baseline["schemas"]


class CachedModel:
    """Synthetic recorded replies prove deterministic in-order controller replay."""

    def __init__(self, delegate, cache):
        self.delegate = delegate
        self.cache = cache
        self.step = 0

    async def complete(self, messages, *, max_output_tokens):
        self.step += 1
        if self.step in self.cache:
            original, reply = self.cache[self.step]
            assert original == messages, "Replay injected changed or future observations"
            return reply
        reply = await self.delegate.complete(messages, max_output_tokens=max_output_tokens)
        self.cache[self.step] = (copy.deepcopy(messages), reply)
        return reply


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["stage", "rank", "finish"])
async def test_lost_ack_replay_reconstructs_only_original_receipts_without_duplicate_commits(fault):
    actions = [
        *prefix(),
        lambda messages: ranking_action(messages, finish=False),
        *second_branch(),
        lambda messages: ranking_action(messages),
    ]
    delegate = RecordingModel(actions)
    cache = {}
    sink = FixtureSink(
        lost_stage=fault == "stage", lost_rank=fault == "rank", lost_finish=fault == "finish"
    )
    limits = ResearchLimits(max_model_calls=20, max_actions=16)
    with pytest.raises(RuntimeError, match="synthetic lost"):
        await ResearchController(
            CachedModel(delegate, cache),
            DiscoveryFixtureTools(),
            limits,
            explore_collection_sink=sink,
        ).investigate(GOAL, explore_collection_intent=ExploreCollectionIntent())
    already = len(cache)
    assert already in (len(prefix()), len(prefix()) + 1, len(actions))
    outcome = await ResearchController(
        CachedModel(delegate, cache), DiscoveryFixtureTools(), limits, explore_collection_sink=sink
    ).investigate(GOAL, explore_collection_intent=ExploreCollectionIntent())
    assert outcome.status == "completed" and len(outcome.stages) == 2
    assert delegate.calls == len(actions)
    assert len(sink.packets) == 2 and len(sink.receipts) == 2
    assert len(sink.ranking_packets) == 2 and len(sink.rankings) == 2 and len(sink.finishes) == 1
    assert outcome.usage.model_calls == len(actions)
    assert outcome.rankings[0].covered_through_ordinal == 0
    assert outcome.final_ranking.covered_through_ordinal == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["stage", "ranking"])
async def test_invalid_server_ack_is_interruption_not_a_repairable_model_mistake(kind):
    class BadSink(FixtureSink):
        async def commit(self, packet):
            receipt = await super().commit(packet)
            if kind == "stage":
                return receipt.model_copy(update={"depth": 2})
            return receipt

        async def rank(self, packet):
            receipt = await super().rank(packet)
            if kind == "ranking":
                return receipt.model_copy(update={"packet_sha256": "f" * 64})
            return receipt

    sink = BadSink()
    model = RecordingModel([*prefix(), lambda messages: ranking_action(messages), insufficient()])
    with pytest.raises(RuntimeError, match="interrupted"):
        await ResearchController(
            model,
            DiscoveryFixtureTools(),
            ResearchLimits(max_model_calls=20, max_actions=16),
            explore_collection_sink=sink,
        ).investigate(GOAL, explore_collection_intent=ExploreCollectionIntent())
    assert model.calls == len(prefix()) + (kind == "ranking")


@pytest.mark.asyncio
async def test_new_schema_cannot_fall_back_to_an_ordinary_positive_conclusion():
    outcome, model, _, sink = await run([*prefix()[:-1], bridge(), insufficient()])
    assert outcome.status == "insufficient_evidence" and not sink.packets
    assert any(
        json.loads(message["content"]).get("observation", {}).get("error")
        == "invalid_action_schema"
        for message in model.messages[-1]
        if message["role"] == "user"
    )


@pytest.mark.asyncio
async def test_shared_action_and_repair_limits_are_not_reset_by_a_saved_bridge():
    bad = {"kind": "not_a_tool", "private": "This must never become a repair observation."}
    actions = [bad, *prefix(), bad, bad, lambda messages: ranking_action(messages)]
    outcome, model, _, sink = await run(actions)
    assert outcome.status == "failed" and outcome.error_code == "repair_limit"
    assert len(outcome.stages) == 1 and not sink.finishes
    assert outcome.usage.model_calls == len(prefix()) + 3
    assert model.calls == len(prefix()) + 3
    assert "This must never become" not in json.dumps(
        [event.model_dump() for event in outcome.events]
    )


@pytest.mark.asyncio
async def test_memory_identity_overflow_stops_before_another_model_call_without_truncating_reads():
    saved = []
    for lead in range(3):
        sources = tuple(
            MemorySourceReference(
                retrieval_id=fixture_id(f"overflow-retrieval:{lead}:{index}"),
                source=SourceReference(
                    source_id=f"s{lead}-{index}", title="m", url=f"https://x.org/{lead}/{index}"
                ),
                content_sha256="d" * 64,
                content_kind="abstract",
            )
            for index in range(64)
        )
        claims = [
            AnswerClaim(
                id=f"C{group}",
                text=f"Synthetic known claim{group}",
                label="known",
                evidence=[
                    ClaimQuote(
                        source_id=sources[group * 8 + offset].source.source_id,
                        quote="A synthetic source sentence.",
                        supports="s",
                    )
                    for offset in range(8)
                ],
            )
            for group in range(8)
        ]
        finding = AnswerFinding(
            outcome="answer",
            question="A synthetic earlier question?",
            summary=claims[0].text,
            claims=claims,
            limitations=["Synthetic sources only."],
        )
        saved.append(
            MemoryFinding(
                reference=MemoryFindingReference(
                    result_id=fixture_id(f"overflow-result{lead}"),
                    run_id=fixture_id(f"overflow-run{lead}"),
                    content_sha256="e" * 64,
                    title="Earlier Ask",
                    summary="Eight classified claims.",
                    outcome="answer",
                ),
                finding=finding,
                sources=sources,
                overview_binding=AskOverviewBinding(claim_ids=("C0",)),
            )
        )

    class Memory:
        def __init__(self):
            self.finding_reads = []

        async def search(self, query):
            return MemorySearchResults(
                findings=tuple(item.reference for item in saved), truncated=False
            )

        async def read_finding(self, result_id):
            self.finding_reads.append(result_id)
            return next(item for item in saved if item.reference.result_id == result_id)

    class MetadataTools:
        def __init__(self):
            self.searches = 0

        async def search(self, query, purpose):
            self.searches += 1
            return [
                SourceReference(
                    source_id=f"searched:{self.searches}:{index}",
                    title="Metadata-only search result",
                    url=f"https://x.org/search/{self.searches}/{index}",
                )
                for index in range(10)
            ]

    memory = Memory()
    outcome, model, _, sink = await run(
        [
            selection(),
            *(
                {
                    "kind": "search_literature",
                    "purpose": "discovery",
                    "query": f"Metadata page{index}",
                }
                for index in range(7)
            ),
            search_memory(),
            *(read_finding(item.reference.result_id) for item in saved),
            insufficient(),
        ],
        tools=MetadataTools(),
        memory=memory,
    )
    assert outcome.status == "limit_reached" and outcome.error_code == "identity_context_limit"
    assert model.calls == 12 and len(memory.finding_reads) == 3
    assert len(outcome.result.memory_reads) == 3 and not sink.packets


@pytest.mark.asyncio
async def test_whole_run_memory_read_limit_survives_field_changes():
    template = MemoryFixture().findings[FIRST_RESULT]
    saved = [
        template.model_copy(
            update={
                "reference": template.reference.model_copy(
                    update={
                        "result_id": fixture_id(f"lead{index}"),
                        "run_id": fixture_id(f"lead-run{index}"),
                    }
                )
            }
        )
        for index in range(11)
    ]

    class Memory:
        def __init__(self):
            self.finding_reads = []
            self.searches = 0

        async def search(self, query):
            batch = saved[:10] if self.searches == 0 else saved[10:]
            self.searches += 1
            return MemorySearchResults(
                findings=tuple(item.reference for item in batch), truncated=False
            )

        async def read_finding(self, result_id):
            self.finding_reads.append(result_id)
            return next(item for item in saved if item.reference.result_id == result_id)

    memory = Memory()
    outcome, model, _, _ = await run(
        [
            selection(),
            search_memory(),
            search_memory(),
            *(read_finding(item.reference.result_id) for item in saved[:5]),
            selection(),
            *(read_finding(item.reference.result_id) for item in saved[5:]),
            insufficient(),
        ],
        memory=memory,
    )
    assert outcome.status == "insufficient_evidence" and model.calls == 16
    assert len(memory.finding_reads) == 10 and len(outcome.result.memory_reads) == 10
    assert saved[-1].reference.result_id not in memory.finding_reads


@pytest.mark.asyncio
async def test_legacy_and_bound_ask_controller_messages_and_results_are_unchanged():
    from explore_controller_baseline import capture_controller_evidence

    original = json.loads(BASELINE.read_text())
    current = await capture_controller_evidence()
    for key in ("legacy_research", "bound_ask", "legacy_discovery"):
        assert current[key] == original[key]


def test_controller_baseline_is_bound_to_the_actual_pushed_sc_release():
    baseline = json.loads(BASELINE.read_text())
    assert baseline["source_commit"] == "a95011ae46fcb3d66b784abab7444167e39c33fc"
    assert baseline["archive_verified_files"] == 284
    assert "provenance" not in baseline
