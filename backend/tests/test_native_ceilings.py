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


def test_research_manifest_builds_for_a_48_call_proposal_with_explore_and_ladder():
    # The first live re-activation after pinning the action budgets failed here:
    # the historical Explore check demanded every quantity, including the action
    # budget, to equal the research profile's (48 against the pinned 16).
    from datetime import UTC, datetime, timedelta

    from pydantic import SecretStr
    from test_meta_readiness_contracts import proposal_data

    from polymath.discovery_contracts import DiscoveryIntent
    from polymath.meta_probe_contracts import ProviderAccountAttestation
    from polymath.meta_readiness_contracts import MetaReadinessProposal
    from polymath.meta_research_contracts import (
        ExploreCollectionIntent,
        NativeResearchPolicy,
        NativeResearchReview,
        PassedCanaryBinding,
        ScientificContextIntent,
        build_research_manifest,
        validate_research_manifest,
    )

    now = datetime.now(UTC).replace(microsecond=0)
    stamp = lambda value: value.isoformat().replace("+00:00", "Z")  # noqa: E731
    digest = "a" * 64
    data = proposal_data()
    data["application_limits"]["max_model_calls"] = 48
    data["application_limits"]["max_context_characters"] = 200_000
    data["observed_at"] = stamp(now - timedelta(minutes=2))
    data["valid_until"] = stamp(now + timedelta(days=10))
    proposal = MetaReadinessProposal.model_validate(data)
    policy = NativeResearchPolicy(
        paid_research_enabled=True,
        native_account_id=proposal.account.account_id,
        model_id=proposal.model_id,
        api_key=SecretStr("k" * 40),
    )
    canary = PassedCanaryBinding(
        receipt_id="2705b93b-a961-4df5-b11e-0726ecf3f4e6",
        receipt_sha256=digest,
        credential_binding_sha256=policy.credential_binding_sha256,
        account_id=proposal.account.account_id,
        model_id=proposal.model_id,
    )
    review = NativeResearchReview.model_validate(
        {
            "observed_at": stamp(now - timedelta(minutes=2)),
            "valid_until": stamp(now + timedelta(days=10)),
            "evidence": [{"url": "https://dev.meta.ai/docs", "content_sha256": digest}],
            "model_wire_usage_confirmed": True,
            "pricing_bounds_confirmed": True,
            "funding_cap_confirmed": True,
        }
    )
    attestation = ProviderAccountAttestation.model_validate(
        {
            "provider_billing_account_id": "team-test",
            "model_id": proposal.model_id,
            "currency": "USD",
            "usage_contract": "prompt_completion_inclusive_v1",
            "observed_at": stamp(now - timedelta(minutes=2)),
            "valid_until": stamp(now + timedelta(days=10)),
            "available_credit_microdollars": 150_000_000,
            "remaining_provider_allowance_microdollars": 150_000_000,
            "provider_hard_cap_microdollars": 150_000_000,
            "evidence": [{"url": "https://example.org/billing", "content_sha256": digest}],
        }
    )
    manifest = build_research_manifest(
        "22c19e72-8fd3-436a-8cd7-39940fc5d651",
        proposal,
        policy=policy,
        native_control_epoch=5,
        canary=canary,
        review=review,
        provider_account_attestation=attestation,
        valid_until=now + timedelta(minutes=15),
        discovery=DiscoveryIntent(max_stages=3, max_depth=2),
        scientific_context=ScientificContextIntent(),
        explore_collection=ExploreCollectionIntent(max_bridges=3),
    )
    validate_research_manifest(manifest, proposal)
    assert manifest.task_profiles[0].profile.max_actions == 48
    assert manifest.explore_collection_profile.profile.max_actions == 16
    assert manifest.discovery_ladder_profile.profile.max_actions == 20
