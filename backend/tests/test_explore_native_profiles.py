"""Explore approval profiles preserve old protocols and use actual new helpers."""

import hashlib
import json

import pytest
from pydantic import ValidationError
from test_meta_research_contracts import research_fixture
from test_public_research_contracts import public_fixture

import polymath.meta_research_contracts as contracts
from polymath.discovery_contracts import DiscoveryIntent
from polymath.explore_collection_contracts import ExploreCollectionIntent
from polymath.meta_readiness_contracts import MetaReadinessProposal
from polymath.meta_research_api import PreviewResearch
from polymath.meta_research_contracts import (
    ExploreCollectionAccess,
    MetaResearchManifest,
    NativeExploreCollectionProfile,
    NativeTaskProfile,
    NativeTaskProfileBinding,
    ScientificContextIntent,
    build_research_manifest,
    derive_explore_collection_profile,
    minimum_controller_context_characters,
    minimum_explore_collection_characters,
    select_profile_with_explore,
    select_scientific_profile,
    task_profile_sha256,
    validate_current_explore_collection_profile,
)
from polymath.public_research_api import PreviewPublicResearch
from polymath.public_research_contracts import (
    PublicResearchManifest,
    build_public_research_manifest,
    validate_current_public_research_manifest,
)
from polymath.research import explore_collection as controller


def manifest(*, public=False, maximum=None, scientific=False, ladder=False):
    intent = ExploreCollectionIntent(max_bridges=maximum) if maximum is not None else None
    discovery = DiscoveryIntent() if ladder else None
    scientific_context = ScientificContextIntent() if scientific else None
    proposal, policy, canary, review, attestation, old = research_fixture()
    if public:
        fixture = public_fixture()
        return build_public_research_manifest(
            old.readiness_id,
            proposal,
            research_policy=fixture.research_policy,
            public_policy=fixture.public_policy,
            sponsor_policy_sha256=fixture.sponsor_policy_sha256,
            native_control_epoch=old.native_control_epoch,
            canary=canary,
            review=review,
            provider_account_attestation=attestation,
            public_limits=fixture.public_limits,
            valid_until=old.valid_until,
            now=fixture.now,
            discovery=discovery,
            scientific_context=scientific_context,
            explore_collection=intent,
        )
    return build_research_manifest(
        old.readiness_id,
        proposal,
        policy=policy,
        native_control_epoch=old.native_control_epoch,
        canary=canary,
        review=review,
        provider_account_attestation=attestation,
        valid_until=old.valid_until,
        discovery=discovery,
        scientific_context=scientific_context,
        explore_collection=intent,
    )


@pytest.mark.parametrize("maximum", [1, 2, 3, 4])
def test_actual_new_profile_keeps_ordinary_quantities_and_hashes_real_helper(maximum):
    proposal, _, _, _, _, old = research_fixture()
    intent = ExploreCollectionIntent(max_bridges=maximum)
    extension = derive_explore_collection_profile(proposal, intent)
    profile = extension.profile
    expected = hashlib.sha256(
        json.dumps(
            controller.explore_collection_protocol_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    assert profile.controller_sha256 == expected
    assert profile.controller_protocol == "polymath-meta-explore-collection-v1"
    assert profile.collection_protocol == intent.protocol and profile.max_bridges == maximum
    for name in NativeTaskProfile.model_fields:
        if name not in ("controller_protocol", "controller_sha256"):
            assert getattr(profile, name) == getattr(old.task_profiles[0].profile, name)
    assert profile.max_actions == 16 and profile.tool_timeout_seconds == 20
    assert extension.profile_sha256 == task_profile_sha256(profile)
    validate_current_explore_collection_profile(extension, proposal)


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize(
    "scientific,ladder", [(False, False), (True, False), (False, True), (True, True)]
)
def test_optional_extension_changes_no_old_fields_or_legacy_profile_selection(
    public, scientific, ladder
):
    old = manifest(public=public, scientific=scientific, ladder=ladder)
    value = manifest(public=public, maximum=3, scientific=scientific, ladder=ladder)
    raw = value.model_dump(mode="json")
    assert set(raw) == set(old.model_dump(mode="json")) | {"explore_collection_profile"}
    assert {
        key: item for key, item in raw.items() if key != "explore_collection_profile"
    } == old.model_dump(mode="json")
    assert "explore_collection_profile" not in old.model_dump(mode="json")
    for kind in ("research", "formalise", "write_summary", "write_paper", "reassess_finding"):
        assert select_profile_with_explore(value, kind, "explore") == select_scientific_profile(
            old, kind, "explore"
        )


@pytest.mark.parametrize("maximum", [1, 2, 3, 4])
def test_tighter_actual_run_intent_retains_original_approved_profile_and_hash(maximum):
    value = manifest(maximum=maximum)
    approved = value.explore_collection_profile
    for bound in range(1, maximum + 1):
        result = select_profile_with_explore(
            value,
            "research",
            "explore",
            explore_collection_intent=ExploreCollectionIntent(max_bridges=bound),
        )
        assert result == approved.profile and result.max_bridges == maximum
        assert task_profile_sha256(result) == approved.profile_sha256
    if maximum < 4:
        with pytest.raises(ValueError, match="explore_collection_profile_not_approved"):
            select_profile_with_explore(
                value,
                "research",
                "explore",
                explore_collection_intent=ExploreCollectionIntent(max_bridges=maximum + 1),
            )


@pytest.mark.parametrize("public", [False, True])
def test_historical_original_profile_never_loads_current_controller(public, monkeypatch):
    value = manifest(public=public, maximum=3, scientific=True, ladder=True)
    raw = value.model_dump(mode="json")
    raw["explore_collection_profile"]["profile"]["controller_sha256"] = "e" * 64
    profile = NativeExploreCollectionProfile.model_validate_json(
        json.dumps(raw["explore_collection_profile"]["profile"])
    )
    raw["explore_collection_profile"]["profile_sha256"] = task_profile_sha256(profile)
    cls = PublicResearchManifest if public else MetaResearchManifest
    historical = cls.model_validate_json(json.dumps(raw))

    def no_current_helper():
        pytest.fail("Historical profile reached current controller")

    monkeypatch.setattr(contracts, "_explore_controller", no_current_helper)
    selected = select_profile_with_explore(
        historical,
        "research",
        "explore",
        explore_collection_intent=ExploreCollectionIntent(max_bridges=1),
    )
    assert selected.controller_sha256 == "e" * 64 and selected.max_bridges == 3
    assert cls.model_validate_json(historical.model_dump_json()) == historical


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "ask"},
        {"mode": "surprise"},
        {"task_kind": "formalise"},
        {"parent_result_ids": ["00000000-0000-0000-0000-000000000001"]},
        {"parent_result_ids": ""},
        {"discovery": DiscoveryIntent()},
        {"scientific_intent": ScientificContextIntent()},
    ],
)
def test_explicit_collection_scope_conflict_never_falls_back(change):
    args = {
        "manifest": manifest(maximum=3),
        "task_kind": "research",
        "mode": "explore",
        "explore_collection_intent": ExploreCollectionIntent(),
        **change,
    }
    with pytest.raises(ValueError):
        select_profile_with_explore(**args)


@pytest.mark.parametrize("public", [False, True])
def test_explicit_collection_requires_the_saved_extension(public):
    with pytest.raises(ValueError, match="explore_collection_profile_not_approved"):
        select_profile_with_explore(
            manifest(public=public),
            "research",
            "explore",
            explore_collection_intent=ExploreCollectionIntent(),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"max_actions": 20},
        {"max_actions": True},
        {"max_model_calls": 21},
        {"tool_timeout_seconds": 5.0},
        {"max_bridges": 0},
        {"max_bridges": 5},
        {"max_bridges": True},
        {"max_bridges": "3"},
        {"task_kind": "write_paper"},
        {"controller_protocol": "polymath-surprise-ladder-v1"},
        {"collection_protocol": "polymath-surprise-ladder-v1"},
    ],
)
def test_new_profile_has_strict_original_limits_and_separate_protocol_identity(changes):
    raw = manifest(maximum=3).explore_collection_profile.profile.model_dump(mode="json")
    with pytest.raises(ValidationError):
        NativeExploreCollectionProfile.model_validate_json(json.dumps(raw | changes))


@pytest.mark.parametrize(
    "field,value",
    [("max_model_calls", 1), ("max_output_tokens", 32), ("max_context_characters", 1000)],
)
def test_profile_cannot_replace_saved_quantities_even_with_smaller_values(field, value):
    raw = manifest(maximum=3).model_dump(mode="json")
    raw["explore_collection_profile"]["profile"][field] = value
    profile = NativeExploreCollectionProfile.model_validate_json(
        json.dumps(raw["explore_collection_profile"]["profile"])
    )
    raw["explore_collection_profile"]["profile_sha256"] = task_profile_sha256(profile)
    with pytest.raises(ValidationError, match="explore_collection_quantitative_profile_changed"):
        MetaResearchManifest.model_validate_json(json.dumps(raw))


def test_old_typed_task_profile_binding_cannot_serialize_away_new_fields():
    value = manifest(maximum=3)
    with pytest.raises(ValidationError):
        NativeTaskProfileBinding(
            profile=value.explore_collection_profile.profile,
            profile_sha256=value.explore_collection_profile.profile_sha256,
        )


def test_actual_prompt_change_refuses_fresh_use_without_breaking_historical_selection(monkeypatch):
    value = manifest(maximum=3)
    extension = value.explore_collection_profile
    monkeypatch.setattr(
        controller,
        "EXPLORE_COLLECTION_SYSTEM_PROMPT",
        controller.EXPLORE_COLLECTION_SYSTEM_PROMPT + "\nChanged actual scientific policy.",
    )
    with pytest.raises(ValueError, match="explore_collection_profile_changed"):
        validate_current_explore_collection_profile(extension, value.proposal)
    assert (
        select_profile_with_explore(
            value,
            "research",
            "explore",
            explore_collection_intent=ExploreCollectionIntent(max_bridges=1),
        )
        == extension.profile
    )


def test_actual_fixed_prompt_boundary_is_measured_without_token_inference():
    proposal, *_ = research_fixture()
    intent = ExploreCollectionIntent()
    minimum = max(
        sum(
            len(item["content"])
            for item in controller.explore_collection_initial_messages(
                "", intent, memory_available=memory
            )
        )
        for memory in (False, True)
    )
    assert minimum_explore_collection_characters(intent) == minimum
    raw = proposal.model_dump(mode="json")
    raw["application_limits"]["max_context_characters"] = minimum - 1
    with pytest.raises(ValueError, match="explore_collection_fixed_prompt_exceeds_proposal"):
        derive_explore_collection_profile(
            MetaReadinessProposal.model_validate_json(json.dumps(raw)), intent
        )
    raw["application_limits"]["max_context_characters"] = max(
        minimum,
        *(
            minimum_controller_context_characters(kind)
            for kind in (
                "research",
                "formalise",
                "write_summary",
                "write_paper",
                "reassess_finding",
            )
        ),
    )
    assert derive_explore_collection_profile(
        MetaReadinessProposal.model_validate_json(json.dumps(raw)), intent
    )


@pytest.mark.parametrize("public", [False, True])
def test_explicit_null_profile_or_bad_hash_is_not_absent_optional_authority(public):
    cls = PublicResearchManifest if public else MetaResearchManifest
    raw = manifest(public=public).model_dump(mode="json")
    with pytest.raises(ValidationError):
        cls.model_validate_json(json.dumps(raw | {"explore_collection_profile": None}))
    raw = manifest(public=public, maximum=3).model_dump(mode="json")
    raw["explore_collection_profile"]["profile_sha256"] = "f" * 64
    with pytest.raises(ValidationError):
        cls.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("extended", [False, True])
def test_public_current_validation_keeps_all_original_policy_and_identity_checks(extended):
    fixture = public_fixture()
    value = manifest(public=True, maximum=3 if extended else None)
    kwargs = {
        "research_policy": fixture.research_policy,
        "public_policy": fixture.public_policy,
        "sponsor_policy_sha256": fixture.sponsor_policy_sha256,
        "now": fixture.now,
    }
    validate_current_public_research_manifest(value, **kwargs)
    with pytest.raises(ValueError):
        validate_current_public_research_manifest(
            value, **(kwargs | {"sponsor_policy_sha256": "f" * 64})
        )


def test_public_current_validation_rejects_changed_actual_explore_protocol(monkeypatch):
    fixture = public_fixture()
    value = manifest(public=True, maximum=3)
    monkeypatch.setattr(
        controller,
        "EXPLORE_COLLECTION_SYSTEM_PROMPT",
        controller.EXPLORE_COLLECTION_SYSTEM_PROMPT + "\nNew actual prompt.",
    )
    with pytest.raises(ValueError, match="explore_collection_profile_changed"):
        validate_current_public_research_manifest(
            value,
            research_policy=fixture.research_policy,
            public_policy=fixture.public_policy,
            sponsor_policy_sha256=fixture.sponsor_policy_sha256,
            now=fixture.now,
        )


def test_new_capability_projection_has_only_three_keys_and_no_private_authority():
    for body in (
        {"activation_sha256": None, "explore_collection_intent": None},
        {"activation_sha256": "a" * 64, "explore_collection_intent": None},
        {"activation_sha256": "a" * 64, "explore_collection_intent": ExploreCollectionIntent()},
    ):
        value = ExploreCollectionAccess(**body).model_dump(mode="json")
        assert set(value) == {"protocol", "activation_sha256", "explore_collection_intent"}
    for body in (
        {"activation_sha256": None, "explore_collection_intent": ExploreCollectionIntent()},
        {"activation_sha256": "PRIVATE", "explore_collection_intent": None},
        {"activation_sha256": None, "explore_collection_intent": None, "capabilities": []},
    ):
        with pytest.raises(ValidationError):
            ExploreCollectionAccess(**body)


@pytest.mark.parametrize("public", [False, True])
def test_preview_marker_is_actual_strict_intent_and_null_is_not_requested_protocol(public):
    fixture = public_fixture()
    value = fixture.manifest
    body = {
        "readiness_id": value.readiness_id,
        "canary_receipt_id": value.canary.receipt_id,
        "review": value.review.model_dump(mode="json"),
        "provider_account_attestation": value.provider_account_attestation.model_dump(mode="json"),
    }
    cls = PreviewPublicResearch if public else PreviewResearch
    if public:
        body.update(
            public_limits=value.public_limits.model_dump(mode="json"),
            valid_until=value.valid_until.isoformat(),
        )
    assert cls.model_validate_json(json.dumps(body)).explore_collection is None
    actual = cls.model_validate_json(
        json.dumps(
            body
            | {
                "explore_collection": {
                    "protocol": "polymath-explore-collection-v1",
                    "max_bridges": 2,
                }
            }
        )
    )
    assert actual.explore_collection == ExploreCollectionIntent(max_bridges=2)
    for marker in (None, {"protocol": "polymath-explore-collection-v1", "max_bridges": True}):
        with pytest.raises(ValidationError):
            cls.model_validate_json(json.dumps(body | {"explore_collection": marker}))
