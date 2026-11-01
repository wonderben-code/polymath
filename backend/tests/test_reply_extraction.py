"""A model reply with prose or a code fence around its JSON action still counts."""

import json

import pytest
from test_research_modes import ModeFixtureTools, rejection, research_prefix

from polymath.research.controller import ResearchController
from polymath.research.meta import ModelReply
from polymath.research.reply import action_json

SEARCH = {"kind": "search_literature", "query": "sandpile model", "purpose": "discovery"}


@pytest.mark.parametrize(
    "reply",
    [
        json.dumps(SEARCH),
        # The exact shape Muse Spark produced on the first live run.
        "SOC spans physics, faults, and brain avalanches — gathering cross-field evidence next."
        + json.dumps(SEARCH),
        "Hypothesis: faults realise sandpile scaling.\n\n" + json.dumps(SEARCH) + "\n\nNext: read.",
        "```json\n" + json.dumps(SEARCH) + "\n```",
        "Here is my action:\n```\n" + json.dumps(SEARCH, indent=2) + "\n```",
    ],
)
def test_the_action_is_found_inside_prose_or_fences(reply):
    assert json.loads(action_json(reply)) == SEARCH


def test_braces_inside_strings_and_a_second_object_do_not_confuse_it():
    first = {"kind": "record_note", "summary": 'a {b} "c" } d', "state": "proposed"}
    reply = "Note:" + json.dumps(first) + " and then " + json.dumps(SEARCH)
    assert json.loads(action_json(reply)) == first


@pytest.mark.parametrize("reply", ["no json here", "{unbalanced", "", "}{"])
def test_a_reply_without_a_balanced_object_is_returned_unchanged(reply):
    assert action_json(reply) == reply


class ProseFixtureModel:
    """Scripted replies that wrap every action in a sentence, as the live model did."""

    def __init__(self, actions):
        self.actions = iter(actions)
        self.observations = []

    async def complete(self, messages, *, max_output_tokens):
        self.observations.append(json.loads(messages[-1]["content"]).get("observation"))
        return ModelReply(
            "Reasoning first — then the action.\n" + json.dumps(next(self.actions)), 10, 15
        )


@pytest.mark.asyncio
async def test_a_whole_investigation_of_prose_wrapped_actions_is_never_rejected():
    model = ProseFixtureModel([*research_prefix(), rejection("Synthetic: not enough.")])
    result = await ResearchController(model, ModeFixtureTools()).investigate("Synthetic concept")
    assert result.status == "insufficient_evidence"
    assert not any(observation and observation.get("error") for observation in model.observations)


def test_stage_protocols_parse_a_recorded_reply_with_prose_around_it():
    from polymath.explore_collection_contracts import ExploreCollectionIntent
    from polymath.stage_protocols import StageProtocolBinding

    binding = StageProtocolBinding(
        mode="explore",
        intent=ExploreCollectionIntent(protocol="polymath-explore-collection-v1", max_bridges=3),
        protocol="polymath-explore-collection-v1",
        config_sha256="a" * 64,
    )
    reply = "SOC spans physics and faults — gathering evidence next." + json.dumps(SEARCH)
    assert binding.parse_action(reply).query == "sandpile model"
