"""Fresh profiles hash actual frozen shadow controllers, never placeholder digests."""

import hashlib
import json

import pytest
from test_meta_research_contracts import research_fixture
from test_public_research_contracts import public_fixture

import polymath.meta_research_contracts as contracts
from polymath.meta_readiness_contracts import MetaReadinessProposal
from polymath.meta_research_contracts import (
    SCIENTIFIC_PROFILE_ROWS,
    ArtifactScientificContextIntent,
    AskOverviewIntent,
    AskReassessmentOverviewIntent,
    MetaResearchManifest,
    NativeScientificContextProfiles,
    ScientificContextIntent,
    build_research_manifest,
    canonical_research_manifest_json,
    derive_scientific_context_profiles,
    derive_task_profiles,
    minimum_controller_context_characters,
    minimum_scientific_context_characters,
    scientific_controller_sha256,
    select_scientific_profile,
    validate_current_scientific_context_profiles,
    validate_historical_scientific_context_profiles,
)
from polymath.public_research_contracts import (
    build_public_research_manifest,
    validate_current_public_research_manifest,
    validate_public_research_manifest,
)


def actual_payload(capability, kind):
    if capability == "artifact_scientific_context_v1":
        from polymath.artifact_controller import artifact_scientific_context_protocol_payload

        return artifact_scientific_context_protocol_payload(kind)
    if capability == "ask_claim_overview_v1":
        from polymath.research.ask_overview import ask_overview_protocol_payload

        return ask_overview_protocol_payload()
    from polymath.assessment_overview import ask_reassessment_overview_protocol_payload

    return ask_reassessment_overview_protocol_payload()


def current_private():
    proposal, policy, canary, review, attestation, old = research_fixture()
    return build_research_manifest(
        old.readiness_id,
        proposal,
        policy=policy,
        native_control_epoch=old.native_control_epoch,
        canary=canary,
        review=review,
        provider_account_attestation=attestation,
        valid_until=old.valid_until,
        scientific_context=ScientificContextIntent(),
    )


def current_public():
    value = public_fixture()
    manifest = build_public_research_manifest(
        value.manifest.readiness_id,
        value.proposal,
        research_policy=value.research_policy,
        public_policy=value.public_policy,
        sponsor_policy_sha256=value.sponsor_policy_sha256,
        native_control_epoch=value.manifest.native_control_epoch,
        canary=value.canary,
        review=value.review,
        provider_account_attestation=value.attestation,
        public_limits=value.public_limits,
        valid_until=value.manifest.valid_until,
        now=value.now,
        scientific_context=ScientificContextIntent(),
    )
    return value, manifest


def test_all_five_current_profiles_reuse_original_quantities_and_hash_actual_static_semantics():
    proposal, *_ = research_fixture()
    extension = derive_scientific_context_profiles(proposal)
    base = {item.profile.task_kind: item.profile for item in derive_task_profiles(proposal)}
    assert len(extension.bindings) == 5
    for item, (capability, kind, mode, protocol) in zip(
        extension.bindings, SCIENTIFIC_PROFILE_ROWS, strict=True
    ):
        assert (item.capability, item.mode, item.profile.controller_protocol) == (
            capability,
            mode,
            protocol,
        )
        data = actual_payload(capability, kind)
        expected = hashlib.sha256(
            json.dumps(
                data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        assert item.profile.controller_sha256 == expected
        assert scientific_controller_sha256(capability, kind) == expected
        fields = item.profile.model_dump(mode="json")
        original = base[kind].model_dump(mode="json")
        for changed in ("controller_protocol", "controller_sha256"):
            fields.pop(changed)
            original.pop(changed)
        assert fields == original
        assert item.profile.max_context_characters >= minimum_scientific_context_characters(
            capability, kind
        )
    validate_historical_scientific_context_profiles(extension, proposal)
    validate_current_scientific_context_profiles(extension, proposal)


def test_private_builder_derives_only_after_explicit_marker_and_keeps_base_five_exact():
    _, _, _, _, _, old = research_fixture()
    new = current_private()
    assert new.task_profiles == old.task_profiles
    assert old.scientific_context_profiles is None
    assert new.scientific_context_profiles == derive_scientific_context_profiles(old.proposal)
    assert canonical_research_manifest_json(new) != canonical_research_manifest_json(old)
    assert "scientific_context_profiles" in new.model_dump(mode="json")


def test_public_builder_carries_server_derived_common_extension_and_current_validates_it():
    value, manifest = current_public()
    assert manifest.task_profiles == value.manifest.task_profiles
    assert manifest.scientific_context_profiles == derive_scientific_context_profiles(
        value.proposal
    )
    validate_current_public_research_manifest(
        manifest,
        research_policy=value.research_policy,
        public_policy=value.public_policy,
        sponsor_policy_sha256=value.sponsor_policy_sha256,
        now=value.now,
    )


@pytest.mark.parametrize(
    "intent,kind",
    [
        (ArtifactScientificContextIntent(), "formalise"),
        (ArtifactScientificContextIntent(), "write_summary"),
        (ArtifactScientificContextIntent(), "write_paper"),
        (AskOverviewIntent(), "research"),
        (AskReassessmentOverviewIntent(), "reassess_finding"),
    ],
)
def test_actual_current_variant_can_be_selected_under_private_and_public_admission(intent, kind):
    private = current_private()
    _, public = current_public()
    for manifest in (private, public):
        actual = select_scientific_profile(manifest, kind, "ask", scientific_intent=intent)
        expected = next(
            item.profile
            for item in manifest.scientific_context_profiles.bindings
            if item.profile.task_kind == kind
        )
        assert actual == expected


def test_historical_present_extension_never_loads_current_controller_helpers(monkeypatch):
    private = current_private()
    _, public = current_public()

    def forbidden(*args):
        pytest.fail("Historical parsing loaded current controller semantics")

    monkeypatch.setattr(contracts, "derive_scientific_context_profiles", forbidden)
    monkeypatch.setattr(contracts, "scientific_controller_sha256", forbidden)
    saved = MetaResearchManifest.model_validate_json(private.model_dump_json())
    validate_public_research_manifest(public)
    validate_historical_scientific_context_profiles(
        saved.scientific_context_profiles, saved.proposal
    )
    assert (
        select_scientific_profile(saved, "research", "ask", scientific_intent=AskOverviewIntent())
        == saved.scientific_context_profiles.bindings[3].profile
    )


def test_changed_current_variant_fences_fresh_validation_without_invalidating_historical_bytes(
    monkeypatch,
):
    value, manifest = current_public()
    original = canonical_research_manifest_json(current_private())
    current_hash = contracts.scientific_controller_sha256

    def changed(capability, kind):
        return "0" * 64 if capability == "ask_claim_overview_v1" else current_hash(capability, kind)

    monkeypatch.setattr(contracts, "scientific_controller_sha256", changed)
    validate_public_research_manifest(manifest)
    assert (
        canonical_research_manifest_json(MetaResearchManifest.model_validate_json(original))
        == original
    )
    with pytest.raises(ValueError, match="scientific_context_profile_changed"):
        validate_current_scientific_context_profiles(
            manifest.scientific_context_profiles, value.proposal
        )
    with pytest.raises(ValueError, match="scientific_context_profile_changed"):
        validate_current_public_research_manifest(
            manifest,
            research_policy=value.research_policy,
            public_policy=value.public_policy,
            sponsor_policy_sha256=value.sponsor_policy_sha256,
            now=value.now,
        )


def test_new_fixed_context_size_is_checked_separately_from_legacy_controller_size():
    proposal, *_ = research_fixture()
    old_min = max(minimum_controller_context_characters(kind) for kind in contracts.TASK_KINDS)
    new_min = max(
        minimum_scientific_context_characters(capability, kind)
        for capability, kind, _, _ in SCIENTIFIC_PROFILE_ROWS
    )
    assert new_min > old_min
    data = proposal.model_dump(mode="json")
    data["application_limits"]["max_context_characters"] = old_min
    limited = MetaReadinessProposal.model_validate_json(json.dumps(data))
    assert len(derive_task_profiles(limited)) == 5
    with pytest.raises(ValueError, match="scientific_context_fixed_prompt_exceeds_proposal"):
        derive_scientific_context_profiles(limited)


def test_missing_actual_variant_helper_never_falls_back_to_old_protocol(monkeypatch):
    import polymath.artifact_controller as artifacts

    monkeypatch.delattr(artifacts, "artifact_scientific_context_protocol_payload")
    proposal, *_ = research_fixture()
    assert len(derive_task_profiles(proposal)) == 5
    with pytest.raises(ValueError, match="scientific_context_protocol_unavailable"):
        derive_scientific_context_profiles(proposal)


def test_profile_derivation_never_constructs_native_model(monkeypatch):
    import polymath.research.meta as meta

    def forbidden(*args, **kwargs):
        pytest.fail("Pure profile calculation constructed a model client")

    monkeypatch.setattr(meta, "MetaModel", forbidden)
    manifest = current_private()
    assert len(manifest.scientific_context_profiles.bindings) == 5


def test_new_profile_parser_does_not_trust_model_copy_hash_bypass():
    extension = current_private().scientific_context_profiles
    wrong = extension.bindings[0].model_copy(update={"profile_sha256": "f" * 64})
    forged = extension.model_copy(update={"bindings": (wrong,) + extension.bindings[1:]})
    with pytest.raises(ValueError):
        NativeScientificContextProfiles.model_validate_json(forged.model_dump_json())
