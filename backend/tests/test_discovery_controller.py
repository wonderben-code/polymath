"""Synthetic adaptive ladder tests; no scientific/native capability claim."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from pydantic import ValidationError
from test_research_controller import (
    LEFT_QUOTE,
    RIGHT_QUOTE,
    SequenceModel,
    bridge,
    source,
)

from polymath.discovery_contracts import (
    DISCOVERY_ACTION_ADAPTER,
    DiscoveryIntent,
    DiscoveryOutcome,
    DiscoveryRejection,
    StageAssessment,
    StageCommitReceipt,
    StageParentFinding,
    StageParentQuote,
)
from polymath.meta_research_contracts import (
    derive_discovery_ladder_profile,
    profile_for_task,
    task_profile_sha256,
)
from polymath.research import ModelReply, ResearchController, ResearchLimits
from polymath.research.discovery import DiscoveryLimits, cited_prior_work_matches
from polymath.research.models import (
    MemoryFindingReference,
    MemorySearchResults,
    MemorySourceReference,
    ProviderFailure,
    SearchResults,
    SourceBibliography,
    SourceCoverage,
    SourceDocument,
    SourceReference,
    VerifiedQuote,
)

THIRD_QUOTE = "Distributed populations retain redundant responses to environmental variation."
RUN_ID = "12345678-1234-1234-1234-123456789abc"


def fixture_id(value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"polymath-discovery-fixture:{value}"))


def assessment_data():
    return {
        "novelty": "uncertain",
        "novelty_rationale": "Only bounded synthetic prior-work searches were performed.",
        "confidence": "tentative",
        "confidence_score": 23,
        "confidence_scope": "uncalibrated_model_judgement",
        "confidence_rationale": "This fixture demonstrates integrity, not scientific validity.",
        "limitations": ["Synthetic evidence is not a scientific discovery."],
    }


def selection_action():
    return {
        "kind": "select_fields",
        "fields": ["Information theory", "Biology"],
        "rationale": "Investigate a synthetic cross-field redundancy comparison.",
    }


def finding_data(stage):
    value = bridge()["conclusion"]
    value["title"] = f"Synthetic discovery stage{stage + 1}"
    value["explanation"] = f"Fixture candidate{stage + 1}; shared evidence is not corroboration."
    if stage:
        value["evidence"][1] |= {"source_id": "third", "quote": THIRD_QUOTE}
    return value


class DiscoveryFixtureTools:
    def __init__(self):
        self.documents = {
            "left": source("left", LEFT_QUOTE),
            "right": source("right", RIGHT_QUOTE),
            "third": source("third", THIRD_QUOTE),
        }
        self.searches = []
        self.reads = []

    async def search(self, query, purpose):
        self.searches.append((query, purpose))
        return (
            [
                SourceReference(
                    source_id=doc.source_id,
                    title=doc.title,
                    url=doc.url,
                )
                for doc in self.documents.values()
            ]
            if purpose == "discovery"
            else []
        )

    async def read(self, source_id):
        self.reads.append(source_id)
        return self.documents[source_id]


class DiscoveryFixtureModel:
    """Choose the next action from actual observations, including generated receipts."""

    def __init__(self, *, stop_after_stage=None, insufficient_after_stage=None):
        self.calls = 0
        self.stop_after_stage = stop_after_stage
        self.insufficient_after_stage = insufficient_after_stage

    async def complete(self, messages, *, max_output_tokens):
        self.calls += 1
        observations = [
            json.loads(m["content"]).get("observation") for m in messages if m["role"] == "user"
        ]
        observations = [value for value in observations if value is not None]
        saved = [value for value in observations if value.get("kind") == "stage_committed"]
        last = observations[-1] if observations else {}
        count = len(saved)
        kind = last.get("kind")
        if not last:
            action = selection_action()
        elif kind == "stage_committed" and count == self.stop_after_stage:
            action = {
                "kind": "finish_saved",
                "stage": {
                    "result_id": last["result_id"],
                    "run_id": last["run_id"],
                    "content_sha256": last["result_sha256"],
                },
                "reason": "Stop with this explicitly preferred saved stage.",
            }
        elif kind == "stage_committed" and count == self.insufficient_after_stage:
            action = {
                "kind": "conclude",
                "conclusion": {
                    "outcome": "insufficient_evidence",
                    "reason": "A higher connection is unsupported.",
                },
            }
        elif kind == "fields_selected":
            action = (
                {"kind": "search_literature", "query": "fixture redundancy", "purpose": "discovery"}
                if not count
                else {"kind": "read_source", "source_id": "third"}
            )
        elif kind == "search_results" and last["purpose"] == "discovery":
            action = {"kind": "read_source", "source_id": "left"}
        elif kind == "source_text" and not count and last["source_id"] == "left":
            action = {"kind": "read_source", "source_id": "right"}
        elif kind == "source_text" and count < 2:
            action = {
                "kind": "search_literature",
                "query": "fixture failures",
                "purpose": "counterevidence",
            }
        elif kind == "search_results" and last["purpose"] == "counterevidence":
            action = {
                "kind": "search_literature",
                "query": "fixture previous connections",
                "purpose": "prior_work",
            }
        elif kind == "search_results" and last["purpose"] == "prior_work":
            action = {
                "kind": "record_bridge" if count < 2 else "conclude",
                "finding" if count < 2 else "conclusion": finding_data(count),
                "assessment": assessment_data(),
            }
        elif kind == "stage_committed" and count == 1:
            action = selection_action()
        elif kind == "stage_committed" and count == 2:
            action = {
                "kind": "select_parents",
                "fields": selection_action()["fields"],
                "rationale": "Compare two saved mappings while preserving primary evidence.",
                "parents": [
                    {
                        "result_id": row["result_id"],
                        "run_id": row["run_id"],
                        "content_sha256": row["result_sha256"],
                    }
                    for row in saved
                ],
            }
        elif kind == "stage_context":
            action = {"kind": "read_stage", "result_id": last["parent_refs"][0]["result_id"]}
        elif kind == "stage_parent":
            wanted = "left" if last["reference"]["result_id"] == saved[0]["result_id"] else "third"
            item = next(item for item in last["sources"] if item["source"]["source_id"] == wanted)
            action = {
                "kind": "read_stage_source",
                "result_id": last["reference"]["result_id"],
                "retrieval_id": item["retrieval_id"],
            }
        elif kind == "source_text" and last.get("parent_result_id") == saved[0]["result_id"]:
            action = {"kind": "read_stage", "result_id": saved[1]["result_id"]}
        elif kind == "source_text":
            action = {
                "kind": "search_literature",
                "query": "fixture higher failures",
                "purpose": "counterevidence",
            }
        else:
            raise AssertionError(f"Unexpected fixture observation {last}")
        return ModelReply(json.dumps(action), 10, 15)


class DiscoveryFixtureSink:
    def __init__(self, tools):
        self.tools = tools
        self.contexts = []
        self.packets = []
        self.receipts = []
        self.finishes = []

    async def begin_context(self, context):
        self.contexts.append(context)

    async def commit(self, packet):
        index = len(self.packets)
        depth = 1 + max(
            (
                self.receipts[
                    next(i for i, r in enumerate(self.receipts) if r.result_id == p.result_id)
                ].depth
                for p in packet.context.parent_refs
            ),
            default=0,
        )
        receipt = StageCommitReceipt(
            stage_id=fixture_id(f"stage{index}"),
            run_id=RUN_ID,
            ordinal=index,
            result_id=fixture_id(f"result{index}"),
            result_sha256=hashlib.sha256(packet.result.model_dump_json().encode()).hexdigest(),
            depth=depth,
            assessment=packet.assessment,
            parent_refs=packet.context.parent_refs,
            action_ordinal=packet.action_ordinal,
            model_request_id=packet.model_request_id,
        )
        self.packets.append(packet)
        self.receipts.append(receipt)
        return receipt

    async def read_parent(self, result_id, context_ordinal):
        index = next(i for i, receipt in enumerate(self.receipts) if receipt.result_id == result_id)
        result, receipt = self.packets[index].result, self.receipts[index]
        documents = {q.source_id: self.tools.documents[q.source_id] for q in result.quotes}
        return StageParentFinding(
            reference=receipt.reference,
            stage_id=receipt.stage_id,
            ordinal=receipt.ordinal,
            depth=receipt.depth,
            finding=result.finding,
            sources=tuple(
                MemorySourceReference(
                    retrieval_id=fixture_id(f"retrieval:{doc.source_id}"),
                    source=SourceReference(source_id=doc.source_id, title=doc.title, url=doc.url),
                    content_sha256=doc.content_sha256,
                    content_kind=doc.content_kind,
                )
                for doc in documents.values()
            ),
            quotes=tuple(
                StageParentQuote(
                    retrieval_id=fixture_id(f"retrieval:{q.source_id}"),
                    version_id=fixture_id(f"version:{q.source_id}"),
                    quote=q,
                )
                for q in result.quotes
            ),
        )

    async def read_parent_source(self, result_id, retrieval_id, context_ordinal):
        return next(
            doc
            for doc in self.tools.documents.values()
            if fixture_id(f"retrieval:{doc.source_id}") == retrieval_id
        )

    async def finish(self, packet):
        self.finishes.append(packet)


async def run_fixture(*, model=None, tools=None, sink=None, limits=None, intent=None):
    tools = tools or DiscoveryFixtureTools()
    sink = sink or DiscoveryFixtureSink(tools)
    model = model or DiscoveryFixtureModel()
    result = await ResearchController(
        model, tools, limits or DiscoveryLimits(), discovery_sink=sink
    ).investigate("", mode="surprise", discovery=intent or DiscoveryIntent())
    return result, model, tools, sink


@pytest.mark.asyncio
async def test_adaptive_two_base_bridges_and_higher_bridge_share_twenty_call_budget():
    result, model, tools, sink = await run_fixture()
    assert isinstance(result, DiscoveryOutcome) and result.status == "completed"
    assert model.calls == result.usage.model_calls == 20
    assert result.usage.input_tokens_reported == 200
    assert [stage.depth for stage in result.stages] == [1, 1, 2]
    assert result.selected_stage == result.stages[2] and result.result is None
    assert [packet.result.usage.model_calls for packet in sink.packets] == [7, 12, 20]
    assert tools.reads == ["left", "right", "third"]
    assert [purpose for _, purpose in tools.searches].count("counterevidence") == 3
    assert [purpose for _, purpose in tools.searches].count("prior_work") == 3
    assert len(sink.finishes) == 1
    assert [e.sequence for e in result.events] == list(range(1, len(result.events) + 1))


@pytest.mark.asyncio
async def test_model_can_finish_one_saved_bridge_without_forced_climb():
    result, _, _, sink = await run_fixture(model=DiscoveryFixtureModel(stop_after_stage=1))
    assert result.status == "completed" and len(result.stages) == 1
    assert result.usage.model_calls == 8 and result.selected_stage == result.stages[0]
    assert len(sink.contexts) == 1


@pytest.mark.asyncio
async def test_unsupported_higher_or_limit_keeps_stages_without_automatic_preference():
    result, _, _, _ = await run_fixture(model=DiscoveryFixtureModel(insufficient_after_stage=1))
    assert result.status == "insufficient_evidence" and len(result.stages) == 1
    assert result.selected_stage is None
    limited, _, _, _ = await run_fixture(limits=DiscoveryLimits(max_model_calls=7))
    assert limited.status == "limit_reached" and len(limited.stages) == 1
    assert limited.selected_stage is None and limited.usage.model_calls == 7


@pytest.mark.asyncio
async def test_new_context_cannot_reuse_prior_search_purposes():
    actions = [
        selection_action(),
        {"kind": "search_literature", "query": "fixtures", "purpose": "discovery"},
        {"kind": "read_source", "source_id": "left"},
        {"kind": "read_source", "source_id": "right"},
        {"kind": "search_literature", "query": "failures", "purpose": "counterevidence"},
        {"kind": "search_literature", "query": "prior", "purpose": "prior_work"},
        {"kind": "record_bridge", "finding": finding_data(0), "assessment": assessment_data()},
        selection_action(),
        {"kind": "record_bridge", "finding": finding_data(0), "assessment": assessment_data()},
        {
            "kind": "conclude",
            "conclusion": {
                "outcome": "insufficient_evidence",
                "reason": "Fresh challenges are needed.",
            },
        },
    ]
    result, _, _, sink = await run_fixture(model=SequenceModel(actions))
    assert result.status == "insufficient_evidence" and len(sink.packets) == 1
    assert any(event.kind == "action_rejected" for event in result.events)


@pytest.mark.asyncio
async def test_stage_commit_control_stop_never_triggers_next_paid_call():
    class Stop(BaseException):
        pass

    tools = DiscoveryFixtureTools()

    class LostSink(DiscoveryFixtureSink):
        async def commit(self, packet):
            await super().commit(packet)
            raise Stop("commit_response_lost")

    model = DiscoveryFixtureModel()
    with pytest.raises(Stop):
        await run_fixture(model=model, tools=tools, sink=LostSink(tools))
    assert model.calls == 7


@pytest.mark.parametrize("failure", ["value_error", "wrong_receipt", "malformed_receipt"])
@pytest.mark.asyncio
async def test_response_loss_or_bad_receipt_after_commit_never_becomes_paid_repair(failure):
    tools = DiscoveryFixtureTools()

    class BadReceiptSink(DiscoveryFixtureSink):
        async def commit(self, packet):
            receipt = await super().commit(packet)
            if failure == "value_error":
                raise ValueError("raw internal database details must not become a model repair")
            if failure == "malformed_receipt":
                return receipt.model_dump()
            return receipt.model_copy(update={"ordinal": 3})

    model = DiscoveryFixtureModel()
    sink = BadReceiptSink(tools)
    with pytest.raises(RuntimeError, match="^discovery_commit_"):
        await run_fixture(model=model, tools=tools, sink=sink)
    assert model.calls == 7 and len(sink.packets) == 1


@pytest.mark.asyncio
async def test_only_explicit_safe_stage_validation_rejection_can_use_bounded_repair():
    tools = DiscoveryFixtureTools()

    class RejectedSink(DiscoveryFixtureSink):
        async def commit(self, packet):
            raise DiscoveryRejection("discovery_parent_needs_review")

    class RevisionAwareModel(DiscoveryFixtureModel):
        async def complete(self, messages, *, max_output_tokens):
            observation = json.loads(messages[-1]["content"]).get("observation", {})
            if observation.get("error") == "discovery_parent_needs_review":
                self.calls += 1
                return ModelReply(
                    json.dumps(
                        {
                            "kind": "conclude",
                            "conclusion": {
                                "outcome": "insufficient_evidence",
                                "reason": "The premise requires review before reuse.",
                            },
                        }
                    ),
                    10,
                    15,
                )
            return await super().complete(messages, max_output_tokens=max_output_tokens)

    result, model, _, sink = await run_fixture(
        model=RevisionAwareModel(), tools=tools, sink=RejectedSink(tools)
    )
    assert result.status == "insufficient_evidence" and not sink.packets
    assert model.calls == 8
    assert str(DiscoveryRejection("secret provider body; unsafe")) == "discovery_stage_rejected"


@pytest.mark.asyncio
async def test_higher_bridge_must_retain_exact_parent_passage_not_another_quote_same_paper():
    alternative = "A different exact passage describes a separate mechanism."
    tools = DiscoveryFixtureTools()
    tools.documents["left"] = source("left", LEFT_QUOTE + " " + alternative)

    class DifferentPassageModel(DiscoveryFixtureModel):
        async def complete(self, messages, *, max_output_tokens):
            reply = await super().complete(messages, max_output_tokens=max_output_tokens)
            action = json.loads(reply.content)
            if action["kind"] == "conclude" and "assessment" in action:
                action["conclusion"]["evidence"][0]["quote"] = alternative
            return ModelReply(json.dumps(action), reply.input_tokens, reply.output_tokens)

    result, model, _, sink = await run_fixture(model=DifferentPassageModel(), tools=tools)
    assert result.status == "limit_reached" and result.selected_stage is None
    assert len(sink.packets) == 2 and model.calls == 20 and not sink.finishes
    assert any(event.kind == "action_rejected" for event in result.events)


@pytest.mark.asyncio
async def test_depth_bound_preserves_bases_without_inventing_higher_result():
    result, model, _, sink = await run_fixture(intent=DiscoveryIntent(max_depth=1))
    assert result.status == "limit_reached" and result.selected_stage is None
    assert len(sink.packets) == 2 and model.calls == 20


@pytest.mark.asyncio
async def test_ladder_never_consults_future_durable_identity_context_during_replay():
    class FutureTools(DiscoveryFixtureTools):
        async def identity_context(self):
            raise AssertionError("Later persisted locators are not replay-visible observations")

    result, model, _, _ = await run_fixture(tools=FutureTools())
    assert result.status == "completed" and model.calls == 20


@pytest.mark.asyncio
async def test_observed_uncited_search_alias_connector_survives_context_reset():
    class AliasTools(DiscoveryFixtureTools):
        def __init__(self):
            super().__init__()
            self.documents["left"] = self.documents["left"].model_copy(
                update={
                    "bibliography": SourceBibliography(doi="10.5555/fixture-left"),
                }
            )
            self.documents["third"] = self.documents["third"].model_copy(
                update={
                    "bibliography": SourceBibliography(arxiv_id="2401.12345"),
                }
            )

        async def search(self, query, purpose):
            if purpose == "counterevidence":
                return [
                    SourceReference(
                        source_id="uncited-connector",
                        title="Bibliographic metadata only",
                        url="https://example.org/fixture-connector",
                        bibliography=SourceBibliography(
                            doi="10.5555/fixture-left",
                            arxiv_id="2401.12345",
                        ),
                    )
                ]
            if purpose == "discovery":
                return [
                    SourceReference(
                        source_id=doc.source_id,
                        title=doc.title,
                        url=doc.url,
                        bibliography=doc.bibliography,
                    )
                    for doc in self.documents.values()
                ]
            return []

    class AliasAwareModel(DiscoveryFixtureModel):
        async def complete(self, messages, *, max_output_tokens):
            observation = json.loads(messages[-1]["content"]).get("observation", {})
            if observation.get("error") == "distinct_sources_required":
                self.calls += 1
                return ModelReply(
                    json.dumps(
                        {
                            "kind": "conclude",
                            "conclusion": {
                                "outcome": "insufficient_evidence",
                                "reason": "The later sources identify the same paper.",
                            },
                        }
                    ),
                    10,
                    15,
                )
            return await super().complete(messages, max_output_tokens=max_output_tokens)

    result, _, _, sink = await run_fixture(model=AliasAwareModel(), tools=AliasTools())
    assert result.status == "insufficient_evidence" and len(sink.packets) == 1
    assert result.selected_stage is None


@pytest.mark.asyncio
async def test_both_failed_providers_cannot_satisfy_fresh_challenge_or_prior_work():
    class FailedTools(DiscoveryFixtureTools):
        async def search(self, query, purpose):
            if purpose != "discovery":
                return SearchResults(
                    sources=(),
                    provider_failures=(
                        ProviderFailure(provider="openalex", code="timeout"),
                        ProviderFailure(provider="arxiv", code="access_denied"),
                    ),
                )
            return await super().search(query, purpose)

    result, _, _, sink = await run_fixture(
        tools=FailedTools(), limits=DiscoveryLimits(max_model_calls=7)
    )
    assert result.status == "limit_reached" and not sink.packets


@pytest.mark.asyncio
async def test_known_novelty_requires_read_cited_prior_work_not_empty_search():
    class KnownModel(DiscoveryFixtureModel):
        async def complete(self, messages, *, max_output_tokens):
            reply = await super().complete(messages, max_output_tokens=max_output_tokens)
            action = json.loads(reply.content)
            if "assessment" in action:
                action["assessment"]["novelty"] = "known"
            return ModelReply(json.dumps(action), reply.input_tokens, reply.output_tokens)

    result, _, _, sink = await run_fixture(
        model=KnownModel(), limits=DiscoveryLimits(max_model_calls=7)
    )
    assert result.status == "limit_reached" and not sink.packets


@pytest.mark.asyncio
async def test_public_hypothesis_notes_are_model_actions_and_never_positive_findings():
    model = SequenceModel(
        [
            selection_action(),
            {
                "kind": "record_note",
                "hypothesis_id": "lead1",
                "state": "proposed",
                "summary": "A possible redundancy analogy.",
                "reason": "Inspect the original papers.",
            },
            {
                "kind": "record_note",
                "hypothesis_id": "lead1",
                "state": "discarded",
                "summary": "Drop this candidate.",
                "reason": "The mechanisms do not establish equivalence.",
            },
            {
                "kind": "conclude",
                "conclusion": {
                    "outcome": "insufficient_evidence",
                    "reason": "The candidate was not supported.",
                },
            },
        ]
    )
    result, _, _, sink = await run_fixture(model=model)
    assert result.status == "insufficient_evidence" and not sink.packets
    assert len([event for event in result.events if event.kind == "research_note"]) == 2
    assert result.usage.model_calls == 4


@pytest.mark.parametrize("outcome", ["bridge", "answer"])
@pytest.mark.asyncio
async def test_external_parent_negative_provenance_and_answer_rejection(outcome):
    _, _, tools, original_sink = await run_fixture(model=DiscoveryFixtureModel(stop_after_stage=1))
    saved = await original_sink.read_parent(original_sink.receipts[0].result_id, 0)
    reference = saved.reference.model_copy(update={"run_id": fixture_id("older-owned-run")})
    external = saved.model_copy(
        update={
            "reference": reference,
            "stage_id": None,
            "ordinal": None,
            "depth": 0,
        }
    )

    class OwnedMemory:
        async def search(self, query):
            return MemorySearchResults(
                findings=(
                    MemoryFindingReference(
                        **reference.model_dump(),
                        title="Owned fixture",
                        summary="Earlier hypothesis",
                        outcome=outcome,
                    ),
                ),
                truncated=False,
            )

    class ExternalSink(DiscoveryFixtureSink):
        parent_reads = 0

        async def read_parent(self, result_id, context_ordinal):
            self.parent_reads += 1
            assert outcome == "bridge"
            return external

    sink = ExternalSink(tools)
    model = SequenceModel(
        [
            selection_action(),
            {"kind": "search_memory", "query": "fixture"},
            {"kind": "read_stage", "result_id": reference.result_id},
            {
                "kind": "conclude",
                "conclusion": {
                    "outcome": "insufficient_evidence",
                    "reason": "More original evidence is needed.",
                },
            },
        ]
    )
    result = await ResearchController(
        model, tools, DiscoveryLimits(), memory=OwnedMemory(), discovery_sink=sink
    ).investigate("", mode="surprise", discovery=DiscoveryIntent())
    assert result.status == "insufficient_evidence"
    assert result.result.memory_reads == ((reference,) if outcome == "bridge" else ())
    assert sink.parent_reads == (1 if outcome == "bridge" else 0)


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence_score": True},
        {"confidence_score": -1},
        {"confidence_score": 101},
        {"confidence_scope": "calibrated_probability"},
        {"novelty": "proved_new"},
        {"confidence_rationale": "bad\x00text"},
    ],
)
def test_stage_assessment_rejects_fabricated_calibration_and_bad_text(changes):
    with pytest.raises(ValidationError):
        StageAssessment.model_validate_json(json.dumps(assessment_data() | changes))


def test_assessment_score_is_required_and_ordinary_limits_remain_sixteen():
    value = assessment_data()
    del value["confidence_score"]
    with pytest.raises(ValidationError):
        StageAssessment.model_validate_json(json.dumps(value))
    with pytest.raises(ValueError):
        ResearchLimits(max_actions=20)
    assert DiscoveryLimits(max_actions=20, model_timeout_seconds=90).max_actions == 20
    with pytest.raises(ValueError):
        DiscoveryLimits(max_actions=21)


def test_ordinary_bridge_schema_does_not_gain_ladder_fields():
    from polymath.research.models import ACTION_ADAPTER

    ACTION_ADAPTER.validate_json(json.dumps(bridge()))
    with pytest.raises(ValidationError):
        DISCOVERY_ACTION_ADAPTER.validate_json(json.dumps(bridge()))


@pytest.mark.parametrize("kind", ["read_memory_finding", "read_memory_source"])
def test_ladder_excludes_legacy_memory_read_actions_and_preserves_ordinary_schema(kind):
    from polymath.research.models import ACTION_ADAPTER

    action = {"kind": kind, "result_id": fixture_id("owned-legacy-finding")}
    if kind == "read_memory_source":
        action["retrieval_id"] = fixture_id("owned-legacy-source")
    ACTION_ADAPTER.validate_json(json.dumps(action))
    with pytest.raises(ValidationError):
        DISCOVERY_ACTION_ADAPTER.validate_json(json.dumps(action))


def test_native_extension_omission_explicit_selection_and_reviewed_bounds():
    from test_meta_research_contracts import research_fixture

    proposal, _, _, _, _, manifest = research_fixture()
    old = manifest.model_dump(mode="json")
    assert "discovery_ladder_profile" not in old
    binding = derive_discovery_ladder_profile(proposal, DiscoveryIntent())
    changed = manifest.model_copy(update={"discovery_ladder_profile": binding})
    assert profile_for_task(changed, "research").max_actions == 16
    assert profile_for_task(changed, "research", discovery=DiscoveryIntent()).max_actions == 20
    assert binding.profile_sha256 == task_profile_sha256(binding.profile)
    with pytest.raises(ValueError):
        profile_for_task(manifest, "research", discovery=DiscoveryIntent())
    with pytest.raises(ValueError):
        profile_for_task(changed, "research", discovery=DiscoveryIntent(max_stages=4))


def test_historical_ladder_profile_is_loadable_without_current_prompt_equality(monkeypatch):
    from test_meta_research_contracts import research_fixture

    from polymath import meta_research_contracts as contracts

    proposal, _, _, _, _, manifest = research_fixture()
    binding = derive_discovery_ladder_profile(proposal, DiscoveryIntent())
    historical_profile = binding.profile.model_copy(
        update={
            "controller_protocol": "polymath-meta-discovery-controller-old",
            "controller_sha256": "f" * 64,
        }
    )
    historical_binding = binding.model_copy(
        update={
            "profile": historical_profile,
            "profile_sha256": task_profile_sha256(historical_profile),
        }
    )
    historical = manifest.model_copy(update={"discovery_ladder_profile": historical_binding})
    loaded = contracts.MetaResearchManifest.model_validate_json(historical.model_dump_json())
    assert profile_for_task(loaded, "research", discovery=DiscoveryIntent()) == historical_profile
    monkeypatch.setattr(contracts, "discovery_controller_sha256", lambda: "a" * 64)
    assert derive_discovery_ladder_profile(proposal, DiscoveryIntent()) != historical_binding


def test_native_ladder_preflight_uses_actual_fixed_character_size():
    from test_meta_research_contracts import research_fixture

    proposal, _, _, _, _, _ = research_fixture()
    small = proposal.model_copy(
        update={
            "application_limits": proposal.application_limits.model_copy(
                update={
                    "max_context_characters": 1000,
                }
            ),
        }
    )
    with pytest.raises(ValueError, match="fixed discovery prompt/schema"):
        derive_discovery_ladder_profile(small, DiscoveryIntent())


@pytest.mark.parametrize("hit_kind", ["arxiv_abstract", "transitive_doi", "unrelated"])
def test_prior_work_identity_ties_exact_read_html_window_to_abstract_or_doi_hit(hit_kind):
    text = LEFT_QUOTE + " The fixture preserves Unicode α."
    coverage = SourceCoverage(
        snapshot_id=fixture_id("full-html-snapshot"),
        snapshot_sha256="c" * 64,
        arxiv_id="2401.12345",
        arxiv_version=2,
        start=100,
        end=100 + len(text),
        total=1000,
    )
    window = SourceDocument(
        source_id=f"arxiv-html:{coverage.snapshot_id}:{coverage.start}:{coverage.end}",
        title="Exact saved HTML fixture window",
        url="https://arxiv.org/html/2401.12345v2",
        bibliography=SourceBibliography(arxiv_id="2401.12345", arxiv_version=2),
        coverage=coverage,
        text=text,
        content_kind="full_text",
        retrieved_at=source("left", LEFT_QUOTE).retrieved_at,
        extraction_method="fixture_html",
    )
    quote = VerifiedQuote(
        source_id=window.source_id,
        side="left",
        quote=text,
        supports="Actual fixture quote",
        start=0,
        end=len(text),
        source_sha256=window.content_sha256,
        content_kind="full_text",
    )
    if hit_kind == "arxiv_abstract":
        hit = SourceReference(
            source_id="arxiv:2401.12345",
            title="Abstract search hit",
            url="https://arxiv.org/abs/2401.12345",
        )
    else:
        doi = "10.5555/related-paper" if hit_kind == "transitive_doi" else "10.5555/unrelated"
        hit = SourceReference(
            source_id=f"doi:{doi}",
            title="DOI metadata search hit",
            url=f"https://doi.org/{doi}",
            bibliography=SourceBibliography(doi=doi),
        )
    connector = SourceReference(
        source_id="openalex:W123456",
        title="Observed bibliographic connector",
        url="https://openalex.org/W123456",
        bibliography=SourceBibliography(doi="10.5555/related-paper", arxiv_id="2401.12345"),
    )
    context = (window, hit, connector)
    assert cited_prior_work_matches(
        (quote,), {window.source_id: window}, (hit,), alias_context=context
    ) is (hit_kind != "unrelated")
    assert not cited_prior_work_matches((quote,), {}, (hit,), alias_context=context)
    metadata = SourceReference(
        source_id=window.source_id,
        title=window.title,
        url=window.url,
        bibliography=window.bibliography,
        coverage=window.coverage,
    )
    assert not cited_prior_work_matches(
        (quote,), {window.source_id: metadata}, (hit,), alias_context=context
    )
    assert not cited_prior_work_matches(
        (quote.model_copy(update={"source_sha256": "0" * 64}),),
        {window.source_id: window},
        (hit,),
        alias_context=context,
    )


@pytest.mark.asyncio
async def test_discovery_requires_explicit_sink_and_surprise_mode():
    with pytest.raises(ValueError):
        await ResearchController(SequenceModel([]), DiscoveryFixtureTools()).investigate(
            "", mode="surprise", discovery=DiscoveryIntent()
        )
    with pytest.raises(ValueError):
        await ResearchController(SequenceModel([]), DiscoveryFixtureTools()).investigate(
            "concept", mode="explore", discovery=DiscoveryIntent()
        )
