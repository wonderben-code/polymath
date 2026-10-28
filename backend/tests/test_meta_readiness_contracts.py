"""Pure operator-attestation arithmetic/schema checks; no provider facts verified."""

from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from polymath.meta_readiness_contracts import (
    BUDGET_LIMITED_STOP_NOTICE,
    META_COMPLETIONS_URL,
    MetaReadinessProposal,
    MetaReadinessQuote,
    ReadinessError,
    ReadinessEvidence,
    ReadinessPolicy,
    canonical_proposal_json,
    proposal_sha256,
    quote_proposal,
    validate_proposal_time,
)

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)


def proposal_data(*, now=None):
    """Reusable ordinary HTTP JSON payload; all numerical provider facts synthetic."""
    now = now or NOW
    return {
        "protocol": "polymath-meta-readiness-v1",
        "model_id": "fixture-attested-model",
        "endpoint": META_COMPLETIONS_URL,
        "currency": "USD",
        "usage_contract": "prompt_completion_inclusive_v1",
        "observed_at": (now - timedelta(minutes=1)).isoformat(),
        "valid_until": (now + timedelta(hours=1)).isoformat(),
        "billable_bounds": {
            "input_tokens_absolute": 300000,
            "output_tokens_absolute": 4096,
            "evidence": [{"url": "https://example.org/bounds", "content_sha256": "a" * 64}],
        },
        "pricing": {
            "input_microdollars_per_million": 1000000,
            "output_microdollars_per_million": 2000000,
            "evidence": [{"url": "https://example.org/pricing", "content_sha256": "b" * 64}],
        },
        "usage_evidence": [
            {"url": "https://example.org/protocol-and-inclusive-usage", "content_sha256": "c" * 64}
        ],
        "application_limits": {
            "max_output_tokens": 4096,
            "max_context_characters": 90000,
            "max_model_calls": 12,
            "request_timeout_seconds": 45,
        },
        "account": {
            "account_id": "fixture-account",
            "hard_cap_microdollars": 150000000,
            "daily_cap_microdollars": 25000000,
            "run_cap_microdollars": 5000000,
            "max_in_flight": 1,
        },
        "native_status": "not_run",
        "execution_available": False,
    }


def proposal(**changes):
    return MetaReadinessProposal.model_validate(proposal_data() | changes)


def test_explicit_http_payload_accepts_arrays_and_utc_strings_without_native_authority():
    value = proposal()
    assert isinstance(value.billable_bounds.evidence, tuple)
    assert value.observed_at.tzinfo is not None
    assert value.execution_available is False and value.native_status == "not_run"
    assert value.model_id == "fixture-attested-model"
    assert MetaReadinessProposal.model_validate_json(value.model_dump_json()) == value
    assert value.model_dump(mode="json")["application_limits"]["request_timeout_seconds"] == 45.0


def test_separate_ceiling_components_and_reviewed_maximum_run_quote_are_exact():
    value = proposal()
    quote = quote_proposal(value)
    assert quote.input_reservation_microdollars == 300000
    assert quote.output_reservation_microdollars == 8192
    assert quote.per_call_reservation_microdollars == 308192
    assert quote.max_run_reservation_microdollars == 3698304
    assert quote.full_run_reservation_fits is True and quote.budget_limited_stop_notice is None
    assert quote.proposal_sha256 == proposal_sha256(value)
    assert MetaReadinessQuote.model_validate_json(quote.model_dump_json()) == quote
    assert quote.execution_available is False and quote.native_status == "not_run"


def test_each_usage_counter_rounds_up_independently_using_integer_arithmetic():
    data = proposal_data()
    data["billable_bounds"] |= {"input_tokens_absolute": 1, "output_tokens_absolute": 16}
    data["pricing"] |= {"input_microdollars_per_million": 1, "output_microdollars_per_million": 1}
    data["application_limits"]["max_output_tokens"] = 16
    quote = quote_proposal(MetaReadinessProposal.model_validate(data))
    assert quote.input_reservation_microdollars == quote.output_reservation_microdollars == 1
    assert quote.per_call_reservation_microdollars == 2  # combined ceil would be1


def test_one_call_fit_allows_honest_budget_limited_early_stop_without_promising_all_calls():
    data = proposal_data()
    data["account"]["run_cap_microdollars"] = 308192
    quote = quote_proposal(MetaReadinessProposal.model_validate(data))
    assert quote.per_call_reservation_microdollars == data["account"]["run_cap_microdollars"]
    assert quote.max_run_reservation_microdollars == 3698304
    assert quote.full_run_reservation_fits is False
    assert quote.budget_limited_stop_notice == BUDGET_LIMITED_STOP_NOTICE


def test_billable_absolute_input_is_not_inferred_from_application_characters():
    first = proposal()
    data = proposal_data()
    data["application_limits"]["max_context_characters"] = 1000
    second = MetaReadinessProposal.model_validate(data)
    assert (
        quote_proposal(first).per_call_reservation_microdollars
        == quote_proposal(second).per_call_reservation_microdollars
    )
    assert (
        first.billable_bounds.input_tokens_absolute == second.billable_bounds.input_tokens_absolute
    )


def test_canonical_hash_covers_proposal_fields_without_current_usage_or_quote_envelope():
    value = proposal()
    encoded = canonical_proposal_json(value)
    expected = json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert encoded == expected
    assert proposal_sha256(value) == hashlib.sha256(expected.encode()).hexdigest()
    assert "reservation_microdollars" not in encoded and "committed" not in encoded
    assert proposal_sha256(value) != proposal_sha256(proposal(model_id="another-attested-model"))


def test_policy_defaults_are_disabled_and_never_make_a_quote_execution_authority():
    policy = ReadinessPolicy()
    assert policy.preparation_enabled is False and policy.allowed_actor_ids == ()
    assert policy.max_hard_cap_microdollars == 150000000
    quote = quote_proposal(proposal(), policy=policy)
    assert quote.execution_available is False
    with pytest.raises(FrozenInstanceError):
        policy.preparation_enabled = True
    with pytest.raises(ValidationError, match="frozen"):
        proposal().model_id = "changed"


def test_existing_digit_start_and_100_character_operator_ids_are_supported():
    actor = "7" + "x" * 99
    assert ReadinessPolicy(
        preparation_enabled=True, allowed_actor_ids=(actor,)
    ).allowed_actor_ids == (actor,)


@pytest.mark.parametrize(
    "changes",
    [
        {"preparation_enabled": 1},
        {"allowed_actor_ids": ["operator"]},
        {"allowed_actor_ids": ("duplicate", "duplicate")},
        {"allowed_actor_ids": ("bad/actor",)},
        {"allowed_actor_ids": tuple(f"actor-{index}" for index in range(11))},
        {"max_hard_cap_microdollars": 150000001},
        {"max_hard_cap_microdollars": True},
    ],
)
def test_server_policy_rejects_mutable_or_unbounded_configuration(changes):
    with pytest.raises(ValueError):
        ReadinessPolicy(**changes)


def test_server_policy_can_only_lower_the_proposal_hard_cap():
    with pytest.raises(ReadinessError, match="readiness_hard_cap_exceeds_policy"):
        quote_proposal(proposal(), policy=ReadinessPolicy(max_hard_cap_microdollars=25000000))


@pytest.mark.parametrize(
    "group,field",
    [
        (None, "model_id"),
        (None, "observed_at"),
        (None, "valid_until"),
        (None, "usage_evidence"),
        ("billable_bounds", "input_tokens_absolute"),
        ("billable_bounds", "output_tokens_absolute"),
        ("billable_bounds", "evidence"),
        ("pricing", "input_microdollars_per_million"),
        ("pricing", "output_microdollars_per_million"),
        ("pricing", "evidence"),
        ("application_limits", "max_model_calls"),
        ("application_limits", "max_output_tokens"),
        ("application_limits", "max_context_characters"),
        ("application_limits", "request_timeout_seconds"),
        ("account", "account_id"),
        ("account", "max_in_flight"),
    ],
)
def test_no_provider_or_account_fact_is_guessed_when_missing(group, field):
    data = proposal_data()
    (data if group is None else data[group]).pop(field)
    with pytest.raises(ValidationError, match="Field required"):
        MetaReadinessProposal.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_available", True),
        ("execution_available", 0),
        ("execution_available", "false"),
        ("native_status", "verified"),
        ("currency", "EUR"),
        ("usage_contract", "excludes_reasoning"),
        ("endpoint", "https://api.meta.ai.evil/v1/chat/completions"),
        ("endpoint", "https://api.meta.ai/v1/chat/completions?api_key=secret"),
        ("model_id", ""),
        ("model_id", " model "),
        ("model_id", "x" * 121),
    ],
)
def test_native_status_endpoint_usage_and_model_cannot_be_reinterpreted(field, value):
    data = proposal_data() | {field: value}
    with pytest.raises(ValidationError):
        MetaReadinessProposal.model_validate(data)


@pytest.mark.parametrize(
    "group,field,value",
    [
        ("pricing", "input_microdollars_per_million", 0),
        ("pricing", "output_microdollars_per_million", -1),
        ("pricing", "input_microdollars_per_million", 1.0),
        ("pricing", "input_microdollars_per_million", True),
        ("billable_bounds", "input_tokens_absolute", 0),
        ("billable_bounds", "output_tokens_absolute", "4096"),
        ("billable_bounds", "input_tokens_absolute", 2**63),
        ("application_limits", "max_output_tokens", 4097),
        ("application_limits", "max_model_calls", 21),
        ("application_limits", "max_context_characters", 90001),
        ("application_limits", "request_timeout_seconds", float("nan")),
        ("account", "hard_cap_microdollars", 150000001),
        ("account", "max_in_flight", 101),
        ("account", "account_id", "unaddressable/slash"),
    ],
)
def test_scalar_limits_are_strict_and_bounded(group, field, value):
    data = proposal_data()
    data[group][field] = value
    with pytest.raises(ValidationError):
        MetaReadinessProposal.model_validate(data)


@pytest.mark.parametrize(
    "changes",
    [
        {"run_cap_microdollars": 308191},
        {"run_cap_microdollars": 25000001},
        {"daily_cap_microdollars": 150000001},
    ],
)
def test_one_call_and_ordered_nominal_caps_are_required(changes):
    data = proposal_data()
    data["account"] |= changes
    with pytest.raises(ValidationError):
        MetaReadinessProposal.model_validate(data)


def test_absolute_billable_output_must_cover_requested_application_output():
    data = proposal_data()
    data["billable_bounds"]["output_tokens_absolute"] = 4095
    with pytest.raises(ValidationError, match="absolute billable output"):
        MetaReadinessProposal.model_validate(data)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/pricing",
        "https://user:secret@example.org/pricing",
        "https://example.org/pricing?key=secret",
        "https://example.org/pricing#token",
        "https://example.org:8443/pricing",
        "https://example.org/../pricing",
        "https://example.org/pricing\n",
        "https:///missing-host",
    ],
)
def test_evidence_references_are_uncredentialed_https_data_not_fetch_instructions(url):
    with pytest.raises(ValidationError):
        ReadinessEvidence(url=url, content_sha256="a" * 64)


def test_evidence_reference_normalization_and_required_hash_are_deterministic():
    value = ReadinessEvidence(url="https://EXAMPLE.org:443/pricing", content_sha256="a" * 64)
    assert value.url == "https://example.org/pricing"
    for digest in ("A" * 64, "a" * 63, "not-a-hash"):
        with pytest.raises(ValidationError):
            ReadinessEvidence(url="https://example.org/pricing", content_sha256=digest)


def test_one_document_cannot_have_conflicting_attested_hashes_across_purposes():
    data = proposal_data()
    data["usage_evidence"][0]["url"] = data["pricing"]["evidence"][0]["url"]
    with pytest.raises(ValidationError, match="conflicting content hashes"):
        MetaReadinessProposal.model_validate(data)
    data["usage_evidence"][0]["content_sha256"] = "b" * 64
    assert MetaReadinessProposal.model_validate(data).usage_evidence[0].content_sha256 == "b" * 64


@pytest.mark.parametrize("group", ["pricing", "billable_bounds", None])
@pytest.mark.parametrize("count", [0, 9])
def test_every_evidence_purpose_is_explicit_and_bounded(group, count):
    data = proposal_data()
    values = [
        {"url": f"https://example.org/document-{index}", "content_sha256": "d" * 64}
        for index in range(count)
    ]
    if group is None:
        data["usage_evidence"] = values
    else:
        data[group]["evidence"] = values
    with pytest.raises(ValidationError):
        MetaReadinessProposal.model_validate(data)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-28T12:00:00",
        "2026-10-28T13:00:00+01:00",
        "2026-10-28T12:00:00-00:00",
        "2026-10-28 12:00:00Z",
        12345,
        datetime(2026, 10, 28, 12),
    ],
)
def test_timestamps_require_explicit_aware_utc(timestamp):
    data = proposal_data() | {"observed_at": timestamp}
    with pytest.raises(ValidationError):
        MetaReadinessProposal.model_validate(data)


def test_time_admission_is_explicit_against_caller_supplied_database_clock():
    value = proposal()
    validate_proposal_time(value, NOW)
    with pytest.raises(ReadinessError, match="readiness_observation_in_future"):
        validate_proposal_time(value, NOW - timedelta(minutes=2))
    with pytest.raises(ReadinessError, match="readiness_proposal_expired"):
        validate_proposal_time(value, NOW + timedelta(hours=1))
    data = proposal_data()
    data["valid_until"] = data["observed_at"]
    with pytest.raises(ValidationError, match="end after"):
        MetaReadinessProposal.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("per_call_reservation_microdollars", 1),
        ("max_run_reservation_microdollars", 1),
        ("full_run_reservation_fits", False),
        ("proposal_sha256", "0" * 64),
        ("execution_available", True),
        ("native_status", "passed"),
    ],
)
def test_output_quote_cannot_fabricate_cost_hash_fit_or_native_permission(field, value):
    quote = quote_proposal(proposal()).model_dump(mode="json") | {field: value}
    with pytest.raises(ValidationError):
        MetaReadinessQuote.model_validate(quote)


@pytest.mark.parametrize(
    "field",
    [
        "api_key",
        "token",
        "live_enabled",
        "activate",
        "provider_credits_verified",
        "hard_cap_verified",
    ],
)
def test_secret_fields_and_verification_or_activation_toggles_are_forbidden(field):
    with pytest.raises(ValidationError, match="Extra inputs") as caught:
        MetaReadinessProposal.model_validate(proposal_data() | {field: "sensitive-fixture-value"})
    assert "sensitive-fixture-value" not in str(caught.value)


def test_pure_preparation_never_fetches_a_reference_or_constructs_native_model(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Pure preparation attempted network access")

    monkeypatch.setattr(socket, "socket", denied)
    assert quote_proposal(proposal()).execution_available is False


def test_proposed_endpoint_matches_existing_transport_without_changing_legacy_worker_costs():
    from polymath.research.meta import META_COMPLETIONS_URL as existing

    assert META_COMPLETIONS_URL == existing
