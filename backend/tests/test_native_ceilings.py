"""The controllers accept every limit a reviewed native profile may carry.

The first live visitor investigation under a 48-call profile was interrupted
before its first model call: the research controller still stopped at the
legacy ceiling of 20 while the readiness contracts allowed 80.
"""

import pytest

from polymath import meta_readiness_contracts, native_ceilings
from polymath.artifact_controller import ArtifactLimits
from polymath.assessment_controller import AssessmentLimits
from polymath.native_research_worker import (
    NativeArtifactLimits,
    NativeAssessmentLimits,
    NativeResearchLimits,
)
from polymath.research.controller import ResearchLimits


def test_readiness_and_controllers_share_one_set_of_ceilings():
    assert meta_readiness_contracts.MAX_MODEL_CALLS == native_ceilings.MAX_MODEL_CALLS == 80
    assert (
        meta_readiness_contracts.MAX_CONTEXT_CHARACTERS
        == native_ceilings.MAX_CONTEXT_CHARACTERS
        == 240_000
    )


@pytest.mark.parametrize("calls", [12, 20, 48, 80])
def test_research_limits_accept_every_reviewable_call_budget(calls):
    limits = ResearchLimits(
        max_model_calls=calls, max_actions=calls, max_context_characters=200_000
    )
    assert (limits.max_model_calls, limits.max_actions) == (calls, calls)
    native = NativeResearchLimits(
        max_model_calls=calls,
        max_actions=calls,
        max_context_characters=200_000,
        model_timeout_seconds=75.0,
    )
    assert native.max_model_calls == calls


def test_research_limits_still_reject_more_than_the_ceiling():
    with pytest.raises(ValueError, match="max_model_calls"):
        ResearchLimits(max_model_calls=81, max_actions=16)
    with pytest.raises(ValueError, match="max_context_characters"):
        ResearchLimits(max_context_characters=240_001)


def test_artifact_and_assessment_limits_accept_the_wider_context():
    assert ArtifactLimits(max_context_characters=240_000).max_context_characters == 240_000
    assert AssessmentLimits(max_context_characters=240_000).max_context_characters == 240_000
    assert NativeArtifactLimits(max_context_characters=200_000, model_timeout_seconds=75.0)
    assert NativeAssessmentLimits(max_context_characters=200_000, model_timeout_seconds=75.0)
    with pytest.raises(ValueError, match="max_context_characters"):
        ArtifactLimits(max_context_characters=240_001)


def test_discovery_limits_take_the_wider_call_budget_but_keep_twenty_actions():
    # Stage receipts carry the action ordinal, bounded to 20 in the archived
    # protocol, so a wider call budget pays for repairs and retries, not actions.
    from polymath.research.discovery import DiscoveryLimits

    limits = DiscoveryLimits(max_model_calls=48, max_actions=20, model_timeout_seconds=75)
    assert (limits.max_model_calls, limits.max_actions) == (48, 20)
    with pytest.raises(ValueError, match="max_actions"):
        DiscoveryLimits(max_model_calls=48, max_actions=21)


def test_saved_profiles_with_wider_action_budgets_still_parse_but_are_no_longer_current():
    # A manifest activated by an earlier release may carry 48 actions. It must
    # still parse (so the account can be read and the authority retired cleanly)
    # while a fresh derivation pins the archived budget and rejects it as changed.
    import json

    from test_meta_readiness_contracts import proposal_data

    from polymath.meta_readiness_contracts import MetaReadinessProposal
    from polymath.meta_research_contracts import (
        ExploreCollectionIntent,
        NativeExploreCollectionProfileBinding,
        derive_explore_collection_profile,
        task_profile_sha256,
        validate_current_explore_collection_profile,
    )

    data = proposal_data()
    data["application_limits"]["max_model_calls"] = 48
    data["application_limits"]["max_context_characters"] = 200_000
    proposal = MetaReadinessProposal.model_validate(data)
    current = derive_explore_collection_profile(proposal, ExploreCollectionIntent(max_bridges=3))
    assert current.profile.max_actions == 16
    saved = json.loads(current.profile.model_dump_json()) | {"max_actions": 48}
    binding = NativeExploreCollectionProfileBinding.model_validate(
        {
            "profile": saved,
            "profile_sha256": task_profile_sha256(type(current.profile).model_validate(saved)),
        }
    )
    assert binding.profile.max_actions == 48
    with pytest.raises(ValueError, match="explore_collection_profile_changed"):
        validate_current_explore_collection_profile(binding, proposal)
