"""Pure public-delegation schemas and accounting limits; no live facts or calls."""

import json
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_meta_research_contracts import NOW, READINESS_ID, research_fixture

import polymath.public_research_contracts as contracts
from polymath.discovery_contracts import DiscoveryIntent
from polymath.meta_readiness_contracts import ReadinessPolicy
from polymath.meta_research_contracts import (
    TASK_KINDS,
    MetaResearchManifest,
    canonical_research_manifest_json,
)
from polymath.public_research_contracts import (
    MAX_PUBLIC_DELEGATION_SECONDS,
    PUBLIC_ACCESS_REASONS,
    PublicAccessLimits,
    PublicLimits,
    PublicResearchAccess,
    PublicResearchManifest,
    PublicResearchPolicy,
    PublicResourceLimits,
    build_public_research_manifest,
    canonical_public_research_manifest_json,
    public_limits_sha256,
    public_policy_sha256,
    public_profile_for_task,
    public_research_manifest_sha256,
    public_sponsor_policy_sha256,
    validate_current_public_research_manifest,
    validate_public_research_manifest,
)


def limits_data(**changes):
    """Explicit synthetic ceilings; production supplies reviewed amounts."""
    return {
        "public_total_cap_microdollars": 50000000,
        "public_daily_cap_microdollars": 20000000,
        "session_total_cap_microdollars": 5000000,
        "session_daily_cap_microdollars": 5000000,
        **changes,
    }


def public_fixture(*, now=None, discovery=None):
    now = now or NOW
    proposal, native, canary, review, attestation, private = research_fixture(now=now)
    public = PublicResearchPolicy(enabled=True)
    limits = PublicLimits.model_validate(limits_data())
    sponsor = public_sponsor_policy_sha256(
        operator_fingerprint="a" * 64,
        actor_id="fixture-publisher",
        readiness_policy=ReadinessPolicy(
            preparation_enabled=True, allowed_actor_ids=("fixture-publisher",)
        ),
    )
    manifest = build_public_research_manifest(
        READINESS_ID,
        proposal,
        research_policy=native,
        public_policy=public,
        sponsor_policy_sha256=sponsor,
        native_control_epoch=4,
        canary=canary,
        review=review,
        provider_account_attestation=attestation,
        public_limits=limits,
        valid_until=now + timedelta(minutes=30),
        now=now,
        discovery=discovery,
    )
    return SimpleNamespace(
        now=now,
        proposal=proposal,
        research_policy=native,
        public_policy=public,
        sponsor_policy_sha256=sponsor,
        public_limits=limits,
        canary=canary,
        review=review,
        attestation=attestation,
        manifest=manifest,
        private=private,
    )


def validate_current(value, **changes):
    arguments = {
        "research_policy": value.research_policy,
        "public_policy": value.public_policy,
        "sponsor_policy_sha256": value.sponsor_policy_sha256,
        "now": value.now,
        **changes,
    }
    validate_current_public_research_manifest(value.manifest, **arguments)


def access_data(**changes):
    return {
        "protocol": "polymath-research-access-v1",
        "audience": "public_sessions",
        "available": True,
        "reason": None,
        "expected_activation_sha256": "b" * 64,
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "limits": {
            "session_total_cap_microdollars": "5000000",
            "session_daily_cap_microdollars": "2000000",
            "max_jobs_per_session_day": 12,
            "max_active_jobs_per_session": 1,
            "task_kinds": list(TASK_KINDS),
            "discovery_intent": None,
        },
        "remaining_session_allowance_microdollars": "1000000",
        "remaining_session_jobs_today": 0,
        "retry_after_seconds": None,
        **changes,
    }


def test_disabled_policy_has_no_key_model_money_or_authority_and_is_immutable():
    policy = PublicResearchPolicy()
    assert policy.enabled is False and policy.max_delegation_seconds == 86400
    assert not any(hasattr(policy, name) for name in ("api_key", "model_id", "native_account_id"))
    with pytest.raises(FrozenInstanceError):
        policy.enabled = True
    with pytest.raises(ValidationError):
        PublicLimits.model_validate({})


def test_builder_has_distinct_protocol_and_explicit_durable_confirmation():
    value = public_fixture()
    manifest = value.manifest
    assert manifest.protocol == "polymath-meta-public-research-v1"
    assert manifest.audience == "public_sessions"
    assert manifest.authorization_mode == "durable_owner_delegation"
    assert manifest.continues_after_operator_logout is True
    assert not isinstance(manifest, MetaResearchManifest)
    assert manifest.valid_until > value.private.valid_until  # No 20-minute login cap.
    with pytest.raises(ValidationError):
        MetaResearchManifest.model_validate_json(manifest.model_dump_json())


def test_private_bytes_and_five_profiles_remain_unchanged_by_public_construction():
    value = public_fixture()
    before = canonical_research_manifest_json(value.private)
    assert value.manifest.task_profiles == value.private.task_profiles
    canonical_public_research_manifest_json(value.manifest)
    assert canonical_research_manifest_json(value.private) == before
    assert "public_limits" not in before and "authorization_mode" not in before


@pytest.mark.parametrize("confirmation", [None, False, 0, 1, "true"])
def test_durable_confirmation_never_accepts_missing_or_coerced_truth(confirmation):
    raw = public_fixture().manifest.model_dump(mode="json")
    if confirmation is None:
        raw.pop("continues_after_operator_logout")
    else:
        raw["continues_after_operator_logout"] = confirmation
    with pytest.raises(ValidationError):
        PublicResearchManifest.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    "change",
    [
        {"audience": "operator_browser"},
        {"authorization_mode": "operator_session"},
        {"protocol": "polymath-meta-research-activation-v1"},
        {"endpoint": "https://example.org/chat/completions"},
        {"api_key": "synthetic-private-key-never-accepted"},
        {"actor_id": "caller-supplied-actor"},
        {"pricing_verified": True},
        {"execution_available": True},
    ],
)
def test_public_manifest_forbids_authority_substitution_and_extra_private_fields(change):
    raw = public_fixture().manifest.model_dump(mode="json") | change
    with pytest.raises(ValidationError):
        PublicResearchManifest.model_validate_json(json.dumps(raw))


def test_manifest_and_limit_hashes_are_exact_deterministic_and_credential_free():
    value = public_fixture()
    raw = value.manifest.model_dump(mode="json")
    reordered = dict(reversed(tuple(raw.items())))
    restored = PublicResearchManifest.model_validate_json(json.dumps(reordered))
    assert public_research_manifest_sha256(restored) == public_research_manifest_sha256(
        value.manifest
    )
    text = canonical_public_research_manifest_json(restored)
    assert value.research_policy.api_key.get_secret_value() not in text
    assert "discovery_ladder_profile" not in raw
    assert public_limits_sha256(value.public_limits) != public_limits_sha256(
        PublicLimits.model_validate(limits_data(session_daily_cap_microdollars=4000000))
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"public_total_cap_microdollars": True},
        {"public_daily_cap_microdollars": 0},
        {"session_total_cap_microdollars": "5000000"},
        {"session_daily_cap_microdollars": 150000001},
        {"public_total_cap_microdollars": 1000000},
        {"public_daily_cap_microdollars": 4000000},
        {"max_active_jobs_per_session": True},
        {"max_active_jobs_per_session": 2},
        {"max_jobs_per_day": 5},
        {"max_active_jobs": 1},
        {"max_queued_jobs": 5},
    ],
)
def test_limits_reject_coercion_hierarchy_changes_and_active_owner_widening(changes):
    with pytest.raises(ValidationError):
        PublicLimits.model_validate(limits_data(**changes))


@pytest.mark.parametrize(
    "field,value",
    [
        ("submit_session_per_minute", True),
        ("read_service_per_minute", 0),
        ("sse_max_seconds", 301),
        ("body_max_bytes", 65537),
        ("control_service_per_minute", "120"),
    ],
)
def test_resource_limits_are_strict_and_cannot_raise_server_ceilings(field, value):
    with pytest.raises(ValidationError):
        PublicResourceLimits.model_validate({field: value})


def test_resource_policy_hash_binds_body_sse_and_attempt_limits():
    original = PublicResearchPolicy(enabled=True)
    for change in (
        {"body_max_bytes": 4096},
        {"sse_max_seconds": 30},
        {"submit_session_per_minute": 2},
    ):
        resources = PublicResourceLimits.model_validate(change)
        assert public_policy_sha256(original) != public_policy_sha256(
            replace(original, resources=resources)
        )
    assert public_policy_sha256(original) != public_policy_sha256(replace(original, enabled=False))


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": 1},
        {"max_delegation_seconds": True},
        {"max_delegation_seconds": 14 * 86400 + 1},
        {"max_jobs_per_day": 51},
        {"resources": {}},
    ],
)
def test_public_policy_cannot_be_constructed_with_coerced_or_excessive_ceilings(changes):
    with pytest.raises(ValueError):
        PublicResearchPolicy(**changes)


def test_fresh_check_requires_public_native_flags_original_key_and_sponsor_binding():
    value = public_fixture()
    validate_current(value)
    with pytest.raises(ValueError, match="public_research_disabled"):
        validate_current(value, public_policy=replace(value.public_policy, enabled=False))
    with pytest.raises(ValueError, match="public_research_disabled"):
        validate_current(
            value, research_policy=replace(value.research_policy, paid_research_enabled=False)
        )
    with pytest.raises(ValueError, match="public_research_authority_changed"):
        validate_current(value, sponsor_policy_sha256="f" * 64)
    with pytest.raises(ValueError, match="public_research_authority_changed"):
        validate_current(
            value, research_policy=replace(value.research_policy, model_id="different-model")
        )


def test_fresh_deadline_is_bounded_by_server_and_current_evidence_without_login_dependency():
    value = public_fixture()
    with pytest.raises(ValueError):
        validate_current(value, now=value.manifest.valid_until)
    with pytest.raises(ValueError):
        validate_current(value, now=value.now - timedelta(hours=1))
    with pytest.raises(ValueError, match="public_research_authority_changed"):
        validate_current(
            value, public_policy=replace(value.public_policy, max_delegation_seconds=60)
        )
    assert value.manifest.valid_until > value.private.valid_until


def test_exact_twenty_four_hour_builder_limit_is_checked_using_supplied_server_clock():
    value = public_fixture()
    later = value.now + timedelta(days=2)
    proposal = value.proposal.model_copy(update={"valid_until": later})
    review = value.review.model_copy(update={"valid_until": later})
    attestation = value.attestation.model_copy(update={"valid_until": later})
    kwargs = dict(
        research_policy=value.research_policy,
        public_policy=value.public_policy,
        sponsor_policy_sha256=value.sponsor_policy_sha256,
        native_control_epoch=4,
        canary=value.canary,
        review=review,
        provider_account_attestation=attestation,
        public_limits=value.public_limits,
        now=value.now,
    )
    deadline = value.now + timedelta(seconds=MAX_PUBLIC_DELEGATION_SECONDS)
    manifest = build_public_research_manifest(
        READINESS_ID, proposal, valid_until=deadline, **kwargs
    )
    assert manifest.valid_until == deadline
    with pytest.raises(ValueError, match="public_research_authority_expired"):
        build_public_research_manifest(
            READINESS_ID, proposal, valid_until=deadline + timedelta(seconds=1), **kwargs
        )


def test_historical_public_parse_and_profile_selection_never_rederive_current_prompts(monkeypatch):
    value = public_fixture(discovery=DiscoveryIntent())
    raw = value.manifest.model_dump_json()
    monkeypatch.setattr(
        contracts, "derive_task_profiles", lambda _: tuple(reversed(value.manifest.task_profiles))
    )
    monkeypatch.setattr(
        contracts,
        "derive_discovery_ladder_profile",
        lambda *_: pytest.fail("Historical parse derived today's prompt"),
    )
    restored = PublicResearchManifest.model_validate_json(raw)
    validate_public_research_manifest(restored, value.proposal)
    assert (
        public_profile_for_task(restored, "research", discovery=DiscoveryIntent()).max_actions == 20
    )
    assert public_profile_for_task(restored, "research").max_actions == 16
    assert public_research_manifest_sha256(restored) == public_research_manifest_sha256(
        value.manifest
    )
    with pytest.raises(ValueError, match="public_research_profile_changed"):
        validate_current(value)


def test_discovery_profile_is_optional_explicit_and_never_inferred_from_research_kind():
    ordinary = public_fixture()
    with pytest.raises(ValueError, match="public_discovery_intent_exceeds_approved_profile"):
        public_profile_for_task(ordinary.manifest, "research", discovery=DiscoveryIntent())
    ladder = public_fixture(discovery=DiscoveryIntent(max_stages=2, max_depth=1))
    assert "discovery_ladder_profile" in json.loads(
        canonical_public_research_manifest_json(ladder.manifest)
    )
    with pytest.raises(ValueError):
        public_profile_for_task(ladder.manifest, "research", discovery=DiscoveryIntent())
    with pytest.raises(ValueError):
        public_profile_for_task(
            ladder.manifest, "formalise", discovery=DiscoveryIntent(max_stages=2, max_depth=1)
        )


def test_same_profile_coverage_and_proposal_reservation_are_preserved_for_every_task():
    value = public_fixture()
    assert (
        tuple(public_profile_for_task(value.manifest, kind).task_kind for kind in TASK_KINDS)
        == TASK_KINDS
    )
    raw = value.manifest.model_dump(mode="json")
    raw["public_limits"]["session_daily_cap_microdollars"] = 1
    with pytest.raises(
        ValidationError, match="public_caps_cannot_cover_reviewed_native_reservation"
    ):
        PublicResearchManifest.model_validate_json(json.dumps(raw))


def test_sponsor_hash_uses_only_server_fingerprint_and_current_pure_role_policy():
    role = ReadinessPolicy(preparation_enabled=True, allowed_actor_ids=("fixture-publisher",))
    first = public_sponsor_policy_sha256(
        operator_fingerprint="a" * 64, actor_id="fixture-publisher", readiness_policy=role
    )
    second = public_sponsor_policy_sha256(
        operator_fingerprint="b" * 64, actor_id="fixture-publisher", readiness_policy=role
    )
    assert first != second and len(first) == 64
    with pytest.raises(ValueError):
        public_sponsor_policy_sha256(
            operator_fingerprint="a" * 64, actor_id="unlisted", readiness_policy=role
        )


def test_access_available_with_zero_new_job_slots_still_supports_resume():
    value = PublicResearchAccess.model_validate(access_data())
    assert value.available and value.remaining_session_jobs_today == 0
    assert set(value.limits.model_dump()) == {
        "session_total_cap_microdollars",
        "session_daily_cap_microdollars",
        "max_jobs_per_session_day",
        "max_active_jobs_per_session",
        "task_kinds",
        "discovery_intent",
    }


@pytest.mark.parametrize("reason", sorted(PUBLIC_ACCESS_REASONS))
def test_closed_projection_accepts_only_frozen_safe_reasons(reason):
    value = PublicResearchAccess.model_validate(
        access_data(available=False, reason=reason, remaining_session_allowance_microdollars="0")
    )
    assert value.reason == reason and value.available is False


def test_private_access_projection_never_returns_private_authority_or_public_limits():
    empty = {
        key: None
        for key in (
            "expected_activation_sha256",
            "expires_at",
            "limits",
            "remaining_session_allowance_microdollars",
            "remaining_session_jobs_today",
        )
    }
    private = PublicResearchAccess.model_validate(
        access_data(
            audience="operator_browser", available=False, reason="operator_required", **empty
        )
    )
    assert private.limits is None and private.expected_activation_sha256 is None
    with pytest.raises(ValidationError):
        PublicResearchAccess.model_validate(
            access_data(audience="operator_browser", available=False, reason="operator_required")
        )


def test_closed_public_projection_keeps_version_without_inventing_unknown_counters():
    value = PublicResearchAccess.model_validate(
        access_data(
            available=False,
            reason="public_research_resource_unavailable",
            remaining_session_allowance_microdollars=None,
            remaining_session_jobs_today=None,
        )
    )
    assert value.limits is not None and value.expected_activation_sha256 is not None
    assert value.remaining_session_jobs_today is None


@pytest.mark.parametrize(
    "change",
    [
        {"available": True, "remaining_session_allowance_microdollars": "0"},
        {"available": False, "reason": None},
        {"available": False, "reason": "private_actor_identity"},
        {"available": False, "reason": "public_research_stopped", "limits": None},
        {"remaining_session_allowance_microdollars": "0500000"},
        {"remaining_session_allowance_microdollars": "6000000"},
        {"remaining_session_jobs_today": True},
        {"remaining_session_jobs_today": 13},
        {"account_id": "must-not-leak"},
        {"operator_csrf_token": "must-not-leak"},
    ],
)
def test_projection_rejects_scope_mixing_coercion_or_private_fields(change):
    with pytest.raises(ValidationError):
        PublicResearchAccess.model_validate(access_data(**change))


def test_access_caps_are_canonical_strings_and_active_owner_rule_is_strict():
    value = access_data()["limits"]
    for change in (
        {"session_total_cap_microdollars": 5000000},
        {"session_daily_cap_microdollars": "20000000"},
        {"max_active_jobs_per_session": True},
        {"task_kinds": ["research"] * 5},
        {"public_total_cap_microdollars": 50000000},
    ):
        with pytest.raises(ValidationError):
            PublicAccessLimits.model_validate(value | change)


def test_a_server_may_allow_one_delegation_to_span_a_judging_period():
    from polymath.config import Settings
    from polymath.public_research_contracts import (
        MAX_PUBLIC_DELEGATION_CEILING_SECONDS,
        PublicResearchPolicy,
    )

    assert PublicResearchPolicy().max_delegation_seconds == MAX_PUBLIC_DELEGATION_SECONDS
    two_weeks = PublicResearchPolicy(max_delegation_seconds=MAX_PUBLIC_DELEGATION_CEILING_SECONDS)
    assert two_weeks.max_delegation_seconds == 14 * 86400
    with pytest.raises(ValueError, match="invalid_public_research_policy"):
        PublicResearchPolicy(max_delegation_seconds=MAX_PUBLIC_DELEGATION_CEILING_SECONDS + 1)
    assert Settings(_env_file=None).public_max_delegation_seconds == 86400
    with pytest.raises(ValueError):
        Settings(_env_file=None, public_max_delegation_seconds=15 * 86400)
