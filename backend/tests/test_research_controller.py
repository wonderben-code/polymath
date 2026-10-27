"""Synthetic fixtures test protocol behavior, not scientific or live Meta capability."""

import json
from datetime import UTC, datetime

import pytest

from polymath.research import ModelError, ModelReply, ResearchController, ResearchLimits
from polymath.research.models import ProviderFailure, SearchResults, SourceDocument, SourceReference

LEFT_QUOTE = "Shared redundancy protects messages against corruption."
RIGHT_QUOTE = "Redundant biochemical pathways protect signals against perturbations."


def source(source_id: str, text: str) -> SourceDocument:
    return SourceDocument(
        source_id=source_id,
        title=f"Synthetic fixture {source_id}",
        url=f"https://example.org/fixtures/{source_id}",
        text=text,
        content_kind="abstract",
        retrieved_at=datetime(2026, 10, 27, tzinfo=UTC),
        extraction_method="test_fixture",
    )


def bridge() -> dict:
    return {
        "kind": "conclude",
        "conclusion": {
            "outcome": "bridge",
            "title": "Synthetic fixture analogy",
            "field_left": "Information theory",
            "field_right": "Biology",
            "explanation": "Fixture-only comparison of redundant representations.",
            "mappings": [
                {
                    "left": "Redundant messages",
                    "right": "Redundant pathways",
                    "assumption": "Comparison requires a specified noise model.",
                }
            ],
            "open_question": "Does the mapping survive a realistic noise model?",
            "strongest_objection": "The two systems have different mechanisms.",
            "objection_verdict": "An analogy remains; equivalence is not established.",
            "novelty_limitations": "Synthetic fixtures cannot establish novelty or a real finding.",
            "evidence": [
                {
                    "source_id": "left",
                    "side": "left",
                    "quote": LEFT_QUOTE,
                    "supports": "Redundancy",
                },
                {
                    "source_id": "right",
                    "side": "right",
                    "quote": RIGHT_QUOTE,
                    "supports": "Redundancy",
                },
            ],
        },
    }


class Tools:
    def __init__(self, *, contradictory=False):
        self.reads = []
        self.searches = []
        self.contradictory = contradictory

    async def search(self, query, purpose):
        self.searches.append((query, purpose))
        return (
            [
                SourceReference(
                    source_id=side,
                    title=f"Synthetic fixture {side}",
                    url=f"https://example.org/fixtures/{side}",
                )
                for side in ("left", "right")
            ]
            if purpose == "discovery"
            else []
        )

    async def read(self, source_id):
        self.reads.append(source_id)
        text = LEFT_QUOTE if source_id == "left" else RIGHT_QUOTE
        if source_id == "right" and self.contradictory:
            text = "Fixture counterevidence: this proposed redundancy mechanism does not exist."
        return source(source_id, text)


class SequenceModel:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.calls = 0

    async def complete(self, messages, *, max_output_tokens):
        self.calls += 1
        action = next(self.actions)
        return ModelReply(action if isinstance(action, str) else json.dumps(action), 10, 15)


class AdaptiveFixture:
    """Fixture responds to observed source text, unlike a fixed list of conclusions."""

    def __init__(self):
        self.seen = []

    async def complete(self, messages, *, max_output_tokens):
        observation = json.loads(messages[-1]["content"]).get("observation")
        self.seen.append(observation)
        if observation is None:
            action = {
                "kind": "search_literature",
                "query": "fixture redundancy",
                "purpose": "discovery",
            }
        elif observation.get("kind") == "search_results" and observation["purpose"] == "discovery":
            action = {"kind": "read_source", "source_id": observation["sources"][0]["source_id"]}
        elif observation.get("kind") == "source_text" and observation["source_id"] == "left":
            action = {"kind": "read_source", "source_id": "right"}
        elif observation.get("kind") == "source_text" and "counterevidence" in observation["text"]:
            action = {
                "kind": "conclude",
                "conclusion": {
                    "outcome": "insufficient_evidence",
                    "reason": "Fixture source contradicts the hypothesis.",
                },
            }
        elif observation.get("kind") == "source_text":
            action = {
                "kind": "search_literature",
                "query": "fixture failures",
                "purpose": "counterevidence",
            }
        elif observation.get("purpose") == "counterevidence":
            action = {
                "kind": "search_literature",
                "query": "fixture prior connections",
                "purpose": "prior_work",
            }
        else:
            action = bridge()
        return ModelReply(json.dumps(action), 10, 15)


@pytest.mark.asyncio
async def test_partial_provider_failure_is_visible_to_model_and_activity():
    class PartialTools(Tools):
        async def search(self, query, purpose):
            return SearchResults(
                sources=tuple(await super().search(query, purpose)),
                provider_failures=(ProviderFailure(provider="arxiv", code="rate_limited"),),
            )

    class CoverageAwareFixture:
        async def complete(self, messages, *, max_output_tokens):
            observation = json.loads(messages[-1]["content"]).get("observation")
            if observation is None:
                action = {
                    "kind": "search_literature",
                    "query": "fixture redundancy",
                    "purpose": "discovery",
                }
            else:
                assert observation["provider_failures"] == [
                    {"provider": "arxiv", "code": "rate_limited"}
                ]
                assert len(observation["sources"]) == 2
                action = {
                    "kind": "conclude",
                    "conclusion": {
                        "outcome": "insufficient_evidence",
                        "reason": "Fixture search coverage is incomplete.",
                    },
                }
            return ModelReply(json.dumps(action), 10, 15)

    result = await ResearchController(CoverageAwareFixture(), PartialTools()).investigate(
        "A synthetic partial-provider exercise"
    )
    assert result.status == "insufficient_evidence"
    assert any("Some providers were unavailable" in event.summary for event in result.events)


@pytest.mark.asyncio
async def test_observations_drive_next_action_and_citations_preserve_source_version():
    tools = Tools()
    model = AdaptiveFixture()
    result = await ResearchController(model, tools).investigate("A synthetic protocol exercise")
    assert result.status == "bridge"
    assert tools.reads == ["left", "right"]
    assert [purpose for _, purpose in tools.searches] == [
        "discovery",
        "counterevidence",
        "prior_work",
    ]
    assert result.usage.model_calls == 6
    assert result.usage.input_tokens_reported == 60
    assert result.usage.output_tokens_reported == 90
    assert result.usage.calls_without_usage == 0
    assert [event.sequence for event in result.events] == list(range(1, len(result.events) + 1))
    for quote in result.quotes:
        document = next(item for item in result.sources if item.source_id == quote.source_id)
        assert document.text[quote.start : quote.end] == quote.quote
        assert quote.source_sha256 == document.content_sha256
        assert quote.content_kind == "abstract"
    assert result.validation_scope == "source_identity_and_quote_integrity"


@pytest.mark.asyncio
async def test_changed_evidence_changes_fixture_decision_to_rejection():
    result = await ResearchController(AdaptiveFixture(), Tools(contradictory=True)).investigate(
        "Fixture"
    )
    assert result.status == "insufficient_evidence"
    assert result.finding.reason == "Fixture source contradicts the hypothesis."
    assert not result.quotes
    assert result.usage.model_calls == 4


@pytest.mark.asyncio
async def test_malformed_actions_cannot_escape_repair_budget():
    model = SequenceModel(["not JSON", {"kind": "shell", "command": "anything"}, "still invalid"])
    result = await ResearchController(model, Tools(), ResearchLimits(max_repairs=1)).investigate(
        "Fixture"
    )
    assert result.status == "failed"
    assert result.error_code == "repair_limit"
    assert model.calls == 2


@pytest.mark.asyncio
async def test_unknown_sources_never_reach_retrieval_tool():
    tools = Tools()
    result = await ResearchController(
        SequenceModel([{"kind": "read_source", "source_id": "invented"}]),
        tools,
        ResearchLimits(max_repairs=0),
    ).investigate("Fixture")
    assert result.error_code == "repair_limit"
    assert tools.reads == []


@pytest.mark.asyncio
async def test_fabricated_quotes_rejected_even_after_required_searches():
    class FabricatingModel(AdaptiveFixture):
        async def complete(self, messages, *, max_output_tokens):
            reply = await super().complete(messages, max_output_tokens=max_output_tokens)
            action = json.loads(reply.content)
            if action["kind"] == "conclude":
                action["conclusion"]["evidence"][0]["quote"] = "This quotation was never retrieved."
            return ModelReply(json.dumps(action), 10, 15)

    result = await ResearchController(
        FabricatingModel(), Tools(), ResearchLimits(max_repairs=0)
    ).investigate("Fixture")
    assert result.error_code == "repair_limit"
    assert result.status == "failed"
    assert not result.quotes


@pytest.mark.asyncio
async def test_bridge_without_read_sources_and_prior_work_is_rejected():
    result = await ResearchController(
        SequenceModel([bridge()]), Tools(), ResearchLimits(max_repairs=0)
    ).investigate("Fixture")
    assert result.error_code == "repair_limit"
    assert not result.quotes


@pytest.mark.asyncio
async def test_tool_failures_are_observable_without_leaking_exception_content():
    secret = "secret-fixture-value"

    class FailingTools(Tools):
        async def search(self, query, purpose):
            raise RuntimeError(secret)

    class RecoveryModel:
        async def complete(self, messages, *, max_output_tokens):
            if len(messages) == 2:
                return ModelReply(
                    json.dumps(
                        {"kind": "search_literature", "query": "fixture", "purpose": "discovery"}
                    )
                )
            assert secret not in json.dumps(messages)
            assert (
                json.loads(messages[-1]["content"])["observation"]["error"]
                == "literature_search_failed"
            )
            return ModelReply(
                json.dumps(
                    {
                        "kind": "conclude",
                        "conclusion": {
                            "outcome": "insufficient_evidence",
                            "reason": "Source access is unavailable.",
                        },
                    }
                )
            )

    result = await ResearchController(RecoveryModel(), FailingTools()).investigate("Fixture")
    assert result.status == "insufficient_evidence"
    assert secret not in result.model_dump_json()
    assert result.usage.calls_without_usage == 2


@pytest.mark.asyncio
async def test_model_errors_are_sanitized_and_uncertain_usage_retained():
    class FailingModel:
        async def complete(self, messages, *, max_output_tokens):
            raise ModelError("a-secret-fixture-value")

    result = await ResearchController(FailingModel(), Tools()).investigate("Fixture")
    assert result.status == "failed"
    assert result.error_code == "model_failed"
    assert result.usage.calls_without_usage == 1
    assert "a-secret-fixture-value" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_call_and_context_limits_stop_without_automatic_retries():
    model = SequenceModel(
        [{"kind": "search_literature", "query": "fixture", "purpose": "discovery"}]
    )
    result = await ResearchController(
        model, Tools(), ResearchLimits(max_model_calls=1)
    ).investigate("Fixture")
    assert result.status == "limit_reached"
    assert model.calls == 1
    context_model = SequenceModel([])
    result = await ResearchController(
        context_model, Tools(), ResearchLimits(max_context_characters=1000)
    ).investigate("Fixture")
    assert result.error_code == "context_limit"
    assert context_model.calls == 0


def test_limits_and_source_metadata_are_validated():
    with pytest.raises(ValueError):
        ResearchLimits(max_model_calls=100)
    with pytest.raises(ValueError):
        ResearchLimits(max_actions=True)
    with pytest.raises(ValueError):
        SourceDocument(
            source_id="x",
            title="Fixture",
            url="https://example.org/x",
            text="Fixture",
            content_kind="abstract",
            retrieved_at=datetime(2026, 10, 27),
            extraction_method="fixture",
        )


@pytest.mark.asyncio
async def test_slow_tools_are_cancelled_and_observed_as_failure():
    import asyncio

    class SlowTools(Tools):
        async def search(self, query, purpose):
            await asyncio.sleep(1)
            return []

    model = SequenceModel(
        [
            {"kind": "search_literature", "query": "fixture", "purpose": "discovery"},
            {
                "kind": "conclude",
                "conclusion": {
                    "outcome": "insufficient_evidence",
                    "reason": "Tool did not respond.",
                },
            },
        ]
    )
    result = await ResearchController(
        model, SlowTools(), ResearchLimits(tool_timeout_seconds=0.01)
    ).investigate("Fixture")
    assert result.status == "insufficient_evidence"
    assert any(event.kind == "tool_failed" for event in result.events)


@pytest.mark.asyncio
async def test_conflicting_source_identity_in_one_search_is_rejected():
    class ConflictingTools(Tools):
        async def search(self, query, purpose):
            return [
                SourceReference(source_id="same", title="First", url="https://example.org/first"),
                SourceReference(source_id="same", title="Second", url="https://example.org/second"),
            ]

    result = await ResearchController(
        SequenceModel(
            [
                {"kind": "search_literature", "query": "fixture", "purpose": "discovery"},
                {"kind": "read_source", "source_id": "same"},
            ]
        ),
        ConflictingTools(),
        ResearchLimits(max_repairs=0),
    ).investigate("Fixture")
    assert result.status == "failed"
    assert result.error_code == "repair_limit"
    assert not result.sources


@pytest.mark.asyncio
async def test_model_wall_clock_limit_preserves_unknown_usage():
    import asyncio

    class SlowModel:
        async def complete(self, messages, *, max_output_tokens):
            await asyncio.sleep(1)
            return ModelReply("{}")

    result = await ResearchController(
        SlowModel(), Tools(), ResearchLimits(model_timeout_seconds=0.01)
    ).investigate("Fixture")
    assert result.status == "failed"
    assert result.error_code == "model_timeout_outcome_unknown"
    assert result.usage.calls_without_usage == 1


@pytest.mark.asyncio
async def test_read_document_must_match_discovered_canonical_url():
    class MismatchingTools(Tools):
        async def read(self, source_id):
            return source(source_id, LEFT_QUOTE).model_copy(
                update={"url": "https://example.org/different-paper"}
            )

    result = await ResearchController(
        SequenceModel(
            [
                {"kind": "search_literature", "query": "fixture", "purpose": "discovery"},
                {"kind": "read_source", "source_id": "left"},
                {
                    "kind": "conclude",
                    "conclusion": {
                        "outcome": "insufficient_evidence",
                        "reason": "Identity could not be established.",
                    },
                },
            ]
        ),
        MismatchingTools(),
    ).investigate("Fixture")
    assert result.status == "insufficient_evidence"
    assert not result.sources
    assert any(event.kind == "tool_failed" for event in result.events)


@pytest.mark.asyncio
async def test_duplicate_canonical_urls_cannot_supply_two_independent_evidence_sides():
    class AliasedTools(Tools):
        async def search(self, query, purpose):
            results = await super().search(query, purpose)
            return [
                item.model_copy(update={"url": "https://example.org/same-paper"})
                for item in results
            ]

        async def read(self, source_id):
            return (await super().read(source_id)).model_copy(
                update={"url": "https://example.org/same-paper"}
            )

    result = await ResearchController(
        AdaptiveFixture(), AliasedTools(), ResearchLimits(max_repairs=0)
    ).investigate("Fixture")
    assert result.status == "failed"
    assert result.error_code == "repair_limit"
    assert not result.quotes
