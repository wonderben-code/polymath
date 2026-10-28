"""Exact pre-extension D2 bytes, isolated from new current-profile derivation."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_meta_research_contracts import research_fixture
from test_public_research_contracts import public_fixture

from polymath.discovery_contracts import DiscoveryIntent
from polymath.meta_research_contracts import (
    SCIENTIFIC_CAPABILITIES,
    TASK_KINDS,
    MetaResearchManifest,
    PublicResearchProtocols,
    ScientificContextIntent,
    build_research_manifest,
    canonical_research_manifest_json,
    controller_protocol_sha256,
    derive_task_profiles,
    discovery_controller_sha256,
)
from polymath.public_research_contracts import (
    PublicResearchManifest,
    canonical_public_research_manifest_json,
)

ARCHIVE = json.loads(
    (Path(__file__).parent / "fixtures/pre_scientific_context_baseline.json").read_text()
)
ARCHIVED_VALUES = ARCHIVE["fixtures"]
# Read-only views of the committed D2 archive: no expected value is generated
# from the implementation under test or written back to the fixture.
BASELINE = {
    **{key: value["canonical_json"] for key, value in ARCHIVED_VALUES["manifests"].items()},
    "controllers": {
        kind: value["controller_protocol_sha256"]
        for kind, value in ARCHIVED_VALUES["task_profiles"].items()
    },
    "ladder_controller": ARCHIVED_VALUES["ladder"]["controller_sha256"],
}
ARCHIVED_DISCOVERY = DiscoveryIntent.model_validate_json(
    ARCHIVED_VALUES["ladder"]["intent"]["canonical_json"]
)


def test_profile_baseline_is_the_authoritative_committed_d2_fixture():
    assert ARCHIVE["format"] == "polymath-pre-scientific-context-baseline-v1"
    assert ARCHIVE["provenance"]["source_commit"] == ("6e3d75b663ff65e1f9ae3b518efcbaeb71368f56")


@pytest.mark.parametrize("key", ["private", "private_ladder", "public", "public_ladder"])
def test_old_absent_extension_manifest_bytes_roundtrip_exactly(key):
    public = key.startswith("public")
    cls = PublicResearchManifest if public else MetaResearchManifest
    canonical = (
        canonical_public_research_manifest_json if public else canonical_research_manifest_json
    )
    value = cls.model_validate_json(BASELINE[key])
    assert value.scientific_context_profiles is None
    assert "scientific_context_profiles" not in value.model_dump(mode="json")
    assert canonical(value) == BASELINE[key]
    assert len(value.task_profiles) == 5


@pytest.mark.parametrize("kind", TASK_KINDS)
def test_every_old_controller_and_profile_digest_is_unchanged(kind):
    assert controller_protocol_sha256(kind) == BASELINE["controllers"][kind]
    proposal, _, _, _, _, _ = research_fixture()
    profile = next(
        item for item in derive_task_profiles(proposal) if item.profile.task_kind == kind
    )
    expected = next(
        item
        for item in json.loads(BASELINE["private"])["task_profiles"]
        if item["profile"]["task_kind"] == kind
    )
    assert profile.model_dump(mode="json") == expected


def test_old_ladder_controller_digest_is_unchanged():
    assert discovery_controller_sha256() == BASELINE["ladder_controller"]


@pytest.mark.parametrize("ladder", [False, True])
def test_private_builder_absence_does_not_trigger_any_new_derivation(monkeypatch, ladder):
    import polymath.meta_research_contracts as contracts

    def forbidden(*args):
        pytest.fail("Old private builder tried new scientific profile derivation")

    monkeypatch.setattr(contracts, "derive_scientific_context_profiles", forbidden)
    proposal, policy, canary, review, attestation, old = research_fixture()
    value = build_research_manifest(
        old.readiness_id,
        proposal,
        policy=policy,
        native_control_epoch=old.native_control_epoch,
        canary=canary,
        review=review,
        provider_account_attestation=attestation,
        valid_until=old.valid_until,
        discovery=ARCHIVED_DISCOVERY if ladder else None,
    )
    assert (
        canonical_research_manifest_json(value)
        == BASELINE["private_ladder" if ladder else "private"]
    )


@pytest.mark.parametrize("ladder", [False, True])
def test_public_builder_absence_does_not_trigger_any_new_derivation(monkeypatch, ladder):
    import polymath.meta_research_contracts as contracts

    def forbidden(*args):
        pytest.fail("Old public builder tried new scientific profile derivation")

    monkeypatch.setattr(contracts, "derive_scientific_context_profiles", forbidden)
    value = public_fixture(discovery=ARCHIVED_DISCOVERY if ladder else None)
    assert (
        canonical_public_research_manifest_json(value.manifest)
        == BASELINE["public_ladder" if ladder else "public"]
    )


def test_operator_marker_cannot_supply_profile_or_financial_authority():
    marker = ScientificContextIntent()
    assert marker.model_dump() == {"protocol": "polymath-scientific-context-profiles-v1"}
    for additional in (
        {"bindings": []},
        {"enabled": True},
        {"max_model_calls": 20},
        {"model_id": "fake"},
    ):
        with pytest.raises(ValidationError):
            ScientificContextIntent(**additional)


@pytest.mark.parametrize(
    "activation,capabilities",
    [
        (None, ()),
        ("a" * 64, ()),
        ("a" * 64, SCIENTIFIC_CAPABILITIES),
        ("a" * 64, (SCIENTIFIC_CAPABILITIES[1],)),
    ],
)
def test_protocol_projection_is_exact_three_keys_and_contains_no_private_facts(
    activation, capabilities
):
    value = PublicResearchProtocols(activation_sha256=activation, capabilities=capabilities)
    data = value.model_dump(mode="json")
    assert data == {
        "protocol": "polymath-research-protocols-v1",
        "activation_sha256": activation,
        "capabilities": list(capabilities),
    }
    assert PublicResearchProtocols.model_validate_json(json.dumps(data)) == value


@pytest.mark.parametrize(
    "changes",
    [
        {"capabilities": ["unknown"]},
        {"capabilities": [SCIENTIFIC_CAPABILITIES[0], SCIENTIFIC_CAPABILITIES[0]]},
        {"capabilities": list(reversed(SCIENTIFIC_CAPABILITIES))},
        {"activation_sha256": None, "capabilities": [SCIENTIFIC_CAPABILITIES[0]]},
        {"activation_sha256": "A" * 64},
        {"account_id": "private-ledger"},
        {"actor_id": "private-publisher"},
        {"profile_sha256": "f" * 64},
        {"protocol": "polymath-research-access-v1"},
    ],
)
def test_protocol_projection_rejects_unknown_or_mis_scoped_metadata(changes):
    data = {
        "protocol": "polymath-research-protocols-v1",
        "activation_sha256": "a" * 64,
        "capabilities": [],
        **changes,
    }
    with pytest.raises(ValidationError):
        PublicResearchProtocols.model_validate_json(json.dumps(data))
