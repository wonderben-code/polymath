"""Larger reviewed call budgets widen the action budget; historical ones derive unchanged."""

import json

from polymath.meta_readiness_contracts import MetaReadinessProposal
from polymath.meta_research_contracts import (
    ExploreCollectionIntent,
    action_budget,
    derive_explore_collection_profile,
    derive_task_profiles,
)


def test_profiles_for_legacy_budgets_are_unchanged_and_larger_budgets_get_one_action_per_call():
    from test_meta_readiness_contracts import proposal_data

    assert (action_budget(12, 16), action_budget(20, 16), action_budget(21, 16)) == (16, 16, 21)
    legacy = MetaReadinessProposal.model_validate(proposal_data())
    assert legacy.application_limits.max_model_calls <= 20
    old = {item.profile.task_kind: item.profile for item in derive_task_profiles(legacy)}
    assert old["research"].max_actions == 16 and old["write_paper"].max_actions == 12

    data = proposal_data()
    data["application_limits"]["max_model_calls"] = 48
    data["application_limits"]["max_context_characters"] = 200_000
    big = MetaReadinessProposal.model_validate(data)
    new = {item.profile.task_kind: item.profile for item in derive_task_profiles(big)}
    assert (new["research"].max_model_calls, new["research"].max_actions) == (48, 48)
    # Short tasks keep their small budgets whatever the proposal allows.
    assert (new["write_paper"].max_model_calls, new["write_paper"].max_actions) == (12, 12)
    # Stage receipts carry the action ordinal, bounded in the archived protocol,
    # so Explore collections keep 16 actions and the ladder 20: the wider call
    # budget pays for repairs and retries.
    explore = derive_explore_collection_profile(big, ExploreCollectionIntent(max_bridges=3))
    assert (explore.profile.max_model_calls, explore.profile.max_actions) == (48, 16)
    assert json.loads(explore.profile.model_dump_json())["max_actions"] == 16
    from polymath.discovery_contracts import DiscoveryIntent
    from polymath.meta_research_contracts import derive_discovery_ladder_profile

    ladder = derive_discovery_ladder_profile(big, DiscoveryIntent())
    assert (ladder.profile.max_model_calls, ladder.profile.max_actions) == (48, 20)
