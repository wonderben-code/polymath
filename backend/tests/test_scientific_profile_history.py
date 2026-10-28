"""Historical profile tests use labelled synthetic digests, never fake fresh approval."""

import json
from copy import deepcopy

import pytest
from test_meta_research_contracts import research_fixture
from test_public_research_contracts import public_fixture

import polymath.meta_research_contracts as contracts
from polymath.discovery_contracts import DiscoveryIntent
from polymath.meta_research_contracts import (
    ArtifactScientificContextIntent,
    AskOverviewIntent,
    AskReassessmentOverviewIntent,
    MetaResearchManifest,
    NativeScientificContextProfileBinding,
    NativeScientificContextProfiles,
    NativeTaskProfile,
    ScientificContextIntent,
    derive_discovery_ladder_profile,
    derive_task_profiles,
    profile_for_task,
    select_scientific_profile,
    task_profile_sha256,
    validate_historical_scientific_context_profiles,
)
from polymath.public_research_contracts import PublicResearchManifest, public_profile_for_task

ROWS = (
    (
        "artifact_scientific_context_v1",
        "formalise",
        None,
        "polymath-meta-artifact-scientific-context-v1",
    ),
    (
        "artifact_scientific_context_v1",
        "write_summary",
        None,
        "polymath-meta-artifact-scientific-context-v1",
    ),
    (
        "artifact_scientific_context_v1",
        "write_paper",
        None,
        "polymath-meta-artifact-scientific-context-v1",
    ),
    ("ask_claim_overview_v1", "research", "ask", "polymath-meta-ask-claim-overview-v1"),
    (
        "ask_reassessment_overview_v1",
        "reassess_finding",
        "ask",
        "polymath-meta-ask-reassessment-overview-v1",
    ),
)


def historical_extension(proposal):
    """Synthetic historical SHA values describe no deployed or verified controller."""
    bases = {
        binding.profile.task_kind: binding.profile for binding in derive_task_profiles(proposal)
    }
    values = []
    for index, (capability, kind, mode, protocol) in enumerate(ROWS, start=1):
        profile = NativeTaskProfile.model_validate_json(
            bases[kind]
            .model_copy(
                update={
                    "controller_protocol": protocol,
                    # Deliberately synthetic past-code digest. Never current derivation.
                    "controller_sha256": str(index) * 64,
                }
            )
            .model_dump_json()
        )
        values.append(
            NativeScientificContextProfileBinding(
                capability=capability,
                mode=mode,
                profile=profile,
                profile_sha256=task_profile_sha256(profile),
            )
        )
    return NativeScientificContextProfiles(bindings=tuple(values))


def manifest_with_extension(*, public=False, ladder=False):
    if public:
        value = public_fixture(discovery=DiscoveryIntent() if ladder else None)
        manifest, proposal = value.manifest, value.proposal
        cls = PublicResearchManifest
    else:
        proposal, _, _, _, _, manifest = research_fixture()
        if ladder:
            manifest = manifest.model_copy(
                update={
                    "discovery_ladder_profile": derive_discovery_ladder_profile(
                        proposal, DiscoveryIntent()
                    )
                }
            )
        cls = MetaResearchManifest
    extension = historical_extension(proposal)
    manifest = cls.model_validate_json(
        manifest.model_copy(update={"scientific_context_profiles": extension}).model_dump_json()
    )
    return proposal, manifest, extension


def test_historical_extension_retains_exact_five_canonical_bindings_and_base_quantities():
    proposal, _, extension = manifest_with_extension()
    assert extension.protocol == "polymath-scientific-context-profiles-v1"
    assert len(extension.bindings) == 5
    bases = {
        binding.profile.task_kind: binding.profile for binding in derive_task_profiles(proposal)
    }
    for binding, row in zip(extension.bindings, ROWS, strict=True):
        capability, kind, mode, protocol = row
        assert (binding.capability, binding.profile.task_kind, binding.mode) == (
            capability,
            kind,
            mode,
        )
        assert binding.profile.controller_protocol == protocol
        assert binding.profile_sha256 == task_profile_sha256(binding.profile)
        original = bases[kind].model_dump()
        actual = binding.profile.model_dump()
        for name in original:
            if name not in {"controller_protocol", "controller_sha256"}:
                assert actual[name] == original[name]
    assert validate_historical_scientific_context_profiles(extension, proposal) is None


def test_historical_validation_never_uses_current_controller_or_fresh_profile_derivation(
    monkeypatch,
):
    proposal, _, extension = manifest_with_extension()

    def forbidden(*args, **kwargs):
        pytest.fail("Historical parsing consulted current code")

    monkeypatch.setattr(contracts, "scientific_controller_sha256", forbidden)
    monkeypatch.setattr(contracts, "derive_scientific_context_profiles", forbidden)
    assert validate_historical_scientific_context_profiles(extension, proposal) is None


@pytest.mark.parametrize("public", [False, True])
def test_historical_manifest_roundtrip_accepts_old_digests_without_rehashing_them(
    public, monkeypatch
):
    proposal, manifest, extension = manifest_with_extension(public=public)
    before = manifest.model_dump_json()
    monkeypatch.setattr(contracts, "scientific_controller_sha256", lambda *args: "f" * 64)
    cls = PublicResearchManifest if public else MetaResearchManifest
    parsed = cls.model_validate_json(before)
    assert parsed.model_dump_json() == before
    assert parsed.scientific_context_profiles == extension
    validate_historical_scientific_context_profiles(parsed.scientific_context_profiles, proposal)


@pytest.mark.parametrize("public", [False, True])
def test_absent_extension_is_omitted_whole_and_leaves_old_five_profiles_unchanged(public):
    if public:
        value = public_fixture()
        manifest, proposal = value.manifest, value.proposal
    else:
        proposal, _, _, _, _, manifest = research_fixture()
    raw = manifest.model_dump(mode="json")
    assert "scientific_context_profiles" not in raw
    assert manifest.scientific_context_profiles is None
    assert manifest.task_profiles == derive_task_profiles(proposal)
    roundtrip = type(manifest).model_validate_json(json.dumps(raw))
    assert roundtrip.model_dump_json() == manifest.model_dump_json()


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize(
    "kind", ["research", "formalise", "write_summary", "write_paper", "reassess_finding"]
)
def test_absent_intent_selects_original_base_profile_even_when_extension_is_present(public, kind):
    _, manifest, _ = manifest_with_extension(public=public)
    old_selector = public_profile_for_task if public else profile_for_task
    expected = old_selector(manifest, kind)
    assert select_scientific_profile(manifest, kind, "ask") == expected


@pytest.mark.parametrize("public", [False, True])
def test_absent_new_intent_preserves_explicit_ladder_selection(public):
    _, manifest, _ = manifest_with_extension(public=public, ladder=True)
    intent = DiscoveryIntent()
    original = public_profile_for_task if public else profile_for_task
    expected = original(manifest, "research", discovery=intent)
    assert select_scientific_profile(manifest, "research", "surprise", discovery=intent) == expected
    assert expected.max_actions == 20


@pytest.mark.parametrize("public", [False, True])
@pytest.mark.parametrize("row", range(5))
def test_exact_new_intent_selects_only_its_original_approved_binding(public, row):
    _, manifest, extension = manifest_with_extension(public=public)
    _, kind, mode, _ = ROWS[row]
    intent = (
        ArtifactScientificContextIntent()
        if row < 3
        else AskOverviewIntent()
        if row == 3
        else AskReassessmentOverviewIntent()
    )
    actual = select_scientific_profile(manifest, kind, mode or "explore", scientific_intent=intent)
    assert actual == extension.bindings[row].profile
    assert actual != next(
        binding.profile for binding in manifest.task_profiles if binding.profile.task_kind == kind
    )


@pytest.mark.parametrize("public", [False, True])
def test_requested_variant_never_falls_back_when_extension_is_absent(public):
    _, manifest, _ = manifest_with_extension(public=public)
    manifest = type(manifest).model_validate_json(
        manifest.model_copy(update={"scientific_context_profiles": None}).model_dump_json()
    )
    with pytest.raises(ValueError):
        select_scientific_profile(
            manifest, "research", "ask", scientific_intent=AskOverviewIntent()
        )


@pytest.mark.parametrize(
    "kind,mode,intent",
    [
        ("research", "explore", AskOverviewIntent()),
        ("research", "surprise", AskOverviewIntent()),
        ("write_summary", "ask", AskOverviewIntent()),
        ("reassess_finding", "explore", AskReassessmentOverviewIntent()),
        ("research", "ask", AskReassessmentOverviewIntent()),
        ("research", "ask", ArtifactScientificContextIntent()),
        ("research", "ask", ScientificContextIntent()),
    ],
)
def test_selector_rejects_incompatible_task_mode_and_group_approval_marker(kind, mode, intent):
    _, manifest, _ = manifest_with_extension()
    with pytest.raises(ValueError):
        select_scientific_profile(manifest, kind, mode, scientific_intent=intent)


def test_new_intent_cannot_be_combined_with_ladder_intent():
    _, manifest, _ = manifest_with_extension(ladder=True)
    with pytest.raises(ValueError):
        select_scientific_profile(
            manifest,
            "research",
            "ask",
            discovery=DiscoveryIntent(),
            scientific_intent=AskOverviewIntent(),
        )


def test_selector_revalidates_a_preconstructed_intent_instead_of_trusting_class_identity():
    _, manifest, _ = manifest_with_extension()
    forged = AskOverviewIntent().model_copy(update={"protocol": "forged-protocol"})
    with pytest.raises(ValueError):
        select_scientific_profile(manifest, "research", "ask", scientific_intent=forged)


def test_history_revalidates_model_copy_hash_bypasses():
    proposal, _, extension = manifest_with_extension()
    forged_binding = extension.bindings[0].model_copy(update={"profile_sha256": "0" * 64})
    forged = extension.model_copy(update={"bindings": (forged_binding,) + extension.bindings[1:]})
    with pytest.raises(ValueError):
        validate_historical_scientific_context_profiles(forged, proposal)


@pytest.mark.parametrize(
    "mutation",
    ["short", "reverse", "duplicate", "mode", "capability", "task", "controller_protocol", "hash"],
)
def test_historical_validation_rejects_structural_and_hash_conflicts(mutation):
    proposal, _, extension = manifest_with_extension()
    raw = deepcopy(extension.model_dump(mode="json"))
    bindings = raw["bindings"]
    if mutation == "short":
        raw["bindings"] = bindings[:-1]
    elif mutation == "reverse":
        raw["bindings"] = bindings[::-1]
    elif mutation == "duplicate":
        bindings[1] = deepcopy(bindings[0])
    elif mutation == "mode":
        bindings[3]["mode"] = "explore"
    elif mutation == "capability":
        bindings[0]["capability"] = "ask_claim_overview_v1"
    elif mutation == "task":
        bindings[0]["profile"]["task_kind"] = "write_summary"
        profile = NativeTaskProfile.model_validate_json(json.dumps(bindings[0]["profile"]))
        bindings[0]["profile_sha256"] = task_profile_sha256(profile)
    elif mutation == "controller_protocol":
        bindings[0]["profile"]["controller_protocol"] = "polymath-unapproved-protocol"
        profile = NativeTaskProfile.model_validate_json(json.dumps(bindings[0]["profile"]))
        bindings[0]["profile_sha256"] = task_profile_sha256(profile)
    else:
        bindings[0]["profile_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        parsed = NativeScientificContextProfiles.model_validate_json(json.dumps(raw))
        validate_historical_scientific_context_profiles(parsed, proposal)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_model_calls", 13),
        ("max_output_tokens", 4095),
        ("max_context_characters", 89999),
        ("request_timeout_seconds", 44.0),
    ],
)
def test_historical_profile_cannot_change_reviewed_quantities_even_with_recomputed_hash(
    field, value
):
    proposal, _, extension = manifest_with_extension()
    raw = deepcopy(extension.model_dump(mode="json"))
    # Ask research profile has enough type-level room for these mutations. The
    # original proposal binding, not a copied digest, must reject them.
    row = raw["bindings"][3]
    row["profile"][field] = value
    if field == "request_timeout_seconds":
        row["profile"]["model_timeout_seconds"] = value + 30.0
    profile = NativeTaskProfile.model_validate_json(json.dumps(row["profile"]))
    row["profile_sha256"] = task_profile_sha256(profile)
    with pytest.raises(ValueError):
        extension = NativeScientificContextProfiles.model_validate_json(json.dumps(raw))
        validate_historical_scientific_context_profiles(extension, proposal)
